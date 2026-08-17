# 05 — Semantic Matching: Titles, Skills, Industries Across Languages

The question this layer answers: how does the system know that **"Account Executive"**
and **"Enterprise Sales Executive"** are related roles — or that *"Müşteri İlişkileri
Yönetimi"*, *"CRM"*, and *"Salesforce administration"* belong to the same skill family
— cheaply, consistently, and across Turkish/English?

## 1. The approaches, honestly compared

| Approach | Strength | Weakness | Role in sentHire |
|---|---|---|---|
| **Curated taxonomy / ontology** (ESCO-style occupations & skills; ESCO ships Turkish + English labels) | Deterministic, auditable, cross-lingual by construction, free at query time | Coverage gaps (startup titles, local variants: "Saha Satış Sorumlusu"), maintenance | The **backbone**: canonical IDs everything maps onto |
| **Embeddings** (multilingual: `bge-m3` self-hosted or Voyage hosted) | Handles novel phrasing, typos, cross-lingual out of the box; cheap (~fractions of a cent per CV) | Similarity ≠ equivalence ("Sales Engineer" is *near* "Sales Executive" but a different job); thresholds are fuzzy | **Candidate generation + soft feature**, never a verdict |
| **LLM classification** | Best judgment on genuinely ambiguous strings ("Growth Hacker"?) | Cost/latency if called per string per screening | **Cache-miss adjudicator only** |
| **Hybrid (chosen)** | Each layer covers the others' gaps | More moving parts | See §2 |

## 2. The hybrid design: normalize once, look up forever

Normalization happens at **extraction time** (Stage 1) and its results are cached
globally, so screening-time matching is mostly dictionary lookups:

```text
raw string ("Kıdemli Saha Satış Uzmanı")
   │ 1. exact/alias hit in normalization_cache?  ──── yes ──► canonical id (free)
   ▼ no
2. embed string → top-k nearest taxonomy entries (pgvector, ~ms, ~free)
   │  top-1 similarity ≥ 0.92 and margin ≥ 0.05?  ── yes ──► canonical id + cache it
   ▼ no
3. Haiku adjudication: "map this title to one of these 8 taxonomy candidates,
   or 'other'; consider seniority separately" ──► canonical id + seniority + cache it
```

- `normalization_cache(raw_norm → canonical_id, confidence, source)` is **global
  (cross-tenant)** because it contains no personal data — only strings like job titles.
  It converges fast: after a few thousand CVs, >95% of strings are cache hits and the
  marginal LLM cost of normalization approaches zero.
- The same machinery normalizes **titles**, **skills**, **industries**, **degrees**,
  **fields of study**, and **certifications** — one pipeline, six vocabularies.
- Seniority is modeled as a separate axis (`junior/mid/senior/lead/manager/director`)
  extracted from the raw title + tenure, so "Junior AE" ≠ "AE Director" even though
  both map to occupation `account_executive`.

## 3. Where each signal is used downstream

| Consumer | What it uses |
|---|---|
| Stage 3 deterministic filters | Canonical IDs only (`industry_canonical in [...]`, `tools_technologies contains 'salesforce'`) — deterministic because normalization already happened |
| Stage 4 light screening | (a) canonical matches as hard context; (b) **cosine similarity** between requirement embedding and each experience-chunk embedding, injected as a hint ("most relevant roles: #1, #3"), which focuses the small model and shortens prompts; (c) the rubric text itself |
| Stage 6 scoring | Similarity is a *feature inside* a requirement's score (e.g. title-relevance component), never a standalone pass/fail |
| UI | "Why matched": shows the mapping ("'Enterprise Sales Executive' → Sales/Account Executive family") so HR can see and correct it |

Corrections made by HR ("this title is not sales") write back to the cache as
org-scoped overrides that outrank the global entry — the system learns vocabulary from
its users without cross-tenant leakage.

## 4. Embedding infrastructure

- **Model**: `bge-m3` (1024-dim, multilingual, strong TR/EN) self-hosted on a small
  GPU/CPU node, or Voyage `voyage-3.5` if we prefer zero-ops (cost is negligible at CV
  scale: ~2k tokens per CV ⇒ 100 CVs ≈ 200k tokens ≈ **cents**).
- **What gets embedded**: each experience entry (title+company+summary), the whole
  profile summary, each semantic requirement, and every new raw vocabulary string.
- **Store**: `embeddings` table + HNSW index in pgvector (doc 03). At our scale
  (thousands of vectors per job, millions overall) Postgres is comfortably sufficient;
  a dedicated vector DB is a scale-stage option, not a need ([doc 11](11-mvp-and-scaling.md)).
- **Version pinning**: vectors are stamped with the embedding model id; changing models
  triggers lazy re-embedding (vectors of different models never get compared).

## 5. What semantic matching is *not* allowed to do

- Never knocks a candidate out. Low similarity routes a requirement to LLM judgment;
  it does not fail anyone.
- Never bypasses evidence. Even a 0.99 similarity still requires the Stage-4/5 verdict
  to cite the actual experience entry.
- Never crosses tenants with personal data. Only vocabulary strings are global;
  profile embeddings are org-scoped rows deleted with the candidate (GDPR/KVKK,
  [doc 09](09-fairness-and-compliance.md)).
