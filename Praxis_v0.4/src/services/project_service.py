"""
Project Service - CRUD operations for Projects
"""

from typing import Dict, List, Optional
from datetime import datetime
from sqlalchemy.orm import Session

from ..models import Project, ProjectStatus, Task, Dependency


class ProjectService:
    """Service for managing projects"""
    
    def __init__(self, db_session: Session):
        self.db = db_session
    
    def create_project(
        self,
        name: str,
        description: Optional[str] = None,
        objectives: Optional[List[str]] = None,
        stakeholders: Optional[List[str]] = None,
        deadline: Optional[datetime] = None,
        budget_calls: Optional[int] = None,
        global_constraints: Optional[Dict] = None
    ) -> Project:
        """Create a new project"""
        
        project = Project(
            name=name,
            description=description,
            status=ProjectStatus.ACTIVE,
            deadline=deadline,
            budget_calls=budget_calls,
            global_constraints=global_constraints or {}
        )
        
        self.db.add(project)
        self.db.commit()
        self.db.refresh(project)
        
        return project
    
    def get_project(self, project_id: str) -> Optional[Project]:
        """Get a project by ID with its tasks"""
        return self.db.query(Project).filter(Project.id == project_id).first()
    
    def list_projects(
        self,
        status: Optional[ProjectStatus] = None,
        limit: int = 50
    ) -> List[Project]:
        """List projects with optional filters"""
        query = self.db.query(Project)
        
        if status:
            query = query.filter(Project.status == status)
        
        return query.order_by(Project.created_at.desc()).limit(limit).all()
    
    def add_task_to_project(
        self,
        project_id: str,
        task_id: str
    ) -> bool:
        """Add an existing task to a project"""
        project = self.get_project(project_id)
        if not project:
            return False
        
        task = self.db.query(Task).filter(Task.id == task_id).first()
        if not task:
            return False
        
        task.project_id = project_id
        self.db.commit()
        
        return True
    
    def create_dependency(
        self,
        from_task_id: str,
        to_task_id: str,
        dep_type: str = "blocking",
        description: Optional[str] = None
    ) -> Dependency:
        """Create a dependency between two tasks"""
        
        dependency = Dependency(
            from_task_id=from_task_id,
            to_task_id=to_task_id,
            type=dep_type,
            description=description
        )
        
        self.db.add(dependency)
        self.db.commit()
        self.db.refresh(dependency)
        
        return dependency
    
    def get_task_order(self, project_id: str) -> List[Task]:
        """Get tasks in execution order based on dependencies"""
        from .orchestrator import Orchestrator
        
        project = self.get_project(project_id)
        if not project:
            return []
        
        orchestrator = Orchestrator(self.db)
        return orchestrator.resolve_task_order(project)
    
    def update_project_status(
        self,
        project_id: str,
        status: ProjectStatus
    ) -> Optional[Project]:
        """Update project status"""
        project = self.get_project(project_id)
        if not project:
            return None
        
        project.status = status
        self.db.commit()
        self.db.refresh(project)
        
        return project
    
    def delete_project(self, project_id: str) -> bool:
        """Delete a project (cascades to tasks)"""
        project = self.get_project(project_id)
        if not project:
            return False
        
        self.db.delete(project)
        self.db.commit()
        return True
