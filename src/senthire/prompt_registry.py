"""Every model-facing prompt, bound to the version label its output carries.

Prompt text is code: a wording change moves verdicts exactly like an edited
scoring rule would, so it must be as visible as one. The rule this module
enforces (via tests/test_prompt_versions.py) is that changing any template
below requires bumping the matching label in `Settings.prompt_versions` — the
label that gets stamped onto profiles, specs, run funnels, and corpus labels.
Without the bump, two artifacts produced by different prompts would claim the
same provenance and drift would be untraceable.
"""

from hashlib import sha256

from senthire.compiler import prompts as compiler_prompts
from senthire.evals import autolabel
from senthire.extraction import prompts as extraction_prompts
from senthire.screening import prompts as screening_prompts

# Component key (as in Settings.prompt_versions) -> the full prompt surface of
# that component, in a fixed order. Anything a model reads belongs here.
COMPONENTS: dict[str, tuple[str, ...]] = {
    "extract": (
        extraction_prompts.EXTRACTION_SYSTEM,
        extraction_prompts.TEXT_PATH_USER,
        extraction_prompts.VISION_PATH_USER,
    ),
    "compile": (
        compiler_prompts.COMPILER_SYSTEM,
        compiler_prompts.COMPILER_USER,
        compiler_prompts.REGISTRY_DOC,
    ),
    "light": (
        screening_prompts.LIGHT_SYSTEM,
        screening_prompts.LIGHT_USER_SPEC,
        screening_prompts.LIGHT_USER_PROFILE,
    ),
    "deep": (
        screening_prompts.DEEP_SYSTEM,
        screening_prompts.DEEP_USER_CONTEXT,
        screening_prompts.DEEP_USER_CANDIDATE,
    ),
    # The labeling oracle reuses LIGHT_SYSTEM (covered above); its own surface
    # is the lens framings and the pairwise prompt.
    "oracle": (
        autolabel.PAIR_SYSTEM,
        *(autolabel.LENS_INSTRUCTION[lens] for lens in autolabel.LENSES),
    ),
}


def component_hash(component: str) -> str:
    """Stable fingerprint of one component's entire prompt surface."""
    joined = "\x1e".join(COMPONENTS[component])
    return sha256(joined.encode("utf-8")).hexdigest()[:16]
