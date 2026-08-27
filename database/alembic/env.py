"""Alembic environment.

DATABASE_URL is read from the environment, never from alembic.ini, because
the same immutable artifact is promoted across DEV / Stage / Production and
must not carry an environment-specific URL inside it (ADR-008).
"""
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data"))

from chann_data.db import Base  # noqa: E402
from chann_data import models  # noqa: F401,E402  (import registers the tables)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError("DATABASE_URL is DERIVED_AT_DEPLOY and must be set")

# Deliberately NOT config.set_main_option("sqlalchemy.url", database_url):
# configparser's default interpolation treats a bare "%" as the start of a
# %(name)s reference, and a Cloud SQL Unix-socket URL is full of them
# (?host=%2Fcloudsql%2Fproject%3Aregion%3Ainstance) — passing one through
# raises ValueError("invalid interpolation syntax") before a single query
# runs. database_url is used directly below instead, in both modes, so
# nothing here ever round-trips through configparser at all.

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=database_url, target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(database_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
