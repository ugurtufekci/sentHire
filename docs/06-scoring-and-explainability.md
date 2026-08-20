# 06 — Scoring, Ranking & Explainability

The score is the product's most-scrutinized number, so it is produced by a
**deterministic, versioned scoring engine** — a pure function of (spec, verdicts).
LLMs supply *verdicts with evidence*; they never emit the final number. This gives us:
reproducibility (same inputs ⇒ same score), instant free re-scoring when HR edits
weights, unit-testability, and explanations that are *decomposable by construction*.

## 1. Inputs to the scorer

Per candidate, the best-available verdict per requirement (Stage 5 result where it ran,
else Stage 4, else Stage 3 deterministic):

```text
verdict ∈ {met, partially_met, not_met, unknown, disqualified}
score   ∈ [0,1]        (graded degree of satisfaction, from the rubric)
confidence ∈ [0,1]
info_status ∈ {explicit, inferred, ambiguous, missing}
evidence[] (quotes + spans)
```

## 2a. The scale is a ladder, not a feeling (shipped)

Ask a model for "0..1 satisfaction" and it will answer 0.72 for one candidate
and 0.68 for the next. Neither is wrong and the difference between them is
nothing — sampling noise wearing three digits. Rank ten candidates that way and
part of the order is arbitrary, which is precisely what this product claims not
to do.

So every semantic requirement is judged on **anchored rungs**. The default
ladder is quarters — *tam karşılıyor / büyük ölçüde / yarı yarıya / zayıf /
karşılamıyor* — and the compiler may emit a requirement-specific ladder whose
rungs carry their own definitions ("5+ yıl", "3–5 yıl", "3 yıldan az"). The
model is shown the ladder and asked to pick the rung whose definition matches
the evidence; the code then snaps whatever number came back onto that ladder
(`senthire/domain/anchors.py`), with ties rounding **down** — rounding people
up quietly is how a screening system starts flattering everybody.

Three consequences, all intended:

- two candidates on the same rung score identically on that requirement, so a
  difference in the final score always traces to a difference in a rung;
- re-running a job cannot move a score by a hair, because a hair is not a rung;
- "why 82 and not 79?" has an answer a person can read, and the UI shows the
  rung's own words next to the number.

Deterministic verdicts are **not** snapped: they are computed exactly, including
the partial credit a borderline tolerance grants, and rounding arithmetic onto
a judgment ladder would discard real information.

### Equivalent scores

The smallest difference a judgment can produce is one rung (0.25) in the
lightest category a spec usually carries (weight 0.15) — 3.75 points. Anything
under a point is renormalization and confidence damping: arithmetic residue,
not a finding. Candidates that close together are marked **equivalent** in the
ranking (a quiet ≈ beside the rank) instead of being presented as 4th and 5th,
because telling a recruiter that 80.5 beats 79.7 is telling them something
untrue. The ordering itself is unchanged — someone still has to be listed
first — the display simply stops claiming a difference it cannot support.

### Which criteria did any work

When a run completes, each semantic requirement is checked against the whole
cohort: how many distinct rungs did it produce? A criterion that lands every
candidate on the same rung is not screening anybody — either the pool is
genuinely uniform on it or, far more often, the criterion is too vague or too
easy — and it is carrying weight either way. The run page says so
("hiçbir adayı ayırt etmedi"), as it does for criteria where most CVs simply
had no information.

## 2. The scoring algorithm

```text
1. GATE — hard requirements & disqualifiers
   any hard requirement not_met (post-Stage-5)      → status = "fail" (ranked in rejected list, fully explained)
   any disqualifier triggered                        → status = "fail" + mandatory human-review flag
   hard requirement unknown & missing_policy=unknown → PASSES the gate, flagged "unverified: R…"
                                                       (missing ≠ failing)

2. REQUIREMENT SCORE (per scored requirement r)
   s_r = score_r × conf_adj_r
   conf_adj_r = 0.5 + 0.5 × confidence_r          # low confidence pulls toward neutral,
                                                  # never to zero — uncertainty ≠ failure
   unknown requirements: s_r = ∅ (excluded; weight redistributed within category)

3. CATEGORY SUBSCORE (per category c)
   S_c = Σ_r (w_r × s_r) / Σ_r w_r                # over requirements with s_r ≠ ∅
   if all requirements in c are unknown → S_c = ∅, category weight redistributed

4. BASE SCORE
   base = 100 × Σ_c (W_c × S_c) / Σ_c W_c         # W_c = spec.weights (HR-editable)

5. ADJUSTMENTS (transparent, itemized)
   final = clamp( base
                  − Σ penalties        (e.g. job-hopping −8; each shown with its rule)
                  + min(Σ bonuses, 10) (bonus cap prevents "nice-to-haves" outranking core fit)
                , 0, 100)

6. CONFIDENCE & BAND
   run_confidence = weighted mean of confidence over decisive requirements
   band: top / strong / possible / weak (quantile + threshold based)
   needs_review flag when: run_confidence < 0.6, or hard-gate passed only on
   inferred evidence, or Stage-4/5 disagreement was material
```

Default weights (the spec ships these; HR edits freely; changing them re-ranks in
milliseconds with zero LLM calls):

```text
Hard requirements        gate (pass/fail)
Relevant experience      25%      Education   10%
Skill match              20%      Language     5%
Industry experience      15%      Location     5%
Career stability         10%      Custom HR   10%
```

