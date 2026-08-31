"""
Readiness Engine - §3.3

Calculates readiness score based on task type-specific models.
Each task type has its own dimensions, critical dimensions, and thresholds.
"""

from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session
from ..models import ReadinessModel, Task, TaskType
from ..config import settings


class ReadinessEngine:
    """
    §3.3 - Readiness Engine with type-dependent models
    
    Rule: readiness_global ≥ threshold_global AND
          no critical_dimension < threshold_critical
    """
    
    # Default models for each task type
    DEFAULT_MODELS = {
        TaskType.ANALYSE_DONNEES: {
            "dimensions": ["objectif", "contexte", "donnees", "livrables", 
                          "contraintes", "methode", "ressources"],
            "critical_dimensions": ["donnees", "objectif"],
            "threshold_global": 0.75,
            "threshold_critical": 0.50
        },
        TaskType.REPONSE_AO: {
            "dimensions": ["objectif", "contexte", "donnees", "livrables",
                          "contraintes", "methode", "ressources", "preuves",
                          "risques", "recevabilite_administrative", "profil_candidat"],
            "critical_dimensions": ["preuves", "recevabilite_administrative", "profil_candidat"],
            "threshold_global": 0.85,
            "threshold_critical": 0.60
        },
        TaskType.RAPPORT_EVALUATION: {
            "dimensions": ["objectif", "contexte", "donnees", "livrables",
                          "contraintes", "methode", "ressources", "preuves"],
            "critical_dimensions": ["donnees", "preuves"],
            "threshold_global": 0.80,
            "threshold_critical": 0.55
        },
        TaskType.PLANIFICATION: {
            "dimensions": ["objectif", "contexte", "livrables", "contraintes",
                          "ressources", "jalons"],
            "critical_dimensions": ["objectif", "ressources"],
            "threshold_global": 0.75,
            "threshold_critical": 0.50
        },
        TaskType.RECHERCHE: {
            "dimensions": ["objectif", "contexte", "sources", "methode",
                          "livrables", "contraintes"],
            "critical_dimensions": ["sources", "objectif"],
            "threshold_global": 0.75,
            "threshold_critical": 0.50
        },
        TaskType.REDACTION: {
            "dimensions": ["objectif", "contexte", "contenu", "livrables",
                          "contraintes", "style"],
            "critical_dimensions": ["objectif", "contenu"],
            "threshold_global": 0.75,
            "threshold_critical": 0.50
        },
        TaskType.AUTRE: {
            # Generic fallback model - also used as the .get() default below,
            # so it must exist or every readiness calculation crashes.
            "dimensions": ["objectif", "contexte", "donnees", "livrables",
                          "contraintes", "methode", "ressources"],
            "critical_dimensions": ["objectif"],
            "threshold_global": 0.70,
            "threshold_critical": 0.50
        }
    }
    
    # Human-readable label for each dimension, reused for both clarification
    # questions and the frontend's dynamic readiness form.
    DIMENSION_LABELS = {
        "objectif": "Quel est l'objectif principal que vous souhaitez atteindre avec cette tâche ?",
        "contexte": "Pouvez-vous décrire le contexte dans lequel s'inscrit cette tâche ?",
        "donnees": "Quelles données avez-vous à disposition ? Sous quel format ?",
        "livrables": "Quels livrables attendez-vous exactement (format, contenu, longueur) ?",
        "contraintes": "Y a-t-il des contraintes spécifiques (délais, format, ressources) ?",
        "methode": "Avez-vous une méthode ou approche particulière à suivre ?",
        "ressources": "Quelles ressources sont disponibles (temps, budget, outils) ?",
        "preuves": "Quelles preuves ou sources devez-vous inclure pour étayer vos affirmations ?",
        "risques": "Identifiez-vous des risques particuliers liés à cette tâche ?",
        "recevabilite_administrative": "Quels sont les critères administratifs de recevabilité ?",
        "profil_candidat": "Quel est le profil requis pour cette réponse à appel d'offres ?",
        "sources": "Quelles sources documentaires devez-vous consulter ?",
        "contenu": "Quel contenu spécifique doit être traité ?",
        "style": "Avez-vous des préférences de style ou de ton ?",
        "jalons": "Quels sont les jalons ou étapes clés à respecter ?"
    }

    def __init__(self, db_session: Session):
        self.db = db_session
    
    def get_or_create_model(self, task_type: TaskType) -> ReadinessModel:
        """Get existing model or create default one"""
        model = self.db.query(ReadinessModel).filter(
            ReadinessModel.task_type == task_type
        ).first()
        
        if not model:
            default = self.DEFAULT_MODELS.get(task_type, self.DEFAULT_MODELS[TaskType.AUTRE])
            model = ReadinessModel(
                task_type=task_type,
                dimensions=default["dimensions"],
                critical_dimensions=default["critical_dimensions"],
                threshold_global=default["threshold_global"],
                threshold_critical=default["threshold_critical"]
            )
            self.db.add(model)
            self.db.commit()
            self.db.refresh(model)
        
        return model
    
    def calculate_readiness(
        self,
        task: Task,
        dimension_scores: Dict[str, float]
    ) -> tuple[float, bool, List[str]]:
        """
        Calculate readiness score for a task.
        
        Returns:
            (readiness_global, is_ready, missing_critical_dimensions)
        """
        # Get the model for this task type
        model = self.get_or_create_model(task.type)
        
        # Calculate global score (weighted average of all dimensions)
        total_score = 0.0
        total_weight = 0
        
        for dim in model.dimensions:
            score = dimension_scores.get(dim, 0.0)
            weight = 2.0 if dim in model.critical_dimensions else 1.0
            total_score += score * weight
            total_weight += weight
        
        readiness_global = total_score / total_weight if total_weight > 0 else 0.0
        
        # Check critical dimensions
        missing_critical = []
        for crit_dim in model.critical_dimensions:
            crit_score = dimension_scores.get(crit_dim, 0.0)
            if crit_score < model.threshold_critical:
                missing_critical.append(crit_dim)
        
        # Determine if ready
        is_ready = (
            readiness_global >= model.threshold_global and
            len(missing_critical) == 0
        )
        
        return readiness_global, is_ready, missing_critical
    
    def get_clarification_questions(
        self,
        task: Task,
        dimension_scores: Dict[str, float],
        missing_critical: List[str]
    ) -> List[str]:
        """
        Generate targeted clarification questions for failing dimensions.
        Only asks about critical dimensions that are below threshold.
        """
        questions = []
        
        question_templates = self.DIMENSION_LABELS
        
        for dim in missing_critical:
            if dim in question_templates:
                questions.append(question_templates[dim])
        
        return questions
    
    def update_task_readiness(self, task: Task, dimension_scores: Dict[str, float]) -> Task:
        """Update task with calculated readiness score"""
        readiness_global, is_ready, missing_critical = self.calculate_readiness(
            task, dimension_scores
        )
        
        task.readiness_score = readiness_global
        
        return task
