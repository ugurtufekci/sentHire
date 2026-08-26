# 07 — Model Strategy, Cost Model & Latency

## 1. Model tiers (question J: one model vs many)

**Use multiple models, matched to task difficulty — never one model for everything.**
A single frontier model overpays 3–10× on the 95% of calls that are transcription or
rubric-scoring; a single cheap model underperforms exactly where errors are most
expensive (ambiguity, verification, spec compilation). Two workhorse tiers + one
optional escalation tier:

| Model | API price (in/out per MTok)* | Used for | Why |
|---|---|---|---|
| **Claude Haiku 4.5** (`claude-haiku-4-5`) | $1 / $5 | Stage 1 extraction, Stage 4 light screening, taxonomy adjudication, document-kind classification | Narrow, schema-constrained tasks with the evidence in-context; Haiku's quality gap vs bigger models is small here and errors are downstream-recoverable |
| **Claude Sonnet 5** (`claude-sonnet-5`) | $3 / $15 (intro $2/$10 to 2026-08-31) | Stage 2 spec compilation, Stage 5 deep analysis/verification, extraction escalation | Judgment, ambiguity, verification — where being wrong changes interview lists. Adaptive thinking at `effort: low/medium` keeps cost controlled |
| **Claude Opus 5** (`claude-opus-5`) | $5 / $25 | Optional: premium-tier deep analysis; compiler for very complex multi-page job specs | Escalation valve, not a default; the funnel's economics don't need it |
| Embeddings (`bge-m3` self-hosted or Voyage) | ~$0–0.1 / MTok | Semantic matching (doc 05) | Effectively free at CV scale |

\* Anthropic list prices as of this design (2026-08); Batch API halves them. Re-verify
at implementation time; the *architecture* is price-insensitive — tiers and funnel stay
correct even if absolute numbers move.

Configuration notes per tier:
- Haiku calls run without extended thinking, `max_tokens` sized to the schema (≈1.5k),
  **structured outputs** enforced (schema-invalid output becomes an API-level retry).
- Sonnet 5 has adaptive thinking on by default; we pin `output_config.effort` to
  `low`/`medium` for Stage 5 (verification against a rubric, not open-ended research)
  and `medium` for compilation. Output stays schema-bound.
- Model IDs, prompt versions and effort settings are config, stamped onto every
  evaluation (doc 03) — model upgrades are a config change + canary + golden-suite run,
  not a code change ([doc 11 §4](11-mvp-and-scaling.md)).

## 2. Where the money goes (per-CV unit economics)

Token assumptions: 2–3 page CV; profile JSON ≈ 1.2–1.5k tokens; spec ≈ 1.5k tokens;
shared system prompts 1–2k (cached).

| Stage | Model | Fresh in | Cached in | Out | $/candidate (API) | $/candidate (Batch −50%) |
|---|---|---|---|---|---|---|
| 1 extract (text path) | Haiku | ~2.5k | ~1.5k | ~1.3k | $0.009 | $0.005 |
| 1 extract (vision path) | Haiku | ~7k | ~1.5k | ~1.3k | $0.014 | $0.007 |
| 3 hard filter | — | — | — | — | $0 | $0 |
| 4 light screen | Haiku | ~1.6k | ~3k | ~0.9k | $0.006 | $0.003 |
| 5 deep analysis | Sonnet | ~6k | ~3k | ~1.5k | $0.042 | $0.021 |
| 2 compile (per job) | Sonnet | ~3k | — | ~2k | $0.04/job | — |

Cost concentration insight: **extraction of scanned pages (vision tokens) and Stage 5
output tokens dominate**. Hence: text-path-first extraction, page caps, Stage 5
selection discipline, and bounded output schemas (verdicts, not essays).

## 3. Architecture cost comparison (question I / deliverable 22)

Per 100 CVs, ~20% scanned, 8+5 requirements; and daily at 10k CVs (≈ enterprise scale):

| Architecture | 100 CVs | 10,000 CVs/day | Quality/ops notes |
|---|---|---|---|
| **A. Everything → frontier LLM** (full CV + spec to Opus-class per candidate, single deep pass) | ~$9–12 | ~$900–1,200/day | Slow, no reuse, score is an unexplained model number, re-runs cost full price |
| **B. Everything → mid LLM** (Sonnet single pass) | ~$5.5 | ~$550/day | Cheaper but still re-parses per job; no gating; weakest-link explainability |
| **C. sentHire funnel** (extract-once + deterministic + Haiku + selective Sonnet) | **~$2.0 API / ~$1.1 batch** | **~$110–200/day** | Full explainability; re-runs ~free; quality *higher* than B on the decisive band because Stage 5 verifies |
| **D. Embedding-retrieval + LLM rerank only** (no structured extraction; rank by similarity, LLM reranks top-k) | ~$0.3 | ~$30/day | Cheapest but cannot check hard requirements ("≥3 years", "Ankara"), can't explain, can't survive an audit. Useful as an *ingredient* (doc 05), not an architecture |
| C with cross-job cache at steady state (job boards resend CVs; ~40% profile-cache hits) | ~$1.4 | ~$70–130/day | Extraction amortizes across jobs |