## 3. Worked example — Candidate #042 (the "82/100" question)

```text
GATE: R1 B2B≥3y met (explicit) ✓ · degree ✓ · English B2+ ✓ · sales exp ✓  → PASS

Category subscores (evidence-cited):
  relevant_experience 0.92  skills 0.68  industry 0.91
  career_stability    0.55  education 1.0  language 1.0 (C1)
  location            0.0  (lives in Istanbul; scored pref, not gate)
  custom              0.85

base  = 100 × (.25×.92 + .20×.68 + .15×.91 + .10×.55 + .10×1.0 + .05×1.0 + .05×0 + .10×.85)
      = 79.25
penalties: job_changes_last_5y = 3 → below threshold (>3) → −0
bonuses  : SaaS experience (Acme SaaS, 3y, explicit) → +5
final    = 84.25 → displayed 84, band "top", confidence 0.88 (high)
(this exact computation is pinned by tests/test_scoring.py::test_worked_example_matches_docs)
```

And the rendered explanation (every line traceable to stored data):

```text
Overall 84/100 · Rank 1 of 72 · Confidence: High

Strengths                                   Evidence
+ 5y B2B sales experience                   "Enterprise Sales Executive, Acme SaaS, 2019–2023…" (p.1)
+ 3y SaaS industry (+5 bonus)               employer: Acme SaaS (p.1)
+ Strong CRM usage (Salesforce, HubSpot)    skills section (p.2)
+ English C1                                "İngilizce – İleri seviye (C1)" (p.2)

Weaknesses
− Lives in Istanbul; Ankara was preferred (0/5 location — preference, not requirement)
− 3 job changes in last 4 years (no penalty applied: threshold is >3 — close to limit)

Hard requirements  ✓ 3+y B2B  ✓ Bachelor's  ✓ English  ✓ Sales experience
Missing information: notice period; relocation willingness (not stated in CV)
```

## 4. Explainability architecture

Explainability is a **data contract**, not a post-hoc text generation step. The
`evaluations.result` JSONB stores, per candidate:

```jsonc
{
  "gate": {"status": "pass", "requirements": [{"req_id": "...", "verdict": "met",
            "info_status": "explicit", "evidence": [...]}]},
  "categories": {"relevant_experience": {"score": 0.92, "requirements": [...]}, ...},
  "adjustments": [{"kind": "bonus", "req_id": "R4_saas_bonus", "points": 5, "evidence": [...]}],
  "strengths": [...], "weaknesses": [...],
  "missing_information": ["notice_period", "relocation"],
  "rejection_reasons": null,               // populated for gate-failed candidates
  "corrections": [{"req_id": "...", "from": "not_met", "to": "met",
                   "by": "deep_analysis", "note": "Stage 4 missed role #3"}],
  "provenance": {"profile_version": 2, "spec_version": 3, "pipeline_version": "p12",
                 "models": {"light": "claude-haiku-4-5", "deep": "claude-sonnet-5"},
                 "prompt_versions": {"light": "screen_v9", "deep": "verify_v4"}}
}
```

Principles enforced by pipeline + prompts + verification:

1. **Every decisive claim cites a quote.** Stage 5 string-verifies quotes against the
   stored raw text; a quote that doesn't appear ⇒ the verdict is voided, re-judged, and
   the event is logged as a hallucination incident (metric, [doc 11 §5](11-mvp-and-scaling.md)).
2. **Facts vs interpretations are visually distinct** via `info_status`: *explicit*
   (stated in CV) / *inferred* (strongly supported: "B2B" inferred from enterprise
   clients list) / *ambiguous* / *missing*. Inferred claims render with an "inferred"
   chip — HR is never misled about what the CV literally says.
3. **Rejection is always answerable.** Gate-failed candidates get
   `rejection_reasons: [{req_id, human_label, evidence_or_absence}]` — "Why was Ayşe
   rejected?" is a lookup, not an investigation.
4. **"Why 82 and not 90?"** is answerable arithmetically: the UI shows the weighted
   term-by-term sum — because the score *is* that sum, not a model's opinion.

## 5. Confidence: computed, calibrated, honest

- Per-requirement confidence comes from the judging model but is **adjusted by
  agreement signals**: deterministic/semantic agreement, Stage-4-vs-5 agreement (when
  both ran), extraction confidence of the underlying fields.
- Calibration loop: sampled human labels (HR overrides + periodic QA labeling) →
  reliability diagram per requirement category → temperature-style recalibration
  constants in the scorer (versioned like everything else).
- Low confidence **routes to humans, silently decides nothing**: `needs_review`
  candidates are visually queued for manual look, and are never auto-placed in the
  rejected bucket on low-confidence grounds alone.

## 6. What HR can change without new LLM spend

| Change | Recompute | Cost |
|---|---|---|
| Category weights, bonus cap, penalty points, band thresholds | Stage 6 only | $0, ms |
| Deterministic thresholds (min years, city list, degree) | Stage 3 + 6 | $0, ms |
| Missing-policy of a requirement | Stage 3/6 | $0 |
| Semantic rubric text, new requirement | Stage 4 for that req + selective 5 + 6 | cents |

This table is the contract behind the product promise: *iterate on criteria freely —
the expensive work is never repeated* ([doc 04 §6](04-requirement-engine.md),
[doc 08 §4](08-batch-processing-and-caching.md)).
