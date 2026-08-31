"""
Praxis v0.3 - Main API Application

FastAPI application implementing the REST API for Praxis MVP.
Implements Phase 2 decisions from §16:
- Default autonomy level: 1 (supervised execution)
- KnowledgeBase: empty at start
- Traceability: present but non-blocking
- Job queue: synchronous processing
"""

import os
import shutil
import uuid
import threading

from fastapi import FastAPI, HTTPException, Depends, status, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field

from src.models import (
    Task, TaskStatus, TaskType, Project, ProjectStatus,
    Artifact, ArtifactKind, Deliverable, AutonomyLevel, LearningLog,
    Plan, Execution, KnowledgeBase, KBContentType, KBConfidence,
)
from src.services.database import get_db, init_db, get_engine
from src.services.task_service import TaskService
from src.services.project_service import ProjectService
from src.services.artifact_service import ArtifactService
from src.services.learning_service import LearningService
from src.services.error_recovery import ErrorRecoveryService
from src.services.validation_service import ValidationService
from src.services.execution_engine import ExecutionEngine
from src.services.readiness_engine import ReadinessEngine
from src.services.knowledge_service import KnowledgeService
from src.services import job_tracker
from src.config import settings


# ============================================================================
# PYDANTIC SCHEMAS
# ============================================================================

class TaskCreate(BaseModel):
    title: str
    raw_request: str
    task_type: TaskType = TaskType.AUTRE
    domain: Optional[str] = None
    objective: Optional[str] = None
    project_id: Optional[str] = None
    deadline: Optional[datetime] = None
    estimated_duration: Optional[int] = None
    priority: int = 5


class TaskResponse(BaseModel):
    id: str
    title: str
    raw_request: str
    type: TaskType
    status: TaskStatus
    readiness_score: Optional[float]
    autonomy_level: AutonomyLevel
    project_id: Optional[str]
    created_at: datetime
    
    class Config:
        from_attributes = True


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    deadline: Optional[datetime] = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    status: ProjectStatus
    created_at: datetime
    
    class Config:
        from_attributes = True


class ReadinessUpdate(BaseModel):
    dimension_scores: Dict[str, float]


class PlanValidation(BaseModel):
    approved: bool


class ClarificationAnswers(BaseModel):
    answers: Dict[str, str]


class KnowledgeItemCreate(BaseModel):
    title: str
    domain: str
    content: str
    content_type: str = "reference"
    source_ref: Optional[str] = None
    tags: Optional[List[str]] = None
    summary: Optional[str] = None
    confidence: str = "exploratory"


class KnowledgeAskRequest(BaseModel):
    query: str
    domain: Optional[str] = None
    top_k: int = 5


class DeliverablesUpdate(BaseModel):
    deliverables: List[str]


# ============================================================================
# FASTAPI APPLICATION
# ============================================================================

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Praxis v0.3 - Personal Intelligent Work System (MVP)"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# LIFECYCLE EVENTS
# ============================================================================

@app.on_event("startup")
async def startup_event():
    """Initialize database on startup"""
    try:
        engine = get_engine()
        init_db(engine)
        print("Database initialized successfully")
    except Exception as e:
        import traceback
        print(f"Error initializing database: {e}")
        traceback.print_exc()


# ============================================================================
# TASK ENDPOINTS
# ============================================================================

@app.post("/tasks", response_model=TaskResponse, tags=["Tasks"])
def create_task(task_data: TaskCreate, db: Session = Depends(get_db)):
    """Create a new task"""
    service = TaskService(db)
    
    task = service.create_task(
        title=task_data.title,
        raw_request=task_data.raw_request,
        task_type=task_data.task_type,
        domain=task_data.domain,
        project_id=task_data.project_id,
        objective=task_data.objective,
        deadline=task_data.deadline,
        estimated_duration=task_data.estimated_duration,
        priority=task_data.priority
    )
    
    return task


