from __future__ import annotations

import json

import pytest

from coating_kg.pipeline.oxr_adapter import convert_pages, normalize_bbox, write_mineru_layout
from coating_kg.pipeline.pdf_layout import _load_layout_json
from coating_kg.pipeline.unit_extractor import iter_figure_table_units

SIZE = {"w": 1000, "h": 2000}


def _block(idx: int, kind: str, content: str = "", bbox=(100, 200, 900, 400), fmt: str = "text") -> dict:
    return {"idx": idx, "type": kind, "bbox": list(bbox), "content": content, "format": fmt}


def _page(page_num: int, *blocks: dict) -> dict:
    return {"page_num": page_num, "page_size": SIZE, "blocks": list(blocks)}


def test_normalize_bbox_matches_mineru_scale() -> None:
    assert normalize_bbox([100.4, 200.0, 900.0, 2100.0], SIZE) == [100, 100, 900, 1000]


def test_types_map_to_mineru_and_text_is_kept() -> None:
    blocks = convert_pages([_page(
        3,
        _block(0, "Title", "EXAMPLES"),
        _block(1, "Text", "Example 1 was prepared."),
        _block(2, "Formula", r"E = mc^2", fmt="latex"),
        _block(3, "Chemical Structure", "CCO"),
        _block(4, "Page-footer", "12"),
    )])
    assert [b["type"] for b in blocks] == ["text", "text", "equation", "equation", "footer"]
    assert blocks[0]["text_level"] == 1
    assert blocks[1] == {"type": "text", "text": "Example 1 was prepared.", "bbox": [100, 100, 900, 200], "page_idx": 3}
    assert blocks[2]["text_format"] == "latex"
    assert blocks[3]["text_format"] == "smiles"


def test_captions_and_footnotes_attach_by_reading_order() -> None:
    blocks = convert_pages([_page(
        0,
        _block(0, "Caption", "Table 1 Formulations"),
        _block(1, "Table", "<table><tr><td>A</td></tr></table>", fmt="html"),
        _block(2, "Footnote", "a supplied by X"),
        _block(3, "Caption", "Table 2 Results"),
        _block(4, "Table", "<table><tr><td>B</td></tr></table>", fmt="html"),
        _block(5, "Picture", "![](https://oxr.invalid/a.png)", fmt="markdown"),
        _block(6, "Caption", "FIG. 1 Cross-section"),
        _block(7, "Footnote", "Scale bar 10 um"),
        _block(8, "Text", "body text"),
        _block(9, "Footnote", "page footnote after text"),
    )])
    table1, table2, figure, body, orphan = blocks
    assert table1["table_caption"] == ["Table 1 Formulations"]
    assert table1["table_footnote"] == ["a supplied by X"]
    assert table1["table_body"].startswith("<table>")
    assert table2["table_caption"] == ["Table 2 Results"]
    assert figure["image_caption"] == ["FIG. 1 Cross-section"]
    # A footnote after a caption still follows the same figure.
    assert figure["image_footnote"] == ["Scale bar 10 um"]
    assert "img_path" not in figure  # remote OXR link is not reused
    assert body["text"] == "body text"
    assert orphan == {"type": "text", "text": "page footnote after text", "bbox": [100, 100, 900, 200], "page_idx": 0}


def test_text_table_label_becomes_caption_but_prose_does_not() -> None:
    blocks = convert_pages([_page(
        0,
        _block(0, "Text", "[0129] The weight percentages in Table 1 were based on solids."),
        _block(1, "Text", "[0157] Table 5"),
        _block(2, "Table", "<table><tr><td>A</td></tr></table>", fmt="html"),
    )])
    assert [b["type"] for b in blocks] == ["text", "table"]
    assert blocks[1]["table_caption"] == ["[0157] Table 5"]


def test_math_wrapped_heading_is_unwrapped_but_real_math_is_kept() -> None:
    blocks = convert_pages([_page(
        0,
        _block(0, "Title", r"$\underline{\text{What is claimed is:}}$"),
        _block(1, "Text", r"$x^{2}$"),
    )])
    assert blocks[0]["text"] == "What is claimed is:"
    assert blocks[1]["text"] == r"$x^{2}$"


