"""At most one open invitation per address per workspace.

The route checked for an existing invitation and then inserted one, which two
concurrent requests both pass — a double-clicked button produced several live
invitation links for the same person, and the seat-limit check could be raced.
Only the database can make that check and the insert atomic.

Partial, so accepted and revoked rows never block a fresh invitation.
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_invitations_open_email"


def upgrade() -> None:
    # collapse any duplicates that predate the constraint, newest kept
    op.execute(
        """
        UPDATE invitations SET revoked_at = now()
        WHERE accepted_at IS NULL AND revoked_at IS NULL AND id NOT IN (
            SELECT DISTINCT ON (org_id, lower(email::text)) id
            FROM invitations
            WHERE accepted_at IS NULL AND revoked_at IS NULL
            ORDER BY org_id, lower(email::text), created_at DESC
        )
        """
    )
    op.execute(
        f"CREATE UNIQUE INDEX {INDEX_NAME} ON invitations (org_id, lower(email::text)) "
        "WHERE accepted_at IS NULL AND revoked_at IS NULL"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
