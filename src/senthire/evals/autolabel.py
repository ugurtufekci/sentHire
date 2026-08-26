"""Labeling CVs without reading them: deterministic truth + an oracle ensemble.

Three sources of label, in descending order of trust:

1. **Deterministic** — the rule engine already answers "≥3 years?" exactly.
   Free, exact, no model involved. Most hard requirements land here.
2. **Ensemble** — for semantic requirements, K independent oracle passes with
   deliberately different framings (neutral / advocate / skeptic) and verified
   evidence. Unanimous verdicts are accepted as labels; the confidence we store
   *is* the agreement rate, so downstream gates can be strict about it.
3. **Human** — only the disagreements. A split between "met" and "not_met" is
   exactly the case where a person adds information, and there are few of them.

The oracle is not the product's screening model: it is offline, has no latency
budget, sees the full text, and votes with itself. Grading the cheap production
funnel against it is the whole point — a teacher the student never sees.
"""

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from senthire.config import get_settings
from senthire.domain.spec import EvaluationSpec
from senthire.evals.corpus import AutoLabel, CorpusCase
from senthire.evals.document import profile_document
from senthire.screening import llm, prompts
from senthire.screening.deterministic import run_deterministic_stage
from senthire.screening.evidence import quotes_supported
from senthire.screening.schemas import LightScreenOutput, ReqJudgment

# Framings chosen to disagree when a case is genuinely borderline, and to agree
# when it is not. Identical prompts run K times mostly reproduce the same bias.
LENSES = ("neutral", "advocate", "skeptic")

LENS_INSTRUCTION = {
    "neutral": "",
    "advocate": (
        "\n\nADDITIONAL FRAMING: read the profile in the most favorable light the "
        "evidence honestly allows. You may not invent or stretch evidence — if the "
        "favorable reading is not supported by a quote, the verdict stands as unknown."
    ),
    "skeptic": (
        "\n\nADDITIONAL FRAMING: read the profile strictly. Assume nothing that is not "
        "written. Vague claims without concrete evidence are partially_met at best. "
        "Absence of information is still 'unknown', never 'not_met'."
    ),
}

# Ordered for severity: a met-vs-not_met split is a different animal from a
# met-vs-partially_met one, however the arithmetic of agreement comes out.
VERDICT_RANK = {"not_met": 0, "partially_met": 1, "met": 2}

OracleFn = Callable[[EvaluationSpec, dict, str], LightScreenOutput]


@dataclass
class LabelingReport:
    labeled: int = 0
    deterministic: int = 0
    unanimous: int = 0
    adjudicate: int = 0
    dropped_evidence: int = 0


def aggregate(votes: list[ReqJudgment], *, min_agreement: float = 1.0) -> AutoLabel:
    """Fold K independent judgments into one label plus an honest confidence."""
    if not votes:
        return AutoLabel(
            verdict="unknown", confidence=0.0, info_status="missing",
            source="ensemble", agreement=0.0, needs_adjudication=True,
            rationale="no usable votes (all evidence unverifiable)",
        )
    counts = Counter(v.verdict for v in votes)
    verdict, top = counts.most_common(1)[0]
    agreement = top / len(votes)
    ranks = [VERDICT_RANK[v] for v in counts if v in VERDICT_RANK]
    severe_split = bool(ranks) and (max(ranks) - min(ranks)) >= 2
    winners = [v for v in votes if v.verdict == verdict]
    scores = [v.score for v in winners if v.score is not None]
    self_reported = sum(v.confidence for v in winners) / len(winners)
    # A unanimous "unknown" is a statement about the document ("it isn't in
    # there"), which three lenses — one of them instructed to read favorably —
    # just failed to contradict. Tempering it by the models' own hedging would
    # throw away the most reliable label the ensemble produces.
    tempered = 1.0 if (verdict == "unknown" and agreement == 1.0) else self_reported
    return AutoLabel(
        verdict=verdict,
        score=round(sum(scores) / len(scores), 3) if scores else None,
        # Confidence is the agreement rate tempered by what the models
        # themselves claimed — bravado alone should not buy a strict label.
        confidence=round(agreement * tempered, 3),
        info_status=Counter(v.info_status for v in winners).most_common(1)[0][0],
        source="ensemble",
        agreement=round(agreement, 3),
        votes=dict(counts),
        needs_adjudication=severe_split or agreement < min_agreement,
        rationale=winners[0].reasoning if winners else None,
    )


