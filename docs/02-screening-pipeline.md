# 02 — The Multi-Stage Screening Pipeline

This is the core of sentHire. The pipeline is a funnel: each stage is cheaper per
candidate than the next and exists to shrink the population the next stage must touch.

```text
Stage 0  Intake & dedup             (code, $0)
Stage 1  Document extraction        (Haiku 4.5, once per document ever)
Stage 2  Requirement compilation    (Sonnet 5, once per job spec version)
Stage 3  Deterministic pre-screen   (SQL/code, $0)
Stage 4  Lightweight semantic screen(Haiku 4.5, batched, cached prompts)
Stage 5  Adaptive deep analysis     (Sonnet 5, only where it changes the outcome)
Stage 6  Scoring & ranking          (pure code, $0)
```

---

## Stage 0 — Intake & deduplication

**In:** uploaded files (PDF primarily; DOCX/images accepted).
**Out:** registered `document` rows, deduplicated; `application` rows linking candidate ⇄ job.

- Files land in S3 via presigned URLs (browser → S3 directly; API never proxies bytes).
- Compute `sha256` per file. **Same hash in the same org ⇒ reuse the existing parsed
  profile — the file is never parsed twice**, even across different jobs.
- Quick validation in code: file type sniffing, size caps (e.g. 20 MB / 25 pages),
  encrypted-PDF detection, "is this even a CV?" cheap classifier (Stage 1 model returns
  `document_kind`; cover letters/transcripts get attached, not screened).
- Candidate identity resolution (email > phone > fuzzy name+history match) so one human
  applying with two files becomes one candidate with two document versions.

Cost: $0 (no model calls). Why it exists: dedup is the single highest-ROI cache — job
boards resend the same CVs constantly.

---

## Stage 1 — Document extraction (PDF → structured profile)

**In:** one document (PDF/DOCX/image).
**Out:** a validated `CandidateProfile` JSON ([schema in doc 03](03-data-model.md)) +
provenance spans + extraction confidence, stored and versioned. Raw extracted text is
also stored for Stage 5 and the evidence viewer.

### A. Technology choices for parsing / OCR / layout

Two-path strategy, chosen per document by **text-layer detection** (PyMuPDF: does the
PDF contain a real text layer with sane coverage?):

**Path A — born-digital PDFs (~80–90% of Turkish/English CVs):**
1. Extract text locally with PyMuPDF (blocks + coordinates → reading order that
   survives two-column layouts; tables come out as ordered lines).
2. Send the *text* (not the PDF) to **Claude Haiku 4.5** with a structured-output
   schema → `CandidateProfile`. Text-only input is 3–5× cheaper than sending page
   images and is fully sufficient once layout is linearized.

**Path B — scanned/image PDFs, photos of CVs, DOCX-exported oddities:**
1. Send the PDF directly to **Claude Haiku 4.5 as a document block** (the API rasters
   pages; the model reads them visually). One system handles OCR + layout + tables +
   two-column CVs + Turkish diacritics + handwritten-ish fonts — no separate OCR
   pipeline to build, tune, or keep in sync for the MVP.
2. Escalate to Sonnet 5 only if Haiku's extraction confidence is low or schema
   validation repeatedly fails (rare; logged).

Why not classic OCR first?

| Option | Verdict |
|---|---|
| **Tesseract (+`tur` traineddata)** | Free, but poor layout handling on multi-column CVs, mediocre on low-quality scans; you still need an LLM pass to structure the text. Keep as an offline fallback only. |
| **Azure Document Intelligence / Google Document AI / AWS Textract** | Excellent OCR + layout + tables, per-page pricing (~$1–1.5/1k pages ballpark). The right move **at scale** (≥50–100k pages/month) to cut vision-token cost and add SLA redundancy. Not needed for MVP. |
| **Vision-LLM extraction (chosen)** | One call does OCR + layout + semantics + normalization + schema. Handles Turkish and English natively, mixed-language CVs, and weird templates. Slightly higher per-page cost than managed OCR, massively lower engineering cost. |

Multi-language: the extraction prompt is language-agnostic; the profile schema is
English-keyed with values preserved in source language plus normalized fields (dates →
ISO, cities → gazetteer canonical form: "İst." → `Istanbul, TR`). CEFR mapping for
languages ("iyi derecede İngilizce" → `B2 (inferred)`).

### B. What the extractor produces

The model fills the `CandidateProfile` schema (doc 03) using **structured outputs**
(`output_config.format` with a strict JSON schema — malformed JSON becomes a retry at
the API layer, not a parsing bug). Two hard rules in the extraction prompt:

