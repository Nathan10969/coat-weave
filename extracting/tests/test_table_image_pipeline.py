from __future__ import annotations

import json

from coating_kg.cli import _extract_corrected_table_text, _write_table_ocr_artifacts
from coating_kg.db.models import FigureTableUnit
from coating_kg.pipeline.unit_extractor import iter_figure_table_units
from coating_kg.pipeline.unit_materializer import materialize_unit
from coating_kg.pipeline.unit_router import write_pending_figure


def test_table_unit_keeps_mineru_image_and_html(tmp_path) -> None:
    mineru_doc = tmp_path / "mineru" / "DOC1"
    image_dir = mineru_doc / "images"
    image_dir.mkdir(parents=True)
    (image_dir / "table-1.jpg").write_bytes(b"fake-jpeg")

    layout = {
        "doc_id": "DOC1",
        "data": [
            {"type": "text", "text": "EXAMPLES", "page_idx": 0},
            {
                "type": "table",
                "page_idx": 0,
                "img_path": "images/table-1.jpg",
                "table_caption": ["Table 1 Coating performance"],
                "table_body": "<table><tr><td>Sample</td><td>Gloss</td></tr></table>",
            },
        ],
    }

    units = list(iter_figure_table_units(layout, examples_only=True, image_root=mineru_doc))

    assert len(units) == 1
    unit = units[0]
    assert unit.unit_type == "table"
    assert unit.image_path is not None
    assert unit.image_path.endswith("table-1.jpg")
    assert unit.extracted_table_html is not None

    folder = materialize_unit(unit, tmp_path / "units", mineru_root=tmp_path / "mineru")

    assert (folder / "table.jpg").read_bytes() == b"fake-jpeg"
    assert (folder / "table.html").read_text(encoding="utf-8").startswith("<table>")
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert meta["image_path"].endswith("table-1.jpg")


def test_table_unit_keeps_image_only_tables(tmp_path) -> None:
    mineru_doc = tmp_path / "mineru" / "DOC2"
    image_dir = mineru_doc / "images"
    image_dir.mkdir(parents=True)
    (image_dir / "table-only.png").write_bytes(b"fake-png")

    layout = {
        "doc_id": "DOC2",
        "data": [
            {"type": "text", "text": "EXAMPLES", "page_idx": 0},
            {
                "type": "table",
                "page_idx": 0,
                "img_path": "images/table-only.png",
                "table_caption": ["Table 2"],
            },
        ],
    }

    units = list(iter_figure_table_units(layout, examples_only=True, image_root=mineru_doc))

    assert len(units) == 1
    unit = units[0]
    assert unit.image_path is not None
    assert unit.extracted_table_html is None

    folder = materialize_unit(unit, tmp_path / "units", mineru_root=tmp_path / "mineru")

    assert (folder / "table.png").exists()
    assert not (folder / "table.html").exists()


def test_extract_corrected_table_prefers_html_then_markdown() -> None:
    html, source, filename = _extract_corrected_table_text({
        "corrected_table_html": " <table><tr><td>A</td></tr></table> ",
        "table_markdown_exact": "| A |",
    })
    assert html == "<table><tr><td>A</td></tr></table>"
    assert source == "qwen_table_ocr_html"
    assert filename == "table_corrected.html"

    markdown, source, filename = _extract_corrected_table_text({
        "corrected_table_html": None,
        "table_markdown_exact": " | A | ",
    })
    assert markdown == "| A |"
    assert source == "qwen_table_ocr_markdown"
    assert filename == "table_corrected.md"


def test_write_table_ocr_artifacts_records_effective_source(tmp_path) -> None:
    unit_dir = tmp_path / "U_DOC3_p1_b2"
    unit_dir.mkdir()
    table_image = unit_dir / "table.jpg"
    table_image.write_bytes(b"fake-jpeg")
    (unit_dir / "table.html").write_text("<table><tr><td>raw</td></tr></table>", encoding="utf-8")

    written = _write_table_ocr_artifacts(
        unit_dir,
        {
            "corrected_table_html": "<table><tr><td>corrected</td></tr></table>",
            "table_type": "performance",
            "ocr_confidence": 0.82,
            "html_conflict_notes": [{"row": "1", "column": "2"}],
        },
        table_image=table_image,
        corrected_text="<table><tr><td>corrected</td></tr></table>",
        corrected_filename="table_corrected.html",
        effective_source="qwen_table_ocr_html",
    )

    assert written == "<table><tr><td>corrected</td></tr></table>"
    assert (unit_dir / "table_corrected.html").exists()
    metadata = json.loads((unit_dir / "table_ocr.json").read_text(encoding="utf-8"))
    assert metadata["effective_table_source"] == "qwen_table_ocr_html"
    assert metadata["used_for_fact_extraction"] is True
    assert metadata["metadata"]["ocr_confidence"] == 0.82
    assert metadata["metadata"]["html_conflict_notes"] == [{"row": "1", "column": "2"}]


def test_pending_layer1_table_prefers_image_over_html(tmp_path) -> None:
    unit_id = "U_DOC4_p1_b2"
    unit_dir = tmp_path / "data" / "units" / unit_id
    unit_dir.mkdir(parents=True)
    (unit_dir / "table.html").write_text("<table><tr><td>raw</td></tr></table>", encoding="utf-8")
    (unit_dir / "table.jpg").write_bytes(b"fake-jpeg")

    unit = FigureTableUnit(
        unit_id=unit_id,
        unit_type="table",
        doc_id="DOC4",
        page=1,
        caption_footnote_text="Table 1 structure candidates.",
    )

    out_path = write_pending_figure(
        tmp_path,
        "DOC4",
        unit,
        {"description": "A table-embedded structure.", "table_subject": "structure_or_scheme"},
    )

    record = json.loads(out_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["image_path"] == f"data/units/{unit_id}/table.jpg"
