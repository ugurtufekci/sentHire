"""The one ranking rule.

Screening ranks candidates at the end of a run; a human override re-ranks the
same run minutes later. If those two orderings came from two pieces of code,
they would eventually disagree, and the disagreement would look like the
product silently reshuffling people.
"""


def rank_key(score: float | None, confidence: float | None, application_id) -> tuple:
    """Best first: score, then confidence, then a stable id tie-break."""
    return (-(score or 0.0), -(confidence or 0.0), str(application_id))