def deterministic_labels(spec: EvaluationSpec, case: CorpusCase, *, as_of) -> dict[str, AutoLabel]:
    """Labels the rule engine can produce on its own — exact and free."""
    stage = run_deterministic_stage(spec, profile_document(case.profile, as_of, tag="corpus"))
    return {
        req_id: AutoLabel(
            verdict=verdict.verdict,
            score=verdict.score,
            confidence=1.0,
            info_status=verdict.info_status,
            source="deterministic",
            agreement=1.0,
            rationale="computed by the predicate engine",
        )
        for req_id, verdict in stage.verdicts.items()
    }


def label_case(
    spec: EvaluationSpec,
    case: CorpusCase,
    *,
    oracle: OracleFn,
    as_of,
    lenses: tuple[str, ...] = LENSES,
    min_agreement: float = 1.0,
    report: LabelingReport | None = None,
) -> dict[str, AutoLabel]:
    """Every requirement labeled: deterministic where possible, ensemble where not."""
    report = report or LabelingReport()
    labels = deterministic_labels(spec, case, as_of=as_of)
    report.deterministic += len(labels)

    semantic_ids = {
        r.req_id for r in spec.requirements if r.evaluator in {"semantic", "hybrid"}
    }
    if not semantic_ids:
        return labels

    per_req: dict[str, list[ReqJudgment]] = {req_id: [] for req_id in semantic_ids}
    for lens in lenses:
        output = oracle(spec, case.profile, lens)
        for judgment in output.judgments:
            if judgment.req_id not in per_req:
                continue
            if not quotes_supported(judgment, case.text):
                report.dropped_evidence += 1
                continue
            per_req[judgment.req_id].append(judgment)

    for req_id, votes in per_req.items():
        label = aggregate(votes, min_agreement=min_agreement)
        # A hybrid requirement keeps both readings: the deterministic label is
        # the floor, the ensemble supplies the judgement the rules can't make.
        labels[req_id] = label
        report.labeled += 1
        if label.needs_adjudication:
            report.adjudicate += 1
        else:
            report.unanimous += 1
    return labels


def adjudication_items(
    spec: EvaluationSpec, case: CorpusCase, labels: dict[str, AutoLabel]
) -> list[dict]:
    """The queue a human actually sees: one line per genuine disagreement.

    Deliberately carries the competing verdicts and the winning rationale, not
    the CV — the reviewer is answering "which reading is right?", not reading a
    résumé.
    """
    items = []
    for req_id, label in sorted(labels.items()):
        if not label.needs_adjudication:
            continue
        requirement = spec.by_id(req_id)
        items.append(
            {
                "corpus_id": case.corpus_id,
                "req_id": req_id,
                "requirement": requirement.display_label("tr") if requirement else req_id,
                "rubric": requirement.semantic.rubric if requirement and requirement.semantic else None,
                "votes": label.votes,
                "leading_verdict": label.verdict,
                "rationale": label.rationale,
            }
        )
    return items


def apply_adjudications(labels: dict[str, dict[str, AutoLabel]], decisions: list[dict]) -> int:
    """Fold human decisions ({corpus_id, req_id, verdict}) back into the labels."""
    applied = 0
    for decision in decisions:
        verdict = decision.get("verdict")
        case_labels = labels.get(decision.get("corpus_id", ""))
        if not verdict or case_labels is None or decision.get("req_id") not in case_labels:
            continue
        label = case_labels[decision["req_id"]]
        case_labels[decision["req_id"]] = label.model_copy(
            update={
                "verdict": verdict,
                "source": "human",
                "confidence": 1.0,
                "needs_adjudication": False,
                "rationale": decision.get("note") or "adjudicated by a reviewer",
            }
        )
        applied += 1
    return applied


