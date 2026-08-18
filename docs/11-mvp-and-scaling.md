# 11 — MVP Architecture, Scaling Path & Risk Register

## 1. Recommended MVP (target: first paying-quality release in ~6–8 weeks of build)

**Keep the full funnel — shrink everything around it.** The staged pipeline is the
product; cutting stages to "ship faster" would ship a different (worse) product.

| Area | MVP decision | Deferred (and why it's safe to defer) |
|---|---|---|
| Deployment | Single region; API + one worker service + Postgres + Redis + S3 (Docker Compose → small managed footprint) | K8s/KEDA, multi-region, read replicas — nothing in the design changes later, only replica counts |
| Extraction | Text-path (PyMuPDF) + Claude vision path for scanned; Haiku only | Managed OCR (Azure DI/Textract) — a cost optimization at ≥50–100k pages/mo, slot-in behind the same interface |
| Pipeline | Stages 0–6 complete, incl. memoization + spec versioning (these are *core value*, not polish) | — |
| Transport | Interactive fan-out + Batch API for large runs | Fine-grained hybrid scheduling |
| Semantic matching | Curated starter taxonomy + normalization cache + Haiku adjudication | Embedding prefilter & similarity features (doc 05 §3) — improves prompts/cost, not required for correctness |
| Scoring/explainability | Full deterministic scorer, evidence quotes, info_status, overrides, audit log | Confidence recalibration loop (needs accumulated labels) |
| UX | Template + NL compile/confirm, upload, live funnel, ranked results, candidate detail w/ PDF-highlight, rejected view, overrides, re-run, weights editor | Side-by-side compare, exports polish, override-analytics nudges, blind-mode UI (schema support ships day 1; toggle UI fast-follows) |
| SaaS | Self-serve signup, orgs/roles, per-org metering + budgets | SSO/SAML, billing automation, plan tiers |
| Compliance | Schema-as-allowlist, compliance lint, erasure endpoint, audit log, DPA template | Formal AI-Act documentation pack, residency options |

Explicit non-goals for MVP: ATS integrations, multi-position talent pools —
adjacent products, not the wedge. (Candidate e-mail outreach graduated from this
list to the near-term roadmap: the hiring pipeline now records who should get an
interview invitation, and the transactional-mail infrastructure already exists.)

## 1a. Implementation status (as of 2026-08)

Shipped and covered by the test suite (120 tests incl. end-to-end journeys,
migration round-trips, golden-set gate, browser smoke):

- [x] Funnel Stages 0–6 with memoization, spec versioning, evidence verification
- [x] Requirement compiler + confirm flow (back-translation, clarifying questions)
- [x] Workspace auth: signup, cookie sessions, invitations, password reset,
      admin/member roles, seat limits, last-admin protection
- [x] Transactional e-mail (console/SMTP backends, Celery `mail` queue)
- [x] Billing: CV-volume plans, monthly metering at intake (dedup is free),
      402 quota gate, iyzico subscription checkout (mock provider in dev)
- [x] Batch economy mode: Message Batches transport for Stages 4–5 at 50% token
      price, self-re-enqueueing pollers, per-run cost + savings rollup
- [x] Golden-set harness (offline CI mode + live grading vs answer key)
- [x] Hiring pipeline: drag-and-drop board, bulk shortlist, timeline, agenda
- [ ] SSE progress stream (UI polls today; contract unchanged)
- [ ] Embedding prefilter (doc 05 §3), managed OCR path, SSO — as planned below

## 2. Quality & evaluation harness (build in week 1, not at the end)

- **Golden CV set**: ~150–300 real-shaped CVs (TR/EN, digital + scanned, messy
  layouts) with human-labeled profiles and, for 3 reference jobs, human screening
  decisions.
- CI gates on every prompt/model/pipeline change: extraction field-accuracy,
  compiler spec-accuracy (doc 04 §7), end-to-end ranking agreement (Kendall τ vs
  labels), counterfactual fairness suite (doc 09 §2), cost-per-candidate budgets
  (doc 07 §6).
- Drift monitors in prod: Stage-4↔5 disagreement rate, hallucinated-quote incidents,
  schema-retry rate, human-override rate per requirement.
- Model upgrades = config bump → golden suite → canary org → fleet. Old evaluations
  remain pinned to their versions (doc 03).

## 3. Scaling to 100k+ candidates (and 10k+/day sustained)

The architecture scales by widening, not rearchitecting:

| Dimension | Change at scale |
|---|---|
| Workers | Per-queue pools; KEDA autoscaling on queue depth; extraction fleet sized separately (it's the burstiest) |
| Transport | Batch API becomes the default for everything non-interactive; interactive reserved as a premium/urgent mode; org-level token budgeting against provider rate limits with spill-to-batch |
| Extraction | Add managed OCR for scanned docs (cuts vision-token cost ~3×, adds redundancy); vision-LLM remains the fallback and the structuring step |
| Postgres | Partition `evaluations`/`requirement_results`/`audit_log` by month/org; read replicas for dashboards; PgBouncer; JSONB profiles stay (they're read-heavy, small) |
| Vectors | pgvector holds to ~10⁷ vectors comfortably with HNSW; beyond that, lift the `embeddings` table to a dedicated store behind the same interface |
| Queue | Redis → SQS/Kafka if depth/durability demands it (Celery supports both; task contracts unchanged) |
| Tenancy | Noisy-neighbor isolation via per-org budgets + scheduler (already in MVP design); largest tenants movable to dedicated worker pools |
| Residency | Region-pinned cells (EU / TR): DB+S3+workers per region, control plane global (doc 09 §4) |
| Cost at scale | Steady-state ≈ $110–200 / 10k CVs/day (doc 07 §3) before cross-job cache effects; unit costs *fall* with scale (cache hit rates rise) |

## 4. Build sequence (suggested)

1. **Week 1–2**: schemas + migrations (doc 03), org/auth, upload→S3, Stage 1
   extraction with golden-set harness.
2. **Week 3–4**: requirement compiler + confirm UI (doc 04), Stage 3 predicates,
   deterministic scorer skeleton (doc 06).
3. **Week 5–6**: Stages 4–5 with caching/batch transport (docs 07–08), funnel SSE,
   results + candidate detail UI.
4. **Week 7–8**: overrides/audit/erasure (doc 09), re-run memoization UX (doc 10 §7),
   metering + budgets, hardening (failure catalogue, doc 08 §6).

## 5. Risk register (deliverable 25)

| # | Risk | Likelihood / Impact | Mitigation (where designed) |
|---|---|---|---|
| 1 | Extraction errors on messy/scanned CVs silently reject good candidates | M / H | Borderline tolerance keeps near-threshold candidates in (02 §Stage 3); low-confidence flags; Stage 5 re-reads raw text; golden-set accuracy gate (11 §2) |
| 2 | LLM hallucination of evidence | M / H | Quote verification against stored text; fabricated quote ⇒ verdict voided + incident metric (06 §4) |
| 3 | Prompt injection via CV content | M / M | Layered defense: data framing, schema-bound outputs, evidence discipline, detector, deterministic firewall (09 §5) |
| 4 | Cost blowout (viral usage, pathological docs, prompt regressions) | M / H | Per-org budgets + metering, page caps, bounded output schemas, CI cost gates, batch default at volume (07 §4–6, 08 §1) |
| 5 | Provider rate limits / outage | M / M | Backoff, org smoothing, spill-to-batch, pause/resume state machine; multi-provider abstraction at the transport layer as insurance (08 §6) |
| 6 | NL misinterpretation of HR intent | M / H | Mandatory human confirmation + back-translation + clarifying questions (04 §3, §7); override-pattern detection (10 §6) |
| 7 | Bias / regulatory exposure | L / **Critical** | Schema-as-allowlist, compliance lint, blind mode, counterfactual CI suite, human-in-the-loop, audit trail (09) — and it's a *sales asset* in enterprise deals |
| 8 | Identity dedup mistakes (wrong merge) | L / M | Conservative resolution: strong keys only for merge; fuzzy match only reuses cache, never merges display records (03 §5) |
| 9 | Ranking instability across model upgrades erodes trust | M / M | Version pinning; runs are reproducible; upgrades gated by golden-suite ranking-agreement + canary (11 §2) |
| 10 | Stage-4 (cheap) vs Stage-5 (deep) disagreement too high ⇒ funnel quality doubts | M / M | Disagreement is measured continuously; if >threshold, widen the deep-analysis band automatically (spend ↑, quality guaranteed) — a tunable dial, not a redesign (02 §Stage 5) |
| 11 | Vendor lock-in (single LLM provider) | L / M | Provider-agnostic transport layer; prompts/schemas portable; per-stage model config (07 §1); embeddings already self-hostable |
| 12 | pgvector / Postgres scaling ceiling | L / L | Interfaces isolate stores; lift-out paths named (11 §3) |
| 13 | KVKK/GDPR incident (retention, erasure gaps) | L / Critical | Cascade-tested erasure, retention sweeper, residency pinning, subprocessor DPA, audit of every export (09 §4) |
| 14 | Latency perception on big runs | M / L | SSE live funnel, mode choice with explicit ETA/cost, batch banner (10 §4) |

## 6. The two numbers to keep honest

1. **Quality**: ranking agreement with human decisions on the golden set (target:
   top-10 overlap ≥ 90%, Kendall τ trending up release-over-release).
2. **Unit cost**: $ per screened candidate, all-in (target: < $0.03 steady-state at
   scale, < $0.02 with cache maturity — doc 07).

Every architectural decision above exists in service of moving #1 up while holding #2
down; if a future change can't say which of the two it improves, it probably doesn't
belong in the pipeline.