1. **No derived math.** The model records employment periods as dates; *code* computes
   total experience, tenure, gaps, job-change counts. LLMs are unreliable at date
   arithmetic; Postgres is not.
2. **No protected attributes.** Fields for age/birthdate, gender, religion, ethnicity,
   marital status, photo description are *not in the schema*; the prompt forbids
   inferring them; anything volunteered in the CV is simply not captured
   ([doc 09](09-fairness-and-compliance.md)).

Each substantive field carries provenance: `{page, char_span or bbox, verbatim_quote}` —
this is what powers evidence highlighting in the UI and anti-hallucination checks.

### C. Derived fields (computed in code, Stage 1.5)

```python
derived = {
  "total_experience_months": ...,       # union of employment intervals (overlaps merged)
  "relevant_experience_months": None,   # filled later per-job by Stage 4/5
  "job_count": ..., "avg_tenure_months": ...,
  "job_changes_last_5y": ...,           # for "çok sık iş değiştirmesin"
  "employment_gaps": [{"from": ..., "to": ..., "months": ...}],
  "current_employment_status": "employed|unemployed|unknown",
  "seniority_estimate": "junior|mid|senior|lead|unknown"   # rule-based from titles+years
}
```

**Cost/latency per CV:** Path A ≈ 2–3k input + ~1.2k output tokens on Haiku ⇒
~$0.008 (~$0.004 batch). Path B ≈ 6–9k input (page images) ⇒ ~$0.012–0.02. Once. Ever.

**Why not a more expensive model:** extraction is transcription + light normalization —
Haiku's error rate is within noise of Sonnet here, and every downstream stage can
compensate; the one place quality genuinely matters more (deep judgment) gets Sonnet.

---

## Stage 2 — Requirement compilation (job template + NL → Evaluation Spec)

**In:** selected template (e.g. *Sales Specialist* with its standard criteria), HR's
edits, and free-text instructions (Turkish/English), e.g.:

> "Ankara'da ikamet etmesi önemli. Çok sık iş değiştirmiş olmasın. En az 3 yıl B2B
> satış deneyimi olsun. SaaS deneyimi varsa avantaj."

**Out:** a versioned `EvaluationSpec` — typed requirements with category, type
(hard/soft/bonus/penalty/disqualifier), importance, weights, and for each requirement an
**evaluator**: `deterministic` (a predicate over profile fields), `semantic` (an LLM
rubric), or `hybrid`. Full design in [doc 04](04-requirement-engine.md).

Runs **once per spec version** on **Sonnet 5** (quality matters, volume is tiny —
~$0.02–0.05/job). The draft is shown to HR for confirmation before any screening runs:
the human approves the interpretation of their own words. Ambiguities become explicit
questions with editable defaults ("sık iş değiştirme" → default: *>3 employer changes
in last 5 years*, threshold editable).

---

## Stage 3 — Deterministic pre-screening (the free filter)

**In:** all parsed profiles for the job + the spec's deterministic requirements.
**Out:** per-candidate: `knocked_out | passed`, with per-requirement results
(`pass/fail/unknown`) recorded even for knockouts (explainability requires knowing *why*).

Every requirement whose evaluator is `deterministic` compiles to a safe predicate over
the profile (whitelisted fields + operators — a JSON DSL, never generated code;
[doc 04 §4](04-requirement-engine.md)):

```json
{"all": [
  {"field": "derived.total_experience_months", "op": ">=", "value": 36},
  {"field": "location.city_canonical", "op": "in", "value": ["Ankara"]},
  {"field": "education.highest_degree_rank", "op": ">=", "value": "bachelor"},
  {"field": "languages['en'].cefr_rank", "op": ">=", "value": "B2"}
]}
```

Rules of the stage:

- **Only `type=hard` + `evaluator=deterministic` requirements can knock out.** A soft
  or semantic requirement never eliminates anyone here.
