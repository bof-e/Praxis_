"""
Learning Service - Controlled learning chain (§3.7)

Observation → LearningCandidate → ValidatedPreference

For MVP: only journaling (Observation) and suggestion (Candidate),
automatic adaptation deferred to Phase 4.
"""

from typing import Dict, List, Optional, Any
from datetime import datetime
from sqlalchemy.orm import Session

from ..models import LearningLog, LearningStatus, Task, UserMemory


class LearningService:
    """Service for managing the learning chain"""
    
    def __init__(self, db_session: Session):
        self.db = db_session
    
    def create_observation(
        self,
        task_id: str,
        observation: str,
        what_was_observed: Dict[str, Any]
    ) -> LearningLog:
        """Create a learning observation from a completed task"""
        
        log = LearningLog(
            task_id=task_id,
            observation=observation,
            what_was_observed=what_was_observed,
            status=LearningStatus.OBSERVATION
        )
        
        self.db.add(log)
        self.db.commit()
        self.db.refresh(log)
        
        return log
    
    def promote_to_candidate(
        self,
        log_id: str,
        hypothesis: str,
        confidence: float
    ) -> Optional[LearningLog]:
        """
        Promote an observation to a learning candidate.
        Requires explicit user trigger in MVP.
        """
        log = self.db.query(LearningLog).filter(LearningLog.id == log_id).first()
        if not log:
            return None
        
        log.status = LearningStatus.CANDIDATE
        log.hypothesis = hypothesis
        log.confidence = confidence
        
        self.db.commit()
        self.db.refresh(log)
        
        return log
    
    def get_candidates_for_validation(self) -> List[LearningLog]:
        """Get all learning candidates awaiting user validation"""
        return self.db.query(LearningLog).filter(
            LearningLog.status == LearningStatus.CANDIDATE
        ).all()
    
    def validate_candidate(
        self,
        log_id: str,
        validated_preference: Dict[str, Any],
        validated_by_user: bool = True
    ) -> Optional[LearningLog]:
        """
        Validate a learning candidate and write to UserMemory.
        
        This is the only path by which preferences are automatically adapted.
        """
        log = self.db.query(LearningLog).filter(LearningLog.id == log_id).first()
        if not log:
            return None
        
        log.status = LearningStatus.VALIDATED
        log.validated_preference = validated_preference
        log.validated_at = datetime.utcnow()
        log.validated_by_user = validated_by_user
        
        # Write to UserMemory if applicable
        if validated_preference:
            self._write_to_user_memory(validated_preference)
        
        self.db.commit()
        self.db.refresh(log)
        
        return log
    
    def reject_candidate(self, log_id: str) -> Optional[LearningLog]:
        """Reject a learning candidate"""
        log = self.db.query(LearningLog).filter(LearningLog.id == log_id).first()
        if not log:
            return None
        
        log.status = LearningStatus.REJECTED
        
        self.db.commit()
        self.db.refresh(log)
        
        return log
    
    def _write_to_user_memory(self, preference: Dict[str, Any]):
        """Write validated preference to UserMemory"""
        # Get or create user memory
        user_memory = self.db.query(UserMemory).first()
        
        if not user_memory:
            user_memory = UserMemory(
                profile={"initialized": True},
                preferences=[]
            )
            self.db.add(user_memory)
        
        # Add to preferences
        if "preference_type" in preference:
            if user_memory.preferences is None:
                user_memory.preferences = []
            user_memory.preferences.append(preference)
        
        self.db.commit()
    
    def get_learning_history(
        self,
        task_id: Optional[str] = None,
        status: Optional[LearningStatus] = None,
        limit: int = 50
    ) -> List[LearningLog]:
        """Get learning logs with filters"""
        query = self.db.query(LearningLog)
        
        if task_id:
            query = query.filter(LearningLog.task_id == task_id)
        if status:
            query = query.filter(LearningLog.status == status)
        
        return query.order_by(LearningLog.created_at.desc()).limit(limit).all()
    
    def get_task_observations(self, task_id: str) -> List[LearningLog]:
        """Get all observations for a specific task"""
        return self.db.query(LearningLog).filter(
            LearningLog.task_id == task_id
        ).all()
    
    def count_validated_preferences(self) -> int:
        """Count number of validated preferences learned"""
        return self.db.query(LearningLog).filter(
            LearningLog.status == LearningStatus.VALIDATED
        ).count()
