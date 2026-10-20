"""Evidence ledger builders for Stage 6.5.

The ledger is a routing/provenance surface, not a semantic extractor. It
registers table cells and nearby/full-document prose so the model can cite
stable ledger IDs when it emits sample maps, context assertions, and facts.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

from ..db.models import FigureTableUnit

JSON = dict[str, Any]


def gather_document_paragraphs(
    layout: JSON,
    *,
    min_chars: int = 30,
) -> list[JSON]:
    """Return substantive text blocks from the full MinerU layout.

    This intentionally does not decide coating semantics. It only exposes text
    evidence outside the Examples-only matcher, including Test Methods and
    Preparation sections that often define standards and process conditions.
    """

    out: list[JSON] = []
    for i, block in enumerate(layout.get("data") or []):
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = str(block.get("text") or "").strip()
        if len(text) < min_chars or _looks_like_noise(text):
            continue
        page = int(block.get("page_idx", 0)) + 1
        out.append({"para_id": i, "page": page, "text": text})
    return out


def build_evidence_ledger(
    unit: FigureTableUnit,
    *,
    table_html: str | None = None,
    matched_paragraphs: list[JSON] | None = None,
    document_paragraphs: list[JSON] | None = None,
    max_document_paragraphs: int = 60,
) -> JSON:
    """Build a per-unit evidence ledger sidecar."""

    entries: list[JSON] = []
    seq = 1

    def add(source_type: str, text: str, **extra: Any) -> None:
        nonlocal seq
        clean = str(text or "").strip()
        if not clean:
            return
        entry: JSON = {
            "ledger_id": f"LEDGER_{unit.unit_id}_{seq:04d}",
            "source_type": source_type,
            "unit_id": unit.unit_id,
            "doc_id": unit.doc_id,
            "page": extra.pop("page", unit.page),
            "region_id": unit.region_id,
            "text": clean,
        }
        entry.update({key: value for key, value in extra.items() if value not in (None, "", [], {})})
        entries.append(entry)
        seq += 1

    add("CAPTION_FOOTNOTE", unit.caption_footnote_text or "")
    add("VLM_DESCRIPTION", unit.vlm_description or "")

    for row in _table_cells(table_html or ""):
        add("TABLE_CELL", row["cell"], row=row.get("row"), column=row.get("column"), cell=row.get("cell"))

    for para in matched_paragraphs or []:
        if not isinstance(para, dict):
            continue
        add(
            "MATCHED_PARAGRAPH",
            str(para.get("text") or ""),
            page=para.get("page"),
            para_id=para.get("para_id"),
            score=para.get("score"),
            reason=para.get("reason"),
        )

    for para in _select_document_paragraphs(
        unit,
        document_paragraphs or [],
        max_entries=max_document_paragraphs,
    ):
        add(
            "DOCUMENT_PARAGRAPH",
            str(para.get("text") or ""),
            page=para.get("page"),
            para_id=para.get("para_id"),
            selection_reason=para.get("selection_reason"),
        )

    return {
        "schema_version": "evidence_ledger_v1",
        "unit_id": unit.unit_id,
        "doc_id": unit.doc_id,
        "region_id": unit.region_id,
        "entries": entries,
        "stats": {
            "entry_count": len(entries),
            "table_cell_count": sum(1 for row in entries if row.get("source_type") == "TABLE_CELL"),
            "matched_paragraph_count": sum(
                1 for row in entries if row.get("source_type") == "MATCHED_PARAGRAPH"
            ),
            "document_paragraph_count": sum(
                1 for row in entries if row.get("source_type") == "DOCUMENT_PARAGRAPH"
            ),
        },
    }


def format_evidence_ledger_for_prompt(ledger: JSON | None, *, max_chars: int = 24000) -> str:
    if not isinstance(ledger, dict) or not isinstance(ledger.get("entries"), list):
        return "(none)"
    lines: list[str] = []
    for entry in ledger["entries"]:
        if not isinstance(entry, dict):
            continue
        parts = [
            f"[{entry.get('ledger_id')}]",
            str(entry.get("source_type") or ""),
            f"p{entry.get('page')}" if entry.get("page") is not None else "",
        ]
        if entry.get("row") or entry.get("column"):
            parts.append(f"row={entry.get('row', '')}; col={entry.get('column', '')}")
        text = str(entry.get("text") or "").replace("\n", " ").strip()
        lines.append(f"{' '.join(part for part in parts if part)} :: {text}")
        if sum(len(line) + 1 for line in lines) >= max_chars:
            lines.append("[truncated]")
            break
    return "\n".join(lines) if lines else "(none)"


def _select_document_paragraphs(
    unit: FigureTableUnit,
    paragraphs: list[JSON],
    *,
    max_entries: int,
) -> list[JSON]:
    selected: list[JSON] = []
    unit_page = unit.page
    region = str(unit.region_id or "").casefold()
    context_terms = (
        "test",
        "method",
        "standard",
        "astm",
        "iso",
        "din",
        "gb/t",
        "prepar",
        "comparative",
        "control",
        "formulation",
        "composition",
        "cur",
        "substrate",
        "film thickness",
    )

    for para in paragraphs:
        if not isinstance(para, dict):
            continue
        text = str(para.get("text") or "")
        text_low = text.casefold()
        page = para.get("page")
        reason = ""
        if region and region in text_low:
            reason = "mentions_region"
        elif unit_page is not None and isinstance(page, int) and abs(page - unit_page) <= 2:
            reason = "near_unit_page"
        elif any(term in text_low for term in context_terms):
            reason = "global_context_term"
        if reason:
            row = dict(para)
            row["selection_reason"] = reason
            selected.append(row)
        if len(selected) >= max_entries:
            break
    return selected


def _looks_like_noise(text: str) -> bool:
    stripped = text.strip()
    if stripped.isdigit():
        return True
    upper = stripped.upper()
    return upper in {"EXAMPLES", "CLAIMS", "DESCRIPTION"}


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self._row = []
        elif tag.lower() in {"td", "th"}:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        low = tag.lower()
        if low in {"td", "th"} and self._cell is not None and self._row is not None:
            cell = " ".join("".join(self._cell).split())
            self._row.append(cell)
            self._cell = None
        elif low == "tr" and self._row is not None:
            if any(cell.strip() for cell in self._row):
                self.rows.append(self._row)
            self._row = None


def _table_cells(table_html: str) -> list[JSON]:
    if not table_html.strip():
        return []
    parser = _TableParser()
    parser.feed(table_html)
    out: list[JSON] = []
    headers = parser.rows[0] if parser.rows else []
    for r_idx, row in enumerate(parser.rows):
        row_label = row[0] if row else ""
        for c_idx, cell in enumerate(row):
            if not cell:
                continue
            column = headers[c_idx] if c_idx < len(headers) else f"col_{c_idx + 1}"
            out.append({"row": row_label or f"row_{r_idx + 1}", "column": column, "cell": cell})
    return out
