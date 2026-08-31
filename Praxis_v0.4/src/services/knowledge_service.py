"""
Knowledge Base service - Phase 3 (§6.3 / §9 ResearchAgent)

The KnowledgeBase table (§3.6) already existed in the schema but nothing
ever wrote to or searched it. This is real, working retrieval - not the
pgvector/semantic-embeddings version the design doc describes as the
long-term target, but a genuine TF-IDF lexical search that needs no
external service, no API key, and no model download (this sandbox has no
network access to an embeddings provider or Hugging Face to fetch one).

Upgrade path when a real embeddings API is available: replace
_TfidfIndex with a pgvector similarity query and keep the same
KnowledgeService.search() signature - callers (ResearchAgent, the API
endpoints) would not need to change.
"""
import math
import re
import unicodedata
from collections import Counter
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import KnowledgeBase, KBContentType, KBConfidence
from .llm_client import LLMClient


_STOPWORDS = {
    "le", "la", "les", "un", "une", "des", "de", "du", "et", "ou", "a", "au", "aux",
    "en", "pour", "dans", "sur", "par", "avec", "est", "sont", "ce", "cette", "ces",
    "que", "qui", "quoi", "il", "elle", "ils", "elles", "se", "sa", "son", "ses",
    "au", "aux", "d", "l", "the", "of", "and", "to", "in", "for", "a", "an",
}


def _tokenize(text: str) -> List[str]:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii").lower()
    tokens = re.findall(r"[a-z0-9]{2,}", text)
    return [t for t in tokens if t not in _STOPWORDS]


class _TfidfIndex:
    """Minimal in-memory TF-IDF cosine-similarity index. Rebuilt on every
    search() call - fine for a personal KB of up to a few thousand items;
    not meant to scale further than that (see module docstring)."""

    def __init__(self, documents: List[Tuple[str, str]]):
        # documents: list of (doc_id, text)
        self.doc_ids = [d[0] for d in documents]
        tokenized = [_tokenize(d[1]) for d in documents]
        n_docs = len(tokenized) or 1

        doc_freq: Counter = Counter()
        for tokens in tokenized:
            doc_freq.update(set(tokens))

        self.idf = {
            term: math.log((n_docs + 1) / (freq + 1)) + 1.0
            for term, freq in doc_freq.items()
        }

        self.doc_vectors: List[Dict[str, float]] = []
        for tokens in tokenized:
            tf = Counter(tokens)
            vec = {term: count * self.idf.get(term, 0.0) for term, count in tf.items()}
            norm = math.sqrt(sum(w * w for w in vec.values())) or 1.0
            self.doc_vectors.append({t: w / norm for t, w in vec.items()})

    def query(self, text: str, top_k: int = 5) -> List[Tuple[str, float]]:
        q_tokens = Counter(_tokenize(text))
        if not q_tokens:
            return []
        q_vec = {t: c * self.idf.get(t, 0.0) for t, c in q_tokens.items()}
        norm = math.sqrt(sum(w * w for w in q_vec.values())) or 1.0
        q_vec = {t: w / norm for t, w in q_vec.items()}

        scores = []
        for doc_id, doc_vec in zip(self.doc_ids, self.doc_vectors):
            score = sum(w * doc_vec.get(t, 0.0) for t, w in q_vec.items())
            if score > 0:
                scores.append((doc_id, score))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]


class KnowledgeService:
    def __init__(self, db_session: Session):
        self.db = db_session

    def add_item(
        self, title: str, domain: str, content_type: KBContentType,
        content: str, source_ref: Optional[str] = None,
        tags: Optional[List[str]] = None, summary: Optional[str] = None,
        confidence: KBConfidence = KBConfidence.EXPLORATORY,
    ) -> KnowledgeBase:
        item = KnowledgeBase(
            title=title, domain=domain, content_type=content_type,
            content=content, summary=summary, source_ref=source_ref,
            tags=tags or [], confidence=confidence,
        )
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)
        return item

    def delete_item(self, item_id: str) -> bool:
        item = self.db.query(KnowledgeBase).filter(KnowledgeBase.id == item_id).first()
        if not item:
            return False
        self.db.delete(item)
        self.db.commit()
        return True

    def list_items(self, domain: Optional[str] = None) -> List[KnowledgeBase]:
        query = self.db.query(KnowledgeBase)
        if domain:
            query = query.filter(KnowledgeBase.domain == domain)
        return query.order_by(KnowledgeBase.created_at.desc()).all()

    def search(self, query: str, domain: Optional[str] = None, top_k: int = 5) -> List[Dict]:
        """TF-IDF search over title + summary + content. Returns items with
        a relevance score, most relevant first. Empty list (not an error)
        when the KB has nothing indexable yet."""
        items = self.list_items(domain=domain)
        if not items:
            return []

        documents = [
            (item.id, " ".join(filter(None, [item.title, item.summary, item.content])))
            for item in items
        ]
        index = _TfidfIndex(documents)
        hits = index.query(query, top_k=top_k)

        by_id = {item.id: item for item in items}
        return [
            {
                "id": doc_id, "title": by_id[doc_id].title, "domain": by_id[doc_id].domain,
                "content_type": by_id[doc_id].content_type.value,
                "summary": by_id[doc_id].summary, "content": by_id[doc_id].content,
                "source_ref": by_id[doc_id].source_ref, "score": round(score, 4),
            }
            for doc_id, score in hits
        ]

    def answer(self, query: str, domain: Optional[str] = None, top_k: int = 5) -> Dict:
        """Retrieval, with an optional LLM synthesis pass on top if
        configured. Always returns the raw retrieved items so the answer
        is checkable even when synthesis is unavailable."""
        hits = self.search(query, domain=domain, top_k=top_k)
        result = {"query": query, "sources": hits, "synthesis": None}
        if not hits:
            return result

        client = LLMClient()
        if not client.available:
            return result

        context = "\n\n".join(
            f"[{i+1}] {h['title']} ({h['domain']}): {(h['summary'] or h['content'] or '')[:600]}"
            for i, h in enumerate(hits)
        )
        synthesis = client.complete(
            system=(
                "Tu réponds à une question en te basant UNIQUEMENT sur les extraits fournis. "
                "Cite les sources par leur numéro entre crochets, ex. [1]. "
                "Si les extraits ne suffisent pas à répondre, dis-le clairement."
            ),
            user=f"Question : {query}\n\nExtraits disponibles :\n{context}",
        )
        result["synthesis"] = synthesis
        return result
