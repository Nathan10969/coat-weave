"""Smoke test for MinerU API connectivity.

Tests the URL-based submission path with the official demo PDF.
Does NOT require local file upload.

Usage:
    python scripts/smoke_test_mineru.py

Expected output:
    1. POST status 200 + a task_id
    2. Polls every 5s until done (~30-60s for the demo PDF)
    3. Downloads result ZIP and shows file listing
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Ensure src is on path when run from project root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from coating_kg.config import SETTINGS  # noqa: E402
from coating_kg.pipeline.pdf_layout import (  # noqa: E402
    download_result,
    submit_pdf_url,
    wait_for_completion,
)


DEMO_URL = "https://cdn-mineru.openxlab.org.cn/demo/example.pdf"


def main() -> None:
    if not SETTINGS.mineru.token:
        print("ERROR: MINERU_TOKEN not set in .env")
        sys.exit(1)

    print(f"[1/3] Submitting demo PDF to MinerU…")
    print(f"      URL: {DEMO_URL}")
    print(f"      model_version: vlm")

    task_id = submit_pdf_url(DEMO_URL, model_version="vlm")
    print(f"      ✓ task_id = {task_id}")

    print(f"\n[2/3] Polling task status (every 5s, max 600s)…")
    t0 = time.time()
    result = wait_for_completion(task_id, kind="task")
    elapsed = time.time() - t0
    print(f"      ✓ Done in {elapsed:.0f}s")
    print(f"      State: {result.get('state')}")

    # Try to find a downloadable URL in the result
    zip_url = (
        result.get("full_zip_url")
        or result.get("zip_url")
        or result.get("result_url")
    )
    if not zip_url:
        print(f"      ⚠ No zip_url found in response. Full payload:")
        print(f"      {result}")
        sys.exit(1)
    print(f"      zip url: {zip_url}")

    print(f"\n[3/3] Downloading and unpacking…")
    output_dir = Path(SETTINGS.mineru.output_dir) / "_smoke_test"
    extracted = download_result(zip_url, output_dir)
    print(f"      ✓ Unpacked to: {extracted}")

    # List a few files for verification
    files = sorted(extracted.rglob("*"))[:10]
    print(f"\n      First {min(10, len(files))} files in output:")
    for f in files:
        if f.is_file():
            print(f"        {f.relative_to(extracted)}  ({f.stat().st_size} bytes)")

    print(f"\n✅ MinerU API smoke test PASSED")


if __name__ == "__main__":
    main()
