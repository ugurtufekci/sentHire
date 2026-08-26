"""Prompt text is bound to the version label stamped on its output.

A prompt edit moves verdicts exactly like an edited scoring rule, but without
this test it would ship invisibly: artifacts produced before and after the
edit would carry the same provenance label. The pins below tie each label to
a fingerprint of the actual text, so any wording change fails the build until
the label is bumped alongside it.

When this test fails because you changed a prompt ON PURPOSE:
  1. bump that component's label in Settings.prompt_versions
     (config.py) — e.g. "screen_v1" -> "screen_v2";
  2. add the new label with the new hash below (the failure message prints it).
Never rewrite the hash of an EXISTING label — that is precisely the
provenance lie this test exists to prevent. Labels are append-only.
"""

from senthire.config import Settings
from senthire.prompt_registry import COMPONENTS, component_hash

# Append-only: label -> fingerprint of the prompt surface that label names.
PINNED_HASHES = {
    "extract_v1": "50f3f3d8cbbaa78d",
    "compile_v1": "b186867a337cb43d",
    "screen_v1": "9a8c9bcbb4d71a03",
    "verify_v1": "947e2c69072f1e29",
    "oracle_v1": "7b4ab5a1e1d499dd",
}


def test_every_component_has_a_version_label():
    versions = Settings(_env_file=None).prompt_versions
    assert set(versions) == set(COMPONENTS), (
        "Settings.prompt_versions and prompt_registry.COMPONENTS must list the "
        "same components — a prompt without a label has untraceable output"
    )


def test_labels_are_unique_per_component():
    versions = Settings(_env_file=None).prompt_versions
    labels = list(versions.values())
    assert len(labels) == len(set(labels)), (
        "two components share a version label; bumping one would lie about the other"
    )


def test_prompt_text_matches_its_pinned_label():
    versions = Settings(_env_file=None).prompt_versions
    for component in sorted(COMPONENTS):
        label = versions[component]
        actual = component_hash(component)
        assert label in PINNED_HASHES, (
            f"prompt component '{component}' carries unpinned label '{label}'. "
            f"Add it to PINNED_HASHES with hash \"{actual}\"."
        )
        assert actual == PINNED_HASHES[label], (
            f"the '{component}' prompt text changed but still claims label "
            f"'{label}'. Bump Settings.prompt_versions['{component}'] in "
            f"config.py and append the new label here with hash \"{actual}\". "
            f"Do not rewrite the existing pin."
        )


def test_fingerprint_algorithm_is_stable():
    """If the hashing itself changes, every pin silently invalidates — catch
    that as its own failure instead of five confusing ones."""
    import senthire.prompt_registry as registry

    original = registry.COMPONENTS
    registry.COMPONENTS = {"sentinel": ("a", "b")}
    try:
        assert registry.component_hash("sentinel") == "d30a11ae896cfd98"
    finally:
        registry.COMPONENTS = original
