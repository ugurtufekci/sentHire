"""One live candidate per e-mail per workspace, enforced by the database.

Identity resolution was a SELECT followed by an INSERT. With several parse
workers running — the normal case, since intake is the parallel stage — two
documents for the same person can both miss the SELECT and both insert. The
same candidate then appears twice in the ranking and is screened (and billed)
twice, which quietly breaks the "same CV, five jobs, one parse" economics.

Partial, because a candidate erased under KVKK must not block that person from
ever applying again, and because CVs without an e-mail address are common and
must not collide with each other.
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

INDEX = "uq_candidates_live_email"


def upgrade() -> None:
    # primary_email is citext, so the uniqueness is already case-insensitive.
    op.execute(
        f"""
        CREATE UNIQUE INDEX {INDEX} ON candidates (org_id, primary_email)
        WHERE primary_email IS NOT NULL AND erased_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX}")
