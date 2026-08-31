from src.services import job_tracker


def test_try_start_then_finish(monkeypatch):
    tid = "job-a"
    job_tracker.finish(tid)  # ensure clean slate regardless of test order
    assert job_tracker.is_running(tid) is False
    assert job_tracker.try_start(tid) is True
    assert job_tracker.is_running(tid) is True
    assert job_tracker.started_at(tid) is not None


def test_double_start_is_rejected():
    tid = "job-b"
    job_tracker.finish(tid)
    assert job_tracker.try_start(tid) is True
    assert job_tracker.try_start(tid) is False  # already running
    job_tracker.finish(tid)
    assert job_tracker.try_start(tid) is True  # free again after finish
    job_tracker.finish(tid)


def test_independent_tasks_do_not_interfere():
    job_tracker.finish("job-c1")
    job_tracker.finish("job-c2")
    assert job_tracker.try_start("job-c1") is True
    assert job_tracker.try_start("job-c2") is True
    job_tracker.finish("job-c1")
    job_tracker.finish("job-c2")
