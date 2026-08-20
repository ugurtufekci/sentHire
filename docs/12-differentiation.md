# 12 — Differentiation: Why Not Just Paste CVs into a Chatbot?

Every buyer will ask it, so we answer it head-on: *"Claude and ChatGPT read CVs
fine — why pay for this?"* We use the same frontier models. The product is the
**system built around them**, and that system is where the moat is. This doc
lists what already separates a sentHire run from a chat session, and the
roadmap ordered by how much each item widens the gap.

## 1. What actually happens when you paste CVs into a chatbot

Not a strawman — an honest account, because our users have tried it:

| Chat session | Consequence |
|---|---|
| The model free-forms a score | Ask twice, get 78 then 84. No two runs comparable, no defensible ranking |
| Criteria live in the prompt, implicitly | Nobody confirmed what "senior" or "B2B deneyimi" was taken to mean; every recruiter prompts differently |
| Long context, many CVs | Quality decays down the stack; candidate #40 is skimmed, and there is no evidence which ones were |
| Missing info reads as negative | The concise CV loses to the padded one — the exact bias good screening must avoid |
| Claims have no provenance | "Has quota experience" may be paraphrase, inference, or hallucination; nobody checks the quote |
| Nothing persists | No audit trail, no versioning, no re-run without re-paying, no teammates, no KVKK story for candidate data in a consumer chat tool |

None of these are model defects. They are what happens when a probabilistic
judge is also asked to be the bookkeeper. Our architecture splits those roles.

## 2. The structural answer (shipped today)

**LLMs judge; code decides.** Models emit per-requirement verdicts with quoted
evidence; a deterministic, versioned scorer turns verdicts into scores and
ranks (doc 06). Same inputs → same ranking, byte for byte. A chatbot cannot
promise that, at any price.

**Every claim has provenance.** Evidence quotes are verified against the stored
document text; a quote that does not exist voids the verdict and raises an
incident metric. `info_status` distinguishes *stated* from *inferred* from
*missing*, and **missing ≠ failing** is enforced by the scorer, not by hoping
the prompt is obeyed.

**Criteria are compiled, confirmed, versioned.** Natural Turkish goes through
the compiler into a typed spec the HR user confirms via back-translation
(doc 04). The spec — not a lost prompt — is what every candidate is measured
against, identically, whether there are 5 CVs or 5,000. Editing criteria makes
version N+1; memoization re-scores only what changed, for free.

**Unit economics a chat can't reach.** Staged funnel (cheap models eliminate
early, expensive models only see the shortlist), byte-identical prompt
prefixes for cache hits, Message Batches at half price, parse-once per
document hash, evaluation memoization. Doc 07 does the math; the punchline is
cents per candidate, with re-runs approaching zero.

**A compliance posture.** Multi-tenant isolation, append-only audit log of
every model call and human decision, schema-as-allowlist so forbidden
attributes cannot enter scoring, erasure endpoints, counterfactual fairness
tests in CI (doc 09). "We screen with AI" is a liability sentence in a KVKK
audit; "we can show you every quote and every version" is an asset.

**A QA harness the buyer can inspect.** The golden set (labeled CVs with
pinned expected bands, ranking pairs, and fairness twins) gates every prompt,
model, or pipeline change in CI. A chat has vibes; we have regression tests
for judgment.

**The work product lives somewhere.** Rankings flow into a shared hiring
pipeline — shortlist, contacts, meetings, offers, an agenda of what is owed to
whom (doc 10 §9). A chat transcript is a dead end; a workspace compounds.

## 3. Widening the gap (ordered by moat value)

The model layer is rented and levels up for everyone; nothing below depends on
model exclusivity. The durable assets are **data, calibration, and workflow** —
in that order.

1. **Turkish HR ground truth at scale.** Grow the golden set per vertical
   (sales, logistics, software, finance…): labeled CVs, expected decisions,
   fairness twins. Every labeled case is a permanent quality ratchet a generic
   tool cannot copy. Target: hundreds of cases per template we ship. The
   labeling is mostly automatic — invariant twins and deterministic rules
   carry it, an oracle ensemble judges the rest, and only genuine
   disagreements reach a person ([doc 13](13-corpus-and-labeling.md)).
2. **Turkish normalization layer.** *(first version shipped —
   [doc 05 §2a](05-semantic-matching.md))* Canonical titles and seniority, 81
   provinces and their districts, university aliases, degree phrases, sector
   keywords, CEFR from prose and exam scores — maintained as data with a test
   per alias, feeding both predicates and prompts, and improvable without
   re-parsing a single CV. This is unglamorous accumulation, which is exactly
   why it defends: a chatbot re-derives it, badly, on every conversation.
3. **Outcome feedback loop.** *(collection shipped; calibration in progress)*
   The hiring pipeline is not just UX — it captures ground truth: which 80+
   scores actually got interviews, offers, hires. Verdict corrections are now
   recorded with both verdicts and a reason, and the per-job Öğrenilenler panel
   already reports the workspace's *working* threshold and flags criteria that
   keep being corrected. Per-vertical calibration on top of that data turns
   usage into accuracy nobody can bootstrap without the installed base — and
   every correction is a labeled example the corpus can harvest
   ([doc 13 §8](13-corpus-and-labeling.md)).
4. **Comparability guarantees.** *(shipped — [doc 06 §2a](06-scoring-and-explainability.md))*
   Judged scores land on anchored rungs rather than freehand decimals, scores
   within a point of each other are shown as equivalent instead of ranked, and
   every run reports which criteria actually separated candidates. "82 vs 79"
   now traces to a rung a person can read — the thing hardest to fake with
   ad-hoc prompting, because it requires the scoring to be code rather than
   vibes.
5. **Workflow depth.** Automatic interview e-mails to positive candidates
   (mail infra is live), scheduling hooks, exports that HR actually files.
   Each step moves us from "tool you try" to "system your hiring runs on".
6. **ATS integrations.** Last deliberately: valuable for distribution, but a
   commodity surface — the four items above are why customers stay.

## 4. What we do not claim

We do not claim better base models than anyone else, and we never will. We
claim that screening is a **system problem** — determinism, evidence,
economics, compliance, memory, and workflow around the model — and that every
month of labeled Turkish hiring data and calibrated outcomes makes that system
harder to replicate by prompting a chatbot, including for us-without-our-data.
