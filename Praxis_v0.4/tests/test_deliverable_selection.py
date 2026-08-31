"""Tests for v0.4.4's deliverable selection: "not every deliverable is
necessary" - the person can choose what they actually want, and the plan
(hence which agents run at all) adapts, rather than every task always
producing the same generic docx+pptx bundle regardless of what's wanted.
"""
import pytest

from src.services.task_service import TaskService
from src.services.execution_engine import ExecutionEngine
from src.services.orchestrator import default_deliverables_for, DELIVERABLE_CHOICES
from src.models import TaskType


ALL_STEPS = [
    {"type": "comprehension", "name": "x"},
    {"type": "nettoyage", "name": "x"},
    {"type": "analyse", "name": "x"},
    {"type": "redaction", "name": "x"},
    {"type": "presentation", "name": "x"},
    {"type": "validation", "name": "x"},
]

CONTENT_ONLY_STEPS = [
    {"type": "comprehension", "name": "x"},
    {"type": "recherche", "name": "x"},
    {"type": "redaction", "name": "x"},
    {"type": "validation", "name": "x"},
]


def _prune(db_session, steps, desired):
    return TaskService(db_session)._prune_plan_for_deliverables(steps, desired)


def test_prune_data_only_drops_redaction_and_presentation(db_session):
    result = _prune(db_session, ALL_STEPS, ["donnees_nettoyees"])
    types = [s["type"] for s in result]
    assert "redaction" not in types
    assert "presentation" not in types
    assert "nettoyage" in types and "analyse" in types


def test_prune_presentation_only_drops_redaction_too(db_session):
    """Regression: redaction used to survive even when only a
    presentation was wanted, wasting a DocumentAgent run whose output
    would never be marked as a deliverable anyway."""
    result = _prune(db_session, ALL_STEPS, ["presentation_pptx"])
    types = [s["type"] for s in result]
    assert "redaction" not in types
    assert "presentation" in types


def test_prune_report_only_drops_presentation(db_session):
    result = _prune(db_session, ALL_STEPS, ["rapport_docx"])
    types = [s["type"] for s in result]
    assert "presentation" not in types
    assert "redaction" in types


def test_prune_everything_desired_keeps_full_plan(db_session):
    desired = ["rapport_docx", "presentation_pptx", "donnees_nettoyees"]
    result = _prune(db_session, ALL_STEPS, desired)
    assert [s["type"] for s in result] == [s["type"] for s in ALL_STEPS]


def test_prune_never_produces_an_empty_plan_for_a_content_only_task_type(db_session):
    """REDACTION-type plans have no cleaning step at all - asking for
    "donnees_nettoyees" only on such a task is a mismatch, but redaction
    must stay or the task produces literally nothing."""
    result = _prune(db_session, CONTENT_ONLY_STEPS, ["donnees_nettoyees"])
    assert any(s["type"] == "redaction" for s in result)


def test_default_deliverables_match_pre_v044_behavior():
    """Defaults must reproduce exactly what each type already produced
    before this feature existed, so leaving the question unanswered
    changes nothing."""
    assert default_deliverables_for(TaskType.ANALYSE_DONNEES, True) == ["rapport_docx", "presentation_pptx"]
    assert default_deliverables_for(TaskType.RAPPORT_EVALUATION, True) == ["rapport_docx", "presentation_pptx"]
    assert default_deliverables_for(TaskType.RAPPORT_EVALUATION, False) == ["rapport_docx"]
    assert default_deliverables_for(TaskType.REDACTION, False) == ["rapport_docx"]
    assert default_deliverables_for(TaskType.AUTRE, False) == ["rapport_docx"]


def test_set_desired_deliverables_rejects_unknown_choice(db_session):
    ts = TaskService(db_session)
    task = ts.create_task(title="t", raw_request="r", task_type=TaskType.ANALYSE_DONNEES)
    with pytest.raises(ValueError):
        ts.set_desired_deliverables(task.id, ["un_truc_invente"])


