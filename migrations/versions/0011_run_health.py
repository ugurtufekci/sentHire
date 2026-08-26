"""One evaluation per application per run, and a birth timestamp for runs.

The task layer resolves "was this candidate already evaluated in this run?"
with a SELECT followed by an INSERT. Normal operation enqueues each candidate
exactly once, so the race never fired — but stuck-run recovery re-enqueues
work, and a re-kick racing a still-alive worker would produce two ranking
rows for one person. The database now refuses the second row; the task layer
treats that refusal as "already evaluated".

created_at exists because stall detection needs a clock for the emptiest
failure of all: a run whose start task was lost sits in "queued" with
started_at NULL and no evaluations — nothing to measure silence against.
Existing rows are backfilled with the migration time, which is honest enough
for a column whose only job is measuring silence from now on.
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "screening_runs",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_evaluations_run_application", "evaluations", ["run_id", "application_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_evaluations_run_application", "evaluations", type_="unique")
    op.drop_column("screening_runs", "created_at")
