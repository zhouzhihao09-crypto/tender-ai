"""Phase 15A: Database migration foundation tests.

Verifies that the Alembic migration infrastructure is in place and that
the initial migration correctly represents the current SQLAlchemy schema.
"""

import os
import tempfile
from pathlib import Path

from sqlalchemy import inspect, create_engine, select, text

from backend.app.config import settings
from backend.app.database import Base, engine, initialize_database, run_migrations


def _make_temp_sqlite_url() -> str:
    """Return a fresh SQLite URL backed by a temp file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return f"sqlite:///{path}"


# -- Migration file existence --

def test_alembic_ini_exists() -> None:
    """alembic.ini should exist at the project root."""
    assert Path("alembic.ini").exists()


def test_alembic_versions_dir_exists() -> None:
    """alembic/versions/ directory should exist."""
    assert Path("alembic/versions").is_dir()


def test_initial_migration_file_exists() -> None:
    """An initial migration script should be present."""
    versions = list(Path("alembic/versions").glob("*.py"))
    versions = [v for v in versions if v.name != "__init__.py"]
    assert len(versions) >= 1, "Expected at least one Alembic migration file"


# -- Migration creates full schema on fresh database --

def test_migration_creates_all_tables_on_fresh_db() -> None:
    """Running the initial Alembic migration on a fresh database should
    create every table defined in the models.
    """
    from alembic import command
    from backend.app.database import _alembic_config

    db_url = _make_temp_sqlite_url()
    config = _alembic_config()
    config.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(config, "head")

    fresh_engine = create_engine(db_url)
    inspector = inspect(fresh_engine)
    tables = set(inspector.get_table_names())

    model_tables = set(Base.metadata.tables.keys())
    for table_name in model_tables:
        assert table_name in tables, f"Table '{table_name}' missing after migration"
    assert "alembic_version" in tables

    fresh_engine.dispose()
    Path(db_url.replace("sqlite:///", "")).unlink(missing_ok=True)


def test_migration_matches_model_schema_on_fresh_db() -> None:
    """After migration, inspector column definitions should match
    Base.metadata for every table.
    """
    from alembic import command
    from backend.app.database import _alembic_config

    db_url = _make_temp_sqlite_url()
    config = _alembic_config()
    config.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(config, "head")

    fresh_engine = create_engine(db_url)
    inspector = inspect(fresh_engine)

    for table_name in Base.metadata.tables:
        model_columns = set(Base.metadata.tables[table_name].columns.keys())
        db_columns = {col["name"] for col in inspector.get_columns(table_name)}
        missing = model_columns - db_columns
        assert not missing, f"Missing columns in '{table_name}': {missing}"

    fresh_engine.dispose()
    Path(db_url.replace("sqlite:///", "")).unlink(missing_ok=True)


# -- Migration idempotency / stamping --

def test_stamp_head_on_existing_unmigrated_db(monkeypatch, tmp_path) -> None:
    """An existing database with tables but no alembic_version should be
    stamped as 'head' without error (the schema already matches).
    """
    from alembic import command
    from alembic.config import Config

    db_url = f"sqlite:///{tmp_path / 'legacy.db'}"
    legacy_engine = create_engine(db_url)
    Base.metadata.create_all(bind=legacy_engine)
    legacy_engine.dispose()

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", db_url)
    command.stamp(config, "head")

    check_engine = create_engine(db_url)
    inspector = inspect(check_engine)
    assert "alembic_version" in inspector.get_table_names()
    check_engine.dispose()


# -- run_migrations integration --

def test_run_migrations_on_fresh_db(monkeypatch, tmp_path) -> None:
    """run_migrations() should create the full schema on a fresh database."""
    from sqlalchemy.orm import sessionmaker

    db_url = f"sqlite:///{tmp_path / 'fresh.db'}"
    test_engine = create_engine(db_url)
    monkeypatch.setattr("backend.app.database.engine", test_engine)
    monkeypatch.setattr(
        "backend.app.database.SessionLocal",
        sessionmaker(bind=test_engine, autoflush=False, autocommit=False),
    )

    run_migrations()

    inspector = inspect(test_engine)
    tables = set(inspector.get_table_names())
    for table_name in Base.metadata.tables:
        assert table_name in tables
    assert "alembic_version" in tables

    test_engine.dispose()


# -- Application startup compatibility --

def test_initialize_database_runs_without_error() -> None:
    """The existing initialize_database() should still work (backward compat)."""
    initialize_database()


def test_health_endpoint_still_works() -> None:
    from fastapi.testclient import TestClient
    from backend.app.main import app
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": settings.app_name}


def test_ready_endpoint_still_works() -> None:
    from fastapi.testclient import TestClient
    from backend.app.main import app
    client = TestClient(app)
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


def test_local_dev_user_still_seeds() -> None:
    """The local-dev user should still exist after initialize_database()."""
    with engine.connect() as conn:
        result = conn.execute(text("SELECT email FROM users WHERE email = :email"), {"email": "local-dev@example.invalid"})
        assert result.fetchone() is not None
