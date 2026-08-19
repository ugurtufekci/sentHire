"""One way to turn a stored profile into the document the stages read.

The golden runner, the labeling oracle and the promoter all need the same
thing: an ExtractedProfile plus its derived fields, composed exactly as the
production parse worker composes it. Sharing this keeps a label, an assertion
and a production evaluation talking about the identical document.
"""

from datetime import date

from senthire.domain.derived import compute_derived
from senthire.domain.profile import ExtractedProfile, compose_profile_document


def profile_document(profile: dict, as_of: date, *, tag: str = "golden") -> dict:
    extracted = ExtractedProfile.model_validate(profile)
    derived = compute_derived(extracted, today=as_of)
    return compose_profile_document(
        extracted,
        derived,
        model=tag,
        prompt_version=tag,
        path=tag,
        confidence=profile.get("confidence") or 1.0,
    )
