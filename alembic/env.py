"""Alembic environment configuration.

This file connects Alembic to the application settings so that
migrations always use the same DATABASE_URL (from .env / env vars)
as the running application.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# ------------------------------------------------------------------
# Import the application settings and model metadata.
# This makes the migration environment read the *same* database URL
# that the application uses (from .env / environment variables).
# ------------------------------------------------------------------
from backend.app.config import settings
from backend.app.database import Base  # noqa: F401  — import for side-effect
import backend.app.models  # noqa: F401  — registers all model tables with Base.metadata

# Alembic Config object, provides access to values in alembic.ini.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Use the application models as the autogenerate target.
target_metadata = Base.metadata

# The database URL comes from the application settings so that
# alembic.ini never holds real credentials.
# However, if the caller (_alembic_config() or a test) has already
# set a specific URL on the Config object, respect it and do not
# override — this is essential for testing with temp databases.
_alembic_ini_default = "sqlite:///./data/tender_ai.db"
_current_url = config.get_main_option("sqlalchemy.url")
if not _current_url or _current_url == _alembic_ini_default:
    config.set_main_option("sqlalchemy.url", settings.database_url)


def run_migrations_offline() -> None:
    """Run migrations in offline mode (emit SQL to stdout)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in online mode (connect to live database)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
