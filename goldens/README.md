# Golden set

Hand-labeled reference data that pins the screening pipeline's behavior. Every
code, spec-schema, or prompt change is measured against it instead of guessed
about:

- **Offline** (free, runs in CI): predicates, derived-field math, verdict
  merging, and the scorer run for real; hand-labeled semantic verdicts stand in
  for model output. The run must be 100% clean — any mismatch is a regression.
- **Live** (`--live`, needs `ANTHROPIC_API_KEY`): the real light-screening model
  is graded against the labels as an answer key; reports an exact-agreement
  rate, near-misses, and token usage. Use it before shipping prompt or model
  changes.

```bash
make evals          # offline — also runs inside `make test` via pytest
make evals-live     # grade the real model (spends a few cents of API budget)
python -m senthire.evals --case b2b-sales-ankara --json report.json
```

## Layout

```
goldens/cases/<case-name>/
  spec.json           the job's EvaluationSpec (confirmed form)
  expectations.json   as_of date, ranking + fairness expectations
  candidates/*.json   one labeled candidate per file
```

`expectations.as_of` pins "today" for all date math, so goldens never drift as
real time passes.

## Labeling a candidate

Labels have two roles — keep them straight:

- `labels.semantic` — the TRUE verdict a correct system should reach for every
  `semantic`/`hybrid` requirement (required for all of them). Offline these are
  injected as model output; live they are the answer key.
- Everything else is an **assertion**: `expected_deterministic` (Stage 3
  verdicts), `expected_merged` (post-merge, for hybrids), `gate`,
  `knockout_reqs`, `borderline`, `band`, `score_range`, `needs_review`.
  Numeric pins (`score_range`, `band`) are snapshots taken from a verified
  clean run — when a deliberate scorer change moves them, update the pins in
  the same commit and say why in the commit message.

## What the b2b-sales-ankara case covers

The docs/02 walkthrough scenario ("Ankara'da ikamet etmesi önemli. Çok sık iş
değiştirmiş olmasın. En az 3 yıl B2B satış deneyimi olsun. SaaS deneyimi varsa
avantaj."), exercising: a clean top match, a formatting-only counterfactual
twin (fairness: identical score required), a borderline hard fail (flagged, not
silently dropped), a scored-preference miss (İstanbul), missing location and
missing English (unknown → weight redistribution, never a penalty), a
job-hopping penalty, a deterministic knockout, and an unverifiable hard
requirement that must land in review instead of being rejected.

## Adding cases

Copy an existing case directory, keep candidate files small and readable, and
run `make evals` — the loader fails loudly on schema drift, unknown req_ids,
or missing semantic labels. Grow toward 100+ candidates per role family before
trusting live-mode agreement numbers as a quality signal.
