"""
Execution Engine - the missing link for Phase 2 (§11 roadmap)

Praxis had a rich state machine (Orchestrator), rich services (Readiness,
ErrorRecovery, Validation, Artifact, Learning) and rich Agents on paper -
but nothing actually ran a Plan's steps end to end. This module is that
missing piece: it walks a validated Plan step by step, dispatches each step
to the Agent the Orchestrator assigns, persists whatever Artifacts the Agent
produces (with lineage), and routes failures through ErrorRecoveryService
exactly as described in §3.16 / §5.

Synchronous by design (§16 decision: "Job queue: synchronous processing for
Phase 2" already recorded in settings.py) - a real queue (Celery/Redis) is a
Phase 3 concern, not something the MVP needs.
"""

import os
from datetime import datetime
from typing import Dict, Any, List, Optional

from sqlalchemy.orm import Session

from ..models import (
    Task, TaskStatus, Plan, Execution, ExecutionStatus,
    ErrorType, Artifact, ArtifactKind, Deliverable,
)
from ..config import settings
from ..agents import get_agent
from .orchestrator import Orchestrator
from .artifact_service import ArtifactService
from .error_recovery import ErrorRecoveryService


# Step type -> ErrorType used when that step's agent raises an exception.
# Data-shaped steps default to DATA errors (fixed by the user supplying a
# better file), everything else defaults to TECHNICAL (fixed by retrying).
_DATA_STEP_TYPES = {"nettoyage", "analyse", "statistique"}


