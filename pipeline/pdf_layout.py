"""MinerU online API client (W2.1).

Two submission paths:

1. **URL-based (verified working)** — POST to
   ``${MINERU_API_BASE}/extract/task`` with body
   ``{"url": <pdf_url>, "model_version": "vlm"}``. Returns a
   ``task_id``; status polled at ``/extract/task/{task_id}``.

2. **Local file (needs verification before W2.2 batch run)** — upload
   via the batch presigned-URL flow at ``/file-urls/batch``. Field
   names below follow the public docs but should be re-checked against
   https://mineru.net/apiManage/docs before processing all 348 PDFs.

``model_version`` defaults to ``"vlm"`` per current MinerU docs (their
VLM backend produces better table HTML and figure captions than the
legacy OCR-only pipeline).
"""

from __future__ import annotations

import io
import json
import logging
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any

import requests

from ..config import SETTINGS

logger = logging.getLogger(__name__)


# Default poll interval / overall timeout (seconds). MinerU jobs typically
# finish within ~30s per PDF page; 600s gives ample headroom.
_DEFAULT_POLL_INTERVAL = 5
_DEFAULT_TIMEOUT = 1800


class MinerUError(RuntimeError):
    """Raised when the MinerU online API returns an unrecoverable error."""


def _auth_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {SETTINGS.mineru.token}",
        "Accept": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def _api(path: str) -> str:
    return f"{SETTINGS.mineru.api_base.rstrip('/')}{path}"


def _agent_api(path: str) -> str:
    return f"https://mineru.net/api/v1/agent{path}"


