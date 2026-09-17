"""Unit tests for ``pipeline.section_split.split_examples_section``."""

from __future__ import annotations

from coating_kg.pipeline.section_split import (
    extract_examples_text,
    locate_examples_blocks,
    locate_examples_section,
    split_examples_section,
)


# ---------- 1. English ----------------------------------------------------
EN_DOC = """\
TECHNICAL FIELD
The invention relates to coatings.

SUMMARY OF THE INVENTION
A two-component clearcoat is provided.

EXAMPLES
Example 1
Mix 100 g of acrylic resin with 25 g of HDI trimer.

Example 2
Add 1.5 wt% Tinuvin 292.

CLAIMS
1. A coating composition comprising acrylic resin and HDI trimer.
"""


def test_english_examples_section() -> None:
    span = split_examples_section(EN_DOC)
    assert span is not None
    body = EN_DOC[span[0] : span[1]]
    assert "EXAMPLES" in body
    assert "Example 1" in body
    assert "Example 2" in body
    # CLAIMS heading must NOT be inside the slice
    assert "CLAIMS" not in body
    assert "1. A coating" not in body


# ---------- 2. Chinese ----------------------------------------------------
ZH_DOC = """\
技术领域
本发明涉及涂料。

发明内容
本发明提供了一种双组份清漆。

实施例
实施例 1
将 100 g 丙烯酸树脂与 25 g HDI 三聚体混合。

实施例 2
加入 1.5 wt% Tinuvin 292。

权利要求
1. 一种含有丙烯酸树脂的涂料组合物。
"""


def test_chinese_examples_section() -> None:
    span = split_examples_section(ZH_DOC)
    assert span is not None
    body = ZH_DOC[span[0] : span[1]]
    assert "实施例" in body
    assert "Tinuvin 292" in body
    assert "权利要求" not in body


# ---------- 3. German -----------------------------------------------------
DE_DOC = """\
ZUSAMMENFASSUNG
Die Erfindung betrifft Beschichtungen.

BEISPIELE
Beispiel 1: Acrylharz mit HDI-Trimer mischen.
Beispiel 2: 1,5% Tinuvin 292 zugeben.

PATENTANSPRÜCHE
1. Eine Beschichtungszusammensetzung.
"""


def test_german_examples_section() -> None:
    span = split_examples_section(DE_DOC)
    assert span is not None
    body = DE_DOC[span[0] : span[1]]
    assert "BEISPIELE" in body
    assert "Beispiel 1" in body
    assert "PATENTANSPRÜCHE" not in body


# ---------- 4. No Examples section ----------------------------------------
NO_EXAMPLES = """\
TECHNICAL FIELD
This is just background.

CLAIMS
1. Something.
"""


def test_no_examples_returns_none() -> None:
    assert split_examples_section(NO_EXAMPLES) is None
    assert extract_examples_text(NO_EXAMPLES) is None


# ---------- 5. EMBODIMENTS variant + end-of-doc ---------------------------
EMBODIMENTS_DOC = """\
SUMMARY
Some background.

EMBODIMENTS
Embodiment 1: a clearcoat using fluorocarbon FEVE resin.
Embodiment 2: a primer with epoxysilane KH-560.
"""


def test_embodiments_runs_to_eof() -> None:
    span = split_examples_section(EMBODIMENTS_DOC)
    assert span is not None
    assert span[1] == len(EMBODIMENTS_DOC)
    body = EMBODIMENTS_DOC[span[0] : span[1]]
    assert "Embodiment 1" in body
    assert "Embodiment 2" in body


# ---------- 6. No explicit EXAMPLES heading -------------------------------
FOLLOWING_EXAMPLES_DOC = """\
DETAILED DESCRIPTION
The composition may contain fillers and additives.

The invention will be illustrated by the following non-limiting examples.

Preparation of components
Example 1
Mix the polyaspartic ester with polyisocyanate.

Table 2 shows the coating properties.

CLAIMS
1. A coating composition.
"""


def test_following_examples_sentence_starts_section() -> None:
    span = split_examples_section(FOLLOWING_EXAMPLES_DOC)
    assert span is not None
    body = FOLLOWING_EXAMPLES_DOC[span[0] : span[1]]
    assert "non-limiting examples" in body
    assert "Example 1" in body
    assert "CLAIMS" not in body


DIRECT_EXAMPLE_DOC = """\
DESCRIPTION
The invention relates to coatings.

Example 1
A coating was prepared.

Comparative Example 2
A control coating was prepared.

CLAIMS
1. A coating.
"""


def test_direct_example_heading_starts_section() -> None:
    span = split_examples_section(DIRECT_EXAMPLE_DOC)
    assert span is not None
    body = DIRECT_EXAMPLE_DOC[span[0] : span[1]]
    assert "Example 1" in body
    assert "Comparative Example 2" in body
    assert "CLAIMS" not in body


BARE_EXAMPLE_DOC = """\
DESCRIPTION
The invention relates to coatings.

Example
The coating composition was prepared by mixing resin and crosslinker.
Table 1 shows adhesion ratings.
Example 2
A second coating was prepared.

CLAIMS
1. A coating.
"""


def test_bare_example_heading_requires_nearby_experimental_structure() -> None:
    span = locate_examples_section(BARE_EXAMPLE_DOC)
    assert span is not None
    assert span.mode == "bare_example_heading"
    body = BARE_EXAMPLE_DOC[span.start : span.end]
    assert body.startswith("Example")
    assert "Table 1" in body
    assert "CLAIMS" not in body


INLINE_FOR_EXAMPLE_DOC = """\
BACKGROUND
Coatings may include additives, for example leveling agents and UV absorbers.

CLAIMS
1. A coating.
"""


def test_inline_for_example_does_not_start_examples_section() -> None:
    assert split_examples_section(INLINE_FOR_EXAMPLE_DOC) is None


CLAIMS_EXAMPLE_DOC = """\
DESCRIPTION
No experimental section is provided.

CLAIMS
1. A composition.
Example 1 is outside the description and should not be treated as an examples section.
"""


def test_claims_example_anchor_is_rejected() -> None:
    assert split_examples_section(CLAIMS_EXAMPLE_DOC) is None


BACKGROUND_OPENER_DOC = """\
BACKGROUND
The prior invention will be illustrated by the following non-limiting examples.
Example 1
A known non-coating reference example is described.

CLAIMS
1. A composition.
"""


def test_background_opening_sentence_is_rejected() -> None:
    assert split_examples_section(BACKGROUND_OPENER_DOC) is None


def test_block_level_span_includes_non_text_units_between_start_and_claims() -> None:
    blocks = [
        {"type": "text", "text": "DESCRIPTION", "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "Example", "text_level": 1, "page_idx": 1},
        {
            "type": "text",
            "text": "The coating was prepared and tested.",
            "page_idx": 1,
        },
        {"type": "table", "table_body": "<table><tr><td>5B</td></tr></table>", "page_idx": 1},
        {"type": "text", "text": "Example 2", "page_idx": 2},
        {"type": "image", "img_path": "scheme.png", "page_idx": 2},
        {"type": "text", "text": "CLAIMS", "text_level": 1, "page_idx": 3},
        {"type": "table", "table_body": "<table><tr><td>claim</td></tr></table>", "page_idx": 3},
    ]
    span = locate_examples_blocks(blocks)
    assert span is not None
    assert span.start_block == 1
    assert span.end_block == 6
    assert span.start_page == 2
    assert span.end_page == 3
