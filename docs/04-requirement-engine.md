# 04 — Requirement Engine: Natural Language → Structured Evaluation Spec

The requirement engine turns *"HR describes what they want"* into *"a machine can
screen against it"* — without losing the HR user's intent and without letting an LLM
freestyle the rules at screening time. It runs **once per spec version**, is reviewed
by the human who wrote the requirements, and everything downstream consumes only its
structured output.

```text
Job template  ──┐
HR edits      ──┼──► LLM compiler (Sonnet 5) ──► DRAFT EvaluationSpec + clarifying questions
NL free text  ──┘                                        │
                                                         ▼
                                    HR reviews/edits/answers in the UI (mandatory)
                                                         │
                                                         ▼
                                    CONFIRMED, immutable EvaluationSpec vN
```

## 1. Inputs

1. **Template** — a curated `spec_seed` per role (Sales Specialist ships with: sales
   experience, industry experience, communication skills, CRM usage, quota experience,
   negotiation, education, min-experience, English level). Templates are just
   pre-built requirement objects — same schema, editable like anything else.
2. **HR structured edits** — toggling template items, changing thresholds/weights.
3. **HR natural language** — Turkish or English free text, sentence-level.

## 2. The compiler LLM call

One Sonnet 5 call (structured outputs, low volume ⇒ quality over cost). The prompt
contains: the requirement taxonomy + JSON schema, the field registry of the
CandidateProfile (so it knows what is deterministically checkable), the template
requirements (to merge/dedupe against), compliance rules (doc 09), and few-shot
examples in both languages. Per input sentence it must decide:

| Decision | Options | Signal it uses |
|---|---|---|
| **Type** | `hard` / `scored` / `bonus` / `penalty` / `disqualifier` / `info` | Modality of the language: *"olmalı", "şart", "en az"* → hard; *"önemli", "tercihen"* → scored; *"avantaj", "artı"* → bonus; *"olmasın", "deprioritize"* → penalty/disqualifier |
| **Importance** | critical/high/medium/low | Emphasis, ordering, explicit weights |
| **Evaluator** | `deterministic` / `semantic` / `hybrid` | Is the criterion a predicate over profile fields (years, city, degree, CEFR, counts)? Then deterministic. Judgment-laden (relevance, quality, "strong communication")? Semantic. Both aspects? Hybrid. |
| **Category** | experience/skills/industry/… | For weight grouping |
| **Missing policy** | `unknown` / `fail` / `ignore` | Default `unknown`; `fail` only when HR demands presented evidence ("sertifikasını ibraz etmiş olmalı") |
| **Ambiguity** | emit `clarification` question + default | Vague quantifiers ("çok sık", "yeterli", "iyi derecede") |
| **Compliance** | pass / rewrite / block | Protected-characteristic criteria are refused with an explanation (doc 09) |

### Worked example (the exact input from the product brief)

Input: `"Ankara'da ikamet etmesi önemli. Çok sık iş değiştirmiş olmasın. En az 3 yıl
B2B satış deneyimi olsun. SaaS deneyimi varsa avantaj."`

Compiler output (abridged — full JSON in [doc 03 §4](03-data-model.md)):

| Sentence | req_id | Type | Evaluator | Notes |
|---|---|---|---|---|
| "Ankara'da ikamet etmesi **önemli**" | R2_location_ankara | `scored`, importance high | deterministic (`location.city_canonical == "Ankara"`) | "önemli" ≠ "şart" ⇒ preference, not gate. If HR meant a gate, they flip one toggle in review. |
| "**Çok sık** iş değiştirmiş **olmasın**" | R3_job_stability | `penalty`, medium | deterministic (`derived.job_changes_last_5y > 3` → −8 pts) | "çok sık" is vague ⇒ clarifying question with editable default (3 changes / 5 years) |
| "**En az 3 yıl** B2B satış deneyimi **olsun**" | R1_b2b_sales_3y | `hard`, critical | **hybrid**: deterministic gate on total months ≥ 36 + semantic rubric "count only B2B sales roles" | Years are checkable; *which* roles count as B2B sales is judgment |
| "SaaS deneyimi varsa **avantaj**" | R4_saas_bonus | `bonus`, medium | semantic ("employer builds/sells SaaS; name the employers") | +5 pts, capped with other bonuses |

The compiler also merges with the template (the template's generic "sales experience"
item is absorbed by the stricter R1) and emits the draft weights table.

## 3. Human confirmation is part of the architecture