def test_table_continued_on_next_page_is_merged() -> None:
    head = "<table><tr><td>Sample</td><td>Gloss</td></tr><tr><td>1</td><td>90</td></tr></table>"
    tail = "<table><tr><td>Sample</td><td>Gloss</td></tr><tr><td>2</td><td>85</td></tr></table>"
    blocks = convert_pages([
        _page(0, _block(0, "Caption", "Table 2 Results"), _block(1, "Table", head, fmt="html")),
        _page(1, _block(0, "Table", tail, fmt="html"), _block(1, "Text", "[0141] It can be seen")),
    ])
    assert [b["type"] for b in blocks] == ["table", "text"]
    assert blocks[0]["table_body"] == (
        "<table><tr><td>Sample</td><td>Gloss</td></tr><tr><td>1</td><td>90</td></tr>"
        "<tr><td>2</td><td>85</td></tr></table>"
    )


def test_heading_below_continued_table_does_not_block_merge() -> None:
    head = "<table><tr><td>Component</td><td>WB1</td></tr></table>"
    tail = "<table><tr><td>UV Additive 1</td><td>0.03</td></tr></table>"
    blocks = convert_pages([
        _page(0, _block(0, "Table", head, fmt="html")),
        _page(1, _block(0, "Table", tail, fmt="html"),
              _block(1, "Caption", "Print Trial 1: Critical Film Thickness of SB2")),
    ])
    assert [b["type"] for b in blocks] == ["table", "text"]
    assert blocks[0]["table_body"].count("<tr>") == 2
    assert blocks[0]["table_caption"] == []
    assert blocks[1]["text"] == "Print Trial 1: Critical Film Thickness of SB2"


def test_captioned_table_on_next_page_is_not_merged() -> None:
    table = "<table><tr><td>A</td></tr></table>"
    blocks = convert_pages([
        _page(0, _block(0, "Table", table, fmt="html")),
        _page(1, _block(0, "Caption", "Table 3"), _block(1, "Table", table, fmt="html")),
    ])
    assert [b["type"] for b in blocks] == ["table", "table"]


def test_unattached_caption_stays_as_text() -> None:
    blocks = convert_pages([_page(0, _block(0, "Text", "body"), _block(1, "Caption", "orphan"), _block(2, "Text", "x"))])
    assert [b.get("text") for b in blocks] == ["body", "orphan", "x"]


def test_written_layout_feeds_existing_unit_extractor(tmp_path) -> None:
    fitz = pytest.importorskip("fitz")
    pdf = tmp_path / "DOC1.pdf"
    doc = fitz.open()
    page = doc.new_page(width=500, height=1000)
    page.draw_rect(fitz.Rect(50, 300, 450, 500), color=(0, 0, 0), fill=(0.8, 0.8, 0.8))
    doc.save(pdf)

    payload = {"code": 0, "data": {"task_id": "t", "result": {"file_name": "DOC1.pdf", "outputs": {
        "json": {"pages": [_page(
            0,
            _block(0, "Title", "EXAMPLES", bbox=(100, 100, 400, 140)),
            _block(1, "Caption", "Table 1 Coating performance", bbox=(100, 560, 900, 590)),
            _block(2, "Table", "<table><tr><td>Sample</td><td>Gloss</td></tr></table>",
                   bbox=(100, 600, 900, 1000), fmt="html"),
        )]},
        "markdown": "# EXAMPLES",
    }}}}
    written = write_mineru_layout(payload, tmp_path / "mineru", "DOC1", pdf_path=pdf)

    assert written == tmp_path / "mineru" / "DOC1" / "auto" / "DOC1_content_list.json"
    table = json.loads(written.read_text(encoding="utf-8"))[1]
    assert table["img_path"].startswith("images/")
    assert (written.parent / table["img_path"]).stat().st_size > 0

    layout = _load_layout_json(tmp_path / "mineru", "DOC1")
    units = list(iter_figure_table_units(layout, examples_only=True, image_root=written.parent))
    assert len(units) == 1
    assert units[0].unit_type == "table"
    assert "Table 1 Coating performance" in units[0].caption_footnote_text
    assert units[0].extracted_table_html is not None