Re-run economics (the differentiator HR feels): criteria tweak on C costs **$0–0.6**;
on A/B it costs the full run again.

## 4. The four API cost levers (use all of them)

1. **Message Batches API (−50%)**: default transport for runs >~200 candidates and for
   all re-runs where the user picks "economy". One batch per run stage,
   `custom_id = application_id`, poller workers collect results (typically well under
   1 h; 24 h worst case). Interactive mode remains for the "watch it live" experience.
2. **Prompt caching (~0.1× cached reads)**: prompts are assembled stable-prefix-first:
   `[system + rubric (static per prompt version)] [spec vN] ⟵ cache_control breakpoint
   [candidate payload]`. In a 72-candidate Stage 4 fan-out the ~4.5k-token prefix is
   written once (1.25×) and read 71× at 0.1× ⇒ prefix cost drops ~85%. Same pattern for
   Stages 1 and 5. (Batch requests support caching too; hit rates are best-effort there —
   we still structure prompts identically.)
3. **Structured outputs**: `output_config.format` with strict JSON schemas eliminates
   malformed-JSON retries (a real cost/latency tax at volume) and keeps outputs
   *bounded* — the schema is the verbosity cap.
4. **Right-sizing & memoization**: `count_tokens` pre-flight on unusual documents;
   evaluations memoized on `(profile_version, spec_version, pipeline_version)` so
   nothing is ever paid for twice ([doc 08 §4](08-batch-processing-and-caching.md)).

## 5. Latency optimization (question 16)

| Path | Target | How |
|---|---|---|
| Upload → profiles visible | ~1–3 min for 100 CVs | Parse workers fan out (16–32 concurrent Haiku calls); text path ~3–5 s/CV, vision ~6–10 s/CV; UI shows per-file progress immediately |
| Interactive screening run (100 CVs) | **3–5 min end-to-end** | Stage 3 instant; Stage 4 fan-out bounded by org concurrency budget; Stage 5 only ~20 calls; Stage 6 ms. Progress streams via SSE so perceived latency is low |
| Economy/batch run | ≤ 1 h typical | Batch API; UI sets expectations ("results by ~14:30, 50% cheaper") |
| Criteria re-run | seconds–1 min | Memoization + selective re-evaluation |
| Concurrency governance | — | Per-org concurrency + tokens/min budget (Redis token bucket) sized against our Anthropic rate limits (ITPM/OTPM); global scheduler prevents one tenant's run from starving others; 429/529 → exponential backoff + jitter, automatic spill-over of the remainder into a Batch submission |

Not used deliberately: streaming (no human reads mid-generation output in a pipeline),
extended thinking on Haiku stages, and long outputs anywhere (schemas cap them).

## 6. Cost governance in the product

- Every model call is metered (org, job, run, stage, tokens, cache hits, $) — powering
  an internal margin dashboard and a per-org monthly token budget with soft/hard limits.
- Plans map cleanly to knobs: candidate volume, interactive vs batch default, Stage 5
  depth (band width), optional Opus escalation — pricing tiers are *pipeline
  configuration*, not new code paths.
- Regression guard: cost-per-candidate per stage is tracked in CI against the golden
  suite; a prompt change that doubles output tokens fails the build before it ships.

## 7. Judgment sampling: temperature and borderline voting

Temperature is never left to the provider default. Every judge call — extraction,
compilation, light and deep screening, the labeling oracle — runs at an explicit
low `judge_temperature` (default 0.2): anchored scales do the real variance
control, this trims sampling noise on top of them.

The exception is deliberate. A candidate selected for deep analysis because the
pipeline is **unsure** (borderline knockout, unverified hard requirement, low
confidence on a heavy requirement) gets `deep_borderline_votes` independent deep
passes (default 3) instead of one. Vote 1 runs at the judge temperature — so a
voting run's first pass is exactly the call a non-voting run makes — and votes
2..K sample at `deep_vote_temperature` (default 1.0), because self-consistency
needs diverse reasoning paths, not the same path repeated.

Per requirement, the majority verdict wins; the merged confidence is the
majority's own claim tempered by its share of the pool. Three outcomes route to
a human instead of being averaged away: a met-vs-not_met split anywhere in the
pool, a pool with no majority, and a pool whose evidence all failed verbatim
verification. These surface as `deep_vote_disagreement` in the review reasons,
with the per-requirement vote counts stored on the evaluation (`deep_votes`).

Scope: interactive transport only. The batch lane stays single-pass by design —
it is the bulk cost-optimized path, and tripling it would cancel exactly the
discount it exists for. Candidates deep-analyzed merely for ranking near the top
("decision_band") also stay single-pass: the extra spend goes where the
uncertainty is, which is the only place it buys anything.
