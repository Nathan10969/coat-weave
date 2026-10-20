"""Unit tests for ``pipeline.polarity.classify_polarity``."""

from __future__ import annotations

import pytest

from coating_kg.pipeline.polarity import classify_polarity


@pytest.mark.parametrize(
    "label,expected",
    [
        # English positive
        ("Example E4",       "positive"),
        ("example 7",        "positive"),
        ("Embodiment 3",     "positive"),
        # English negative
        ("Comp Example C1",  "negative"),
        ("Comparative Example 2", "negative"),
        ("Reference 1",      "negative"),
        # Chinese
        ("实施例 4",          "positive"),
        ("对比例 2",          "negative"),
        ("比較例 1",          "negative"),
        # German
        ("Beispiel 5",       "positive"),
        ("Vergleichsbeispiel 1", "negative"),
        # Unknown
        ("Sample X",         "unknown"),
        ("",                 "unknown"),
    ],
)
def test_label_only(label: str, expected: str) -> None:
    assert classify_polarity(label) == expected


def test_vlm_hint_overrides_label() -> None:
    # VLM is sure it's positive even though the label looks ambiguous.
    assert classify_polarity("Sample 99", vlm_hint="positive") == "positive"
    # VLM hint also overrides a "negative-looking" label.
    assert classify_polarity("Comparative Example 2", vlm_hint="positive") == "positive"


def test_vlm_hint_unknown_falls_through() -> None:
    # vlm_hint=None / "" / unrecognised should NOT override the keyword logic.
    assert classify_polarity("Comp Example C1", vlm_hint=None) == "negative"
    assert classify_polarity("Comp Example C1", vlm_hint="maybe") == "negative"


def test_substring_glued_label() -> None:
    # Some patents glue tokens: "CompExampleC1".
    assert classify_polarity("CompExampleC1") == "negative"
