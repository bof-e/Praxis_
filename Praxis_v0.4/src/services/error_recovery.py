"""
Error Recovery Service - §3.16

Formalized error handling and recovery strategies:
- RETRY (same strategy)
- CHANGE_STRATEGY / other Agent
- ASK_USER
- ESCALATE / ABORT
"""

from typing import Dict, List, Optional
from datetime import datetime
from sqlalchemy.orm import Session

from ..models import ErrorEvent, Execution, ExecutionStatus, ErrorType, RecoveryStrategy
from ..config import settings


class ErrorRecoveryService:
    """Service for managing errors and recovery"""
    
    def __init__(self, db_session: Session):
        self.db = db_session
    
    def create_error_event(
        self,
        execution_id: str,
        error_type: ErrorType,
        error_message: str,
        error_context: Optional[Dict] = None
    ) -> ErrorEvent:
        """Create an error event from a failed execution"""
        
        error_event = ErrorEvent(
            execution_id=execution_id,
            error_type=error_type,
            error_message=error_message,
            error_context=error_context or {},
            retry_count=0,
            resolved=False
        )
        
        self.db.add(error_event)
        
        # Update execution status
        execution = self.db.query(Execution).filter(
            Execution.id == execution_id
        ).first()
        if execution:
            execution.status = ExecutionStatus.FAILED
        
        self.db.commit()
        self.db.refresh(error_event)
        
        return error_event
    
    def decide_recovery_strategy(
        self,
        error_event_id: str
    ) -> str:
        """
        Decide recovery strategy based on error type and history.
        
        From Orchestrator logic (§3.5, §3.16):
        - Technical errors → RETRY
        - Data errors → ASK_USER
        - Permission errors → ASK_USER
        - Methodological errors → CHANGE_STRATEGY
        - Beyond retry limit → ESCALATE
        """
        from .orchestrator import Orchestrator
        
        error_event = self.db.query(ErrorEvent).filter(
            ErrorEvent.id == error_event_id
        ).first()
        
        if not error_event:
            return "abort"
        
        orchestrator = Orchestrator(self.db)
        return orchestrator.decide_recovery_strategy(error_event)
    
    def execute_recovery(
        self,
        error_event_id: str,
        strategy: str,
        new_execution_params: Optional[Dict] = None
    ) -> Dict:
        """
        Execute the chosen recovery strategy.
        
        Returns result with next action required.
        """
        error_event = self.db.query(ErrorEvent).filter(
            ErrorEvent.id == error_event_id
        ).first()
        
        if not error_event:
            return {"success": False, "error": "Error event not found"}
        
        result = {
            "strategy": strategy,
            "requires_user_action": False,
            "user_action": None,
            "next_step": None
        }
        
        if strategy == "retry":
            # Increment retry count
            error_event.retry_count += 1
            error_event.attempted_recoveries.append({
                "strategy": strategy,
                "attempted_at": datetime.utcnow().isoformat()
            })
            
            if error_event.retry_count >= settings.MAX_RETRY_ATTEMPTS:
                result["next_step"] = "escalate"
            else:
                result["next_step"] = "retry_execution"
                # Could include new_execution_params here
        
        elif strategy == "change_strategy":
            error_event.attempted_recoveries.append({
                "strategy": strategy,
                "attempted_at": datetime.utcnow().isoformat(),
                "new_params": new_execution_params
            })
            result["next_step"] = "retry_with_new_strategy"
        
        elif strategy == "ask_user":
            error_event.attempted_recoveries.append({
                "strategy": strategy,
                "attempted_at": datetime.utcnow().isoformat()
            })
            result["requires_user_action"] = True
            result["user_action"] = "provide_missing_information"
            result["next_step"] = "await_user_input"
        
        elif strategy == "escalate":
            error_event.attempted_recoveries.append({
                "strategy": "escalate",
                "attempted_at": datetime.utcnow().isoformat()
            })
            result["next_step"] = "escalate_to_user"
            result["requires_user_action"] = True
        
        elif strategy == "abort":
            error_event.resolved = True
            error_event.resolution_notes = "Aborted after recovery attempts"
            error_event.resolved_at = datetime.utcnow()
            result["next_step"] = "abort_task"
        
        self.db.commit()
        
        return result
    
    def resolve_error(
        self,
        error_event_id: str,
        resolution_notes: str,
        success: bool = True
    ) -> Optional[ErrorEvent]:
        """Mark an error as resolved"""
        error_event = self.db.query(ErrorEvent).filter(
            ErrorEvent.id == error_event_id
        ).first()
        
        if not error_event:
            return None
        
        error_event.resolved = True
        error_event.resolution_notes = resolution_notes
        error_event.resolved_at = datetime.utcnow()
        
        # If resolved successfully, reset execution for retry
        if success and error_event.execution:
            error_event.execution.status = ExecutionStatus.PENDING
        
        self.db.commit()
        self.db.refresh(error_event)
        
        return error_event
    
    def get_unresolved_errors(
        self,
        execution_id: Optional[str] = None,
        limit: int = 50
    ) -> List[ErrorEvent]:
        """Get unresolved error events"""
        query = self.db.query(ErrorEvent).filter(
            ErrorEvent.resolved == False
        )
        
        if execution_id:
            query = query.filter(ErrorEvent.execution_id == execution_id)
        
        return query.order_by(ErrorEvent.created_at.desc()).limit(limit).all()
    
    def get_error_statistics(self) -> Dict:
        """Get error statistics for metrics (§14)"""
        total = self.db.query(ErrorEvent).count()
        resolved = self.db.query(ErrorEvent).filter(
            ErrorEvent.resolved == True
        ).count()
        
        by_type = {}
        for error_type in ErrorType:
            count = self.db.query(ErrorEvent).filter(
                ErrorEvent.error_type == error_type
            ).count()
            by_type[error_type.value] = count
        
        return {
            "total_errors": total,
            "resolved_errors": resolved,
            "unresolved_errors": total - resolved,
            "resolution_rate": resolved / total if total > 0 else 0,
            "by_type": by_type
        }
