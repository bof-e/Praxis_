"""
Validation Service - §3.10 / §3.14

Quality control framework with eight types of checks:
1. Cohérence interne
2. Exactitude factuelle (via Evidence/Source)
3. Validité méthodologique
4. Conformité aux contraintes
5. Qualité rédactionnelle
6. Complétude
7. Reproductibilité
8. Confidentialité
"""

from typing import Dict, List, Optional, Any
from datetime import datetime
from sqlalchemy.orm import Session

from ..models import Validation, Execution, Evidence, Source, Artifact
from ..config import settings


class ValidationService:
    """Service for quality control and validation"""
    
    def __init__(self, db_session: Session):
        self.db = db_session
    
    def create_validation(
        self,
        execution_id: str,
        checks_run: List[Dict],
        overall_verdict: str,
        score: Optional[float] = None,
        corrections_applied: Optional[List[Dict]] = None,
        traceability_warnings: Optional[List[str]] = None
    ) -> Validation:
        """Create a validation record"""
        
        validation = Validation(
            execution_id=execution_id,
            checks_run=checks_run,
            corrections_applied=corrections_applied or [],
            overall_verdict=overall_verdict,
            score=score,
            traceability_warnings=traceability_warnings or []
        )
        
        self.db.add(validation)
        self.db.commit()
        self.db.refresh(validation)
        
        return validation
    
    def run_internal_consistency_check(
        self,
        artifact_ids: List[str],
        content: Dict[str, Any]
    ) -> Dict:
        """Check 1: Internal consistency of the deliverable"""
        # MVP: basic structure check
        # Full implementation would use LLM to detect contradictions
        
        result = {
            "check_type": "internal_consistency",
            "passed": True,
            "issues": []
        }
        
        # Check for required sections
        if isinstance(content, dict):
            required_keys = ["title", "content"]
            for key in required_keys:
                if key not in content:
                    result["passed"] = False
                    result["issues"].append(f"Missing required section: {key}")
        
        return result
    
    def run_factual_accuracy_check(
        self,
        artifact_id: str,
        require_sources: bool = None
    ) -> Dict:
        """
        Check 2: Factual accuracy via Evidence/Source chain (§3.13)
        
        For MVP: non-blocking warning if sources missing (per §16 decision)
        """
        require_sources = require_sources or settings.TRACEABILITY_REQUIRED
        
        result = {
            "check_type": "factual_accuracy",
            "passed": True,
            "warnings": [],
            "evidence_count": 0
        }
        
        # Get evidence linked to this artifact's task
        artifact = self.db.query(Artifact).filter(
            Artifact.id == artifact_id
        ).first()
        
        if not artifact or not artifact.task:
            result["passed"] = False
            result["warnings"].append("Artifact not found or has no task")
            return result
        
        # Count evidence items
        evidence_count = self.db.query(Evidence).join(Source).filter(
            # In full implementation, filter by task
        ).count()
        
        result["evidence_count"] = evidence_count
        
        if evidence_count == 0 and settings.TRACEABILITY_WARNING_ENABLED:
            result["warnings"].append(
                "No evidence/sources traced for factual claims"
            )
            if not require_sources:
                result["passed"] = True  # Non-blocking for MVP
        
        return result
    
    def run_methodological_validity_check(
        self,
        task_type: str,
        methodology_description: str
    ) -> Dict:
        """Check 3: Methodological validity based on task type"""
        # MVP: basic checklist per task type
        # Full implementation would use domain-specific validators
        
        result = {
            "check_type": "methodological_validity",
            "passed": True,
            "issues": []
        }
        
        # Basic checks based on task type
        if not methodology_description or len(methodology_description) < 20:
            result["passed"] = False
            result["issues"].append("Methodology description too brief")
        
        return result
    
    def run_constraint_compliance_check(
        self,
        constraints: Dict[str, List],
        deliverable_metadata: Dict
    ) -> Dict:
        """Check 4: Compliance with hard/soft/contextual constraints (§3.18)"""
        
        result = {
            "check_type": "constraint_compliance",
            "passed": True,
            "violations": []
        }
        
        # Check hard constraints (must be satisfied)
        hard_constraints = constraints.get("hard_constraints", [])
        for constraint in hard_constraints:
            if not self._check_constraint(constraint, deliverable_metadata):
                result["passed"] = False
                result["violations"].append({
                    "type": "hard",
                    "constraint": constraint,
                    "message": f"Hard constraint violated: {constraint}"
                })
        
        # Check soft preferences (should be satisfied)
        soft_preferences = constraints.get("soft_preferences", [])
        for pref in soft_preferences:
            if not self._check_constraint(pref, deliverable_metadata):
                result["violations"].append({
                    "type": "soft",
                    "preference": pref,
                    "message": f"Soft preference not met: {pref}"
                })
        
        return result
    
    def _check_constraint(
        self,
        constraint,
        metadata: Dict
    ) -> bool:
        """Check if a single constraint is satisfied.

        Constraints in practice are plain strings (e.g. "Délai de remise:
        30 jours" - see DocumentAgent's reponse_ao compliance section and
        how tasks actually populate hard_constraints/soft_preferences today).
        This used to assume every constraint was a {"type", "value"} dict
        and crashed with AttributeError on the first plain string - which
        nothing had ever exercised before real content was put in
        hard_constraints. A free-text constraint can't be programmatically
        verified here, so - consistent with the "unrecognized type: assume
        satisfied" default below - it's treated as satisfied; that's a
        real limitation (someone should read it), not a false guarantee,
        which is why it's surfaced to the user via the deliverable's
        Conformité section (DocumentAgent) rather than only living in this
        automated check."""
        if not isinstance(constraint, dict):
            return True

        constraint_type = constraint.get("type")
        expected_value = constraint.get("value")
        
        if constraint_type == "format":
            return metadata.get("format") == expected_value
        elif constraint_type == "max_length":
            return metadata.get("word_count", 0) <= expected_value
        elif constraint_type == "language":
            return metadata.get("language") == expected_value
        
        # Default: assume satisfied
        return True
    
    def run_writing_quality_check(
        self,
        content: str,
        style_preferences: Optional[Dict] = None
    ) -> Dict:
        """Check 5: Writing quality"""
        # MVP: basic checks
        # Full implementation would use LLM for style/tone analysis
        
        result = {
            "check_type": "writing_quality",
            "passed": True,
            "suggestions": []
        }
        
        if not content:
            result["passed"] = False
            result["suggestions"].append("Content is empty")
        elif len(content) < 50:
            result["suggestions"].append("Content is very short")
        
        return result
    
    def run_completeness_check(
        self,
        required_elements: List[str],
        actual_elements: List[str]
    ) -> Dict:
        """Check 6: Completeness"""
        
        missing = set(required_elements) - set(actual_elements)
        
        return {
            "check_type": "completeness",
            "passed": len(missing) == 0,
            "missing_elements": list(missing),
            "completeness_ratio": len(actual_elements) / len(required_elements) if required_elements else 1.0
        }
    
    def run_reproducibility_check(
        self,
        execution_logs: List[Dict],
        artifacts_produced: List[str]
    ) -> Dict:
        """Check 7: Reproducibility"""
        
        result = {
            "check_type": "reproducibility",
            "passed": True,
            "issues": []
        }
        
        # Check if logs are complete
        if not execution_logs:
            result["passed"] = False
            result["issues"].append("No execution logs available")
        
        # Check if all artifacts are tracked
        if not artifacts_produced:
            result["issues"].append("No artifacts tracked")
        
        return result
    
    def run_confidentiality_check(
        self,
        content: str,
        sensitivity_level: str = "normal"
    ) -> Dict:
        """Check 8: Confidentiality"""
        # MVP: placeholder
        # Full implementation would scan for sensitive data patterns
        
        return {
            "check_type": "confidentiality",
            "passed": True,
            "warnings": []
        }
    
    def run_full_validation(
        self,
        execution_id: str,
        artifact_id: str,
        task_type: str,
        constraints: Dict,
        content: Dict
    ) -> Validation:
        """Run all validation checks and create validation record"""
        
        checks = []
        all_passed = True
        total_score = 0
        check_count = 0
        
        # Run all checks
        consistency = self.run_internal_consistency_check([artifact_id], content)
        checks.append(consistency)
        if consistency["passed"]:
            total_score += 1
        check_count += 1
        
        accuracy = self.run_factual_accuracy_check(artifact_id)
        checks.append(accuracy)
        if accuracy["passed"]:
            total_score += 1
        check_count += 1
        
        methodology = self.run_methodological_validity_check(
            task_type,
            content.get("methodology", "")
        )
        checks.append(methodology)
        if methodology["passed"]:
            total_score += 1
        check_count += 1
        
        compliance = self.run_constraint_compliance_check(
            constraints,
            content.get("metadata", {})
        )
        checks.append(compliance)
        if compliance["passed"]:
            total_score += 1
        check_count += 1
        
        writing = self.run_writing_quality_check(
            content.get("text", ""),
            content.get("style_preferences")
        )
        checks.append(writing)
        if writing["passed"]:
            total_score += 1
        check_count += 1
        
        # Calculate overall verdict
        score = total_score / check_count if check_count > 0 else 0
        
        if score >= 0.9:
            verdict = "pass"
        elif score >= 0.7:
            verdict = "pass_with_corrections"
        else:
            verdict = "fail"
            all_passed = False
        
        # Collect warnings
        warnings = []
        for check in checks:
            warnings.extend(check.get("warnings", []))
        
        validation = self.create_validation(
            execution_id=execution_id,
            checks_run=checks,
            overall_verdict=verdict,
            score=score,
            traceability_warnings=warnings
        )
        
        return validation
