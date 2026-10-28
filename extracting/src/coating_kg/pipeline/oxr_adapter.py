"""Convert OXR JSON pages into MinerU ``content_list`` blocks.

Downstream stages read MinerU 3.x ``content_list.json``: boxes normalized to
0-1000 and captions/footnotes attached to their table or figure. OXR emits
page-pixel boxes and standalone Caption/Footnote blocks, so this module only
reshapes OXR output; recognized text, HTML and LaTeX are kept verbatim.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_VISUAL_TYPES = {"Table", "Picture"}
_EQUATION_FORMATS = {"Formula": "latex", "Chemical Equation": "latex", "Chemical Structure": "smiles"}
_MARGIN_TYPES = {"Page-header": "header", "Page-footer": "footer"}

# A short "Table N" line right above a table, which OXR sometimes labels Text.
_TABLE_LABEL_RE = re.compile(r"^\s*(?:\[\d+\]\s*)?(?:Table|Tab\.|Tabelle)\s*\d+\b[^\n]{0,150}$", re.IGNORECASE)
# Headings OXR wraps in math, e.g. $\underline{\text{What is claimed is:}}$.
_MATH_WRAPPED_RE = re.compile(r"^\s*\$+(.*?)\$+\s*$", re.DOTALL)
_TEXT_COMMAND_RE = re.compile(r"\\(?:underline|text|textbf|textit|mathrm|mathbf|mathit)\s*\{([^{}]*)\}")
_ROW_RE = re.compile(r"<tr\b.*?</tr>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")

# (page_idx, OXR pixel bbox, OXR page_size) -> image path relative to the content_list.
Cropper = Callable[[int, list[float], dict[str, Any]], str | None]


def load_oxr_pages(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Accept a raw /parse/sync response, its ``result``, or merged ``{"pages": ...}``."""
    if "data" in payload:
        payload = payload["data"]["result"]
    if "outputs" in payload:
        payload = payload["outputs"]
    pages = payload["json"]["pages"] if "json" in payload else payload["pages"]
    return pages, payload.get("markdown") or ""


def normalize_bbox(bbox: list[float], page_size: dict[str, Any]) -> list[int]:
    """OXR pixel box -> MinerU 0-1000 box."""
    w, h = float(page_size["w"]), float(page_size["h"])
    scale = (1000 / w, 1000 / h, 1000 / w, 1000 / h)
    return [min(1000, max(0, round(v * s))) for v, s in zip(bbox, scale)]


def _caption_owners(blocks: list[dict[str, Any]]) -> dict[int, int]:
    """Map Caption/Footnote positions to the table/figure position they describe.

    Uses reading-order adjacency only: table titles normally sit above the
    table and figure captions below the figure; footnotes follow their table
    or figure. Anything that does not fit stays a plain text block.
    """

    def visual_neighbour(pos: int, step: int) -> int | None:
        j = pos + step
        while 0 <= j < len(blocks) and blocks[j]["type"] in ("Caption", "Footnote"):
            j += step
        return j if 0 <= j < len(blocks) and blocks[j]["type"] in _VISUAL_TYPES else None

    owners: dict[int, int] = {}
    for pos, block in enumerate(blocks):
        if (block["type"] == "Text" and pos + 1 < len(blocks) and blocks[pos + 1]["type"] == "Table"
                and _TABLE_LABEL_RE.match(block.get("content") or "")):
            owners[pos] = pos + 1
        elif block["type"] == "Caption":
            nxt, prev = visual_neighbour(pos, 1), visual_neighbour(pos, -1)
            candidates = [(nxt, "Table"), (prev, "Picture"), (nxt, "Picture")]
            # Below a table, only a "Table N" caption belongs to it; OXR also
            # labels following subsection headings as Caption.
            if _TABLE_LABEL_RE.match(block.get("content") or ""):
                candidates.append((prev, "Table"))
            for cand, kind in candidates:
                if cand is not None and blocks[cand]["type"] == kind:
                    owners[pos] = cand
                    break
        elif block["type"] == "Footnote":
            prev = visual_neighbour(pos, -1)
            if prev is not None:
                owners[pos] = prev
    return owners


def _is_image_ref(content: str) -> bool:
    return content.lstrip().startswith("![")


def _plain_text(content: str) -> str:
    """Unwrap text that is only math-wrapped formatting; leave real math alone."""
    match = _MATH_WRAPPED_RE.match(content)
    if not match:
        return content
    inner, previous = match.group(1), None
    while inner != previous:
        previous, inner = inner, _TEXT_COMMAND_RE.sub(r"\1", inner)
    return content if "\\" in inner or "{" in inner else inner.strip()


def _row_text(row: str) -> str:
    return " ".join(_TAG_RE.sub(" ", row).split())


def _merge_continued_table(previous: dict[str, Any], continued: dict[str, Any]) -> bool:
    """Append a page-top uncaptioned table to the page-bottom table before it.

    MinerU merges tables split across pages; without this the continuation
    becomes a separate unit without its caption. A repeated header row is
    dropped.
    """
    head, tail = previous.get("table_body"), continued.get("table_body")
    if not head or not tail or continued["table_caption"]:
        return False
    rows = _ROW_RE.findall(tail)
    first = _ROW_RE.search(head)
    if rows and first and _row_text(rows[0]) == _row_text(first.group(0)):
        rows = rows[1:]
    if not rows:
        return False
    lower = head.lower()
    cut = lower.rfind("</tbody>") if "</tbody>" in lower else lower.rfind("</table>")
    if cut < 0:
        return False
    previous["table_body"] = head[:cut] + "".join(rows) + head[cut:]
    previous["table_footnote"].extend(continued["table_footnote"])
    return True


