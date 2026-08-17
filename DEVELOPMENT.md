# Development Guide

## Quickstart (Docker)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
docker compose up --build
```

This starts Postgres (pgvector), Redis, MinIO, the API (with migrations + seed run
automatically) and a Celery worker. Then:

```bash
# All requests use the dev placeholder auth header (see below)
H='-H "X-API-Key: dev-local-key" -H "Content-Type: application/json"'

# 1. Create a job from the Sales Specialist template
curl -s -X POST localhost:8000/api/v1/jobs -H "X-API-Key: dev-local-key" \
  -H "Content-Type: application/json" \
  -d '{"title": "Satış Uzmanı — Ankara", "template_slug": "sales-specialist"}'

# 2. Ask for presigned upload URLs
curl -s -X POST localhost:8000/api/v1/jobs/<JOB_ID>/uploads -H "X-API-Key: dev-local-key" \
  -H "Content-Type: application/json" \
  -d '{"files": [{"filename": "cv1.pdf"}]}'

# 3. PUT the PDF to the returned presigned URL, then:
curl -s -X POST localhost:8000/api/v1/jobs/<JOB_ID>/uploads/complete -H "X-API-Key: dev-local-key" \
  -H "Content-Type: application/json" \
  -d '{"files": [{"s3_key": "<S3_KEY>", "filename": "cv1.pdf"}]}'

# 4. Watch intake/extraction progress
curl -s localhost:8000/api/v1/jobs/<JOB_ID>/candidates -H "X-API-Key: dev-local-key"

# 5. Compile requirements from natural language (Stage 2 — async; poll until "draft")
curl -s -X POST localhost:8000/api/v1/jobs/<JOB_ID>/requirements/compile \
  -H "X-API-Key: dev-local-key" -H "Content-Type: application/json" \
  -d '{"natural_language_text": "Ankara'"'"'da ikamet etmesi önemli. Çok sık iş değiştirmiş olmasın. En az 3 yıl B2B satış deneyimi olsun. SaaS deneyimi varsa avantaj."}'
curl -s localhost:8000/api/v1/requirements/<SPEC_ID> -H "X-API-Key: dev-local-key"
# review spec.compiler.back_translation + clarifications, then:

# 6. Confirm the spec (optionally send the HR-edited spec JSON in the body)
curl -s -X POST localhost:8000/api/v1/requirements/<SPEC_ID>/confirm \
  -H "X-API-Key: dev-local-key" -H "Content-Type: application/json" -d '{}'

# 7. Start a screening run (Stages 3–6) and watch the funnel
curl -s -X POST localhost:8000/api/v1/jobs/<JOB_ID>/runs \
  -H "X-API-Key: dev-local-key" -H "Content-Type: application/json" -d '{}'
curl -s localhost:8000/api/v1/runs/<RUN_ID> -H "X-API-Key: dev-local-key"

# 8. Ranked results + full per-candidate explanation
curl -s "localhost:8000/api/v1/runs/<RUN_ID>/results" -H "X-API-Key: dev-local-key"
curl -s localhost:8000/api/v1/runs/<RUN_ID>/results/<APPLICATION_ID> -H "X-API-Key: dev-local-key"
```

Run lifecycle: `queued → screening (Stages 3+4 per candidate) → selecting (Stage 5
policy) → deep_analysis (selected subset) → scoring (Stage 6) → complete`. Phase
transitions are guarded DB updates, so any number of workers can race safely.

API docs: http://localhost:8000/api/docs · MinIO console: http://localhost:9001

## Local (no Docker) setup

```bash
python -m venv .venv && source .venv/bin/activate
make install
# start postgres+redis+minio however you like (docker compose up db redis minio works)
make migrate seed
make api      # terminal 1
make worker   # terminal 2
```

## Tests

`make test` runs the pure-logic suites (no network, no DB): derived-field date math,
the predicate DSL, the deterministic scorer (pinned to the docs/06 worked example),
PDF text-layer detection, and schema validation of the seed templates.

## Auth (temporary)

`X-API-Key: dev-local-key` maps every request to an auto-created "Dev Org". This is a
placeholder wired through a single dependency (`senthire/api/deps.py::get_org`);
the signup/session auth milestone replaces only that function.

## Repository layout

```
docs/                       the architecture (source of truth for design decisions)
src/senthire/
  api/                      FastAPI app + routes (thin: validate, enqueue, report)
  db/                       SQLAlchemy models (docs/03) + session
  domain/                   pure logic: profile & spec schemas, derived fields,
                            predicate DSL (Stage 3), deterministic scorer (Stage 6)
  extraction/               Stage 1: PDF analysis + Claude structured-output extractor
  services/                 S3 storage
  workers/                  Celery app + intake/parse tasks (Stages 0–1)
  templates_seed/           built-in job templates (validated EvaluationSpec seeds)
migrations/                 Alembic (0001 = docs/03 schema)
tests/                      pure-logic test suites
```

## Conventions

- LLM calls live only in workers, never in API handlers (docs/01 §3).
- Anything deterministic (dates, filters, scores, ranks) is plain code (docs/04 §5).
- Version stamps everywhere: `PIPELINE_VERSION`, prompt versions in
  `Settings.prompt_versions`, `SCORER_VERSION` — bump them when behavior changes;
  memoization keys include them (docs/08 §4).
