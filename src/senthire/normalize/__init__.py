"""Turkish normalization: raw CV strings → the canonical vocabulary predicates use.

Deliberately dependency-free (no DB, no models, no network) so it can run
inside the parse worker, inside the corpus tooling, and inside domain code
without dragging anything along.
"""
