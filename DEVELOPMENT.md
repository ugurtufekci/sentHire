# Development Guide

## Quickstart (Docker)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
docker compose up --build
```

This starts Postgres (pgvector), Redis, MinIO, Mailpit, the API (with migrations +
seed run automatically), a Celery worker and the web app at http://localhost:3000 —
the whole flow (signup → invite team → job → criteria → CVs → run → explained
ranking) works from the browser. Outbound email (invitations, password resets)
lands in the Mailpit inbox at http://localhost:8025.

The same flow over raw HTTP, if you prefer curl:

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

## Web app (local dev)

```bash
cd web && npm install && npm run dev   # http://localhost:3000
```

The dev server proxies `/api/*` to `http://localhost:8000` (override with
`API_PROXY_TARGET`), so run the API + worker alongside it. Sign up at
`/signup` to create a workspace; the session cookie flows through the proxy
because everything is same-origin.

Two things matter for uploads to work from a browser:

- The browser PUTs files **directly to S3/MinIO** with presigned URLs. SigV4
  signatures bind the host, so the API signs them against
  `SENTHIRE_S3_PUBLIC_ENDPOINT_URL` (the endpoint the *browser* reaches —
  `http://localhost:9000` in compose) while talking to MinIO over the internal
  endpoint itself.
- In compose, the `web` container proxies `/api/*` to `http://api:8000` via
  `API_PROXY_TARGET`. The image runs `next start` (not the standalone server)
  precisely so that rewrite target is read at container start, not baked at build.

## Billing (CV-volume plans, iyzico)

Pricing is by monthly CV volume: the meter counts a CV when intake accepts it
(valid PDF, not a duplicate) — re-screening already-processed CVs is memoized and
free. Counters are keyed by calendar month (`usage_counters`), so quotas reset at
month boundaries without a scheduler. The upload endpoint enforces the active
plan's quota with HTTP 402; the plan catalog lives in
`senthire/billing/plans.py` (**placeholder prices — set the real ones before
launch**).

Providers (`SENTHIRE_BILLING_PROVIDER`):

- `mock` (default): "Bu plana geç" activates instantly with no payment — local
  dev and demos.
- `iyzico`: real hosted checkout. Requires `SENTHIRE_IYZICO_API_KEY` /
  `SENTHIRE_IYZICO_SECRET_KEY` (sandbox keys work against the default
  `SENTHIRE_IYZICO_BASE_URL`) and `SENTHIRE_IYZICO_PLAN_REFS` — a JSON map from
  our plan ids to pricing-plan reference codes created in the iyzico dashboard,
  e.g. `{"baslangic": "ref-1", "profesyonel": "ref-2", "kurumsal": "ref-3"}`.
  Admins must save billing details (company title, tax number, ...) once; the
  checkout form renders on /billing, iyzico redirects to
  `/api/v1/billing/callback`, and activation happens only after a
  server-to-server retrieve confirms the subscription. Renewal webhooks can be
  enabled by setting `SENTHIRE_BILLING_WEBHOOK_TOKEN` and pointing iyzico at
  `/api/v1/billing/webhook/<that token>`.

## Tests

`make test` runs the pure-logic suites (no network, no DB): derived-field date math,
the predicate DSL, the deterministic scorer (pinned to the docs/06 worked example),
PDF text-layer detection, schema validation of the seed templates, the auth
building blocks (argon2 round-trip, token hashing, cookie flags, pre-DB 401 guards),
and the golden set (below).

`make test-all` additionally runs the suites that need a real Postgres — they
skip silently without one, so CI can run either shape:

- **Migrations** (`tests/test_migrations.py`): every revision applies to an empty
  database, the resulting schema matches the ORM exactly, and `downgrade base`
  leaves nothing behind. This is what catches a migration written against live
  ORM metadata — the kind that passes on a developer's already-migrated database
  and only fails on a fresh deploy.
- **End-to-end journey** (`tests/test_e2e_journey.py`): the product walked as a
  user walks it — signup, invite a colleague, tenant isolation, compile and
  confirm criteria, upload CVs, screen, read the ranking, memoized re-runs,
  billing quota and metering, and batch mode. Model calls and object storage are
  faked; the API, ORM, orchestration, and scorer are real.

## Golden-set evaluation

`goldens/` holds hand-labeled reference candidates and specs; the harness runs
them through the real pipeline stages and diffs every outcome against the labels
(see `goldens/README.md` for the labeling format).

```bash
make evals        # offline: predicates+merge+scorer must match labels 100%
make evals-live   # additionally grades the real light-screening model
                  # against the labels (needs ANTHROPIC_API_KEY; costs cents)
```

Offline mode is also wired into `make test`, so any change that shifts a golden
verdict, gate, band, or pinned score fails CI with the exact candidate and field
that moved. Live mode reports an exact-agreement rate against
`--min-agreement` (default 85%) plus token usage — run it before shipping prompt
or model changes.

Live mode is also the drift watchdog: `.github/workflows/drift.yml` runs it on
a daily schedule (once the `ANTHROPIC_API_KEY` repository secret is set and the
file is on the default branch) and fails loudly when the live model's agreement
with the hand-checked labels drops — catching provider-side model changes
before a customer does. For "what would the new stack change on a real run",
see the shadow tool: `python -m senthire.evals.shadow RUN_ID`.

## Offline demo mode

`SENTHIRE_FAKE_MODELS=1` serves every model call from `senthire.demo` instead
of the Anthropic API: extraction reads the real vocabulary tables, screening
returns deterministic verdicts, and no key or network is needed. Pair it with
`SENTHIRE_STORAGE_BACKEND=local` and the whole product runs on a laptop with
only Postgres and Redis. Runs started this way carry a `fake_models` stamp in
their funnel and the UI shows a demo banner — nobody can mistake the output
for a real screening. The browser audits under `scripts/audit/` run in exactly
this mode.

## Auth & workspaces (B2B tenancy)

The signup unit is the **company**: the first user creates the organization
(workspace) and becomes its admin. Colleagues never sign up separately — an admin
creates an invitation on the **Ekip** page and shares the link; accepting it adds
the colleague to the *same* organization. Everyone in a workspace sees the same
jobs, candidates, and results; all queries are org-scoped through one dependency
chain (`senthire/api/deps.py::get_current_user → get_org`).

Mechanics:

- Browser auth is a server-side session: HttpOnly `senthire_session` cookie holding
  an opaque token; the DB stores only its sha256 (`auth_sessions`). Passwords are
  argon2 hashes. Set `SENTHIRE_SECURE_COOKIES=true` behind HTTPS.
- Invitations (`invitations`) expire after 7 days. Creating one emails the invitee
  (via the `mail` Celery queue, so SMTP never blocks the request) and returns the
  link to the admin as a fallback; only the token hash is stored, so "resend"
  rotates the token and kills the old link.
- Password reset: `POST /auth/forgot-password` always answers 200 (no account
  enumeration), emails a 60-minute single-use link, and caps outstanding links
  per account. Completing a reset revokes all other reset links and sessions.
- Email backends (`SENTHIRE_EMAIL_BACKEND`): `console` (default — emails are
  printed to the API/worker logs, links clickable from the terminal) or `smtp`
  (docker-compose points it at Mailpit; production points it at any provider).
- Roles: `admin` (invite/manage members) and `member`. The API refuses to demote
  or deactivate the last active admin. Deactivating a member revokes their sessions.
- Optional `organizations.seat_limit` caps active members + pending invitations.
- Curl/scripting backdoor: if `SENTHIRE_DEV_API_KEY` is set (docker-compose sets
  `dev-local-key`; unset in production), requests with that `X-API-Key` act as an
  auto-provisioned "Dev Org" admin — this is what the quickstart curl examples use.

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
web/                        Next.js app (App Router): the HR-facing product UI
  app/                      pages: home, job creation, 3-step job journey, run results
  components/               RequirementsStep, UploadStep, CandidateDrawer
  lib/                      typed API client, shared types, Turkish label maps
```

## Conventions

- LLM calls live only in workers, never in API handlers (docs/01 §3).
- Anything deterministic (dates, filters, scores, ranks) is plain code (docs/04 §5).
- Version stamps everywhere: `PIPELINE_VERSION`, prompt versions in
  `Settings.prompt_versions`, `SCORER_VERSION` — bump them when behavior changes;
  memoization keys include them (docs/08 §4).
