"""Unit tests for ``pipeline.section_split.split_examples_section``."""

from __future__ import annotations

from coating_kg.pipeline.section_split import (
    extract_examples_text,
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