# ----------------------------------------------------------------------
# Submission
# ----------------------------------------------------------------------
def submit_pdf_url(
    url: str,
    *,
    model_version: str = "vlm",
) -> str:
    """Submit a publicly-reachable PDF URL for extraction.

    Verified working with MinerU API as of 2026-05-02. POST body:
        {"url": "<pdf url>", "model_version": "vlm"}

    Returns the ``task_id`` MinerU assigns. Use :func:`wait_for_completion`
    with ``kind="task"`` to poll for the result.
    """
    body = {
        "url": url,
        "model_version": model_version,
    }
    resp = requests.post(
        _api("/extract/task"),
        json=body,
        headers=_auth_headers({"Content-Type": "application/json"}),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json().get("data") or {}
    task_id = data.get("task_id")
    if not task_id:
        raise MinerUError(f"submit_pdf_url: no task_id in response: {resp.text}")
    logger.info("MinerU URL task submitted: %s", task_id)
    return task_id


def submit_pdf_local(
    pdf_path: Path,
    *,
    is_ocr: bool = True,
    enable_formula: bool = True,
    enable_table: bool = True,
    model_version: str = "vlm",
) -> str:
    """Upload a local PDF via the batch presigned-URL flow.

    Workflow:
      1. POST ``/file-urls/batch`` with file metadata; receive a
         ``batch_id`` plus a presigned S3 ``file_url``.
      2. PUT the file bytes to the presigned URL (no auth header).
      3. Return the ``batch_id`` so the caller can poll
         ``/extract-results/batch/{batch_id}``.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    body = {
        "files": [
            {"name": pdf_path.name, "data_id": pdf_path.stem},
        ],
        "model_version": model_version,
        "is_ocr": is_ocr,
        "enable_formula": enable_formula,
        "enable_table": enable_table,
    }
    resp = requests.post(
        _api("/file-urls/batch"),
        json=body,
        headers=_auth_headers({"Content-Type": "application/json"}),
        timeout=30,
    )
    if resp.status_code == 401:
        raise MinerUError(
            "MinerU authorization failed (401). The v4 precision API expects "
            "Authorization: Bearer <API Token>; Access Key ID / Secret Access Key "
            "are not accepted by this endpoint."
        )
    resp.raise_for_status()
    payload = resp.json().get("data") or {}
    if resp.json().get("code", 0) not in (0, "0"):
        raise MinerUError(f"submit_pdf_local: API returned error: {resp.text}")
    batch_id = payload.get("batch_id")
    file_urls = payload.get("file_urls") or []
    if not batch_id or not file_urls:
        raise MinerUError(f"submit_pdf_local: bad response: {resp.text}")

    # 2. Upload bytes to the presigned URL. Note: do NOT send the
    #    Authorization header here — the URL itself is signed.
    presigned = file_urls[0]
    with pdf_path.open("rb") as fp:
        put = requests.put(presigned, data=fp.read(), timeout=120)
    put.raise_for_status()
    logger.info("MinerU batch %s: uploaded %s", batch_id, pdf_path.name)
    return batch_id


# ----------------------------------------------------------------------
# Polling
# ----------------------------------------------------------------------
def wait_for_completion(
    task_or_batch_id: str,
    *,
    kind: str = "batch",
    timeout: int = _DEFAULT_TIMEOUT,
    poll_interval: int = _DEFAULT_POLL_INTERVAL,
) -> dict[str, Any]:
    """Poll MinerU until the task/batch is in a terminal state.

    ``kind`` is either ``"task"`` (single-URL submission) or ``"batch"``
    (file-upload submission). Returns the final response payload, which
    contains a ``full_zip_url`` (or per-file equivalents) the caller can
    pass to :func:`download_result`.
    """
    if kind == "task":
        url = _api(f"/extract/task/{task_or_batch_id}")
    elif kind == "batch":
        url = _api(f"/extract-results/batch/{task_or_batch_id}")
    else:
        raise ValueError(f"unknown kind={kind!r}")

    deadline = time.time() + timeout
    last_payload: dict[str, Any] = {}
    while time.time() < deadline:
        resp = requests.get(url, headers=_auth_headers(), timeout=30)
        resp.raise_for_status()
        last_payload = resp.json().get("data") or {}

        # Terminal-state detection. Task path uses ``state``; batch path
        # uses per-file ``extract_result.state``. We treat any of
        # ``done`` / ``success`` / ``failed`` as terminal.
        state = (last_payload.get("state") or "").lower()
        if state in {"done", "success", "completed"}:
            return last_payload
        if state in {"failed", "error"}:
            raise MinerUError(f"MinerU job failed: {last_payload}")

        # Batch shape: list of files, each with their own state.
        results = last_payload.get("extract_result") or last_payload.get("results")
        if isinstance(results, list) and results:
            states = [(r.get("state") or "").lower() for r in results]
            if all(s in {"done", "success", "completed"} for s in states):
                return last_payload
            if any(s in {"failed", "error"} for s in states):
                raise MinerUError(f"MinerU batch had failures: {last_payload}")

        time.sleep(poll_interval)

    raise MinerUError(
        f"MinerU job {task_or_batch_id} did not finish within {timeout}s; "
        f"last payload: {last_payload}"
    )


# ----------------------------------------------------------------------
# Download
# ----------------------------------------------------------------------
def download_result(result_url: str, output_dir: Path) -> Path:
    """Download the MinerU result ZIP and unpack it under ``output_dir``.

    Returns the directory the ZIP was unpacked into. The unpacked layout
    follows the standard magic-pdf on-disk convention:
        <output_dir>/<doc_id>_content_list.json
        <output_dir>/<doc_id>_layout.json
        <output_dir>/images/*.png
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    resp = requests.get(result_url, timeout=120)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        zf.extractall(output_dir)
    logger.info("MinerU result unpacked into %s", output_dir)
    return output_dir


# ----------------------------------------------------------------------
# High-level helper used by the CLI
# ----------------------------------------------------------------------
def parse_pdf(pdf_path: str | Path, output_dir: str | Path) -> dict[str, Any]:
    """End-to-end: submit ``pdf_path`` to MinerU, wait, download, return JSON.

    This is an **async-style** flow: submit -> poll -> download. Allow
    up to ``_DEFAULT_TIMEOUT`` seconds total. Network errors propagate.
    """
    pdf_path = Path(pdf_path).resolve()
    output_dir = Path(output_dir).resolve()
    doc_id = pdf_path.stem
    doc_out = output_dir / doc_id
    doc_out.mkdir(parents=True, exist_ok=True)

    if not SETTINGS.mineru.token:
        logger.warning(
            "MINERU_TOKEN is empty; returning stub for %s. "
            "Populate .env to enable online extraction.",
            pdf_path,
        )
        return {"doc_id": doc_id, "pages": [], "_stub": True}

    batch_id = submit_pdf_local(pdf_path)
    final = wait_for_completion(batch_id, kind="batch")

    # Extract the per-file result url (batch shape).
    results = final.get("extract_result") or final.get("results") or []
    if not results:
        raise MinerUError(f"MinerU batch returned no results: {final}")
    result_url = (
        results[0].get("full_zip_url")
        or results[0].get("zip_url")
        or results[0].get("result_url")
    )
    if not result_url:
        raise MinerUError(f"MinerU result missing zip url: {results[0]}")

    download_result(result_url, doc_out)
    return _load_layout_json(output_dir, doc_id)


def parse_pdf_as_doc_id(pdf_path: str | Path, output_dir: str | Path, doc_id: str) -> dict[str, Any]:
    """Online parse helper that stores MinerU output under an explicit doc_id.

    The raw PDF file name often includes a random MinerU prefix, while the
    pipeline uses the patent id as ``doc_id``.  This wrapper submits a temporary
    copy named ``<doc_id>.pdf`` so the downloaded output lands under the stable
    patent-id directory.
    """
    pdf_path = Path(pdf_path).resolve()
    output_dir = Path(output_dir).resolve()
    tmp_dir = output_dir / "_submit"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_pdf = tmp_dir / f"{doc_id}.pdf"
    shutil.copy2(pdf_path, tmp_pdf)
    try:
        return parse_pdf(tmp_pdf, output_dir)
    finally:
        try:
            tmp_pdf.unlink()
        except FileNotFoundError:
            pass


def parse_pdf_agent_markdown(
    pdf_path: str | Path,
    output_dir: str | Path,
    doc_id: str,
    *,
    page_range: str | None = None,
    timeout: int = 300,
    poll_interval: int = 3,
) -> dict[str, Any]:
    """Use MinerU's no-token Agent API and convert Markdown to a simple layout.

    The Agent API is useful for local smoke tests when a Bearer API Token is not
    available.  It only returns Markdown, so table/image geometry is not
    available; the returned layout is intentionally marked with
    ``source_format='agent_markdown'``.
    """
    pdf_path = Path(pdf_path).resolve()
    output_dir = Path(output_dir).resolve()
    doc_dir = output_dir / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "file_name": f"{doc_id}.pdf",
        "language": "en",
        "enable_table": True,
        "is_ocr": False,
        "enable_formula": True,
    }
    if page_range:
        payload["page_range"] = page_range

    resp = requests.post(_agent_api("/parse/file"), json=payload, timeout=30)
    resp.raise_for_status()
    initial = resp.json()
    if initial.get("code") != 0:
        raise MinerUError(f"Agent parse/file failed: {initial}")
    task_id = initial.get("data", {}).get("task_id")
    file_url = initial.get("data", {}).get("file_url")
    if not task_id or not file_url:
        raise MinerUError(f"Agent parse/file missing task_id/file_url: {initial}")

    with pdf_path.open("rb") as fp:
        put = requests.put(file_url, data=fp, timeout=120)
    if put.status_code not in (200, 201):
        raise MinerUError(f"Agent file upload failed: HTTP {put.status_code}: {put.text[:200]}")

    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        status = requests.get(_agent_api(f"/parse/{task_id}"), timeout=30)
        status.raise_for_status()
        last = status.json().get("data") or {}
        state = (last.get("state") or "").lower()
        if state == "done":
            md_url = last.get("markdown_url")
            if not md_url:
                raise MinerUError(f"Agent result missing markdown_url: {last}")
            md_resp = requests.get(md_url, timeout=120)
            md_resp.raise_for_status()
            md_text = md_resp.text
            md_path = doc_dir / "full.md"
            md_path.write_text(md_text, encoding="utf-8")
            content_path = doc_dir / f"{doc_id}_content_list.json"
            layout = _markdown_to_layout(md_text, doc_id, source=str(md_path), source_format="agent_markdown")
            content_path.write_text(json.dumps(layout["data"], ensure_ascii=False, indent=2), encoding="utf-8")
            return layout | {"source": str(content_path)}
        if state == "failed":
            raise MinerUError(f"Agent parse failed: {last}")
        time.sleep(poll_interval)

    raise MinerUError(f"Agent parse timed out; last payload: {last}")


def _markdown_to_layout(
    markdown: str,
    doc_id: str,
    *,
    source: str,
    source_format: str,
) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    lines = markdown.splitlines()
    i = 0
    block_idx = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        if "|" in line and i + 1 < len(lines) and _is_markdown_separator(lines[i + 1]):
            table_lines = [line, lines[i + 1]]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                table_lines.append(lines[i])
                i += 1
            blocks.append({
                "type": "table",
                "page_idx": 0,
                "table_body": _markdown_table_to_html(table_lines),
                "table_caption": None,
                "source_format": source_format,
                "block_idx": block_idx,
            })
            block_idx += 1
            continue

        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not ("|" in lines[i] and i + 1 < len(lines) and _is_markdown_separator(lines[i + 1])):
            para.append(lines[i])
            i += 1
        blocks.append({
            "type": "text",
            "page_idx": 0,
            "text": "\n".join(para).strip(),
            "source_format": source_format,
            "block_idx": block_idx,
        })
        block_idx += 1

    return {"doc_id": doc_id, "source": source, "source_format": source_format, "data": blocks}


def _is_markdown_separator(line: str) -> bool:
    stripped = line.strip()
    return "|" in stripped and all(ch in "|:- " for ch in stripped)


def _markdown_table_to_html(lines: list[str]) -> str:
    rows: list[list[str]] = []
    for idx, line in enumerate(lines):
        if idx == 1 and _is_markdown_separator(line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells:
            rows.append(cells)
    html_rows = []
    for row in rows:
        html_rows.append("<tr>" + "".join(f"<td rowspan=1 colspan=1>{_escape_html(cell)}</td>" for cell in row) + "</tr>")
    return "<table>" + "".join(html_rows) + "</table>"


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ----------------------------------------------------------------------
# Local-disk JSON loader (kept for downstream pipeline stages)
# ----------------------------------------------------------------------
def _load_layout_json(output_dir: Path, doc_id: str) -> dict[str, Any]:
    """Locate MinerU's content_list/layout JSON under the conventional path.

    The unpacked ZIP may sit at ``<output_dir>/<doc_id>/`` or
    ``<output_dir>/<doc_id>/auto/`` depending on the MinerU version.  The
    online v4 API can also prefix files with a MinerU UUID rather than the
    submitted PDF stem, so we accept both exact and globbed names.
    """
    doc_dir = output_dir / doc_id
    candidates = [
        output_dir / doc_id / f"{doc_id}_content_list.json",
        output_dir / doc_id / "auto" / f"{doc_id}_content_list.json",
    ]
    for base in (doc_dir, doc_dir / "auto"):
        if base.exists():
            candidates.extend(sorted(base.glob("*_content_list.json")))
            candidates.extend(sorted(base.glob("*_content_list_v2.json")))
    candidates.extend([
        output_dir / doc_id / f"{doc_id}_layout.json",
        output_dir / doc_id / f"{doc_id}_middle.json",
        output_dir / doc_id / "auto" / f"{doc_id}_layout.json",
        output_dir / doc_id / "auto" / f"{doc_id}_middle.json",
        output_dir / doc_id / "layout.json",
    ])

    seen: set[Path] = set()
    for cand in candidates:
        if cand in seen:
            continue
        seen.add(cand)
        if cand.exists():
            with cand.open("r", encoding="utf-8") as fp:
                return {"doc_id": doc_id, "source": str(cand), "data": json.load(fp)}
    logger.warning("No MinerU JSON output found under %s/%s/", output_dir, doc_id)
    return {"doc_id": doc_id, "pages": [], "_stub": True}
