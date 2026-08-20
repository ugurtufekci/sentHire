"""The one ranking rule.

Screening ranks candidates at the end of a run; a human override re-ranks the
same run minutes later. If those two orderings came from two pieces of code,
they would eventually disagree, and the disagreement would look like the
product silently reshuffling people.
"""


def rank_key(score: float | None, confidence: float | None, application_id) -> tuple:
    """Best first: score, then confidence, then a stable id tie-break."""
    return (-(score or 0.0), -(confidence or 0.0), str(application_id))


# The smallest difference that can come from an actual judgment: one rung of a
# requirement's ladder (0.25) in the lightest category a spec usually carries
# (weight 0.15) is 3.75 points. Anything under a point is renormalization and
# confidence damping — arithmetic residue, not a finding. Candidates that close
# together are presented as equivalent instead of as 4th and 5th, because
# telling a recruiter that 80.5 beats 79.7 is telling them something untrue.
EQUIVALENCE_EPSILON = 1.0


def equivalence_groups(scores: list[float | None]) -> list[int]:
    """Group id per candidate, given scores already sorted best-first.

    Consecutive candidates within EQUIVALENCE_EPSILON share a group. Grouping
    is deliberately by neighbour rather than by cluster width: it is local,
    stable, and never reorders anyone — the ranking still has one order, the
    display just stops claiming a difference it cannot support.
    """
    groups: list[int] = []
    group = 0
    previous: float | None = None
    for score in scores:
        value = score or 0.0
        if previous is not None and abs(previous - value) >= EQUIVALENCE_EPSILON:
            group += 1
        groups.append(group)
        previous = value
    return groups
