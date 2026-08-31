"""Tests for the Knowledge Base service and ResearchAgent (Phase 3)."""
from src.services.knowledge_service import KnowledgeService
from src.models import KBContentType


def _seed(service):
    service.add_item(
        title="Guide du Cadre Logique (GAR)", domain="Planification",
        content_type=KBContentType.METHOD,
        content="Le cadre logique structure objectifs, résultats, activités et indicateurs pour la planification de projet.",
    )
    service.add_item(
        title="Norme ISO 26000", domain="Qualité",
        content_type=KBContentType.NORM,
        content="La norme ISO 26000 définit des lignes directrices relatives à la responsabilité sociétale des organisations.",
    )
    service.add_item(
        title="Modèle de rapport d'évaluation socio-économique", domain="Suivi-Évaluation",
        content_type=KBContentType.TEMPLATE,
        content="Gabarit de rapport pour une enquête ménage : méthodologie, résultats, limites, recommandations.",
    )


def test_search_returns_empty_list_on_empty_kb(db_session):
    service = KnowledgeService(db_session)
    assert service.search("cadre logique") == []


def test_search_ranks_relevant_item_first(db_session):
    service = KnowledgeService(db_session)
    _seed(service)
    hits = service.search("planification de projet cadre logique")
    assert hits
    assert hits[0]["title"] == "Guide du Cadre Logique (GAR)"


def test_search_respects_domain_filter(db_session):
    service = KnowledgeService(db_session)
    _seed(service)
    hits = service.search("rapport", domain="Qualité")
    assert all(h["domain"] == "Qualité" for h in hits)


def test_search_returns_nothing_for_unrelated_query(db_session):
    service = KnowledgeService(db_session)
    _seed(service)
    assert service.search("recette de cuisine poulet roti") == []


def test_delete_item(db_session):
    service = KnowledgeService(db_session)
    item = service.add_item(
        title="À supprimer", domain="Test", content_type=KBContentType.REFERENCE, content="x",
    )
    assert service.delete_item(item.id) is True
    assert service.delete_item(item.id) is False  # already gone


def test_answer_has_no_synthesis_without_llm(db_session):
    service = KnowledgeService(db_session)
    _seed(service)
    result = service.answer("comment structurer les objectifs d'un projet ?")
    assert result["sources"]
    assert result["synthesis"] is None


def test_research_agent_reports_empty_kb_honestly(db_session):
    from src.agents import ResearchAgent

    agent = ResearchAgent()
    result = agent.execute({
        "db": db_session, "objective": "x", "raw_request": "y", "domain": None,
        "work_dir": "/tmp/praxis-test-research-empty",
    })
    assert result["status"] == "success"
    assert result["artifacts"] == []
    assert "vide" in result["summary"] or "trouvé" in result["summary"]


def test_research_agent_produces_sources_artifact_when_kb_has_matches(db_session, tmp_path):
    from src.agents import ResearchAgent

    service = KnowledgeService(db_session)
    _seed(service)

    agent = ResearchAgent()
    result = agent.execute({
        "db": db_session,
        "objective": "Expliquer le cadre logique pour la planification de projet",
        "raw_request": "Rédiger une note sur le cadre logique",
        "domain": None,
        "work_dir": str(tmp_path),
    })
    assert result["status"] == "success"
    roles = [a["role"] for a in result["artifacts"]]
    assert "research_sources" in roles
