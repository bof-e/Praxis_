"""init_db() must reconcile columns for tables that already exist -
Base.metadata.create_all() only CREATEs missing tables, it never ALTERs
one that's already there. Without the fix this test pins, adding a field
to a model (Task.desired_deliverables) would silently do nothing for
anyone with an existing praxis.db: the column would be missing and
nothing would say so until some read/write touching it broke confusingly
far from here - or a real user's task data would need to be wiped just to
pick up a code change.
"""
from sqlalchemy import create_engine, inspect, text

from src.services.database import init_db


def test_init_db_adds_missing_columns_without_losing_existing_rows(tmp_path):
    db_path = tmp_path / "old_shape.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    # Simulate a database created before desired_deliverables existed.
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE tasks (id VARCHAR PRIMARY KEY, title VARCHAR NOT NULL, "
            "raw_request TEXT NOT NULL, status VARCHAR)"
        ))
        conn.execute(text(
            "INSERT INTO tasks (id, title, raw_request, status) "
            "VALUES ('t1', 'Ancienne tâche', 'test', 'draft')"
        ))

    init_db(engine)

    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("tasks")}
    assert "desired_deliverables" in columns

    with engine.connect() as conn:
        row = conn.execute(text("SELECT id, title FROM tasks WHERE id='t1'")).fetchone()
    assert row is not None
    assert row[1] == "Ancienne tâche"


def test_init_db_is_idempotent_on_an_already_current_database(tmp_path):
    """Calling init_db() again (e.g. every app startup) must not fail once
    the columns already exist."""
    db_path = tmp_path / "current.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    init_db(engine)
    init_db(engine)  # must not raise