def test_leaving_deliverables_unset_preserves_full_bundle_behavior(db_session, pilot_xlsx):
    """No explicit choice -> today's default full-bundle plan, unchanged."""
    ts = TaskService(db_session)
    task = ts.create_task(title="t", raw_request="r", task_type=TaskType.ANALYSE_DONNEES)
    task.data_sources = [{"path": pilot_xlsx["path"], "original_name": "x.xlsx"}]
    db_session.commit()
    ts.update_task_readiness(task.id, {
        "objectif": 0.9, "contexte": 0.8, "donnees": 1.0, "livrables": 0.8,
        "contraintes": 0.7, "methode": 0.7, "ressources": 0.8,
    })
    plan = ts.propose_plan(task.id)["plan"]
    types = [s["type"] for s in plan.steps]
    assert "redaction" in types and "presentation" in types


def test_choosing_only_cleaned_data_produces_no_docx_or_pptx(db_session, pilot_xlsx):
    """End-to-end: the actual point of this feature - choosing
    "donnees_nettoyees" alone must mean DocumentAgent/PresentationAgent
    never even run, not just that their output goes unmarked."""
    ts = TaskService(db_session)
    task = ts.create_task(title="t", raw_request="r", task_type=TaskType.ANALYSE_DONNEES)
    task.data_sources = [{"path": pilot_xlsx["path"], "original_name": "x.xlsx"}]
    db_session.commit()
    ts.set_desired_deliverables(task.id, ["donnees_nettoyees"])
    ts.update_task_readiness(task.id, {
        "objectif": 0.9, "contexte": 0.8, "donnees": 1.0, "livrables": 0.8,
        "contraintes": 0.7, "methode": 0.7, "ressources": 0.8,
    })
    plan = ts.propose_plan(task.id)["plan"]
    assert "redaction" not in [s["type"] for s in plan.steps]
    assert "presentation" not in [s["type"] for s in plan.steps]
    ts.validate_plan(plan.id, approved=True)

    result = ExecutionEngine(db_session).execute_plan(task.id)
    assert result["final_status"] == "deliverable"
    roles_produced = {a["role"] for a in result["artifacts"]}
    assert "report_docx" not in roles_produced
    assert "presentation_pptx" not in roles_produced
    assert len(result["deliverables"]) == 1
    assert result["deliverables"][0]["role"] == "donnees_nettoyees"


def test_choosing_presentation_only_marks_only_that_as_deliverable(db_session, pilot_xlsx):
    ts = TaskService(db_session)
    task = ts.create_task(title="t", raw_request="r", task_type=TaskType.ANALYSE_DONNEES)
    task.data_sources = [{"path": pilot_xlsx["path"], "original_name": "x.xlsx"}]
    db_session.commit()
    ts.set_desired_deliverables(task.id, ["presentation_pptx"])
    ts.update_task_readiness(task.id, {
        "objectif": 0.9, "contexte": 0.8, "donnees": 1.0, "livrables": 0.8,
        "contraintes": 0.7, "methode": 0.7, "ressources": 0.8,
    })
    plan = ts.propose_plan(task.id)["plan"]
    ts.validate_plan(plan.id, approved=True)
    result = ExecutionEngine(db_session).execute_plan(task.id)

    assert len(result["deliverables"]) == 1
    assert result["deliverables"][0]["role"] == "presentation_pptx"


def test_deliverable_choices_api_endpoints(fresh_app):
    """Covers the two new HTTP endpoints via TestClient against an
    isolated app instance (see conftest.fresh_app)."""
    from fastapi.testclient import TestClient

    with TestClient(fresh_app) as client:
        r = client.post("/tasks", json={"title": "t", "raw_request": "r", "task_type": "analyse_donnees"})
        task_id = r.json()["id"]

        r = client.get(f"/tasks/{task_id}/default-deliverables")
        assert r.status_code == 200
        body = r.json()
        assert body["defaults"] == ["rapport_docx", "presentation_pptx"]
        assert {c["key"] for c in body["choices"]} == set(DELIVERABLE_CHOICES)

        r = client.post(f"/tasks/{task_id}/deliverables", json={"deliverables": ["rapport_docx"]})
        assert r.status_code == 200
        assert r.json()["desired_deliverables"] == ["rapport_docx"]

        r = client.post(f"/tasks/{task_id}/deliverables", json={"deliverables": ["nonexistent"]})
        assert r.status_code == 422