# --------------------------------------------------------------------------- #
# The real oracle (offline; costs are irrelevant here, correctness is not)
# --------------------------------------------------------------------------- #


def model_oracle(spec: EvaluationSpec, profile: dict, lens: str) -> LightScreenOutput:
    settings = get_settings()
    model = getattr(settings, "label_oracle_model", None) or settings.deep_analysis_model
    output, _usage = llm.call_model(
        model,
        prompts.LIGHT_SYSTEM + LENS_INSTRUCTION.get(lens, ""),
        llm.light_content(spec, profile),
        LightScreenOutput,
        llm.LIGHT_MAX_TOKENS,
    )
    return output


# --------------------------------------------------------------------------- #
# Pairwise preference — the label that is not circular
# --------------------------------------------------------------------------- #

PAIR_SYSTEM = """\
You compare two anonymized candidate profiles against one job's requirements and say
which is the better hire, or that they are equivalent.

Rules:
1. Judge ONLY against the listed requirements and their importance.
2. NEVER use or infer protected characteristics (age, gender, ethnicity, nationality,
   religion, marital status, health, appearance). Names carry no information.
3. Missing information is missing — not a weakness. A profile that simply doesn't
   mention something is not thereby worse than one that does.
4. Answer "tie" whenever the difference is not clear. Ties are useful, not a cop-out.
5. Give one or two sentences of reasoning citing the decisive requirements.
"""


class PairVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    winner: str  # "a" | "b" | "tie"
    decisive_req_ids: list[str] = []
    reasoning: str


PairOracleFn = Callable[[EvaluationSpec, dict, dict, str], PairVerdict]


def pair_oracle(spec: EvaluationSpec, profile_a: dict, profile_b: dict, lens: str) -> PairVerdict:
    settings = get_settings()
    model = getattr(settings, "label_oracle_model", None) or settings.deep_analysis_model
    content = [
        {
            "type": "text",
            "text": "REQUIREMENTS:\n" + json.dumps(
                [r.model_dump(mode="json") for r in spec.requirements],
                ensure_ascii=False, sort_keys=True,
            ),
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": "CANDIDATE A:\n" + json.dumps(profile_a, ensure_ascii=False, sort_keys=True)
            + "\n\nCANDIDATE B:\n" + json.dumps(profile_b, ensure_ascii=False, sort_keys=True),
        },
    ]
    verdict, _usage = llm.call_model(
        model, PAIR_SYSTEM + LENS_INSTRUCTION.get(lens, ""), content, PairVerdict, 1024
    )
    return verdict


def rank_pair(
    spec: EvaluationSpec,
    case_a: CorpusCase,
    case_b: CorpusCase,
    *,
    oracle: PairOracleFn,
    lenses: tuple[str, ...] = LENSES,
) -> dict | None:
    """Which candidate is better, decided by an ensemble — and by A/B order swap.

    Position bias is real, so half the passes see the pair reversed. Only an
    unanimous, order-independent preference becomes an ordering label.
    """
    votes: list[str] = []
    for index, lens in enumerate(lenses):
        swapped = index % 2 == 1
        first, second = (case_b, case_a) if swapped else (case_a, case_b)
        verdict = oracle(spec, first.profile, second.profile, lens)
        winner = verdict.winner
        if swapped and winner in {"a", "b"}:
            winner = "b" if winner == "a" else "a"
        votes.append(winner)

    counts = Counter(votes)
    winner, top = counts.most_common(1)[0]
    if winner == "tie" or top < len(votes):
        return None  # only unanimous, order-independent preferences are labels
    higher = case_a.corpus_id if winner == "a" else case_b.corpus_id
    lower = case_b.corpus_id if winner == "a" else case_a.corpus_id
    return {"higher": higher, "lower": lower, "votes": dict(counts)}
