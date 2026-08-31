"""API-level test of /tasks/{id}/execute-async - uses the shared fresh_app
fixture (see conftest.py) since the background thread's own DB session
needs to hit the exact same singleton engine the test's HTTP requests use.
"""
import time

import pytest


def test_execute_async_runs_in_background_and_completes(fresh_app, tmp_path, pilot_xlsx):
    from fastapi.testclient import TestClient

    with TestClient(fresh_app) as client:
        r = client.post("/tasks", json={
            "title": "Async test", "raw_request": "test async",
            "task_type": "analyse_donnees", "objective": "test",
        })
        task_id = r.json()["id"]

        with open(pilot_xlsx["path"], "rb") as f:
            client.post(f"/tasks/{task_id}/data", files={"file": ("pilote.xlsx", f, "application/vnd.ms-excel")})

        client.post(f"/tasks/{task_id}/readiness", json={"dimension_scores": {
            "objectif": 0.9, "contexte": 0.8, "donnees": 1.0, "livrables": 0.8,
            "contraintes": 0.7, "methode": 0.7, "ressources": 0.8,
        }})
        plan_id = client.post(f"/tasks/{task_id}/plan").json()["plan"]["id"]
        client.post(f"/plans/{plan_id}/validate", json={"approved": True})

        r = client.post(f"/tasks/{task_id}/execute-async")
        assert r.status_code == 200
        assert r.json()["started"] is True

        deadline = time.time() + 15
        while time.time() < deadline:
            status = client.get(f"/tasks/{task_id}/execution-status").json()
            if not status["running"]:
                break
            time.sleep(0.2)
        else:
            raise AssertionError("execute-async did not finish within 15s")

        assert status["status"] == "deliverable"
        detail = client.get(f"/tasks/{task_id}/detail").json()
        assert len(detail["deliverables"]) == 2


def test_execute_async_rejects_double_start(fresh_app, tmp_path, pilot_xlsx):
    from fastapi.testclient import TestClient
    from src.services import job_tracker

    with TestClient(fresh_app) as client:
        r = client.post("/tasks", json={
            "title": "Double start", "raw_request": "test", "task_type": "analyse_donnees",
        })
        task_id = r.json()["id"]
        with open(pilot_xlsx["path"], "rb") as f:
            client.post(f"/tasks/{task_id}/data", files={"file": ("pilote.xlsx", f, "application/vnd.ms-excel")})
        client.post(f"/tasks/{task_id}/readiness", json={"dimension_scores": {"objectif": 0.9, "donnees": 1.0}})
        plan_id = client.post(f"/tasks/{task_id}/plan").json()["plan"]["id"]
        client.post(f"/plans/{plan_id}/validate", json={"approved": True})

        # Simulate "already running" deterministically rather than racing a
        # real thread, which the fast synthetic fixture makes unreliable.
        job_tracker.try_start(task_id)
        try:
            r = client.post(f"/tasks/{task_id}/execute-async")
            assert r.status_code == 409
        finally:
            job_tracker.finish(task_id)