@app.get("/tasks", tags=["Tasks"])
def list_tasks(
    project_id: Optional[str] = None,
    status: Optional[TaskStatus] = None,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """List tasks with optional filters"""
    try:
        query = db.query(Task)
        
        if project_id:
            query = query.filter(Task.project_id == project_id)
        if status:
            query = query.filter(Task.status == status)
        
        tasks = query.order_by(Task.created_at.desc()).limit(limit).all()
        return [
            {
                "id": t.id, "title": t.title, "status": t.status.value,
                "type": t.type.value if t.type else None,
                "readiness_score": t.readiness_score,
                "created_at": t.created_at,
            }
            for t in tasks
        ]
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tasks/{task_id}", response_model=TaskResponse, tags=["Tasks"])
def get_task(task_id: str, db: Session = Depends(get_db)):
    """Get a task by ID"""
    service = TaskService(db)
    task = service.get_task(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return task


DELIVERABLE_LABELS = {
    "rapport_docx": "Rapport Word (.docx)",
    "presentation_pptx": "Présentation (.pptx)",
    "donnees_nettoyees": "Données nettoyées (.xlsx)",
}


@app.get("/tasks/{task_id}/default-deliverables", tags=["Tasks"])
def get_default_deliverables(task_id: str, db: Session = Depends(get_db)):
    """"Not every deliverable is necessary" (v0.4.4): the defaults for
    this task's type/data state, plus the full choice list with labels,
    so the frontend can render checkboxes pre-checked sensibly rather
    than asking the person to pick from bare strings. Returns the
    already-chosen selection too, if there is one."""
    from src.services.orchestrator import default_deliverables_for, DELIVERABLE_CHOICES

    service = TaskService(db)
    task = service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return {
        "choices": [{"key": k, "label": DELIVERABLE_LABELS.get(k, k)} for k in DELIVERABLE_CHOICES],
        "defaults": default_deliverables_for(task.type, bool(task.data_sources)),
        "current_selection": task.desired_deliverables,
    }


@app.post("/tasks/{task_id}/deliverables", tags=["Tasks"])
def set_desired_deliverables(task_id: str, body: DeliverablesUpdate, db: Session = Depends(get_db)):
    """Records the person's answer to "what do you actually want out of
    this?" - propose_plan reads this to prune steps for anything not
    chosen (PresentationAgent, DocumentAgent) rather than always running
    the full generic bundle. Call before generating the plan; calling
    after a plan already exists updates the choice but won't retroactively
    change that plan (create a new task, or regenerate the plan, if the
    choice changes)."""
    service = TaskService(db)
    try:
        task = service.set_desired_deliverables(task_id, body.deliverables)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"task_id": task.id, "desired_deliverables": task.desired_deliverables}


@app.post("/tasks/{task_id}/route-domain", tags=["Tasks"])
def route_domain_for_task(task_id: str, db: Session = Depends(get_db)):
    """Praxis v1.0 Phase 4 (docs/PRAXIS_V1_ARCHITECTURE.md §4): classifies
    the request into one or more of the 11 deliverable poles, instead of
    the single fixed TaskType - a real impact evaluation is Économètre +
    Suivi-Évaluation + Rédacteur at once. Call before /plan; a low-
    confidence result (needs_clarification=True) does NOT get stored -
    /plan then falls back to the existing TaskType-based generation
    rather than acting on a guess."""
    service = TaskService(db)
    try:
        return service.route_domain_for_task(task_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/expertise-domains", tags=["Tasks"])
def list_expertise_domains(db: Session = Depends(get_db)):
    """The closed list of 11 deliverable poles domain routing chooses
    from, with their declared capacités - lets the frontend show what
    Praxis currently knows how to route to."""
    from src.services.domain_router import seed_default_domains
    from src.models import ExpertiseDomain

    seed_default_domains(db)
    domains = db.query(ExpertiseDomain).order_by(ExpertiseDomain.code.asc()).all()
    return [
        {"code": d.code, "nom": d.nom, "capacites": d.capacites, "librairies": d.librairies}
        for d in domains
    ]


@app.get("/tasks/{task_id}/readiness-model", tags=["Tasks"])
def get_readiness_model(task_id: str, db: Session = Depends(get_db)):
    """Dimensions/labels/thresholds for this task's type, so the frontend
    can render the readiness form dynamically instead of assuming the
    ANALYSE_DONNEES dimension set for every task type."""
    service = TaskService(db)
    task = service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    engine = ReadinessEngine(db)
    model = engine.get_or_create_model(task.type)
    return {
        "dimensions": model.dimensions,
        "critical_dimensions": model.critical_dimensions,
        "threshold_global": model.threshold_global,
        "threshold_critical": model.threshold_critical,
        "labels": {d: ReadinessEngine.DIMENSION_LABELS.get(d, d) for d in model.dimensions},
    }


@app.post("/tasks/{task_id}/readiness", tags=["Tasks"])
def update_readiness(
    task_id: str,
    readiness_data: ReadinessUpdate,
    db: Session = Depends(get_db)
):
    """Update task readiness and get clarification questions if needed"""
    service = TaskService(db)
    
    try:
        result = service.update_task_readiness(
            task_id=task_id,
            dimension_scores=readiness_data.dimension_scores
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/tasks/{task_id}/clarification", response_model=TaskResponse, tags=["Tasks"])
def answer_clarification(
    task_id: str,
    answers: ClarificationAnswers,
    db: Session = Depends(get_db)
):
    """Submit answers to clarification questions"""
    service = TaskService(db)
    
    try:
        task = service.answer_clarification(task_id, answers.answers)
        return task
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/tasks/{task_id}/reformulate", tags=["Tasks"])
def reformulate_task(task_id: str, db: Session = Depends(get_db)):
    """LLM-assisted starting point for the readiness sliders (Phase 3).
    Returns {"available": false} rather than an error when no LLM is
    configured, so the frontend can degrade to the Phase 2 empty-sliders
    form without treating it as a failure."""
    service = TaskService(db)
    try:
        suggestion = service.suggest_reformulation(task_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if suggestion is None:
        return {"available": False}
    return {"available": True, **suggestion}


@app.post("/tasks/{task_id}/plan", tags=["Tasks"])
def propose_plan(task_id: str, db: Session = Depends(get_db)):
    """Generate a plan proposal for a task"""
    service = TaskService(db)
    
    try:
        result = service.propose_plan(task_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/plans/{plan_id}/validate", tags=["Plans"])
def validate_plan(
    plan_id: str,
    validation: PlanValidation,
    db: Session = Depends(get_db)
):
    """Validate or reject a plan"""
    service = TaskService(db)
    
    try:
        plan = service.validate_plan(plan_id, validation.approved)
        return {"plan": plan, "status": plan.status}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/tasks/{task_id}/data", tags=["Tasks"])
async def upload_data_source(
    task_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload a raw-data file (e.g. the survey Excel workbook) for a task.
    Stored under STORAGE_PATH/uploads/{task_id}/ and appended to
    Task.data_sources so the Data Analysis Agent can find it at execution time."""
    service = TaskService(db)
    task = service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    dest_dir = os.path.join(settings.UPLOAD_PATH, task_id)
    os.makedirs(dest_dir, exist_ok=True)
    safe_name = os.path.basename(file.filename or f"upload-{uuid.uuid4().hex}")
    dest_path = os.path.join(dest_dir, safe_name)

    with open(dest_path, "wb") as out:
        shutil.copyfileobj(file.file, out)

    data_sources = list(task.data_sources or [])
    data_sources.append({"path": dest_path, "original_name": file.filename})
    task.data_sources = data_sources
    db.commit()

    return {"task_id": task_id, "stored_path": dest_path, "data_sources": task.data_sources}


@app.post("/tasks/{task_id}/execute", tags=["Tasks"])
def execute_task(task_id: str, force: bool = False, db: Session = Depends(get_db)):
    """Run the validated Plan through the real agents (§11 Phase 2).
    Synchronous - blocks until the run finishes or pauses on an error/
    checkpoint. Prefer /execute-async for anything but quick tests: a real
    Excel file can take long enough to make the browser tab feel stuck.
    force=true regenerates everything even if the task already has deliverables."""
    engine = ExecutionEngine(db)
    try:
        return engine.execute_plan(task_id, force=force)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/tasks/{task_id}/execute-async", tags=["Tasks"])
def execute_task_async(task_id: str, force: bool = False, db: Session = Depends(get_db)):
    """Starts the same execution as /execute, but in a background thread,
    and returns immediately. Poll /execution-status (or GET /tasks/{id}/detail)
    for progress. 409 if this task already has a run in progress.

    No Celery/Redis - see settings.py for why a background thread is the
    right amount of infrastructure here. The thread opens its own DB
    session (get_session(), not the request-scoped get_db()) since the
    request's session closes as soon as this handler returns."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if not job_tracker.try_start(task_id):
        raise HTTPException(status_code=409, detail="Une exécution est déjà en cours pour cette tâche.")

    def _run_in_background():
        from src.services.database import get_session
        bg_db = get_session()
        try:
            ExecutionEngine(bg_db).execute_plan(task_id, force=force)
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            bg_db.close()
            job_tracker.finish(task_id)

    thread = threading.Thread(target=_run_in_background, daemon=True)
    thread.start()
    return {"started": True, "task_id": task_id}


@app.get("/tasks/{task_id}/execution-status", tags=["Tasks"])
def get_execution_status(task_id: str, db: Session = Depends(get_db)):
    """Lightweight polling endpoint for the async execution above."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "task_id": task_id,
        "status": task.status.value,
        "running": job_tracker.is_running(task_id),
        "started_at": job_tracker.started_at(task_id),
    }


@app.get("/tasks/{task_id}/executions", tags=["Tasks"])
def list_executions(task_id: str, db: Session = Depends(get_db)):
    """List execution log rows for a task, in step order."""
    executions = (
        db.query(Execution)
        .filter(Execution.task_id == task_id)
        .order_by(Execution.step_index.asc())
        .all()
    )
    return [
        {
            "id": e.id,
            "step_index": e.step_index,
            "agent_assigned": e.agent_assigned,
            "status": e.status.value if e.status else None,
            "logs": e.logs,
            "outputs_produced": e.outputs_produced,
            "started_at": e.started_at,
            "ended_at": e.ended_at,
        }
        for e in executions
    ]


@app.get("/tasks/{task_id}/detail", tags=["Tasks"])
def get_task_detail(task_id: str, db: Session = Depends(get_db)):
    """Convenience endpoint bundling task + plan + artifacts + deliverables
    + executions, so the frontend doesn't need five round trips per page load."""
    service = TaskService(db)
    task = service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    plan = db.query(Plan).filter(Plan.task_id == task_id).first()
    artifact_service = ArtifactService(db)
    artifacts = artifact_service.list_artifacts(task_id=task_id)
    deliverables = artifact_service.get_deliverables(task_id=task_id)
    executions = (
        db.query(Execution).filter(Execution.task_id == task_id)
        .order_by(Execution.step_index.asc()).all()
    )

    return {
        "task": {
            "id": task.id, "title": task.title, "raw_request": task.raw_request,
            "objective": task.objective, "type": task.type.value if task.type else None,
            "domain": task.domain, "status": task.status.value if task.status else None,
            "readiness_score": task.readiness_score,
            "autonomy_level": task.autonomy_level.value if task.autonomy_level else None,
            "data_sources": task.data_sources, "created_at": task.created_at,
        },
        "plan": {
            "id": plan.id, "status": plan.status, "steps": plan.steps,
            "checkpoints": plan.checkpoints,
        } if plan else None,
        "artifacts": [
            {"id": a.id, "kind": a.kind.value, "format": a.format, "version": a.version,
             "produced_by_agent": a.produced_by_agent, "created_at": a.created_at}
            for a in artifacts
        ],
        "deliverables": [
            {"id": d.id, "artifact_id": d.artifact_id, "validated": d.validated}
            for d in deliverables
        ],
        "executions": [
            {"id": e.id, "step_index": e.step_index, "agent_assigned": e.agent_assigned,
             "status": e.status.value if e.status else None, "logs": e.logs}
            for e in executions
        ],
    }


@app.delete("/tasks/{task_id}", tags=["Tasks"])
def delete_task(task_id: str, db: Session = Depends(get_db)):
    """Delete a task"""
    service = TaskService(db)
    
    if not service.delete_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    
    return {"message": "Task deleted successfully"}


# ============================================================================
# PROJECT ENDPOINTS
# ============================================================================

@app.post("/projects", response_model=ProjectResponse, tags=["Projects"])
def create_project(project_data: ProjectCreate, db: Session = Depends(get_db)):
    """Create a new project"""
    service = ProjectService(db)
    
    project = service.create_project(
        name=project_data.name,
        description=project_data.description,
        deadline=project_data.deadline
    )
    
    return project


@app.get("/projects", response_model=List[ProjectResponse], tags=["Projects"])
def list_projects(
    status: Optional[ProjectStatus] = None,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """List projects"""
    service = ProjectService(db)
    return service.list_projects(status=status, limit=limit)


@app.get("/projects/{project_id}", response_model=ProjectResponse, tags=["Projects"])
def get_project(project_id: str, db: Session = Depends(get_db)):
    """Get a project by ID"""
    service = ProjectService(db)
    project = service.get_project(project_id)
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    return project


@app.post("/projects/{project_id}/tasks/{task_id}", tags=["Projects"])
def add_task_to_project(
    project_id: str,
    task_id: str,
    db: Session = Depends(get_db)
):
    """Add a task to a project"""
    service = ProjectService(db)
    
    if not service.add_task_to_project(project_id, task_id):
        raise HTTPException(status_code=404, detail="Project or task not found")
    
    return {"message": "Task added to project successfully"}


@app.get("/projects/{project_id}/task-order", tags=["Projects"])
def get_task_order(project_id: str, db: Session = Depends(get_db)):
    """Get tasks in execution order based on dependencies"""
    service = ProjectService(db)
    
    tasks = service.get_task_order(project_id)
    
    return {
        "project_id": project_id,
        "ordered_tasks": [
            {"id": t.id, "title": t.title, "status": t.status.value}
            for t in tasks
        ]
    }


# ============================================================================
# ARTIFACT ENDPOINTS
# ============================================================================

@app.get("/artifacts", tags=["Artifacts"])
def list_artifacts(
    task_id: Optional[str] = None,
    kind: Optional[ArtifactKind] = None,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """List artifacts with filters"""
    service = ArtifactService(db)
    return service.list_artifacts(task_id=task_id, kind=kind, limit=limit)


@app.get("/artifacts/{artifact_id}/download", tags=["Artifacts"])
def download_artifact(artifact_id: str, db: Session = Depends(get_db)):
    """Download an artifact's underlying file."""
    service = ArtifactService(db)
    artifact = service.get_artifact(artifact_id)
    if not artifact or not os.path.exists(artifact.file_ref):
        raise HTTPException(status_code=404, detail="Artifact file not found")
    return FileResponse(
        artifact.file_ref,
        filename=os.path.basename(artifact.file_ref),
        media_type="application/octet-stream",
    )


@app.get("/artifacts/{artifact_id}/provenance", tags=["Artifacts"])
def get_artifact_provenance(artifact_id: str, db: Session = Depends(get_db)):
    """Get full provenance chain for an artifact"""
    service = ArtifactService(db)
    return service.get_artifact_provenance(artifact_id)


@app.get("/tasks/{task_id}/traceability", tags=["Tasks"])
def explain_row(task_id: str, row_id: str, db: Session = Depends(get_db)):
    """§6.2 traceability example, made real: 'why did row X disappear/change?'
    Searches this task's audit trail for row_id and returns the anomaly
    entry (variable, reason, action taken) if one exists."""
    service = ArtifactService(db)
    return service.explain_row(task_id, row_id)


@app.get("/deliverables", tags=["Deliverables"])
def list_deliverables(
    task_id: Optional[str] = None,
    project_id: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """List deliverables"""
    service = ArtifactService(db)
    return service.get_deliverables(task_id=task_id, project_id=project_id)


@app.post("/deliverables/{deliverable_id}/validate", tags=["Deliverables"])
def validate_deliverable(deliverable_id: str, db: Session = Depends(get_db)):
    """Mark a deliverable as validated"""
    service = ArtifactService(db)
    
    result = service.validate_deliverable(deliverable_id)
    if not result:
        raise HTTPException(status_code=404, detail="Deliverable not found")
    
    return {"message": "Deliverable validated", "deliverable": result}


# ============================================================================
# LEARNING ENDPOINTS
# ============================================================================

@app.get("/learning/candidates", tags=["Learning"])
def get_learning_candidates(db: Session = Depends(get_db)):
    """Get learning candidates awaiting validation"""
    service = LearningService(db)
    candidates = service.get_candidates_for_validation()
    
    return {
        "count": len(candidates),
        "candidates": [
            {
                "id": c.id,
                "task_id": c.task_id,
                "hypothesis": c.hypothesis,
                "confidence": c.confidence,
                "created_at": c.created_at
            }
            for c in candidates
        ]
    }


@app.post("/learning/{log_id}/validate", tags=["Learning"])
def validate_learning_candidate(
    log_id: str,
    preference: Dict[str, Any],
    db: Session = Depends(get_db)
):
    """Validate a learning candidate"""
    service = LearningService(db)
    
    result = service.validate_candidate(log_id, preference)
    if not result:
        raise HTTPException(status_code=404, detail="Learning log not found")
    
    return {"message": "Learning candidate validated", "log": result}


@app.post("/learning/{log_id}/reject", tags=["Learning"])
def reject_learning_candidate(log_id: str, db: Session = Depends(get_db)):
    """Reject a learning candidate"""
    service = LearningService(db)
    
    result = service.reject_candidate(log_id)
    if not result:
        raise HTTPException(status_code=404, detail="Learning log not found")
    
    return {"message": "Learning candidate rejected"}


# ============================================================================
# METRICS ENDPOINTS (§14)
# ============================================================================

@app.get("/metrics/errors", tags=["Metrics"])
def get_error_metrics(db: Session = Depends(get_db)):
    """Get error statistics for system metrics"""
    service = ErrorRecoveryService(db)
    return service.get_error_statistics()


@app.get("/metrics/learning", tags=["Metrics"])
def get_learning_metrics(db: Session = Depends(get_db)):
    """Get learning statistics"""
    service = LearningService(db)
    
    return {
        "validated_preferences": service.count_validated_preferences(),
        "total_observations": db.query(LearningLog).count()
    }


# ============================================================================
# KNOWLEDGE BASE (§6.3, §9 ResearchAgent) - Phase 3
# ============================================================================

@app.post("/knowledge", tags=["Knowledge"])
def add_knowledge_item(item: KnowledgeItemCreate, db: Session = Depends(get_db)):
    """Add an item to the local Knowledge Base (methodology guides, norms,
    templates...) that ResearchAgent can later retrieve from."""
    service = KnowledgeService(db)
    try:
        content_type = KBContentType(item.content_type)
        confidence = KBConfidence(item.confidence)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    created = service.add_item(
        title=item.title, domain=item.domain, content_type=content_type,
        content=item.content, source_ref=item.source_ref, tags=item.tags,
        summary=item.summary, confidence=confidence,
    )
    return {
        "id": created.id, "title": created.title, "domain": created.domain,
        "content_type": created.content_type.value, "created_at": created.created_at,
    }


@app.get("/knowledge", tags=["Knowledge"])
def list_or_search_knowledge(
    q: Optional[str] = None, domain: Optional[str] = None,
    top_k: int = 10, db: Session = Depends(get_db),
):
    """List all items (no `q`), or TF-IDF search when `q` is given."""
    service = KnowledgeService(db)
    if q:
        return service.search(q, domain=domain, top_k=top_k)
    items = service.list_items(domain=domain)
    return [
        {
            "id": i.id, "title": i.title, "domain": i.domain,
            "content_type": i.content_type.value, "summary": i.summary,
            "confidence": i.confidence.value if i.confidence else None,
            "tags": i.tags, "created_at": i.created_at,
        }
        for i in items
    ]


@app.delete("/knowledge/{item_id}", tags=["Knowledge"])
def delete_knowledge_item(item_id: str, db: Session = Depends(get_db)):
    service = KnowledgeService(db)
    if not service.delete_item(item_id):
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    return {"deleted": True}


@app.post("/knowledge/ask", tags=["Knowledge"])
def ask_knowledge_base(request: KnowledgeAskRequest, db: Session = Depends(get_db)):
    """TF-IDF retrieval + optional LLM synthesis over the Knowledge Base.
    `synthesis` is null when no LLM is configured - `sources` is always
    populated so the answer stays checkable either way."""
    service = KnowledgeService(db)
    return service.answer(request.query, domain=request.domain, top_k=request.top_k)


# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.get("/health", tags=["System"])
def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "version": settings.APP_VERSION,
        "database": settings.DATABASE_URL.split(":")[0]
    }


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
