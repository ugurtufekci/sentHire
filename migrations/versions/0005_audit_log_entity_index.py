"""Index the audit-log lookup that run cost aggregation depends on.

Finishing a run reads back every `llm.call` row for that run, matched on a JSONB
field. Without an index that is a sequential scan of the whole audit log — fine
on day one, progressively slower as the table accumulates every org's calls
forever. A partial expression index keeps it small (only llm.call rows) and
matches the query exactly.
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_audit_log_llm_call_entity_id"


def upgrade() -> None:
    op.execute(
        f"CREATE INDEX {INDEX_NAME} ON audit_log ((entity->>'id')) WHERE event = 'llm.call'"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
