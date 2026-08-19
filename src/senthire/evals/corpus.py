"""The evaluation corpus: many real CVs, de-identified, stored as fixtures.

A *pool* is a directory of CV cases with no job attached — just profiles. Jobs
(specs) are layered on top, and labels are always relative to a (pool, job)
pair, because "does this candidate meet R2?" only means something against a
spec.

    corpus/<pool>/
        cases/<corpus_id>.json          CorpusCase — de-identified profile + text
        jobs/<job>/spec.json            the EvaluationSpec labels refer to
        jobs/<job>/labels.json          LabelSet — auto-labels + provenance
        jobs/<job>/adjudication.json    the small queue a human still decides

Nothing here talks to the production database, and no file written by this
module contains personal data ([deidentify](deidentify.py) runs at import).
"""

import hashlib
import json
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from senthire.domain.profile import ExtractedProfile
from senthire.domain.spec import EvaluationSpec
from senthire.evals.deidentify import deidentify_profile, scrub_text


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CorpusCase(_Strict):
    corpus_id: str
    # sha256 of the *pseudonymized* profile: stable, and not a handle on the
    # original document.
    fingerprint: str
    profile: dict
    text: str | None = None  # scrubbed CV text, for evidence verification
    identity_class: str = "unknown"
    imported_at: date
    source: dict = Field(default_factory=dict)  # extractor model/version/path
    tags: list[str] = []


class AutoLabel(_Strict):
    verdict: str  # met | partially_met | not_met | unknown
    score: float | None = None
    confidence: float = 1.0
    info_status: str = "explicit"
    # deterministic = computed by the rule engine (free, exact)
    # ensemble      = agreed by K independent oracle passes
    # human         = a person adjudicated a split
    source: str = "ensemble"
    agreement: float = 1.0
    votes: dict[str, int] = {}
    needs_adjudication: bool = False
    rationale: str | None = None


class LabelSet(_Strict):
    pool: str
    job: str
    spec_version: int
    labeled_at: date
    oracle: dict = Field(default_factory=dict)  # model, lenses, k
    # corpus_id -> req_id -> label
    cases: dict[str, dict[str, AutoLabel]] = {}

    def unresolved(self) -> list[tuple[str, str]]:
        return [
            (corpus_id, req_id)
            for corpus_id, reqs in sorted(self.cases.items())
            for req_id, label in sorted(reqs.items())
            if label.needs_adjudication
        ]


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fingerprint(profile: dict) -> str:
    return hashlib.sha256(
        json.dumps(profile, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def make_case(
    profile: dict,
    *,
    salt: str,
    seed: str,
    text: str | None = None,
    imported_at: date,
    source: dict | None = None,
    tags: list[str] | None = None,
) -> CorpusCase:
    """De-identify one extracted profile into a corpus case.

    `seed` is the original document's hash: it keeps the pseudonym stable for
    the same person without storing anything that points back at them.
    """
    ExtractedProfile.model_validate(profile)  # fail fast on schema drift
    clean = deidentify_profile(profile, salt=salt, seed=seed)
    scrubbed = scrub_text(text, clean.replacements) if text else None
    digest = fingerprint(clean.profile)
    return CorpusCase(
        corpus_id=digest[:12],
        fingerprint=digest,
        profile=clean.profile,
        text=scrubbed,
        identity_class=clean.identity_class,
        imported_at=imported_at,
        source=source or {},
        tags=tags or [],
    )


class Pool:
    """A corpus pool on disk."""

    def __init__(self, root: Path, name: str):
        self.root = root
        self.name = name
        self.dir = root / name

    @property
    def cases_dir(self) -> Path:
        return self.dir / "cases"

    def job_dir(self, job: str) -> Path:
        return self.dir / "jobs" / job

    def add(self, case: CorpusCase) -> bool:
        """Store a case. Returns False when an identical profile is already in
        the pool — duplicate CVs are common and must not be labeled twice."""
        for existing in self.cases():
            if existing.fingerprint == case.fingerprint:
                return False
        write_json(self.cases_dir / f"{case.corpus_id}.json", case.model_dump(mode="json"))
        return True

    def cases(self) -> list[CorpusCase]:
        if not self.cases_dir.is_dir():
            return []
        return [
            CorpusCase.model_validate(read_json(p))
            for p in sorted(self.cases_dir.glob("*.json"))
        ]

    def case(self, corpus_id: str) -> CorpusCase:
        return CorpusCase.model_validate(read_json(self.cases_dir / f"{corpus_id}.json"))

    def spec(self, job: str) -> EvaluationSpec:
        return EvaluationSpec.model_validate(read_json(self.job_dir(job) / "spec.json"))

    def save_spec(self, job: str, spec: EvaluationSpec) -> None:
        write_json(self.job_dir(job) / "spec.json", spec.model_dump(mode="json"))

    def labels(self, job: str) -> LabelSet | None:
        path = self.job_dir(job) / "labels.json"
        return LabelSet.model_validate(read_json(path)) if path.exists() else None

    def save_labels(self, labels: LabelSet) -> None:
        write_json(self.job_dir(labels.job) / "labels.json", labels.model_dump(mode="json"))

    def save_adjudication(self, job: str, queue: list[dict]) -> None:
        write_json(self.job_dir(job) / "adjudication.json", {"items": queue})

    def adjudication(self, job: str) -> list[dict]:
        path = self.job_dir(job) / "adjudication.json"
        return read_json(path)["items"] if path.exists() else []

    def jobs(self) -> list[str]:
        jobs_dir = self.dir / "jobs"
        return sorted(d.name for d in jobs_dir.iterdir() if d.is_dir()) if jobs_dir.is_dir() else []
