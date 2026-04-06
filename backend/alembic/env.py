"""Alembic environment — sync execution via asyncpg DSN.

We use a synchronous connection wrapper so Alembic's standard migration
engine works with the asyncpg-style DATABASE_URL that Railway provides.
"""
import os
import re
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Alembic Config object
config = context.config

# Configure logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── DSN resolution ────────────────────────────────────────────────────────────

def _get_sync_url() -> str:
    """
    Read DATABASE_URL from environment and convert to a synchronous
    psycopg2-compatible URL for Alembic.

    Railway / asyncpg use:  postgresql://... or postgres://...
    SQLAlchemy sync needs:  postgresql+psycopg2://...

    Falls back to env.DATABASE_URL, then alembic.ini sqlalchemy.url.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        # Fallback: read from alembic.ini (placeholder will fail, but that's ok
        # — the developer should set DATABASE_URL before running migrations)
        url = config.get_main_option("sqlalchemy.url", "")
    if not url or url == "placeholder":
        raise RuntimeError(
            "DATABASE_URL is not set. "
            "Export it before running alembic: export DATABASE_URL=postgresql://..."
        )
    # Normalize: postgres:// → postgresql://, add +psycopg2 driver
    url = re.sub(r"^postgres(ql)?://", "postgresql://", url)
    if "postgresql://" in url and "+psycopg2" not in url and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


# ── Migration modes ───────────────────────────────────────────────────────────

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generates SQL, no DB connection)."""
    url = _get_sync_url()
    context.configure(
        url=url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connects to DB)."""
    cfg_section = config.get_section(config.config_ini_section, {})
    cfg_section["sqlalchemy.url"] = _get_sync_url()

    connectable = engine_from_config(
        cfg_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