The draft renders as editable requirement cards: type badge, importance, threshold,
weight, the *original sentence it came from*, and any clarifying question. HR must
confirm before the spec is runnable. This step is not UX sugar — it is the mechanism
that makes the system's interpretation of natural language *accountable to the human
who wrote it*, and it is where compliance rewrites are accepted or appealed. Confirmed
specs are immutable; edits create version N+1 (cheap re-runs via memoization,
[doc 08 §4](08-batch-processing-and-caching.md)).

## 4. The predicate DSL (deterministic requirements)

Deterministic checks are **data, not code**. The compiler emits JSON predicates
against a **whitelisted field registry**; the backend compiles them to SQL/JSONB (bulk
Stage 3) and to a Python evaluator (single-candidate re-checks). No generated code is
ever executed.

```jsonc
// Grammar
predicate := {"all": [predicate,…]} | {"any": [predicate,…]} | {"not": predicate}
           | {"field": <registry-path>, "op": <op>, "value": <literal>}
ops       := == | != | > | >= | < | <= | in | not_in | contains | exists | date_before | date_after
```

Registry excerpt (path → type → notes):

```text
derived.total_experience_months   int
derived.job_changes_last_5y       int
derived.employment_gaps[].months  int      (aggregations: max, count exposed as virtual fields)
location.city_canonical           enum(city gazetteer)
education.highest_degree_rank     int      (0 none … 5 doctorate)
languages[<code>].cefr_rank       int      (A1=1 … C2=6)
experience[].industry_canonical   enum(industry taxonomy)
tools_technologies                string[] (canonical ids)
certifications[].name_canonical   enum
```

Validation at compile time: unknown field or op ⇒ the requirement is downgraded to
`semantic` with a warning (never silently dropped). Values are type-checked and
enum-resolved through the same canonicalization used at extraction (doc 05), so
"Ankara" matches "Çankaya/Ankara".

`unknown` propagates three-valued-logic style: a predicate over a missing field
returns `unknown`, and `missing_policy` decides what that means (default: proceed +
surface the gap).

## 5. Rule engine vs LLM — the boundary (question D)

| Concern | Deterministic code / SQL | Embeddings / vector search | Small LLM (Haiku 4.5) | Large LLM (Sonnet 5 / Opus 5) |
|---|---|---|---|---|
| Date math, experience totals, tenure, gaps, counts | ✅ always | — | never | never |
| Threshold checks (years, degree rank, CEFR, city, cert present) | ✅ Stage 3 | — | — | — |
| Boolean spec logic (all/any/not), knockouts, borderline tolerance | ✅ | — | — | — |
| Scoring, weighting, ranking, penalties/bonuses | ✅ Stage 6 | — | never | never |
| Title/skill similarity ("Account Executive" ~ sales) | cache lookup | ✅ candidate generation | ✅ adjudicate cache misses | rare escalation |
| Per-requirement rubric judgment with evidence | — | assist (retrieval of relevant experience chunks) | ✅ Stage 4 workhorse | verification subset |
| Ambiguity/conflict resolution, career-narrative reading, anti-hallucination verification | — | — | — | ✅ Stage 5 |
| NL → structured spec compilation | validation only | — | — | ✅ once per version |
| Extraction (document → schema) | PDF text layer | — | ✅ Stage 1 workhorse | escalation on low confidence |

Litmus test used throughout: **if two runs on the same input must provably give the
same answer, it cannot be an LLM step.** Filters, math and ranking must be provable;
judgment must be evidenced.

## 6. Requirement lifecycle & re-screening semantics

- Spec vN+1 diff is computed structurally: added / removed / threshold-changed /
  weight-changed / rubric-changed per `req_id`.
- **Weight-only change** → Stage 6 re-runs. $0, milliseconds.
- **Deterministic threshold change** → Stage 3 + Stage 6 re-run. $0.
- **New/changed semantic requirement** → Stage 4 re-evaluates *only that requirement*
  (verdicts are stored per `req_id`), selective Stage 5, Stage 6. Cents.
- Removed requirement → verdicts retained (audit), excluded from scoring.

This is exactly the UX promise "adjust criteria and re-run without reprocessing CVs"
([doc 10 §7](10-product-ux.md)) implemented in data.

## 7. Compiler quality controls

- **Round-trip check**: a second cheap call renders the compiled spec back into plain
  Turkish/English; shown beside HR's original text in the review UI ("Anladığımız:
  …"). Misinterpretations get caught by the human at confirm time, not at run time.
- **Golden suite**: a versioned test set of NL instructions (both languages, messy
  phrasing) with expected spec outputs; every prompt/model change runs the suite
  (regression gate; see [doc 11 §4](11-mvp-and-scaling.md)).
- **Determinism aid**: compiler runs with structured outputs and low temperature-free
  settings; identical input text + template + prompt version reproduces the same spec
  (cached by input hash).
