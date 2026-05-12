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


_MINERU_ATTEMPTS = 3
_DOWNLOAD_ATTEMPTS = 4


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
        "enable_formula": enable_formula,
        "enable_table": enable_table,
        "files": [
            {"name": pdf_path.name, "is_ocr": is_ocr},
        ],
    }
    resp = requests.post(
        _api("/file-urls/batch"),
        json=body,
        headers=_auth_headers({"Content-Type": "application/json"}),
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json().get("data") or {}
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

    last_exc: Exception | None = None
    for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
        try:
            resp = requests.get(result_url, timeout=120)
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                zf.extractall(output_dir)
            break
        except (requests.RequestException, zipfile.BadZipFile) as exc:
            last_exc = exc
            if attempt >= _DOWNLOAD_ATTEMPTS:
                raise
            delay = min(30, 2 ** attempt)
            logger.warning(
                "MinerU result download failed for %s (attempt %d/%d): %s; retrying in %ss",
                output_dir,
                attempt,
                _DOWNLOAD_ATTEMPTS,
                exc,
                delay,
            )
            time.sleep(delay)
    if last_exc is not None:
        logger.info("MinerU result download recovered for %s", output_dir)
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

    cached = _load_layout_json(output_dir, doc_id, warn=False)
    if cached.get("data") is not None:
        logger.info("using cached MinerU layout for %s", doc_id)
        return cached

    if not SETTINGS.mineru.token:
        logger.warning(
            "MINERU_TOKEN is empty; returning stub for %s. "
            "Populate .env to enable online extraction.",
            pdf_path,
        )
        return {"doc_id": doc_id, "pages": [], "_stub": True}

    last_exc: Exception | None = None
    for attempt in range(1, _MINERU_ATTEMPTS + 1):
        try:
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
            loaded = _load_layout_json(output_dir, doc_id)
            if loaded.get("data") is None:
                raise MinerUError(f"MinerU download did not contain a layout JSON for {doc_id}")
            return loaded
        except (MinerUError, requests.RequestException, zipfile.BadZipFile) as exc:
            last_exc = exc
            if attempt >= _MINERU_ATTEMPTS:
                raise
            delay = min(60, 5 * attempt)
            logger.warning(
                "MinerU parse failed for %s (attempt %d/%d): %s; retrying in %ss",
                doc_id,
                attempt,
                _MINERU_ATTEMPTS,
                exc,
                delay,
            )
            time.sleep(delay)
    raise MinerUError(f"MinerU parse failed for {doc_id}: {last_exc}")


# ----------------------------------------------------------------------
# Local-disk JSON loader (kept for downstream pipeline stages)
# ----------------------------------------------------------------------
def _load_layout_json(output_dir: Path, doc_id: str, *, warn: bool = True) -> dict[str, Any]:
    """Locate MinerU's content_list/layout JSON under the conventional path.

    The unpacked ZIP may sit at ``<output_dir>/<doc_id>/`` or
    ``<output_dir>/<doc_id>/auto/`` depending on the MinerU version, so
    we probe both.
    """
    candidates = [
        output_dir / doc_id / f"{doc_id}_content_list.json",
        output_dir / doc_id / f"{doc_id}_layout.json",
        output_dir / doc_id / f"{doc_id}_middle.json",
        output_dir / doc_id / "auto" / f"{doc_id}_content_list.json",
        output_dir / doc_id / "auto" / f"{doc_id}_layout.json",
        output_dir / doc_id / "auto" / f"{doc_id}_middle.json",
    ]
    doc_dir = output_dir / doc_id
    for pattern in (
        "*_content_list.json",
        "content_list.json",
        "layout.json",
        "*_layout.json",
        "*_middle.json",
    ):
        candidates.extend(sorted(doc_dir.glob(pattern)))
        candidates.extend(sorted((doc_dir / "auto").glob(pattern)))
    for cand in candidates:
        if cand.exists():
            with cand.open("r", encoding="utf-8") as fp:
                return {"doc_id": doc_id, "source": str(cand), "data": json.load(fp)}
    if warn:
        logger.warning("No MinerU JSON output found under %s/%s/", output_dir, doc_id)
    return {"doc_id": doc_id, "pages": [], "_stub": True}
