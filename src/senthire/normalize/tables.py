"""Load the normalization vocabularies.

They are data files, not code, on purpose: growing them is the point of this
layer, and a non-programmer should be able to add "Saha Satış Sorumlusu" to a
family without touching Python or waiting for a release.
"""

import json
from functools import cache
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


@cache
def table(name: str) -> dict:
    return json.loads((DATA_DIR / f"{name}.json").read_text(encoding="utf-8"))


def version_signature() -> str:
    """Bump-able identity of the whole vocabulary set.

    Stamped onto every normalized profile so a profile can be re-normalized
    (cheap: no re-parsing, no model call) and evaluations that depended on the
    old vocabulary can be invalidated deliberately rather than silently.
    """
    names = sorted(p.stem for p in DATA_DIR.glob("*.json"))
    return ".".join(f"{name}{table(name)['version']}" for name in names)
