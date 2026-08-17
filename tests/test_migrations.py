"""Migrations must build the ORM schema exactly, from empty, every time.

This is the test that catches the class of bug where a migration is written
against live ORM metadata: it passes on a developer's already-migrated database
and fails only on a fresh deploy. Needs a real Postgres (with citext + vector);
skipped when SENTHIRE_TEST_DATABASE_URL is unset, so the default suite stays
network- and service-free.

    SENTHIRE_TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5432/postgres \\
        pytest tests/test_migrations.py
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

ADMIN_URL = os.environ.get("SENTHIRE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not ADMIN_URL, reason="set SENTHIRE_TEST_DATABASE_URL to run migration tests"
)

# Created by raw SQL because neither has an ORM declaration: pgvector's HNSW
# index type, and a partial expression index on a JSONB path. Autogenerate
# therefore always reports both as extra.
EXPECTED_EXTRA_INDEXES = {
    "ix_embeddings_vector_hnsw",
    "ix_audit_log_llm_call_entity_id",
    "uq_invitations_open_email",
}


@pytest.fixture
def fresh_database():
    """A brand-new database, dropped afterwards."""
    name = f"senthire_mig_{uuid.uuid4().hex[:12]}"
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = ADMIN_URL.rsplit("/", 1)[0] + "/" + name
    try:
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": name},
            )
            conn.execute(text(f'DROP DATABASE "{name}"'))


def _upgrade(url: str, revision: str = "head") -> None:
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, revision)


def test_migrations_run_clean_on_an_empty_database(fresh_database):
    _upgrade(fresh_database)  # raises if any revision conflicts with another


def test_migrated_schema_matches_the_orm(fresh_database):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from senthire.db import models  # noqa: F401  — populate metadata
    from senthire.db.base import Base

    _upgrade(fresh_database)
    engine = create_engine(fresh_database)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)

    unexplained = [
        op
        for op in diff
        if not (
            isinstance(op, tuple)
            and op[0] == "remove_index"
            and op[1].name in EXPECTED_EXTRA_INDEXES
        )
    ]
    assert not unexplained, f"migrations drifted from the ORM: {unexplained}"


def test_downgrade_removes_everything(fresh_database):
    """A migration that cannot be rolled back is a one-way door in production."""
    from alembic import command
    from alembic.config import Config

    _upgrade(fresh_database)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", fresh_database)
    command.downgrade(config, "base")

    engine = create_engine(fresh_database)
    with engine.connect() as conn:
        remaining = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name <> 'alembic_version'"
            )
        ).scalars().all()
    assert not remaining, f"downgrade left tables behind: {remaining}"
