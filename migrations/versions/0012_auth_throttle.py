"""Abuse counters for the auth surface.

Login, signup and the password-reset flows had no pacing: a password list
could be tried without limit and the forgot-password form could probe or
bomb. Counters are one row per scope (hashed identifier, never a raw
address) with a fixed window — in the database so every API process shares
them, matching how the rest of the system keeps state.
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_throttle",
        sa.Column("scope", sa.Text(), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("auth_throttle")
