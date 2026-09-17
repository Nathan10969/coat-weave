from __future__ import annotations

from coating_kg.db.models import FigureTableUnit
from coating_kg.pipeline.evidence_ledger import (
    build_evidence_ledger,
    gather_document_paragraphs,
)


def test_gather_document_paragraphs_keeps_methods_outside_examples() -> None:
    layout = {
        "data": [
            {"type": "text", "page_idx": 0, "text": "1"},
            {
                "type": "text",
                "page_idx": 1,
                "text": "Test methods were conducted according to ASTM D523 at 60 degrees.",
            },
            {
                "type": "text",
                "page_idx": 4,
                "text": "Example 1 was prepared by mixing the coating components.",
            },
        ]
    }

    paragraphs = gather_document_paragraphs(layout)

    assert [row["page"] for row in paragraphs] == [2, 5]
    assert "ASTM D523" in paragraphs[0]["text"]


def test_build_evidence_ledger_registers_cells_and_global_context() -> None:
    unit = FigureTableUnit(
        unit_id="U_DOC_p5_b7",
        unit_type="table",
        doc_id="DOC",
        page=5,
        region_id="Table 1",
        caption_footnote_text="Table 1. Gloss results for Example 1.",
        vlm_description="Table with Example 1 gloss and composition results.",
    )
    table_html = """
    <table>
      <tr><th>Sample</th><th>Gloss</th></tr>
      <tr><td>Example 1</td><td>92</td></tr>
    </table>
    """
    ledger = build_evidence_ledger(
        unit,
        table_html=table_html,
        matched_paragraphs=[
            {
                "para_id": 10,
                "page": 5,
                "score": 0.96,
                "reason": "Names Example 1",
                "text": "Example 1 was cured for 20 min.",
            }
        ],
        document_paragraphs=[
            {
                "para_id": 3,
                "page": 2,
                "text": "Gloss was measured according to ASTM D523 at 60 degrees.",
            }
        ],
    )

    source_types = {entry["source_type"] for entry in ledger["entries"]}
    assert {
        "TABLE_CELL",
        "CAPTION_FOOTNOTE",
        "VLM_DESCRIPTION",
        "MATCHED_PARAGRAPH",
        "DOCUMENT_PARAGRAPH",
    } <= source_types
    assert any(entry.get("cell") == "92" for entry in ledger["entries"])
    assert any("ASTM D523" in entry["text"] for entry in ledger["entries"])
    assert ledger["stats"]["table_cell_count"] >= 4
