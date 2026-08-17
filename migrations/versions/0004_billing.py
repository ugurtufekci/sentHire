"""Billing: per-org subscription state + monthly CV usage counters."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "subscriptions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column("plan_id", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default="pending_checkout"
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_ref", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
    )
    # unique index (not an inline constraint) so the DB matches the ORM's
    # mapped_column(unique=True, index=True) exactly — see the drift test
    op.create_index("ix_subscriptions_org_id", "subscriptions", ["org_id"], unique=True)

    op.create_table(
        "usage_counters",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column("cvs_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("org_id", "period"),
    )
    op.create_index("ix_usage_counters_org_id", "usage_counters", ["org_id"])


def downgrade() -> None:
    op.drop_table("usage_counters")
    op.drop_table("subscriptions")
