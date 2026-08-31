"""Tests for Praxis v1.0 Phase 4 (docs/PRAXIS_V1_ARCHITECTURE.md §4):
domain routing into one or more of the 11 deliverable poles, dynamic plan
assembly from those poles, and the honest "not yet implemented" stub for
poles without a dedicated agent yet (Phase 5+).
"""
from unittest.mock import patch

from src.services import domain_router
from src.services.orchestrator import Orchestrator
from src.services.task_service import TaskService
from src.services.execution_engine import ExecutionEngine
from src.models import ExpertiseDomain, TaskType


def test_seed_default_domains_creates_exactly_the_eleven_poles(db_session):
    domain_router.seed_default_domains(db_session)
    domains = db_session.query(ExpertiseDomain).all()
    assert len(domains) == 11
    assert {d.code for d in domains} == set(domain_router.POLE_CODES)


def test_seed_default_domains_is_idempotent(db_session):
    domain_router.seed_default_domains(db_session)
    domain_router.seed_default_domains(db_session)
    assert db_session.query(ExpertiseDomain).count() == 11


def test_deterministic_fallback_matches_task_type_without_llm(db_session):
    """No LLM configured in tests - route_domain must fall back to the
    existing TaskType, not guess, and never break for anyone without an
    LLM set up."""
    result = domain_router.route_domain(
        db_session, raw_request="Évaluer l'impact du programme", title="t",
        task_type=TaskType.RAPPORT_EVALUATION,
    )
    assert result["source"] == "deterministic_fallback"
    assert result["confiance"] == 1.0
    assert result["needs_clarification"] is False
    assert set(result["poles"]) == {"P3", "P9", "P12"}


def test_deterministic_fallback_always_appends_redacteur(db_session):
    result = domain_router.route_domain(
        db_session, raw_request="x", title="t", task_type=TaskType.ANALYSE_DONNEES,
    )
    assert "P12" in result["poles"]


def test_llm_routing_filters_hallucinated_pole_codes(db_session):
    """A hallucinated pole code must never survive into assigned_poles -
    same validate-against-known-vocabulary discipline as
    llm_assist.generate_plan_steps."""
    class _FakeClient:
        available = True
        def complete_json(self, system, user, max_tokens=None):
            return {"poles": ["P3", "P99_INVENTE", "P9"], "type_livrable": "evaluation", "confiance": 0.9}

    with patch.object(domain_router, "LLMClient", _FakeClient):
        result = domain_router.route_domain(db_session, "x", "t", task_type=TaskType.AUTRE)

    assert "P99_INVENTE" not in result["poles"]
    assert set(result["poles"]) == {"P3", "P9", "P12"}
    assert result["source"] == "llm"


def test_llm_routing_below_confidence_threshold_requests_clarification(db_session):
    class _FakeClient:
        available = True
        def complete_json(self, system, user, max_tokens=None):
            return {"poles": ["P8"], "type_livrable": "plan", "confiance": 0.3}

    with patch.object(domain_router, "LLMClient", _FakeClient):
        result = domain_router.route_domain(db_session, "x", "t", task_type=TaskType.AUTRE)

    assert result["needs_clarification"] is True


def test_llm_routing_malformed_response_falls_back_deterministically(db_session):
    class _FakeClient:
        available = True
        def complete_json(self, system, user, max_tokens=None):
            return None  # LLM unreachable/unparseable JSON

    with patch.object(domain_router, "LLMClient", _FakeClient):
        result = domain_router.route_domain(db_session, "x", "t", task_type=TaskType.REDACTION)

    assert result["source"] == "deterministic_fallback"


def test_assemble_plan_from_poles_orders_collecte_before_analytique_before_redacteur():
    o = Orchestrator.__new__(Orchestrator)
    steps = [s["type"] for s in o.assemble_plan_from_poles(["P3", "P4", "P12"])]
    assert steps.index("nettoyage") < steps.index("analyse") < steps.index("redaction")
    assert steps[0] == "comprehension"
    assert steps[-1] == "validation"


