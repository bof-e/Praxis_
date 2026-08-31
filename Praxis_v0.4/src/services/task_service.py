"""
Task Service - CRUD operations and business logic for Tasks
"""

from typing import Dict, List, Optional, Any
from datetime import datetime
from sqlalchemy.orm import Session

from ..models import Task, TaskStatus, TaskType, AutonomyLevel, Project
from .orchestrator import Orchestrator, default_deliverables_for, DELIVERABLE_CHOICES
from .readiness_engine import ReadinessEngine
from . import llm_assist


class TaskService:
    """Service for managing tasks through their lifecycle"""
    
    def __init__(self, db_session: Session):
        self.db = db_session
        self.orchestrator = Orchestrator(db_session)
        self.readiness_engine = ReadinessEngine(db_session)
    
    def create_task(
        self,
        title: str,
        raw_request: str,
        task_type: TaskType = TaskType.AUTRE,
        domain: Optional[str] = None,
        project_id: Optional[str] = None,
        objective: Optional[str] = None,
        deliverables: Optional[List[str]] = None,
        hard_constraints: Optional[List[Dict]] = None,
        soft_preferences: Optional[List[Dict]] = None,
        contextual_preferences: Optional[List[Dict]] = None,
        deadline: Optional[datetime] = None,
        estimated_duration: Optional[int] = None,
        priority: int = 5,
        data_sources: Optional[List[str]] = None,
        success_criteria: Optional[List[str]] = None
    ) -> Task:
        """Create a new task in DRAFT status"""
        
        task = Task(
            title=title,
            raw_request=raw_request,
            type=task_type,
            domain=domain,
            project_id=project_id,
            objective=objective,
            autonomy_level=AutonomyLevel.SUPERVISED,  # Default per §16
            deadline=deadline,
            estimated_duration=estimated_duration,
            priority=priority,
            hard_constraints=hard_constraints or [],
            soft_preferences=soft_preferences or [],
            contextual_preferences=contextual_preferences or [],
            data_sources=data_sources or [],
            status=TaskStatus.DRAFT
        )
        
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        
        return task
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID"""
        return self.db.query(Task).filter(Task.id == task_id).first()
    
    def list_tasks(
        self,
        project_id: Optional[str] = None,
        status: Optional[TaskStatus] = None,
        limit: int = 50
    ) -> List[Task]:
        """List tasks with optional filters"""
        query = self.db.query(Task)
        
        if project_id:
            query = query.filter(Task.project_id == project_id)
        if status:
            query = query.filter(Task.status == status)
        
        return query.order_by(Task.created_at.desc()).limit(limit).all()
    
    def update_task_readiness(
        self,
        task_id: str,
        dimension_scores: Dict[str, float]
    ) -> Dict[str, Any]:
        """
        Update task readiness and transition to CLARIFICATION if needed.
        Returns readiness info and any clarification questions.
        """
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        
        # Calculate readiness
        readiness_global, is_ready, missing_critical = \
            self.readiness_engine.calculate_readiness(task, dimension_scores)
        
        task.readiness_score = readiness_global
        
        # Transition based on readiness
        if not is_ready:
            task.status = TaskStatus.CLARIFICATION
            questions = self.readiness_engine.get_clarification_questions(
                task, dimension_scores, missing_critical
            )
        else:
            task.status = TaskStatus.CONTEXTUALIZATION
            questions = []
        
        self.db.commit()
        self.db.refresh(task)
        
        return {
            "task_id": task.id,
            "readiness_score": readiness_global,
            "is_ready": is_ready,
            "missing_critical_dimensions": missing_critical,
            "clarification_questions": questions,
            "new_status": task.status.value
        }
    
    def answer_clarification(
        self,
        task_id: str,
        answers: Dict[str, str]
    ) -> Task:
        """Record answers to clarification questions"""
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        
        # Store answers in missing_info or context
        # For MVP, we'll just transition to CONTEXTUALIZATION
        task.status = TaskStatus.CONTEXTUALIZATION
        
        self.db.commit()
        self.db.refresh(task)
        
        return task
    
    def suggest_reformulation(self, task_id: str) -> Optional[Dict[str, Any]]:
        """LLM-assisted starting point for the readiness form (Phase 3).
        Returns None if no LLM is configured - the frontend falls back to
        empty/default sliders exactly as in Phase 2, nothing regresses."""
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        model = self.readiness_engine.get_or_create_model(task.type)
        return llm_assist.suggest_reformulation(
            raw_request=task.raw_request, title=task.title,
            task_type=task.type.value, dimensions=model.dimensions,
            labels={d: ReadinessEngine.DIMENSION_LABELS.get(d, d) for d in model.dimensions},
            domain=task.domain, existing_objective=task.objective,
        )

    def set_desired_deliverables(self, task_id: str, deliverables: List[str]) -> Task:
        """The "not every deliverable is necessary" question (v0.4.4):
        lets the person choose what they actually want out of a task
        before (or instead of) generating a plan, so the agents that
        would otherwise run (PresentationAgent for a slide deck nobody
        asked for, DocumentAgent for a report when only the cleaned
        dataset was wanted) simply don't. Invalid choices are rejected
        rather than silently ignored - see DELIVERABLE_CHOICES."""
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        unknown = set(deliverables) - set(DELIVERABLE_CHOICES)
        if unknown:
            raise ValueError(f"Livrable(s) inconnu(s): {', '.join(unknown)}. Choix valides: {', '.join(DELIVERABLE_CHOICES)}")
        task.desired_deliverables = deliverables
        self.db.commit()
        self.db.refresh(task)
        return task

    def route_domain_for_task(self, task_id: str) -> Dict[str, Any]:
        """Praxis v1.0 Phase 4 (docs/PRAXIS_V1_ARCHITECTURE.md §4): classifies
        the request into one or more of the 11 deliverable poles, storing
        the result on Task.assigned_poles so propose_plan composes a plan
        from those poles instead of the fixed TaskType template. Call
        before propose_plan; calling after a plan already exists updates
        the stored poles but won't retroactively change that plan.

        When confidence is below the threshold, assigned_poles is
        deliberately left unset ("pas de plan généré à l'aveugle" - §4
        point 3) - propose_plan then falls back to the existing TaskType-
        based generation rather than acting on a low-confidence guess.
        The caller sees needs_clarification=True and can ask for more
        detail before retrying."""
        from . import domain_router

        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        result = domain_router.route_domain(
            self.db, raw_request=task.raw_request, title=task.title,
            task_type=task.type, domain=task.domain,
        )
        if not result["needs_clarification"]:
            task.assigned_poles = result["poles"]
            self.db.commit()
        return result

    def propose_plan(self, task_id: str) -> Dict[str, Any]:
        """
        Generate a plan proposal for the task.

        Praxis v1.0 Phase 4: when domain routing was invoked for this task
        (task.assigned_poles is set - see route_domain_for_task), the plan
        is composed dynamically from those poles (Orchestrator.
        assemble_plan_from_poles) instead of the fixed per-TaskType
        template - a request routed to Économètre+Suivi-Évaluation+
        Rédacteur gets a plan built from exactly those, not a generic
        "rapport_evaluation" skeleton. Domain routing is opt-in per task;
        leaving it unset keeps today's Phase 3 behavior (LLM-tailored
        plan, falling back to the deterministic heuristic below) exactly
        as it was before this existed.
        """
        from ..models import Plan
        
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        
        # Check if plan already exists
        existing_plan = self.db.query(Plan).filter(Plan.task_id == task_id).first()
        if existing_plan:
            return {"plan": existing_plan, "created": False}

        if task.assigned_poles is not None:
            plan_steps = self.orchestrator.assemble_plan_from_poles(task.assigned_poles)
            generation_method = "poles"
        else:
            plan_steps = llm_assist.generate_plan_steps(
                raw_request=task.raw_request, title=task.title,
                task_type=task.type.value, domain=task.domain,
                desired_deliverables=task.desired_deliverables,
            )
            generation_method = "llm"
            if not plan_steps:
                plan_steps = self._generate_basic_plan_steps(task)
                generation_method = "heuristic"

        # Only prune when the person actually answered the deliverables
        # question - leaving it unset keeps today's full-bundle behavior
        # exactly as it was before this existed.
        if task.desired_deliverables is not None:
            plan_steps = self._prune_plan_for_deliverables(plan_steps, task.desired_deliverables)
        
        # Propose autonomy level
        proposed_autonomy = self.orchestrator.propose_autonomy_level(task)
        
        # Get checkpoints based on autonomy
        checkpoints = self.orchestrator.get_checkpoints_for_autonomy(
            proposed_autonomy,
            plan_steps
        )
        
        plan = Plan(
            task_id=task_id,
            steps=plan_steps,
            checkpoints=checkpoints,
            estimated_effort=task.estimated_duration or 60,
            status="draft"
        )
        
        self.db.add(plan)
        self.db.commit()
        self.db.refresh(plan)
        
        return {
            "plan": plan,
            "created": True,
            "proposed_autonomy": proposed_autonomy.value,
            "checkpoints": checkpoints,
            "generation_method": generation_method,
        }
    
    def _prune_plan_for_deliverables(self, steps: List[Dict], desired: List[str]) -> List[Dict]:
        """Drops plan steps whose only purpose is a deliverable the person
        didn't ask for - "presentation" if no slide deck was wanted,
        "redaction" if no document was wanted. Never prunes down to a plan
        that couldn't produce ANY of the desired deliverables at all: if
        the only things left standing (a surviving "presentation" step, or
        a cleaning step feeding "donnees_nettoyees") can't satisfy what was
        asked for, redaction stays regardless - something is always better
        than a task that silently produces nothing."""
        has_cleaning_step = any(s["type"] in ("nettoyage", "analyse") for s in steps)
        presentation_survives = (
            any(s["type"] == "presentation" for s in steps) and "presentation_pptx" in desired
        )
        donnees_survive = has_cleaning_step and "donnees_nettoyees" in desired
        survives_without_redaction = presentation_survives or donnees_survive

        pruned = []
        for step in steps:
            step_type = step["type"]
            if step_type == "presentation" and "presentation_pptx" not in desired:
                continue
            if step_type == "redaction" and "rapport_docx" not in desired and survives_without_redaction:
                continue
            pruned.append(step)
        return pruned

    def _generate_basic_plan_steps(self, task: Task) -> List[Dict]:
        """Generate basic plan steps based on task type.

        Every TaskType needs a real entry here - the fallback used to be
        a step of type "execution", which does not match any keyword in
        Orchestrator.AGENT_MAPPING and therefore silently resolved to
        PlanningAgent (a no-op). Concretely: REPONSE_AO, RAPPORT_EVALUATION,
        PLANIFICATION and AUTRE tasks used to reach 'deliverable' status
        with zero artifacts produced - a fake success. Fixed by giving
        every type a plan built only from step types Orchestrator actually
        dispatches to a working agent."""

        base_steps = {
            TaskType.ANALYSE_DONNEES: [
                {"type": "comprehension", "name": "Comprendre la demande", "is_critical": False},
                {"type": "nettoyage", "name": "Nettoyer les données", "is_critical": True},
                {"type": "analyse", "name": "Analyser les données", "is_critical": True},
                {"type": "redaction", "name": "Rédiger le rapport", "is_critical": False},
                {"type": "presentation", "name": "Créer la présentation", "is_critical": False},
                {"type": "validation", "name": "Contrôle qualité", "is_critical": True}
            ],
            TaskType.RAPPORT_EVALUATION: (
                # A rapport d'évaluation is usually built on underlying data
                # (§8 pilot case) - but not always (a qualitative review has
                # none). Branch on whether a file was actually attached
                # rather than assuming, so a data-less evaluation task
                # doesn't hard-fail on a "nettoyage" step with nothing to
                # clean (see docs/REFONTE_v0.4.md for the case that surfaced
                # this).
                [
                    {"type": "comprehension", "name": "Comprendre la demande", "is_critical": False},
                    {"type": "nettoyage", "name": "Nettoyer les données", "is_critical": True},
                    {"type": "analyse", "name": "Analyser les données", "is_critical": True},
                    {"type": "redaction", "name": "Rédiger le rapport d'évaluation", "is_critical": True},
                    {"type": "presentation", "name": "Créer la présentation", "is_critical": False},
                    {"type": "validation", "name": "Contrôle qualité", "is_critical": True}
                ] if task.data_sources else [
                    {"type": "comprehension", "name": "Comprendre la demande", "is_critical": False},
                    {"type": "recherche", "name": "Rechercher des références", "is_critical": False},
                    {"type": "redaction", "name": "Rédiger le rapport d'évaluation", "is_critical": True},
                    {"type": "validation", "name": "Contrôle qualité", "is_critical": True}
                ]
            ),
            TaskType.REDACTION: [
                {"type": "comprehension", "name": "Comprendre la demande", "is_critical": False},
                {"type": "recherche", "name": "Rechercher les sources", "is_critical": True},
                {"type": "redaction", "name": "Rédiger le contenu", "is_critical": True},
                {"type": "validation", "name": "Contrôle qualité", "is_critical": True}
            ],
            TaskType.RECHERCHE: [
                {"type": "comprehension", "name": "Comprendre la demande", "is_critical": False},
                {"type": "recherche", "name": "Rechercher les sources", "is_critical": True},
                {"type": "analyse", "name": "Analyser les résultats", "is_critical": True},
                {"type": "redaction", "name": "Synthétiser", "is_critical": False},
                {"type": "validation", "name": "Contrôle qualité", "is_critical": True}
            ],
            TaskType.REPONSE_AO: [
                # Compliance-oriented: DocumentAgent adds a checklist section
                # from task.hard_constraints for this type (§3.1 - recevabilité
                # administrative, profil candidat).
                {"type": "comprehension", "name": "Comprendre les exigences", "is_critical": True},
                {"type": "recherche", "name": "Rechercher références et preuves", "is_critical": True},
                {"type": "redaction", "name": "Rédiger la réponse", "is_critical": True},
                {"type": "validation", "name": "Contrôle qualité et conformité", "is_critical": True}
            ],
            TaskType.PLANIFICATION: [
                {"type": "comprehension", "name": "Comprendre les objectifs", "is_critical": True},
                {"type": "recherche", "name": "Rechercher méthodologies et gabarits", "is_critical": False},
                {"type": "redaction", "name": "Rédiger le plan", "is_critical": True},
                {"type": "validation", "name": "Contrôle qualité", "is_critical": True}
            ],
        }

        return base_steps.get(task.type, [
            # AUTRE and any future TaskType not listed above: a minimal but
            # *working* generic plan - comprehension, best-effort research,
            # a real written deliverable, and quality control. No step type
            # here that Orchestrator.AGENT_MAPPING doesn't recognize.
            {"type": "comprehension", "name": "Comprendre la demande", "is_critical": False},
            {"type": "recherche", "name": "Rechercher des éléments pertinents", "is_critical": False},
            {"type": "redaction", "name": "Rédiger le livrable", "is_critical": True},
            {"type": "validation", "name": "Contrôle qualité", "is_critical": True}
        ])
    
    def validate_plan(self, plan_id: str, approved: bool):
        """Validate or reject a plan"""
        from ..models import Plan
        
        plan = self.db.query(Plan).filter(Plan.id == plan_id).first()
        if not plan:
            raise ValueError(f"Plan {plan_id} not found")
        
        if approved:
            plan.status = "validated"
            # Transition task to TOOL_SELECTION
            task = plan.task
            if task:
                task.status = TaskStatus.TOOL_SELECTION
        else:
            plan.status = "rejected"
            # Return task to PLANNING
            task = plan.task
            if task:
                task.status = TaskStatus.PLANNING
        
        self.db.commit()
        self.db.refresh(plan)
        
        return plan
    
    def delete_task(self, task_id: str) -> bool:
        """Delete a task"""
        task = self.get_task(task_id)
        if not task:
            return False
        
        self.db.delete(task)
        self.db.commit()
        return True
