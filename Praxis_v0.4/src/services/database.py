"""
Database setup and session management for Praxis v0.3

Uses a single module-level engine/sessionmaker (created once) instead of a
new engine per call, and exposes get_db() as a generator dependency so
FastAPI closes the session at the end of every request. get_session() is
kept as a convenience for scripts/tests that need a standalone session
they close themselves.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from src.models import Base
from src.config import settings


_engine = None
_SessionLocal = None


def get_engine():
    """Return the singleton database engine, creating it on first call.

    timeout=30 (SQLite only) makes a connection wait up to 30s for a lock
    instead of raising "database is locked" immediately - needed now that
    v0.4 can have a background execution thread writing while the request
    thread concurrently reads for status polling (see job_tracker.py)."""
    global _engine
    if _engine is None:
        is_sqlite = "sqlite" in settings.DATABASE_URL
        _engine = create_engine(
            settings.DATABASE_URL,
            connect_args={"check_same_thread": False, "timeout": 30} if is_sqlite else {}
        )
    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())
    return _SessionLocal


def init_db(engine=None):
    """Initialize database tables, then reconcile columns for tables that
    already exist (Base.metadata.create_all only CREATEs missing tables -
    it never ALTERs an existing one). Without this, adding a field to a
    model (e.g. Task.desired_deliverables in v0.4.4) silently does nothing
    for anyone with an existing database - the column is missing and every
    read/write touching it breaks confusingly far from here, or real task
    data has to be wiped just to pick up the change.

    Postgres deployments run gunicorn with several worker *processes*
    (docker/Dockerfile.backend: -w 4), each independently calling init_db()
    at startup against the same database. This is genuinely unsafe without
    the advisory lock below: Postgres native ENUM types (used for every
    Enum column - TaskStatus, TaskType, ...) are created via their own
    CREATE TYPE statement with no atomic guard on the SQLAlchemy versions
    this project pins, and several workers racing to create the same type
    on a freshly-provisioned database can crash create_all() itself for
    every worker but one - reproduced by running 4 real concurrent
    processes against a fresh local Postgres instance before this fix (see
    docs/REFONTE_v0.4.md), not just reasoned about. A session-scoped
    Postgres advisory lock serializes the whole migration across workers:
    whichever one acquires it runs to completion while the others block,
    then each of those finds nothing left to do."""
    if engine is None:
        engine = get_engine()

    if engine.dialect.name == "postgresql":
        _init_db_locked_for_postgres(engine)
    else:
        Base.metadata.create_all(bind=engine)
        _add_missing_columns(engine)


# Arbitrary fixed key for the startup-migration advisory lock - only needs
# to be consistent across this app's own workers, not unique across every
# use of Postgres on the machine.
_MIGRATION_LOCK_KEY = 875_301_442


def _init_db_locked_for_postgres(engine) -> None:
    from sqlalchemy import text

    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _MIGRATION_LOCK_KEY})
        try:
            Base.metadata.create_all(bind=engine)
            _add_missing_columns(engine)
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _MIGRATION_LOCK_KEY})


def _add_missing_columns(engine) -> None:
    import logging
    from sqlalchemy import inspect as sa_inspect, text

    logger = logging.getLogger(__name__)
    inspector = sa_inspect(engine)
    existing_tables = set(inspector.get_table_names())
    is_postgres = engine.dialect.name == "postgresql"

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # brand-new table - create_all already has it fully
        existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing_columns:
                continue
            col_type = column.type.compile(engine.dialect)
            # Always add as nullable, regardless of the model's own
            # nullable=False - a NOT NULL column with no DEFAULT fails
            # to ADD on a table that already has rows (no value to
            # backfill them with). A genuinely required new column
            # needs a real migration with a backfill strategy; this
            # lightweight utility's job is to keep simple/optional
            # field additions (like this one) from silently breaking,
            # not to replace that.
            #
            # IF NOT EXISTS (Postgres only, 9.6+) makes this ALTER
            # atomic and race-proof: the Docker image runs gunicorn with
            # several worker *processes* (docker/Dockerfile.backend: -w 4),
            # each independently calling init_db() at startup against the
            # same fresh database - without this, multiple workers can
            # all see a column as missing at the same instant and race
            # to add it, and the losers get a real "column already
            # exists" error (reproduced against a live Postgres instance
            # with concurrent init_db() calls before this fix - see
            # docs/REFONTE_v0.4.md). SQLite has no IF NOT EXISTS for ADD
            # COLUMN, so it keeps relying on the except-and-log-benignly
            # path below - an acceptable gap since SQLite is this
            # project's single-process personal-use default, not the
            # multi-worker Docker deployment where the race actually happens.
            if_not_exists = "IF NOT EXISTS " if is_postgres else ""
            try:
                with engine.begin() as conn:
                    conn.execute(text(
                        f'ALTER TABLE "{table.name}" ADD COLUMN {if_not_exists}"{column.name}" {col_type}'
                    ))
                logger.info(f"Migration: added missing column {table.name}.{column.name}")
            except Exception as e:
                message = str(e).lower()
                if "already exists" in message or "duplicate column" in message:
                    # Another worker (or IF NOT EXISTS itself, on some
                    # Postgres/driver combinations that still raise rather
                    # than silently no-op) won the race first - the
                    # column is there, which is exactly the goal, not a
                    # failure.
                    logger.info(
                        f"Migration: {table.name}.{column.name} was already added "
                        "(concurrent worker or already present) - no action needed."
                    )
                else:
                    logger.warning(
                        f"Migration: could not add column {table.name}.{column.name} ({e}) - "
                        "continuing; this column's operations will keep failing until it's added manually."
                    )


def get_session() -> Session:
    """Get a standalone database session. Caller is responsible for closing it.
    Intended for scripts and tests, not for FastAPI route dependencies."""
    SessionLocal = get_session_factory()
    return SessionLocal()


def get_db():
    """FastAPI dependency: yields a session and guarantees it is closed
    after the request, even if the handler raises."""
    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def seed_default_data(session: Session):
    """Seed database with default data (readiness models are created lazily
    by ReadinessEngine on first use, so there is nothing to pre-seed yet)."""
    session.commit()
