"""Hiring pipeline: where each candidate stands after screening, and why.

Screening ends with a ranked list; hiring continues for weeks afterwards. These
columns and the event table track the human process — contacted, interviewing,
offered — so the work does not fall out of the product into someone's inbox.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "applications", sa.Column("stage", sa.Text(), nullable=False, server_default="new")
    )
    op.add_column(
        "applications", sa.Column("stage_changed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "applications",
        sa.Column("owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    )
    op.add_column("applications", sa.Column("next_action", sa.Text(), nullable=True))
    op.add_column(
        "applications", sa.Column("next_action_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_applications_stage", "applications", ["stage"])
    op.create_index("ix_applications_next_action_at", "applications", ["next_action_at"])

    op.create_table(
        "pipeline_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "application_id",
            UUID(as_uuid=True),
            sa.ForeignKey("applications.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("actor_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("from_stage", sa.Text(), nullable=True),
        sa.Column("to_stage", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("occurs_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detail", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_pipeline_events_org_id", "pipeline_events", ["org_id"])
    op.create_index("ix_pipeline_events_application_id", "pipeline_events", ["application_id"])
    op.create_index("ix_pipeline_events_occurs_at", "pipeline_events", ["occurs_at"])


def downgrade() -> None:
    op.drop_table("pipeline_events")
    op.drop_index("ix_applications_next_action_at", table_name="applications")
    op.drop_index("ix_applications_stage", table_name="applications")
    for column in ("next_action_at", "next_action", "owner_id", "stage_changed_at", "stage"):
        op.drop_column("applications", column)
