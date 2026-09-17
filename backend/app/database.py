"""Database layer — SQLAlchemy engine, session factory, and migration helpers.

Production schema changes are managed by Alembic (see alembic/).

The startup initialize_database() call in main.py ensures the database
is migrated to the latest revision.

Three scenarios are handled:

1. Fresh database: alembic upgrade head creates full schema.

2. Legacy local database (tables exist, no alembic_version): create_all()
   as a safety net, then stamp head to establish tracking.

3. Migrated database: alembic upgrade head applies pending revisions.

Local development and tests:

    Tests use isolated temp SQLite databases (tests/conftest.py).
    Base.metadata.create_all() is available for tests.
"""

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

_pool_kwargs: dict = {}

if not settings.database_url.startswith("sqlite"):
    _pool_kwargs = {"pool_pre_ping": True, "pool_recycle": 300, "pool_size": 20, "max_overflow": 10}

engine = create_engine(settings.database_url, connect_args=_connect_args, **_pool_kwargs)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _alembic_ini_path() -> Path:
    """Return the absolute path to alembic.ini (project root)."""
    return Path(__file__).resolve().parents[2] / "alembic.ini"


def _alembic_config():
    """Build an Alembic Config object from the project alembic.ini.

    The sqlalchemy.url is taken from the module-level *engine* so that
    tests which monkeypatch ``backend.app.database.engine`` are respected.
    """
    from alembic.config import Config

    cfg = Config(str(_alembic_ini_path()))
    cfg.set_main_option("sqlalchemy.url", str(engine.url))
    return cfg


def _database_has_tables() -> bool:
    """Return True if the database contains at least one user table."""
    from sqlalchemy import inspect

    inspector = inspect(engine)
    return len(inspector.get_table_names()) > 0


def _database_has_alembic_version() -> bool:
    """Return True if the alembic_version table exists."""
    from sqlalchemy import inspect

    inspector = inspect(engine)
    return "alembic_version" in inspector.get_table_names()


def run_migrations() -> None:
    """Apply database schema migrations using Alembic.

    Handles three cases:

    1. Fresh database: alembic upgrade head creates all tables.

    2. Legacy database (tables exist, no alembic_version): create_all()
       as a safety net for missing tables, then stamp head.

    3. Migrated database: alembic upgrade head applies pending revisions.
    """
    from alembic import command

    cfg = _alembic_config()

    if _database_has_alembic_version():
        # Case 3: already tracked by Alembic -> normal upgrade path.
        command.upgrade(cfg, "head")
    elif _database_has_tables():
        # Case 2: legacy database created by old initialiser.
        # create_all() is idempotent; a safety net for missing tables.
        Base.metadata.create_all(bind=engine)
        command.stamp(cfg, "head")
    else:
        # Case 1: fresh database -> let Alembic build the schema.
        command.upgrade(cfg, "head")


def _seed_development_data() -> None:
    """Seed or normalise development data.

    Creates the local-dev user and workspace if they do not exist, and
    back-fills workspace_id on any orphaned records.
    """
    from .models import CompanyEvidenceDocument, CompanyProfile, Tender, User, Workspace

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "local-dev@example.invalid"))

        if not user:
            user = User(
                email="local-dev@example.invalid",
                password_hash=None,
                plan="BUSINESS",
                subscription_status="NOT_CONFIGURED",
            )
            db.add(user)
            db.flush()
        else:
            user.plan = "BUSINESS"
        db.execute(text("UPDATE users SET plan = 'free' WHERE plan = 'LOCAL' OR plan = ''"))

        workspace = db.scalar(select(Workspace).where(Workspace.slug == "local-development"))

        if not workspace:
            workspace = Workspace(
                user_id=user.id,
                name="Local development workspace",
                slug="local-development",
            )
            db.add(workspace)
            db.flush()
        for tender in db.scalars(select(Tender).where(Tender.workspace_id.is_(None))):
            tender.workspace_id = workspace.id
        for document in db.scalars(
            select(CompanyEvidenceDocument).where(CompanyEvidenceDocument.workspace_id.is_(None))
        ):
            document.workspace_id = workspace.id
        for profile in db.scalars(
            select(CompanyProfile).where(CompanyProfile.workspace_id.is_(None))
        ):
            profile.workspace_id = workspace.id
        db.commit()


def initialize_database() -> None:
    """Initialize the database: apply migrations then seed development data.

    In production this runs Alembic migrations.
    In local development existing databases are preserved and stamped as
    current; fresh databases are created via the initial migration.
    """
    run_migrations()
    _seed_development_data()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
