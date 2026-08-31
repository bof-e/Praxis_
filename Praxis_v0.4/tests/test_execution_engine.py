"""End-to-end tests for the Execution Engine against the pilot use case
(§8 of the design doc): create task -> readiness -> plan -> validate ->
execute -> real docx/pptx deliverables with a traceable audit trail.
"""
import os
import pytest

from src.services.task_service import TaskService
from src.services.execution_engine import ExecutionEngine
from src.services.artifact_service import ArtifactService
from src.models import TaskType, TaskStatus


READINESS = {
    "objectif": 0.9, "contexte": 0.8, "donnees": 1.0, "livrables": 0.8,
    "contraintes": 0.7, "methode": 0.7, "ressources": 0.8,
}


def _make_ready_task(db_session, data_path=None, title="Pilote"):
    ts = TaskService(db_session)
    task = ts.create_task(
        title=title, raw_request="Analyser l'enquête pilote et produire un rapport.",
        task_type=TaskType.ANALYSE_DONNEES, objective="Évaluer la satisfaction socio-économique.",
    )
    if data_path:
        task.data_sources = [{"path": data_path, "original_name": os.path.basename(data_path)}]
        db_session.commit()
    ts.update_task_readiness(task.id, READINESS)
    plan = ts.propose_plan(task.id)["plan"]
    ts.validate_plan(plan.id, approved=True)
    return task


def test_full_pipeline_produces_real_deliverables(db_session, pilot_xlsx):
    task = _make_ready_task(db_session, pilot_xlsx["path"])

    result = ExecutionEngine(db_session).execute_plan(task.id)

    assert result["final_status"] == "deliverable"
    assert not result["requires_user_action"]
    assert len(result["deliverables"]) == 2  # rapport.docx + presentation.pptx

    art_service = ArtifactService(db_session)
    for d in result["deliverables"]:
        artifact = art_service.get_artifact(d["artifact_id"])
        assert os.path.exists(artifact.file_ref)
        assert os.path.getsize(artifact.file_ref) > 0


def test_cleaning_detects_the_three_injected_anomaly_types(db_session, pilot_xlsx):
    task = _make_ready_task(db_session, pilot_xlsx["path"])
    result = ExecutionEngine(db_session).execute_plan(task.id)

    audit_artifact_id = next(a["id"] for a in result["artifacts"] if a["role"] == "audit_trail")
    art_service = ArtifactService(db_session)
    artifact = art_service.get_artifact(audit_artifact_id)

    import json
    with open(artifact.file_ref) as f:
        audit = json.load(f)

    reasons = {a["reason"] for a in audit["anomalies"]}
    assert any("max" in r for r in reasons)          # age out of range
    assert any("min" in r for r in reasons)           # negative income
    assert "valeur hors liste autorisée" in reasons   # bad category
    assert "doublon" in reasons                       # duplicate row
    assert audit["n_rows_cleaned"] == pilot_xlsx["n_raw"] - 1  # only the duplicate is dropped


def test_missing_data_pauses_for_user_then_resumes(db_session, pilot_xlsx):
    task = _make_ready_task(db_session, data_path=None)

    first = ExecutionEngine(db_session).execute_plan(task.id)
    assert first["requires_user_action"] is True
    assert first["user_action"] == "provide_missing_information"
    db_session.refresh(task)
    assert task.status == TaskStatus.ERROR_RECOVERY

    # user fixes it
    task.data_sources = [{"path": pilot_xlsx["path"], "original_name": "pilote.xlsx"}]
    db_session.commit()

    second = ExecutionEngine(db_session).execute_plan(task.id)
    assert second["resumed_from_step"] == 1  # step 0 (comprehension) had already succeeded
    assert second["final_status"] == "deliverable"


def test_cannot_silently_rerun_a_delivered_task(db_session, pilot_xlsx):
    task = _make_ready_task(db_session, pilot_xlsx["path"])
    ExecutionEngine(db_session).execute_plan(task.id)

    with pytest.raises(ValueError):
        ExecutionEngine(db_session).execute_plan(task.id)

    # force=True is the documented escape hatch
    result = ExecutionEngine(db_session).execute_plan(task.id, force=True)
    assert result["final_status"] == "deliverable"


def test_traceability_explains_a_dropped_duplicate(db_session, pilot_xlsx):
    task = _make_ready_task(db_session, pilot_xlsx["path"])
    ExecutionEngine(db_session).execute_plan(task.id)

    art_service = ArtifactService(db_session)
    answer = art_service.explain_row(task.id, pilot_xlsx["dup_id"])
    assert answer["found"] is True
    assert answer["matches"][0]["action"] == "excluded_duplicate"

    answer_unknown = art_service.explain_row(task.id, "MEN_999")
    assert answer_unknown["found"] is False