def _convert_block(block: dict[str, Any]) -> dict[str, Any]:
    kind = block["type"]
    content = block.get("content") or ""
    if kind in ("Title", "Text"):
        content = _plain_text(content)
    if kind == "Title":
        return {"type": "text", "text": content, "text_level": 1}
    if kind in _MARGIN_TYPES:
        return {"type": _MARGIN_TYPES[kind], "text": content}
    if kind == "Table":
        item: dict[str, Any] = {"type": "table", "table_caption": [], "table_footnote": []}
        if content and not _is_image_ref(content):
            item["table_body" if block.get("format") == "html" else "text"] = content
        return item
    if kind == "Picture":
        # OXR's picture content is a link to its own server; crops are made locally.
        return {"type": "image", "image_caption": [], "image_footnote": []}
    if kind in _EQUATION_FORMATS:
        item = {"type": "equation"}
        if content and not _is_image_ref(content):
            item.update(text=content, text_format=_EQUATION_FORMATS[kind])
        return item
    # Text, Stamp, Code Block, unattached Caption/Footnote and any new OXR type.
    return {"type": "text", "text": content}


def convert_pages(pages: list[dict[str, Any]], *, crop: Cropper | None = None) -> list[dict[str, Any]]:
    """Return MinerU-style ``content_list`` blocks in OXR reading order."""
    content_list: list[dict[str, Any]] = []
    previous_last: dict[str, Any] | None = None
    for page in pages:
        blocks = page.get("blocks") or []
        size = page["page_size"]
        page_idx = int(page["page_num"])
        owners = _caption_owners(blocks)
        converted: dict[int, dict[str, Any]] = {}
        for pos, block in enumerate(blocks):
            if pos in owners:
                continue
            item = _convert_block(block)
            # Equations stay image units, matching MinerU; downstream treats
            # an equation with img_path as a chemical-structure figure.
            if crop is not None and item["type"] in ("table", "image", "equation"):
                img_path = crop(page_idx, block["bbox"], size)
                if img_path:
                    item["img_path"] = img_path
            item["bbox"] = normalize_bbox(block["bbox"], size)
            item["page_idx"] = page_idx
            converted[pos] = item
        for pos, owner in sorted(owners.items()):
            target = converted[owner]
            prefix = "table" if target["type"] == "table" else "image"
            suffix = "footnote" if blocks[pos]["type"] == "Footnote" else "caption"
            target[f"{prefix}_{suffix}"].append(blocks[pos].get("content") or "")
        items = [converted[pos] for pos in sorted(converted)]
        body = [item for item in items if item["type"] not in ("header", "footer")]
        if (body and previous_last is not None and body[0]["type"] == "table"
                and previous_last["type"] == "table" and _merge_continued_table(previous_last, body[0])):
            items.remove(body[0])
            body = body[1:]
        if body:
            previous_last = body[-1]
        content_list.extend(items)
        for note in page.get("unattached_formula_numbering") or []:
            content_list.append({"type": "text", "text": note.get("content") or "", "page_idx": page_idx})
    return content_list


def pdf_cropper(pdf_path: Path, image_dir: Path, *, dpi: int = 200) -> Cropper:
    """Crop OXR boxes from the source PDF into ``image_dir`` (MinerU naming)."""
    import fitz  # PyMuPDF; only needed when crops are requested

    doc = fitz.open(pdf_path)
    image_dir.mkdir(parents=True, exist_ok=True)

    def crop(page_idx: int, bbox: list[float], page_size: dict[str, Any]) -> str | None:
        page = doc[page_idx]
        # OXR boxes are in the displayed page; PyMuPDF clips in the same space.
        rect = page.rect
        sx, sy = rect.width / float(page_size["w"]), rect.height / float(page_size["h"])
        x1, y1, x2, y2 = bbox
        clip = fitz.Rect(rect.x0 + x1 * sx, rect.y0 + y1 * sy, rect.x0 + x2 * sx, rect.y0 + y2 * sy) & rect
        if clip.is_empty:
            logger.warning("empty OXR box on %s page %d: %s", pdf_path.name, page_idx, bbox)
            return None
        key = f"{pdf_path.name}:{page_idx}:{','.join(f'{v:.2f}' for v in bbox)}"
        name = hashlib.sha256(key.encode("utf-8")).hexdigest() + ".jpg"
        target = image_dir / name
        if not target.exists():
            page.get_pixmap(dpi=dpi, clip=clip).save(str(target))
        return f"{image_dir.name}/{name}"

    return crop


def write_mineru_layout(
    payload: dict[str, Any],
    out_root: Path,
    doc_id: str,
    *,
    pdf_path: Path | None = None,
) -> Path:
    """Write ``<out_root>/<doc_id>/auto/<doc_id>_content_list.json`` (+ images, md)."""
    pages, markdown = load_oxr_pages(payload)
    method_dir = out_root / doc_id / "auto"
    method_dir.mkdir(parents=True, exist_ok=True)
    crop = pdf_cropper(pdf_path, method_dir / "images") if pdf_path else None
    content_list = convert_pages(pages, crop=crop)
    target = method_dir / f"{doc_id}_content_list.json"
    target.write_text(json.dumps(content_list, ensure_ascii=False, indent=2), encoding="utf-8")
    if markdown:
        (method_dir / f"{doc_id}.md").write_text(markdown, encoding="utf-8")
    return target
