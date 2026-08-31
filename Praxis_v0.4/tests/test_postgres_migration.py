"""Regression test for a real production incident: init_db()'s automatic
column-reconciliation migration (see test_database_migration.py) was
gated to SQLite only, on the assumption Postgres deployments would use a
real Alembic migration - but no such migration actually exists in this
repo, so a real docker-compose (Postgres) deployment hit exactly the
missing-column crash this whole mechanism exists to prevent the moment
Task.desired_deliverables was added. Reproduced and fixed against a real
local Postgres instance, not just reasoned about - see docs/REFONTE_v0.4.md.

Skips cleanly if no Postgres server is reachable (most dev/CI machines
won't have one running) rather than failing the whole suite; run
`apt-get install postgresql && service postgresql start` (or use the
project's docker-compose db service) to actually exercise this locally.
"""
import uuid

import pytest

try:
    import psycopg2
    _PSYCOPG2_AVAILABLE = True
except ImportError:
    _PSYCOPG2_AVAILABLE = False

TEST_POSTGRES_ADMIN_URL = "postgresql://postgres:postgres@localhost:5432/postgres"


def _postgres_available() -> bool:
    if not _PSYCOPG2_AVAILABLE:
        return False
    try:
        conn = psycopg2.connect(TEST_POSTGRES_ADMIN_URL, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_available(),
    reason="No local Postgres server reachable at localhost:5432 - this test only runs against a real instance.",
)


@pytest.fixture()
def postgres_db_url():
    """Creates a throwaway database for this test, drops it afterward."""
    db_name = f"praxis_pytest_{uuid.uuid4().hex[:12]}"
    admin_conn = psycopg2.connect(TEST_POSTGRES_ADMIN_URL)
    admin_conn.autocommit = True
    with admin_conn.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{db_name}"')
    admin_conn.close()

    yield f"postgresql://postgres:postgres@localhost:5432/{db_name}"

    admin_conn = psycopg2.connect(TEST_POSTGRES_ADMIN_URL)
    admin_conn.autocommit = True
    with admin_conn.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
    admin_conn.close()


def test_init_db_migrates_a_real_postgres_database_without_losing_data(postgres_db_url):
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from src.models import Base, Task, TaskType, TaskStatus
    from src.services.database import init_db

    engine = create_engine(postgres_db_url)

    # Build the current-shape schema, insert a real row via the ORM (exactly
    # as the app does), then drop the column to reproduce "deployed before
    # this field existed".
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    db.add(Task(
        id="real-task-1", title="Évaluation Agri-Borgou", raw_request="test",
        type=TaskType.ANALYSE_DONNEES, status=TaskStatus.DRAFT,
    ))
    db.commit()
    db.close()

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE tasks DROP COLUMN desired_deliverables"))

    # This must not raise, and must actually add the column back.
    init_db(engine)

    from sqlalchemy import inspect
    columns = {c["name"] for c in inspect(engine).get_columns("tasks")}
    assert "desired_deliverables" in columns

    db = SessionLocal()
    task = db.query(Task).filter(Task.id == "real-task-1").first()
    assert task is not None
    assert task.title == "Évaluation Agri-Borgou"
    assert task.desired_deliverables is None
    db.close()


def test_api_list_tasks_works_after_migrating_an_old_shape_postgres_db(postgres_db_url, fresh_app_with_url):
    """The exact end-to-end symptom reported: GET /tasks 500ing with
    psycopg2.errors.UndefinedColumn."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    setup_engine = create_engine(postgres_db_url)
    from src.models import Base, Task, TaskType, TaskStatus
    Base.metadata.create_all(bind=setup_engine)
    SessionLocal = sessionmaker(bind=setup_engine)
    db = SessionLocal()
    db.add(Task(id="t1", title="Old task", raw_request="x", type=TaskType.AUTRE, status=TaskStatus.DRAFT))
    db.commit()
    db.close()
    with setup_engine.begin() as conn:
        conn.execute(text("ALTER TABLE tasks DROP COLUMN desired_deliverables"))
    setup_engine.dispose()

    app = fresh_app_with_url(postgres_db_url)
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        r = client.get("/tasks")
        assert r.status_code == 200
        assert len(r.json()) == 1
        assert r.json()[0]["title"] == "Old task"


def test_init_db_survives_concurrent_workers_on_a_fresh_database(postgres_db_url):
    """The actual root cause behind the reported crash: docker/Dockerfile.
    backend runs gunicorn with several worker *processes* (-w 4), each
    independently calling init_db() at startup. Base.metadata.create_all()
    on its own is not safe against this: Postgres native ENUM types
    (TaskStatus, TaskType, ...) are created via their own CREATE TYPE
    statement, and several workers racing to create the same type on a
    genuinely fresh database can crash create_all() itself for every
    worker but one. Reproduced with real concurrent threads, each opening
    its own engine/connection (a distinct Postgres session, exactly what
    multiple gunicorn workers are) against the same fresh database."""
    import concurrent.futures
    from sqlalchemy import create_engine, inspect

    from src.services.database import init_db

    def _worker():
        engine = create_engine(postgres_db_url)
        init_db(engine)
        engine.dispose()
        return True

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(_worker) for _ in range(4)]
        results = [f.result() for f in futures]  # .result() re-raises any worker exception

    assert all(results)

    verify_engine = create_engine(postgres_db_url)
    inspector = inspect(verify_engine)
    assert "expertise_domains" in inspector.get_table_names()
    assert "tasks" in inspector.get_table_names()
    task_columns = {c["name"] for c in inspector.get_columns("tasks")}
    assert "assigned_poles" in task_columns
    verify_engine.dispose()


def test_init_db_survives_concurrent_workers_adding_a_new_column(postgres_db_url):
    """Same race, at the column-migration layer specifically (the exact
    symptom in the original bug report: 'column "id" of relation
    "expertise_domains" already exists'). Starts from an already-created
    schema with one real row, then drops a column to reproduce "several
    workers all need to add this column back on startup"."""
    import concurrent.futures
    from sqlalchemy import create_engine, text, inspect
    from sqlalchemy.orm import sessionmaker

    from src.models import Base, Task, TaskType, TaskStatus
    from src.services.database import init_db

    setup_engine = create_engine(postgres_db_url)
    init_db(setup_engine)
    SessionLocal = sessionmaker(bind=setup_engine)
    db = SessionLocal()
    db.add(Task(id="real-task", title="Ancienne tâche", raw_request="x",
                type=TaskType.AUTRE, status=TaskStatus.DRAFT))
    db.commit()
    db.close()
    with setup_engine.begin() as conn:
        conn.execute(text("ALTER TABLE tasks DROP COLUMN assigned_poles"))
    setup_engine.dispose()

    def _worker():
        engine = create_engine(postgres_db_url)
        init_db(engine)
        engine.dispose()
        return True

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(_worker) for _ in range(4)]
        results = [f.result() for f in futures]

    assert all(results)

    verify_engine = create_engine(postgres_db_url)
    inspector = inspect(verify_engine)
    assert "assigned_poles" in {c["name"] for c in inspector.get_columns("tasks")}
    SessionLocal = sessionmaker(bind=verify_engine)
    db = SessionLocal()
    task = db.query(Task).filter(Task.id == "real-task").first()
    assert task is not None
    assert task.title == "Ancienne tâche"
    db.close()
    verify_engine.dispose()
