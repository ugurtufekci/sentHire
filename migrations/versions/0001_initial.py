"""Initial schema (docs/03-data-model.md).

FROZEN: this migration describes the schema as it existed at revision 0001 and
must never be regenerated from the ORM models. A migration that materializes
live metadata silently changes shape every time a model changes, which makes
later migrations fail on a fresh database (0002 would re-add columns 0001 had
already created). Every schema change since is its own migration.
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = [
    "requirement_results",
    "evaluations",
    "embeddings",
    "overrides",
    "screening_runs",
    "candidate_profiles",
    "applications",
    "documents",
    "evaluation_specs",
    "jobs",
    "candidates",
    "users",
    "job_templates",
    "audit_log",
    "organizations",
]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    op.create_table('audit_log',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('actor', sa.UUID(), nullable=True),
    sa.Column('event', sa.Text(), nullable=False),
    sa.Column('entity', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('detail', postgresql.JSONB(astext_type=Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_log_org_id'), 'audit_log', ['org_id'], unique=False)
    op.create_table('candidates',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('primary_email', postgresql.CITEXT(), nullable=True),
    sa.Column('primary_phone', sa.Text(), nullable=True),
    sa.Column('display_name', sa.Text(), nullable=True),
    sa.Column('identity_keys', postgresql.JSONB(astext_type=Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('erased_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_candidates_org_id'), 'candidates', ['org_id'], unique=False)
    op.create_table('embeddings',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('scope', sa.Text(), nullable=False),
    sa.Column('ref_id', sa.UUID(), nullable=False),
    sa.Column('chunk_key', sa.Text(), nullable=False),
    sa.Column('model', sa.Text(), nullable=False),
    sa.Column('vector', Vector(1024), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('scope', 'ref_id', 'chunk_key', 'model')
    )
    op.create_index(op.f('ix_embeddings_org_id'), 'embeddings', ['org_id'], unique=False)
    op.create_table('job_templates',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('slug', sa.Text(), nullable=False),
    sa.Column('locale', sa.Text(), server_default=sa.text("'tr'"), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('spec_seed', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('organizations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('region', sa.Text(), server_default=sa.text("'eu'"), nullable=False),
    sa.Column('settings', postgresql.JSONB(astext_type=Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('users',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('email', postgresql.CITEXT(), nullable=False),
    sa.Column('role', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('jobs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('template_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'draft'"), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['template_id'], ['job_templates.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_jobs_org_id'), 'jobs', ['org_id'], unique=False)
    op.create_table('documents',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('candidate_id', sa.UUID(), nullable=True),
    sa.Column('upload_job_id', sa.UUID(), nullable=True),
    sa.Column('original_filename', sa.Text(), nullable=True),
    sa.Column('sha256', sa.Text(), nullable=False),
    sa.Column('s3_key', sa.Text(), nullable=False),
    sa.Column('mime', sa.Text(), nullable=False),
    sa.Column('page_count', sa.Integer(), nullable=True),
    sa.Column('size_bytes', sa.BigInteger(), nullable=True),
    sa.Column('document_kind', sa.Text(), server_default=sa.text("'cv'"), nullable=False),
    sa.Column('parse_status', sa.Text(), server_default=sa.text("'pending'"), nullable=False),
    sa.Column('parse_error', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], ),
    sa.ForeignKeyConstraint(['upload_job_id'], ['jobs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'sha256')
    )
    op.create_index(op.f('ix_documents_org_id'), 'documents', ['org_id'], unique=False)
    op.create_index(op.f('ix_documents_upload_job_id'), 'documents', ['upload_job_id'], unique=False)
    op.create_table('evaluation_specs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('spec', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('source_nl_text', sa.Text(), nullable=True),
    sa.Column('compiler_model', sa.Text(), nullable=True),
    sa.Column('compiler_prompt_version', sa.Text(), nullable=True),
    sa.Column('confirmed_by', sa.UUID(), nullable=True),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['confirmed_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('job_id', 'version')
    )
    op.create_index(op.f('ix_evaluation_specs_job_id'), 'evaluation_specs', ['job_id'], unique=False)
    op.create_index(op.f('ix_evaluation_specs_org_id'), 'evaluation_specs', ['org_id'], unique=False)
    op.create_table('applications',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('document_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.Text(), server_default=sa.text("'received'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], ),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('job_id', 'candidate_id')
    )
    op.create_index(op.f('ix_applications_candidate_id'), 'applications', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_applications_job_id'), 'applications', ['job_id'], unique=False)
    op.create_index(op.f('ix_applications_org_id'), 'applications', ['org_id'], unique=False)
    op.create_table('candidate_profiles',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('document_id', sa.UUID(), nullable=False),
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('profile', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('raw_text', sa.Text(), nullable=False),
    sa.Column('extraction_confidence', sa.Float(), nullable=True),
    sa.Column('extractor_model', sa.Text(), nullable=True),
    sa.Column('extractor_prompt_version', sa.Text(), nullable=True),
    sa.Column('pipeline_version', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], ),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('document_id', 'version')
    )
    op.create_index(op.f('ix_candidate_profiles_candidate_id'), 'candidate_profiles', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_candidate_profiles_document_id'), 'candidate_profiles', ['document_id'], unique=False)
    op.create_index(op.f('ix_candidate_profiles_org_id'), 'candidate_profiles', ['org_id'], unique=False)
    op.create_table('screening_runs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('spec_id', sa.UUID(), nullable=False),
    sa.Column('mode', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), server_default=sa.text("'queued'"), nullable=False),
    sa.Column('funnel', postgresql.JSONB(astext_type=Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('cost', postgresql.JSONB(astext_type=Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
    sa.ForeignKeyConstraint(['spec_id'], ['evaluation_specs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_screening_runs_job_id'), 'screening_runs', ['job_id'], unique=False)
    op.create_index(op.f('ix_screening_runs_org_id'), 'screening_runs', ['org_id'], unique=False)
    op.create_table('evaluations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('application_id', sa.UUID(), nullable=False),
    sa.Column('profile_version', sa.Integer(), nullable=False),
    sa.Column('spec_version', sa.Integer(), nullable=False),
    sa.Column('pipeline_version', sa.Text(), nullable=False),
    sa.Column('stage_reached', sa.Text(), nullable=False),
    sa.Column('hard_result', sa.Text(), nullable=False),
    sa.Column('overall_score', sa.Float(), nullable=True),
    sa.Column('rank', sa.Integer(), nullable=True),
    sa.Column('band', sa.Text(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('result', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('models_used', postgresql.JSONB(astext_type=Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['screening_runs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'application_id')
    )
    op.create_index(op.f('ix_evaluations_application_id'), 'evaluations', ['application_id'], unique=False)
    op.create_index(op.f('ix_evaluations_org_id'), 'evaluations', ['org_id'], unique=False)
    op.create_index(op.f('ix_evaluations_run_id'), 'evaluations', ['run_id'], unique=False)
    op.create_table('overrides',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('application_id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=True),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('action', sa.Text(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['screening_runs.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_overrides_application_id'), 'overrides', ['application_id'], unique=False)
    op.create_index(op.f('ix_overrides_org_id'), 'overrides', ['org_id'], unique=False)
    op.create_table('requirement_results',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('evaluation_id', sa.UUID(), nullable=False),
    sa.Column('req_id', sa.Text(), nullable=False),
    sa.Column('verdict', sa.Text(), nullable=False),
    sa.Column('score', sa.Float(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('info_status', sa.Text(), nullable=True),
    sa.Column('evidence', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('source_stage', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['evaluation_id'], ['evaluations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_requirement_results_evaluation_id'), 'requirement_results', ['evaluation_id'], unique=False)

    # HNSW index for cosine similarity (docs/03 §2)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_embeddings_vector_hnsw "
        "ON embeddings USING hnsw (vector vector_cosine_ops)"
    )


def downgrade() -> None:
    for table in TABLES:  # children before parents
        op.drop_table(table)