class ExecutionEngine:
    """Runs a Task's validated Plan through its Agents."""

    def __init__(self, db_session: Session):
        self.db = db_session
        self.orchestrator = Orchestrator(db_session)
        self.artifact_service = ArtifactService(db_session)
        self.error_recovery = ErrorRecoveryService(db_session)

    def execute_plan(self, task_id: str, force: bool = False) -> Dict[str, Any]:
        task = self.db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise ValueError(f"Task {task_id} not found")

        plan = self.db.query(Plan).filter(Plan.task_id == task_id).first()
        if not plan:
            raise ValueError(f"Task {task_id} has no plan yet - call propose_plan first")
        if plan.status != "validated":
            raise ValueError(f"Plan is '{plan.status}', not 'validated' - a human checkpoint must approve it first")

        already_delivered = task.status in (
            TaskStatus.DELIVERABLE, TaskStatus.FINAL_VALIDATION, TaskStatus.DEPLOYED,
        )
        if already_delivered and not force:
            raise ValueError(
                f"Task {task_id} already reached '{task.status.value}'. "
                "Pass force=True to regenerate everything from scratch."
            )

        task.status = TaskStatus.EXECUTION
        self.db.commit()

        work_dir = os.path.join(settings.STORAGE_PATH, task_id, "_work")
        os.makedirs(work_dir, exist_ok=True)

        # Resume support: reconstruct previous_outputs from steps that
        # already completed in an earlier call (e.g. the run paused on
        # ERROR_RECOVERY and the user just fixed the data and re-called
        # /execute) instead of blindly redoing everything from step 0.
        previous_outputs: Dict[str, Dict] = {}
        start_index = 0
        if not force:
            completed = (
                self.db.query(Execution)
                .filter(Execution.task_id == task_id, Execution.plan_id == plan.id,
                        Execution.status == ExecutionStatus.COMPLETED)
                .order_by(Execution.step_index.asc())
                .all()
            )
            for execution in completed:
                for artifact_id in (execution.outputs_produced or []):
                    artifact = self.artifact_service.get_artifact(artifact_id)
                    role = (artifact.extra_metadata or {}).get("role") if artifact else None
                    if artifact and role:
                        previous_outputs[role] = {
                            "file_path": artifact.file_ref,
                            "metadata": artifact.extra_metadata,
                            "artifact_id": artifact.id,
                        }
                start_index = max(start_index, execution.step_index + 1)

        steps_run: List[Dict] = []
        result = {
            "task_id": task_id,
            "steps_run": steps_run,
            "resumed_from_step": start_index if start_index else None,
            "requires_user_action": False,
            "user_action": None,
            "artifacts": [],
            "deliverables": [],
            "validation": None,
        }

        for index, step in enumerate(plan.steps):
            if index < start_index:
                steps_run.append({"step": step, "status": "already_completed"})
                continue

            agent_name = self.orchestrator.assign_agent_to_step(step)
            agent = get_agent(agent_name)

            execution = Execution(
                task_id=task_id,
                plan_id=plan.id,
                step_index=index,
                agent_assigned=agent_name,
                tool_used=agent.name if agent else agent_name,
                status=ExecutionStatus.RUNNING,
                started_at=datetime.utcnow(),
                logs=[],
                outputs_produced=[],
            )
            self.db.add(execution)
            self.db.commit()
            self.db.refresh(execution)

            context = self._build_context(task, step, work_dir, previous_outputs, execution.id)

            outcome = self._run_agent_with_recovery(agent, agent_name, context, execution)

            if outcome["status"] == "failed":
                # _run_agent_with_recovery already updated execution + task
                # status and created the ErrorEvent; stop the loop here and
                # hand control back to the user. Re-calling /execute later
                # will resume from this same step (see start_index above).
                steps_run.append({
                    "step": step, "agent": agent_name, "status": "failed",
                    "error": outcome.get("error"),
                })
                result["requires_user_action"] = True
                result["user_action"] = outcome.get("user_action", "decide_recovery")
                self.db.commit()
                return result

            # Success: persist artifacts with lineage, update running context
            execution.status = ExecutionStatus.COMPLETED
            execution.ended_at = datetime.utcnow()
            execution.logs = [outcome.get("summary", "")]

            produced_ids = []
            for art in outcome.get("artifacts", []):
                derived_from = self._lineage_for(art, previous_outputs)
                artifact = self.artifact_service.create_artifact(
                    task_id=task_id,
                    kind=ArtifactKind(art["kind"]),
                    format=art["format"],
                    file_path=art["file_path"],
                    produced_by_agent=agent_name,
                    execution_id=execution.id,
                    derived_from=derived_from,
                    metadata={**art.get("metadata", {}), "role": art["role"]},
                )
                previous_outputs[art["role"]] = {
                    "file_path": artifact.file_ref,
                    "metadata": artifact.extra_metadata,
                    "artifact_id": artifact.id,
                }
                produced_ids.append(artifact.id)
                result["artifacts"].append({
                    "id": artifact.id, "role": art["role"],
                    "kind": art["kind"], "format": art["format"],
                })

            execution.outputs_produced = produced_ids
            self.db.commit()

            step_result = {"step": step, "agent": agent_name, "status": "success", "summary": outcome.get("summary")}
            if outcome.get("extra"):
                step_result["extra"] = outcome["extra"]
                if "verdict" in outcome["extra"]:
                    result["validation"] = outcome["extra"]
            steps_run.append(step_result)

        # All steps completed without an unresolved error.
        task.status = TaskStatus.VALIDATION
        self.db.commit()
        task = self.orchestrator.transition_to_deliverable(task)
        self.db.commit()

        for role, desired_key in self._deliverable_targets(task, previous_outputs):
            ref = previous_outputs.get(role)
            if not ref:
                continue
            existing = (
                self.db.query(Deliverable)
                .filter(Deliverable.artifact_id == ref["artifact_id"])
                .first()
            )
            if existing:
                deliverable = existing
            else:
                deliverable = self.artifact_service.create_deliverable_from_artifact(
                    artifact_id=ref["artifact_id"], task_id=task_id,
                )
            result["deliverables"].append({"id": deliverable.id, "artifact_id": ref["artifact_id"], "role": desired_key})

        result["final_status"] = task.status.value
        return result

    # ------------------------------------------------------------------
    def _build_context(
        self, task: Task, step: Dict, work_dir: str,
        previous_outputs: Dict[str, Dict], execution_id: str,
    ) -> Dict[str, Any]:
        return {
            "task_id": task.id,
            "task_type": task.type.value if task.type else None,
            "title": task.title,
            "raw_request": task.raw_request,
            "objective": task.objective,
            "domain": task.domain,
            "data_sources": task.data_sources or [],
            "hard_constraints": task.hard_constraints or [],
            "soft_preferences": task.soft_preferences or [],
            "work_dir": work_dir,
            "step": step,
            "previous_outputs": previous_outputs,
            "strategy": (step or {}).get("strategy", "B"),
            "execution_id": execution_id,
            "db": self.db,
        }

    def _deliverable_targets(self, task: Task, previous_outputs: Dict[str, Dict]) -> List[tuple]:
        """Maps the person's chosen deliverables (or the type's defaults,
        if they never answered that question) to the previous_outputs role
        that actually holds each one. "donnees_nettoyees" prefers the
        enriched dataset over the plain cleaned one when both exist (the
        enriched version is a strict superset - see DataAnalysisAgent.
        _run_enrichment) - this is what turns "not every deliverable is
        necessary" into something the engine actually enforces, not just
        the plan: a report nobody asked for isn't produced in the first
        place (pruned at planning time), and if one somehow exists anyway
        it still won't be marked as a Deliverable the person has to review."""
        from .orchestrator import default_deliverables_for

        desired = task.desired_deliverables
        if desired is None:
            desired = default_deliverables_for(task.type, bool(task.data_sources))

        targets = []
        for key in desired:
            if key == "rapport_docx":
                targets.append(("report_docx", key))
            elif key == "presentation_pptx":
                targets.append(("presentation_pptx", key))
            elif key == "donnees_nettoyees":
                role = "enriched_data" if "enriched_data" in previous_outputs else "cleaned_data"
                targets.append((role, key))
        return targets

    def _lineage_for(self, artifact: Dict, previous_outputs: Dict[str, Dict]) -> List[str]:
        """An artifact is derived from whatever the agent had already read
        via previous_outputs when it ran - i.e. every artifact id currently
        in previous_outputs at the time this step's outputs are recorded."""
        return [ref["artifact_id"] for ref in previous_outputs.values() if ref.get("artifact_id")]

    def _run_agent_with_recovery(
        self, agent, agent_name: str, context: Dict[str, Any], execution: Execution,
    ) -> Dict[str, Any]:
        """Runs the agent; on both raised exceptions and an explicit
        status="failed" result, routes through ErrorRecoveryService and
        retries technical failures up to MAX_RETRY_ATTEMPTS before
        escalating to the user."""
        step_type = (context["step"] or {}).get("type", "")
        error_type = ErrorType.DATA if step_type in _DATA_STEP_TYPES else ErrorType.TECHNICAL

        error_event = None
        while True:
            try:
                if agent is None:
                    raise RuntimeError(f"Agent inconnu: {agent_name}")
                outcome = agent.execute(context)
            except Exception as e:
                outcome = {"status": "failed", "error": str(e)}

            if outcome.get("status") != "failed":
                return outcome

            execution.status = ExecutionStatus.FAILED
            execution.ended_at = datetime.utcnow()
            self.db.commit()

            # One ErrorEvent per failing step, with retry_count tracked on
            # it across attempts (rather than a fresh event each time) -
            # decide_recovery_strategy's retry-limit check depends on that.
            if error_event is None:
                error_event = self.error_recovery.create_error_event(
                    execution_id=execution.id,
                    error_type=error_type,
                    error_message=outcome.get("error", "unknown_error"),
                    error_context={"step": context["step"]},
                )
            else:
                error_event.error_message = outcome.get("error", "unknown_error")
                self.db.commit()

            strategy = self.orchestrator.decide_recovery_strategy(error_event)
            recovery = self.error_recovery.execute_recovery(error_event.id, strategy)

            if strategy == "retry":
                execution.status = ExecutionStatus.RUNNING
                execution.started_at = datetime.utcnow()
                self.db.commit()
                continue

            # Not retryable (data/permission -> ask_user, methodological ->
            # change_strategy, or retries exhausted -> escalate): pause the
            # task for the user rather than looping forever.
            task = self.db.query(Task).filter(Task.id == context["task_id"]).first()
            task.status = TaskStatus.ERROR_RECOVERY
            self.db.commit()

            return {
                "status": "failed",
                "error": outcome.get("error"),
                "user_action": recovery.get("user_action") or "decide_recovery",
            }
