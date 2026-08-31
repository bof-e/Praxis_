"""
Job tracker - v0.4 lightweight async execution

Tracks which task_ids currently have a background execution thread running,
so /execute-async can refuse to double-start one (409) and
/execution-status can report "running" without needing a real job queue.

Deliberately in-memory: this is a single-process, single-user tool (see
settings.py). State resets on server restart, which is fine - a restart
mid-execution leaves the Task in EXECUTION/ERROR_RECOVERY status in the DB
either way, and the user can just re-call /execute-async to resume (the
Execution Engine's resume-from-last-completed-step logic - see
execution_engine.py - handles picking back up correctly).
"""
import threading
from typing import Dict, Optional

_lock = threading.Lock()
_running: Dict[str, dict] = {}  # task_id -> {"started_at": iso str}


def try_start(task_id: str) -> bool:
    """Returns True and marks the task as running if it wasn't already;
    returns False without changing anything if it was."""
    from datetime import datetime
    with _lock:
        if task_id in _running:
            return False
        _running[task_id] = {"started_at": datetime.utcnow().isoformat()}
        return True


def finish(task_id: str) -> None:
    with _lock:
        _running.pop(task_id, None)


def is_running(task_id: str) -> bool:
    with _lock:
        return task_id in _running


def started_at(task_id: str) -> Optional[str]:
    with _lock:
        entry = _running.get(task_id)
        return entry["started_at"] if entry else None
