"""ORM models mirroring the schema contract in docs/03-data-model.md."""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from senthire.db.base import Base, created_at_col, uuid_pk

JSONB_EMPTY_OBJ = text("'{}'::jsonb")
JSONB_EMPTY_ARR = text("'[]'::jsonb")


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(Text)
    region: Mapped[str] = mapped_column(Text, server_default=text("'eu'"))
    settings: Mapped[dict] = mapped_column(JSONB, server_default=JSONB_EMPTY_OBJ)
    # None = unlimited. Enforced on invitation create/accept (active members +
    # pending invitations), not retroactively.
    seat_limit: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = created_at_col()


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    role: Mapped[str] = mapped_column(Text)  # admin | member
    # Nullable: the auto-provisioned dev-key user has no password.
    password_hash: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()


class AuthSession(Base):
    """Server-side browser session; the cookie carries only the raw token.

    Only the sha256 of the token is stored, so a database leak does not leak
    usable session credentials.
    """

    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = created_at_col()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Subscription(Base):
    """One row per organization: its paid plan and provider state.

    No row (or a non-active status) means the organization is on the free
    trial plan. Plan definitions live in code (billing/plans.py); this row
    stores which one is active and how it is paid.
    """

    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), unique=True, index=True
    )
    plan_id: Mapped[str] = mapped_column(Text)
    # pending_checkout | active | past_due | canceled
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending_checkout'"))
    provider: Mapped[str] = mapped_column(Text)  # mock | iyzico
    # checkout token while pending, subscription reference once active
    provider_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageCounter(Base):
    """Monthly CV-processing counter per organization (pricing is CV-volume based).

    Keyed by calendar month ("2026-08"), so usage resets naturally at month
    boundaries without a scheduled job. Incremented when intake accepts a new
    (non-duplicate, valid) CV — the point where model cost starts.
    """

    __tablename__ = "usage_counters"
    __table_args__ = (UniqueConstraint("org_id", "period"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    period: Mapped[str] = mapped_column(Text)  # "YYYY-MM"
    cvs_processed: Mapped[int] = mapped_column(Integer, server_default=text("0"))


class PasswordReset(Base):
    """One-time password-reset token (sha256 stored, raw token only in the email)."""

    __tablename__ = "password_resets"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = created_at_col()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Invitation(Base):
    """Admin-issued invitation for a colleague to join the organization."""

    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    email: Mapped[str] = mapped_column(CITEXT)
    role: Mapped[str] = mapped_column(Text, server_default=text("'member'"))
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    invited_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at_col()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobTemplate(Base):
    __tablename__ = "job_templates"

    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(Text, unique=True)
    locale: Mapped[str] = mapped_column(Text, server_default=text("'tr'"))
    title: Mapped[str] = mapped_column(Text)
    spec_seed: Mapped[dict] = mapped_column(JSONB)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    title: Mapped[str] = mapped_column(Text)
    template_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_templates.id"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'draft'"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at_col()


class EvaluationSpecRow(Base):
    __tablename__ = "evaluation_specs"
    __table_args__ = (UniqueConstraint("job_id", "version"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)  # draft | confirmed | superseded
    spec: Mapped[dict] = mapped_column(JSONB)
    source_nl_text: Mapped[str | None] = mapped_column(Text)
    compiler_model: Mapped[str | None] = mapped_column(Text)
    compiler_prompt_version: Mapped[str | None] = mapped_column(Text)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    primary_email: Mapped[str | None] = mapped_column(CITEXT)
    primary_phone: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    identity_keys: Mapped[list] = mapped_column(JSONB, server_default=JSONB_EMPTY_ARR)
    created_at: Mapped[datetime] = created_at_col()
    erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("org_id", "sha256"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("candidates.id"))
    # Which job's upload flow brought this file in (intake-status convenience;
    # the authoritative candidate⇄job link is `applications`).
    upload_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"), index=True)
    original_filename: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(Text)
    s3_key: Mapped[str] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    document_kind: Mapped[str] = mapped_column(Text, server_default=text("'cv'"))
    parse_status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    parse_error: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at_col()


class CandidateProfileRow(Base):
    __tablename__ = "candidate_profiles"
    __table_args__ = (UniqueConstraint("document_id", "version"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidates.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    profile: Mapped[dict] = mapped_column(JSONB)
    raw_text: Mapped[str] = mapped_column(Text)
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    extractor_model: Mapped[str | None] = mapped_column(Text)
    extractor_prompt_version: Mapped[str | None] = mapped_column(Text)
    pipeline_version: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("job_id", "candidate_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), index=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidates.id"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"))
    # Screening state: received -> profiled -> screened. Set by the pipeline.
    status: Mapped[str] = mapped_column(Text, server_default=text("'received'"))
    # Hiring state: where this person is in the human process after screening.
    # Denormalized from pipeline_events (which stay the history of record) so
    # the board can filter and count without walking every timeline.
    stage: Mapped[str] = mapped_column(Text, server_default=text("'new'"), index=True)
    stage_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    # The next thing a human owes this candidate, and when. Drives the
    # "needs action" and "overdue" views — the questions HR actually asks.
    next_action: Mapped[str | None] = mapped_column(Text)
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = created_at_col()


class PipelineEvent(Base):
    """Append-only history of what a human did with a candidate.

    Every stage move, note, contact, and meeting lands here. The denormalized
    `applications.stage` is a cache of the latest stage_change; this table is
    what "why is this candidate here?" is answered from, and it is never edited.
    """

    __tablename__ = "pipeline_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id"), index=True
    )
    # stage_change | note | contact | meeting | outcome
    kind: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    from_stage: Mapped[str | None] = mapped_column(Text)
    to_stage: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    # When the thing happened or is due (a meeting's start, a call's time).
    occurs_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    detail: Mapped[dict] = mapped_column(JSONB, server_default=JSONB_EMPTY_OBJ)
    created_at: Mapped[datetime] = created_at_col()


class ScreeningRun(Base):
    __tablename__ = "screening_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), index=True)
    spec_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluation_specs.id"))
    mode: Mapped[str] = mapped_column(Text)  # interactive | batch
    status: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    funnel: Mapped[dict] = mapped_column(JSONB, server_default=JSONB_EMPTY_OBJ)
    cost: Mapped[dict] = mapped_column(JSONB, server_default=JSONB_EMPTY_OBJ)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Evaluation(Base):
    __tablename__ = "evaluations"
    __table_args__ = (UniqueConstraint("run_id", "application_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("screening_runs.id"), index=True)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    profile_version: Mapped[int] = mapped_column(Integer)
    spec_version: Mapped[int] = mapped_column(Integer)
    pipeline_version: Mapped[str] = mapped_column(Text)
    stage_reached: Mapped[str] = mapped_column(Text)  # hard_filter | light | deep
    hard_result: Mapped[str] = mapped_column(Text)  # pass | fail | borderline
    overall_score: Mapped[float | None] = mapped_column(Float)
    rank: Mapped[int | None] = mapped_column(Integer)
    band: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    result: Mapped[dict] = mapped_column(JSONB)
    models_used: Mapped[dict] = mapped_column(JSONB, server_default=JSONB_EMPTY_OBJ)
    created_at: Mapped[datetime] = created_at_col()


class RequirementResult(Base):
    __tablename__ = "requirement_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    evaluation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluations.id"), index=True)
    req_id: Mapped[str] = mapped_column(Text)
    verdict: Mapped[str] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    info_status: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[list | None] = mapped_column(JSONB)
    source_stage: Mapped[str | None] = mapped_column(Text)


class Embedding(Base):
    __tablename__ = "embeddings"
    __table_args__ = (UniqueConstraint("scope", "ref_id", "chunk_key", "model"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    scope: Mapped[str] = mapped_column(Text)  # profile | experience | requirement | title
    ref_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    chunk_key: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    vector: Mapped[list[float]] = mapped_column(Vector(1024))


class Override(Base):
    __tablename__ = "overrides"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("screening_runs.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(Text)  # promote | reject | restore | note
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    actor: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    event: Mapped[str] = mapped_column(Text)
    entity: Mapped[dict] = mapped_column(JSONB)
    detail: Mapped[dict] = mapped_column(JSONB, server_default=JSONB_EMPTY_OBJ)
    at: Mapped[datetime] = created_at_col()