def test_assemble_plan_from_poles_dedupes_overlapping_step_types():
    """P3 (Économètre) and P5 (Data/Analyste) both resolve to "analyse" -
    must appear exactly once, not twice."""
    o = Orchestrator.__new__(Orchestrator)
    steps = [s["type"] for s in o.assemble_plan_from_poles(["P3", "P5", "P12"])]
    assert steps.count("analyse") == 1
    assert steps.count("nettoyage") == 1


def test_assemble_plan_from_poles_maps_unbuilt_pole_to_stub_step():
    o = Orchestrator.__new__(Orchestrator)
    steps = [s["type"] for s in o.assemble_plan_from_poles(["P8", "P12"])]
    assert "pole_p8" in steps
    assert Orchestrator.AGENT_MAPPING["pole_p8"] == "PoleNotImplementedAgent"


def test_pole_not_implemented_agent_reports_honestly():
    from src.agents import PoleNotImplementedAgent

    agent = PoleNotImplementedAgent()
    result = agent.execute({"step": {"type": "pole_p8"}})
    assert result["status"] == "success"
    assert result["artifacts"] == []
    assert "P8 Planificateur" in result["summary"]
    assert "pas encore construit" in result["summary"]


def test_route_domain_for_task_stores_poles_and_plan_uses_them(db_session, pilot_xlsx):
    """End-to-end: routing -> assigned_poles stored -> propose_plan
    composes from those poles instead of the fixed RAPPORT_EVALUATION
    template -> execution runs the real P3 analysis and honestly reports
    P9 as not yet built."""
    ts = TaskService(db_session)
    task = ts.create_task(
        title="Évaluation Agri-Borgou", raw_request="Évaluer l'impact du programme sur le revenu",
        task_type=TaskType.RAPPORT_EVALUATION,
    )
    task.data_sources = [{"path": pilot_xlsx["path"], "original_name": "x.xlsx"}]
    db_session.commit()

    routing = ts.route_domain_for_task(task.id)
    assert routing["needs_clarification"] is False
    db_session.refresh(task)
    assert task.assigned_poles == routing["poles"]

    ts.update_task_readiness(task.id, {
        "objectif": 0.9, "contexte": 0.8, "donnees": 1.0, "livrables": 0.8,
        "contraintes": 0.7, "methode": 0.7, "ressources": 0.8,
    })
    plan = ts.propose_plan(task.id)["plan"]
    assert "pole_p9" in [s["type"] for s in plan.steps]  # P9 stub, not the old fixed template

    ts.validate_plan(plan.id, approved=True)
    result = ExecutionEngine(db_session).execute_plan(task.id)
    assert result["final_status"] == "deliverable"
    agents_run = {s.get("agent") for s in result["steps_run"]}
    assert "PoleNotImplementedAgent" in agents_run
    assert "DataAnalysisAgent" in agents_run


def test_leaving_domain_routing_unset_preserves_existing_plan_behavior(db_session, pilot_xlsx):
    """No route_domain_for_task call -> assigned_poles stays None ->
    propose_plan behaves exactly as it did before Phase 4 existed."""
    ts = TaskService(db_session)
    task = ts.create_task(title="t", raw_request="r", task_type=TaskType.RAPPORT_EVALUATION)
    task.data_sources = [{"path": pilot_xlsx["path"], "original_name": "x.xlsx"}]
    db_session.commit()
    assert task.assigned_poles is None

    ts.update_task_readiness(task.id, {
        "objectif": 0.9, "contexte": 0.8, "donnees": 1.0, "livrables": 0.8,
        "contraintes": 0.7, "methode": 0.7, "ressources": 0.8,
    })
    plan = ts.propose_plan(task.id)["plan"]
    types = [s["type"] for s in plan.steps]
    assert "pole_p9" not in types  # the old fixed template, not pole-based
    assert "nettoyage" in types and "analyse" in types and "presentation" in types
