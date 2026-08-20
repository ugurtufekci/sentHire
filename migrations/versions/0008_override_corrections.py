"""Overrides record which requirement a human corrected, and to what.

The table existed with only a candidate-level action. A verdict correction is
both the useful product action ("no, this candidate does have B2B experience")
and the highest-value training label the system can collect, so it needs the
requirement and the two verdicts.
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("overrides", sa.Column("req_id", sa.Text(), nullable=True))
    op.add_column("overrides", sa.Column("from_verdict", sa.Text(), nullable=True))
    op.add_column("overrides", sa.Column("to_verdict", sa.Text(), nullable=True))


def downgrade() -> None:
    for column in ("to_verdict", "from_verdict", "req_id"):
        op.drop_column("overrides", column)
