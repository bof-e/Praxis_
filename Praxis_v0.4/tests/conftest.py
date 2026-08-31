"""Shared pytest fixtures for the Praxis test suite.

Each test gets its own SQLite file DB (via a dedicated engine/session, not
the app's global singleton) and its own STORAGE_PATH/UPLOAD_PATH temp
directory, so tests never share state or touch the real praxis.db.
"""
import os
import sys
import uuid

import pytest
import pandas as pd
import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.models import Base           # noqa: E402
from src.config import settings       # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_path = tmp_path / f"test_{uuid.uuid4().hex}.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()

    storage_dir = tmp_path / "artifacts"
    upload_dir = tmp_path / "uploads"
    storage_dir.mkdir(exist_ok=True)
    upload_dir.mkdir(exist_ok=True)
    original_storage, original_upload = settings.STORAGE_PATH, settings.UPLOAD_PATH
    settings.STORAGE_PATH = str(storage_dir)
    settings.UPLOAD_PATH = str(upload_dir)

    yield session

    session.close()
    engine.dispose()
    settings.STORAGE_PATH, settings.UPLOAD_PATH = original_storage, original_upload


@pytest.fixture()
def pilot_xlsx(tmp_path):
    """A small synthetic dataset shaped like the pilot household survey:
    a duplicate row, an out-of-range numeric value, and a disallowed
    categorical value - the same three anomaly types the design doc's
    pilot case describes."""
    np.random.seed(0)
    n = 20
    ids = [f"MEN_{i:03d}" for i in range(1, n + 1)]
    df = pd.DataFrame({
        "id_menage": ids,
        "age_chef": np.random.randint(25, 75, n).tolist(),
        "revenu_mensuel": np.random.randint(50000, 500000, n).tolist(),
        "satisfaction": np.random.choice(
            ["Tres_satisfait", "Satisfait", "Peu_satisfait", "Insatisfait"], n
        ).tolist(),
    })
    df.loc[3, "age_chef"] = 150             # out of range
    df.loc[7, "revenu_mensuel"] = -100      # out of range
    df.loc[10, "satisfaction"] = "Bizarre"  # disallowed value
    dup = df.iloc[5].copy()
    dup_id = dup["id_menage"]
    df = pd.concat([df, pd.DataFrame([dup])], ignore_index=True)

    dictionnaire = pd.DataFrame({
        "variable": ["age_chef", "revenu_mensuel", "satisfaction"],
        "type": ["numeric", "numeric", "text"],
        "min": [18, 0, None],
        "max": [100, 2000000, None],
        "allowed_values": [None, None, "Tres_satisfait,Satisfait,Peu_satisfait,Insatisfait"],
    })
    notes = pd.DataFrame({"menage": ["MEN_004"], "note": ["Âge du chef de ménage à vérifier sur le terrain."]})

    path = tmp_path / "pilote.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Donnees_Brutes", index=False)
        dictionnaire.to_excel(writer, sheet_name="Dictionnaire", index=False)
        notes.to_excel(writer, sheet_name="Notes_Terrain", index=False)

    return {"path": str(path), "dup_id": dup_id, "n_raw": len(df)}


import contextlib


@contextlib.contextmanager
def _reload_src_with_env(env_overrides: dict):
    """Safely reimports every src.* module against different environment
    variables (needed because Settings()/the engine singleton are created
    at import time), then restores the exact prior module set and env vars
    on exit. Centralized here because the save/restore is what makes this
    safe: reimporting src.* without restoring it afterward rebinds every
    subsequent test's imports to different class objects (e.g. two
    non-identical TaskStatus enums), which breaks SQLAlchemy confusingly
    far from whichever test forgot to clean up - exactly what happened the
    first two times this was written inline per test file instead of
    shared from here."""
    env_backup = {k: os.environ.get(k) for k in env_overrides}
    os.environ.update(env_overrides)

    src_modules_backup = {k: v for k, v in sys.modules.items() if k.startswith("src.") or k == "src"}
    for mod in list(src_modules_backup):
        del sys.modules[mod]

    try:
        yield
    finally:
        for k in list(sys.modules):
            if k.startswith("src.") or k == "src":
                del sys.modules[k]
        sys.modules.update(src_modules_backup)

        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture()
def fresh_app(tmp_path):
    """For tests that need src.api.main's app rebuilt against a fresh
    SQLite DATABASE_URL/STORAGE_PATH/UPLOAD_PATH - e.g. a background-thread
    test whose thread opens its own session via database.get_session()
    and needs to hit the exact same singleton engine the test's HTTP
    requests use, or an API-level test that would otherwise share the
    module-level engine with whatever DATABASE_URL an earlier test
    happened to set. For a Postgres-backed equivalent, see
    fresh_app_with_url."""
    db_path = tmp_path / f"api_test_{uuid.uuid4().hex}.db"
    with _reload_src_with_env({
        "DATABASE_URL": f"sqlite:///{db_path}",
        "STORAGE_PATH": str(tmp_path / "artifacts"),
        "UPLOAD_PATH": str(tmp_path / "uploads"),
    }):
        from src.api.main import app
        yield app


@pytest.fixture()
def fresh_app_with_url():
    """Same as fresh_app, but for a caller-supplied DATABASE_URL (e.g. a
    real Postgres instance - see test_postgres_migration.py) instead of a
    fresh SQLite file. Usage: `app = fresh_app_with_url(postgres_url)`."""
    with contextlib.ExitStack() as stack:
        def _make(database_url: str, storage_path: str = "/tmp/pytest_pg_storage/artifacts",
                   upload_path: str = "/tmp/pytest_pg_storage/uploads"):
            stack.enter_context(_reload_src_with_env({
                "DATABASE_URL": database_url, "STORAGE_PATH": storage_path, "UPLOAD_PATH": upload_path,
            }))
            from src.api.main import app
            return app

        yield _make
