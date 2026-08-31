"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  knowledgeApi,
  KB_CONTENT_TYPES,
  KB_CONTENT_TYPE_LABELS,
  KBContentType,
  KnowledgeItem,
  KnowledgeAnswer,
  ApiError,
} from "@/lib/api";

export default function KnowledgePage() {
  const [items, setItems] = useState<KnowledgeItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [title, setTitle] = useState("");
  const [domain, setDomain] = useState("");
  const [contentType, setContentType] = useState<KBContentType>("reference");
  const [content, setContent] = useState("");
  const [adding, setAdding] = useState(false);

  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<KnowledgeAnswer | null>(null);
  const [asking, setAsking] = useState(false);

  async function refresh() {
    try {
      setItems(await knowledgeApi.list());
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Erreur inconnue");
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim() || !domain.trim() || !content.trim()) return;
    setAdding(true);
    try {
      await knowledgeApi.add({ title, domain, content, content_type: contentType });
      setTitle("");
      setDomain("");
      setContent("");
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Échec de l'ajout");
    } finally {
      setAdding(false);
    }
  }

  async function handleDelete(id: string) {
    try {
      await knowledgeApi.remove(id);
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Échec de la suppression");
    }
  }

  async function handleAsk(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim()) return;
    setAsking(true);
    setAnswer(null);
    try {
      setAnswer(await knowledgeApi.ask(question.trim()));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Échec de la recherche");
    } finally {
      setAsking(false);
    }
  }

  return (
    <div>
      <Link href="/" className="nav-link">
        ← Toutes les tâches
      </Link>
      <h2>Base de connaissances</h2>
      <p className="muted">
        Méthodes, normes, gabarits et références que le <code>ResearchAgent</code> peut retrouver
        pour une tâche (recherche lexicale locale — aucune donnée n&apos;est envoyée à l&apos;extérieur).
      </p>

      {error && <div className="error-banner">{error}</div>}

      {/* Ask */}
      <div className="section">
        <h2>Interroger la base</h2>
        <div className="card">
          <form onSubmit={handleAsk} style={{ display: "flex", gap: 8, marginBottom: 12 }}>
            <input
              placeholder="ex. comment structurer les objectifs d'un projet ?"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              style={{ flex: 1 }}
            />
            <button className="button" type="submit" disabled={asking}>
              {asking ? "Recherche..." : "Chercher"}
            </button>
          </form>
          {answer && (
            <div>
              {answer.synthesis && <p>{answer.synthesis}</p>}
              {!answer.synthesis && answer.sources.length > 0 && (
                <p className="muted">Aucune IA configurée — extraits les plus pertinents ci-dessous.</p>
              )}
              {answer.sources.length === 0 && <p className="muted">Rien de pertinent trouvé.</p>}
              {answer.sources.map((s) => (
                <div className="list-row" key={s.id}>
                  <span>
                    {s.title} <span className="muted">({s.domain})</span>
                  </span>
                  <span className="muted">score {s.score}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Add */}
      <div className="section">
        <h2>Ajouter un élément</h2>
        <form onSubmit={handleAdd} className="card">
          <div className="field">
            <label>Titre</label>
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Guide du Cadre Logique (GAR)" />
          </div>
          <div className="field">
            <label>Domaine</label>
            <input value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="Planification" />
          </div>
          <div className="field">
            <label>Type</label>
            <select value={contentType} onChange={(e) => setContentType(e.target.value as KBContentType)}>
              {KB_CONTENT_TYPES.map((t) => (
                <option key={t} value={t}>
                  {KB_CONTENT_TYPE_LABELS[t]}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>Contenu</label>
            <textarea rows={4} value={content} onChange={(e) => setContent(e.target.value)} />
          </div>
          <button className="button" type="submit" disabled={adding}>
            {adding ? "Ajout..." : "Ajouter"}
          </button>
        </form>
      </div>

      {/* List */}
      <div className="section">
        <h2>Éléments ({items?.length ?? 0})</h2>
        <div className="card">
          {items && items.length === 0 && <p className="muted" style={{ margin: 0 }}>Base vide pour l&apos;instant.</p>}
          {items?.map((item) => (
            <div className="list-row" key={item.id}>
              <span>
                {item.title} <span className="badge">{KB_CONTENT_TYPE_LABELS[item.content_type]}</span>{" "}
                <span className="muted">{item.domain}</span>
              </span>
              <button className="button secondary" onClick={() => handleDelete(item.id)}>
                Supprimer
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