- **`unknown` never knocks out by default.** If the CV doesn't state location, the
  location check returns `unknown`, the candidate proceeds, and the gap is surfaced.
  Only requirements HR explicitly marked `missing_policy=fail` ("must present evidence
  of X") treat absence as failure.
- Borderline guard: values within a configurable tolerance of a numeric threshold
  (e.g. 34 months vs required 36) are *not* knocked out — they are tagged
  `borderline` and routed to Stage 4/5, because extraction may have missed a role.
  Cheap insurance against extraction error becoming silent rejection.

Runs as a single SQL/Python pass over JSONB profiles. **Cost $0, latency ~ms.**
Example effect: 100 → 72 candidates continue.

**Why it exists:** every candidate eliminated here saves *both* Stage 4 and (odds-on)
Stage 5 spend — this is the highest-leverage cost stage, and it's free.

---

## Stage 4 — Lightweight semantic screening

**In:** the 72 surviving profiles + the compiled spec's semantic + soft requirements.
**Out:** per candidate: per-requirement verdicts with evidence pointers, category
subscores, a **preliminary score**, and a **confidence** level.

Mechanics:

- Model: **Claude Haiku 4.5**, structured outputs, no extended thinking.
- Prompt layout is cache-optimized ([doc 07 §4](07-model-strategy-and-cost.md)):
  `[static system + rubric instructions] [evaluation spec] [← cache breakpoint]
  [candidate profile JSON]`. Across 72 candidates the spec+system prefix is written to
  the prompt cache once and read at ~0.1× price 71 times.
- Transport: interactive runs fan out N parallel Messages calls; economy runs submit
  one **Message Batch** (50% price) with `custom_id = application_id`.
- The model evaluates only what deterministic code cannot: title/skill relevance
  ("Account Executive" ≈ sales role — assisted by the semantic-matching layer,
  [doc 05](05-semantic-matching.md)), industry match, career-trajectory reading,
  red-flag detection (unexplained gaps *presented as such, not auto-penalized*),
  requirement-by-requirement judgment with quoted evidence.

Output shape per requirement (stored, feeds the scorer):

```json
{"req_id": "R3_b2b_sales_3y", "verdict": "met", "score": 0.9,
 "evidence": [{"quote": "Enterprise Sales Executive, Acme SaaS, 2019–2023 — managed B2B pipeline", "page": 1}],
 "info_status": "explicit", "confidence": 0.85}
```

Plus `preliminary_score` (computed by the deterministic scorer from these verdicts, not
by the model) and `confidence` aggregated per candidate.

**Cost:** ~1.5–2k fresh input + ~0.9k output on Haiku ⇒ ~$0.006/candidate interactive,
~$0.003 batch. 72 candidates ≈ **$0.25–0.45**.

**Why a cheap model suffices:** the task is *scoring against an explicit rubric with
the evidence in front of it* — narrow, well-scaffolded, schema-constrained. Errors are
caught for the population that matters by Stage 5.

---

## Stage 5 — Adaptive deep analysis (spend only where it changes the outcome)

**In:** a *selected subset* of Stage-4 results. **Out:** verified verdicts, corrected
scores, richer reasoning, final confidence.

### Selection policy (the "adaptive" part)

A candidate is deep-analyzed iff any of:

| Trigger | Rationale |
|---|---|
| Preliminary rank within the **decision band** (e.g. ranks 5–25 when HR wants a top-10) | Ordering errors here change who gets interviewed |
| `confidence < 0.7` on any requirement that carries ≥10% weight | Cheap model wasn't sure where it matters |
| Conflicting evidence (deterministic says fail, semantic found counter-evidence; or `borderline` tag from Stage 3) | Possible extraction error |
| Hard requirement satisfied only by *inferred* (not explicit) evidence | Verify before it gates an interview |
| Flagged patterns needing interpretation (career pivot, gap with context, agency/freelance stints) | Genuinely ambiguous — the case for a stronger model |
| Top-K verification: the final top 10–15 **always** get a deep pass | Never present an unverified shortlist |

Clear rejects (rank ≪ threshold, high confidence) and clear stars pass through with
Stage-4 results; typical selection: **15–25 of 72**.

### The deep call

- Model: **Claude Sonnet 5** (`effort: low|medium` — this is verification against a
  rubric, not open-ended reasoning; adaptive thinking stays useful and cheap at low
  effort). Escalation to Opus 5 is configurable for premium tiers but not default.
- Input: spec (cached prefix) + candidate profile + **raw extracted CV text** (so the
  model can catch what extraction missed) + Stage-4 verdicts to confirm or overturn.
- Tasks: verify each evidence quote actually appears in the text (anti-hallucination),
  re-judge low-confidence requirements, resolve conflicts, produce strengths/weaknesses
  and missing-info list, and emit `corrections[]` where Stage 4 was wrong (logged as a
  drift metric).

**Cost:** ~5–7k input + ~1.5k output on Sonnet ⇒ ~$0.04/candidate (~$0.02 batch).
20 candidates ≈ **$0.4–0.8**.

**Why not deep-analyze everyone:** for the bottom 40 candidates the outcome is already
determined at high confidence; Sonnet would change nothing but the bill (~4× stage
cost) and the latency.

---

## Stage 6 — Final scoring & ranking (deterministic)

**In:** best-available verdicts per candidate (Stage 5 where run, else Stage 4) + spec
weights. **Out:** overall score 0–100, rank, band, pass/fail, full explanation object.

The scorer is a **pure function** — no model, versioned, unit-tested
([doc 06](06-scoring-and-explainability.md) for the formula and worked example):

- Hard requirements: gate (pass/fail), never blended into the number.
- Category subscores = evidence-and-confidence-weighted requirement scores; overall =
  Σ(category × configurable weight); penalties (e.g. job-hopping deprioritizer)
  subtract transparently; bonuses add on top, capped.
- `unknown` requirements redistribute weight rather than scoring 0 (missing ≠ negative),
  and are listed in *Missing information*.
- HR edits a weight → re-rank recomputes in milliseconds with **zero LLM calls**.

---

## End-to-end walkthrough: 100 CVs, "Sales Specialist", 8 template criteria + 5 NL requirements

| # | Stage | Data in → out | Tech / model | Cost (interactive) | Wall-clock | Why this stage / why not a bigger model |
|---|---|---|---|---|---|---|
| 0 | Intake | 100 PDFs → 98 valid docs (1 dupe, 1 corrupt) | S3, hashing, sniffing | $0 | seconds | Dedup is the cheapest cache there is |
| 1 | Extraction | 98 docs → 98 profiles (+text, provenance) | PyMuPDF + Haiku 4.5 structured outputs | ~$0.85 (≈80 text-path × $0.008 + 18 vision-path × $0.015) | ~2 min at 16-way parallelism | Transcription task; Haiku ≈ Sonnet quality here at 1/3 price. Never repeated for these files again |
| 2 | Spec compile | template + 5 NL sentences → spec v1 (13 requirements: 4 hard-det, 6 scored-sem, 2 bonus, 1 penalty) | Sonnet 5, once | ~$0.03 | ~10 s | Quality-critical, volume-tiny ⇒ big model is correct here |
| 3 | Hard filter | 98 profiles → 72 pass, 26 knocked out (each with recorded reasons) | SQL/JSON predicates | $0 | <1 s | Free elimination; saves ~26×(Stage4+likely Stage5) spend |
| 4 | Light screen | 72 profiles → 72 scored w/ verdicts+evidence; 37 high-confidence rejects, 35 contenders | Haiku 4.5, cached spec prefix, 16-way fan-out | ~$0.40 | ~1.5 min | Rubric-scoring with evidence in-context — cheap model, schema-constrained |
| 5 | Deep analysis | 20 selected (decision band 15 + 5 low-confidence) → verified verdicts, 3 rank corrections | Sonnet 5 effort=low, raw text incl. | ~$0.75 | ~1.5 min | Only calls that can change who gets interviewed |
| 6 | Score & rank | 72 → ranked list, top-10 shortlist, reasons for all | Pure code | $0 | <1 s | Deterministic, re-runnable, auditable |
|   | **Total** | | | **≈ $2.0** (≈ **$1.1** in batch mode) | **≈ 3–5 min** (batch: ≤ 1 h) | Naive all-frontier ≈ $9–12 and no reuse |

Re-run after HR adds "Fluent German is a plus": compile delta (~$0.02) + Stage 3 free +
Stage 4 only re-evaluates the *new requirement* against 72 profiles (~$0.15 — the other
verdicts are memoized) + selective Stage 5 → **~$0.3 total, ~1 minute**. Changing only
weights: **$0, instant**.

## Pipeline pseudocode

```python
def run_screening(job, spec, mode):
    apps = applications_with_profiles(job)
    for app in apps:
        if memoized(app.profile_version, spec.version, PIPELINE_VERSION):
            copy_forward(app); continue
        enqueue("screen.application", app.id, spec.version, mode)

def screen_application(app_id, spec_version, mode):
    profile, spec = load(app_id, spec_version)
    det = evaluate_deterministic(profile, spec)          # Stage 3
    record(det)
    if det.knocked_out and not det.borderline:
        return finalize(app_id, det)                     # explained rejection
    light = llm_light_screen(profile, spec, mode)        # Stage 4 (Haiku)
    record(light)
    if needs_deep(light, det, decision_band(spec)):      # Stage 5 policy
        deep = llm_deep_analysis(profile, raw_text(app_id), spec, light, mode)
        record(deep)
    finalize(app_id, score(spec, best_verdicts(app_id))) # Stage 6 (pure code)
```

Failure handling, retries, idempotency and progress reporting for all of the above:
[doc 08](08-batch-processing-and-caching.md).
