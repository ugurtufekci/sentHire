"""Candidate-facing email: the workspace's templates, and what was actually sent.

Screening produces a shortlist; somebody then has to write to those people.
Doing that outside the product means the timeline stops at "shortlisted" and
nobody can answer "did we ever reply to this candidate?".

The outbox stores rendered copy rather than a template reference on purpose:
editing a template later must not rewrite history.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_templates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("updated_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("org_id", "slug"),
    )
    op.create_index("ix_message_templates_org_id", "message_templates", ["org_id"])

    op.create_table(
        "candidate_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "application_id", UUID(as_uuid=True), sa.ForeignKey("applications.id"), nullable=False
        ),
        sa.Column("template_slug", sa.Text(), nullable=True),
        sa.Column("to_email", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_candidate_messages_org_id", "candidate_messages", ["org_id"])
    op.create_index(
        "ix_candidate_messages_application_id", "candidate_messages", ["application_id"]
    )


def downgrade() -> None:
    op.drop_table("candidate_messages")
    op.drop_table("message_templates")
