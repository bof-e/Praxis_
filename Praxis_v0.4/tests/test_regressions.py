"""Regression tests for bugs found while wiring the Execution Engine.

Both were silent: the code ran, appeared to work, and quietly did the
wrong thing (or crashed unconditionally) until exercised end to end.
"""
from src.services.readiness_engine import ReadinessEngine
from src.services.task_service import TaskService
from src.services.orchestrator import Orchestrator
from src.services.error_recovery import ErrorRecoveryService
from src.models import TaskType, ErrorType


def test_readiness_engine_does_not_crash_on_any_task_type(db_session):
    """DEFAULT_MODELS.get(task_type, DEFAULT_MODELS[TaskType.AUTRE]) crashed
    unconditionally because TaskType.AUTRE had no entry - Python evaluates
    the default argument eagerly. This must not raise for *any* task type."""
    ts = TaskService(db_session)
    for task_type in TaskType:
        task = ts.create_task(title=f"t-{task_type.value}", raw_request="x", task_type=task_type)
        result = ts.update_task_readiness(task.id, {"objectif": 0.8})
        assert "readiness_score" in result


def test_decide_recovery_strategy_matches_data_errors_to_ask_user(db_session):
    """error_event.error_type is a plain Enum member; comparing it to a bare
    string ("technical"/"data"/...) is always False for a non-str Enum, so
    every error silently fell through to the retry/ask_user default instead
    of the intended per-type branch. This pins the intended behaviour."""
    from src.models import Execution, ExecutionStatus
    import uuid

    execution = Execution(id=str(uuid.uuid4()), task_id="t", status=ExecutionStatus.FAILED)
    db_session.add(execution)
    db_session.commit()

    recovery = ErrorRecoveryService(db_session)
    orchestrator = Orchestrator(db_session)

    event = recovery.create_error_event(
        execution_id=execution.id, error_type=ErrorType.DATA,
        error_message="fichier introuvable", error_context={},
    )
    assert orchestrator.decide_recovery_strategy(event) == "ask_user"

    event2 = recovery.create_error_event(
        execution_id=execution.id, error_type=ErrorType.TECHNICAL,
        error_message="timeout", error_context={},
    )
    assert orchestrator.decide_recovery_strategy(event2) == "retry"


def test_artifact_metadata_actually_persists(db_session):
    """Artifact(metadata=...) silently set a plain Python attribute that
    shadows SQLAlchemy's Base.metadata, instead of the mapped column
    extra_metadata - so every artifact's metadata was lost. Regression-tests
    that create_artifact persists it correctly."""
    from src.services.artifact_service import ArtifactService
    from src.models import ArtifactKind
    import tempfile, os

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        f.write(b"{}")
        tmp_path = f.name

    service = ArtifactService(db_session)
    artifact = service.create_artifact(
        task_id="t", kind=ArtifactKind.ANALYSIS, format="json",
        file_path=tmp_path, metadata={"role": "audit_trail", "subtype": "audit_trail"},
    )
    os.unlink(tmp_path)

    reloaded = service.get_artifact(artifact.id)
    assert reloaded.extra_metadata.get("subtype") == "audit_trail"
