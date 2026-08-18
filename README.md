# sentHire — AI-Powered CV Screening Platform

sentHire is a recruitment platform for HR departments whose core differentiation is an
**intelligent, configurable, multi-stage CV screening and ranking engine**. An HR
professional creates a job opening, uploads 100–500 CVs (PDF), describes what they are
looking for in **natural language**, and the system converts that intent into a
structured, explainable, cost-efficient screening pipeline.

**Delivery model: 100% hosted, multi-tenant SaaS.** HR teams sign up on the website and
do everything in the browser — no installation, no IT involvement, no on-prem
components. Every design decision below assumes a shared cloud platform with strict
per-organization data isolation (see [docs/01](docs/01-system-overview.md)).

> **The central architectural question**
> *How can we reliably evaluate 100–10,000 CVs against a combination of predefined job
> templates and natural-language HR requirements while minimizing LLM usage, latency,
> and cost — without sacrificing screening quality, explainability, or fairness?*

## The one principle everything follows from

**DO NOT SEND EVERYTHING TO THE MOST EXPENSIVE LLM.**

Every stage of the pipeline exists to eliminate work for the next, more expensive stage:

```text
100 raw CV PDFs
   │
   ▼  Stage 1 — Document extraction (once per document, cheap model, cached forever)
100 structured candidate profiles
   │
   ▼  Stage 3 — Deterministic hard filters (SQL/code, $0)
 72 candidates
   │
   ▼  Stage 4 — Lightweight semantic screening (small model, batched, cached prompts)
 35 candidates above threshold + a review band
   │
   ▼  Stage 5 — Adaptive deep analysis (large model, ONLY where it changes the outcome)
 15 candidates verified
   │
   ▼  Stage 6 — Deterministic weighted scoring & ranking (pure code, $0)
Top 10 presented to HR with full evidence and reasons
```

The LLM is used for **judgment**; plain code is used for **arithmetic, filtering,
ranking and everything deterministic**. LLM outputs are *verdicts with evidence*, never
final scores — the score is computed by a versioned, deterministic scoring engine so it
is reproducible, auditable and explainable.

## Headline numbers (100-CV screening run, detailed derivation in [docs/07](docs/07-model-strategy-and-cost.md))

| Architecture | Cost / 100 CVs | Wall-clock | Notes |
|---|---|---|---|
| Naive: every full CV → frontier model | ~$9–12 | 30–60 min serial | No reuse, no explainable score |
| **sentHire funnel (this design)** | **~$1.3–2.5** | ~3–5 min interactive, ~15–60 min batch | Profiles cached; re-runs cost ~10–25% of first run |
| Re-run after HR edits criteria | ~$0.05–0.60 | seconds–minutes | No re-parsing; deterministic/weight changes are free |

## Technology stack (recommended)

| Layer | Choice | Why |
|---|---|---|
| API backend | Python 3.12 + FastAPI | Best document/AI ecosystem, async, typed |
| Database | PostgreSQL 16 + `pgvector` | Relational + JSONB profiles + vector search in one system |
| Queue / cache | Redis + Celery workers | Mature retries, rate limiting, fan-out |
| Object storage | S3-compatible (S3 / R2 / MinIO) | Original PDFs, extraction artifacts |
| LLM | Anthropic Claude — **Haiku 4.5** (extraction, light screening), **Sonnet 5** (requirement compilation, deep analysis) | Two-tier cost model; Batch API (−50%), prompt caching (~0.1× cached reads), structured outputs |
| Embeddings | `bge-m3` (self-hosted) or Voyage multilingual | Turkish + English cross-lingual similarity |
| PDF processing | PyMuPDF text-layer fast path + Claude vision PDF input for scanned docs | One extraction system handles layout, OCR and structuring |
| Frontend | Next.js/React | Screening dashboard, evidence viewer |

## Documentation map

| Doc | Contents |
|---|---|
| [01 — System overview](docs/01-system-overview.md) | High-level architecture, components, API surface, deployment |
| [02 — Screening pipeline](docs/02-screening-pipeline.md) | Stages 0–6 in detail, PDF/OCR strategy, the multi-stage algorithm, full 100-CV walkthrough |
| [03 — Data model](docs/03-data-model.md) | Database schema, candidate profile JSON schema, evaluation spec schema |
| [04 — Requirement engine](docs/04-requirement-engine.md) | Natural language → structured Evaluation Spec, predicate DSL, rule engine vs LLM boundaries |
| [05 — Semantic matching](docs/05-semantic-matching.md) | Embeddings, taxonomies (ESCO), hybrid title/skill matching |
| [06 — Scoring & explainability](docs/06-scoring-and-explainability.md) | Weighted scoring methodology, evidence model, confidence, "why 82/100?" |
| [07 — Model strategy & cost](docs/07-model-strategy-and-cost.md) | Model selection, token math, cost comparisons, latency optimization |
| [08 — Batch processing & caching](docs/08-batch-processing-and-caching.md) | Queues, workers, idempotency, progress, caching layers, failure handling |
| [09 — Fairness & compliance](docs/09-fairness-and-compliance.md) | Bias mitigation, GDPR/KVKK, audit, human oversight, prompt-injection defense |
| [10 — Product UX](docs/10-product-ux.md) | HR user experience: templates, NL requirements, results, overrides, re-runs |
| [11 — MVP & scaling](docs/11-mvp-and-scaling.md) | Recommended MVP cut, path to 100k+ candidates, risk register |
| [12 — Differentiation](docs/12-differentiation.md) | Why this beats pasting CVs into a chatbot, and the plan to keep it that way |

## Design tenets

1. **Extract once, evaluate many.** A CV is parsed into a structured profile exactly
   once (per document hash). Re-screens, new jobs, edited criteria never re-parse.
2. **LLMs judge, code decides.** Models emit per-requirement verdicts + quoted
   evidence; a deterministic scorer turns verdicts into scores and ranks.
3. **Every claim has provenance.** Any conclusion shown to HR links to a quoted span
   of the CV, or is explicitly labeled *inferred*, *ambiguous* or *missing*.
4. **Missing ≠ failing.** Absent information is scored as *unknown* unless the HR-defined
   rule explicitly requires evidence.
5. **Humans decide.** The system ranks and explains; it never auto-rejects without a
   reviewable, overridable record. Protected characteristics are never extracted,
   inferred, or scored.
6. **Everything is versioned.** Spec version + profile version + prompt version +
   model version are stamped on every evaluation → reproducibility and cheap
   memoization.
