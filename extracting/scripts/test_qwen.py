"""Smoke test for Qwen OpenAI-compatible API connectivity.

Tests both text and VLM endpoints. No DB, no MinerU.

Usage:
    python scripts/test_qwen.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure src is on path when run from project root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from coating_kg.config import SETTINGS  # noqa: E402

try:
    from openai import OpenAI
except ImportError:
    print("ERROR: openai package not installed. Run: pip install -r requirements.txt")
    sys.exit(1)


def main() -> None:
    if not SETTINGS.openai.api_key:
        print("ERROR: OPENAI_API_KEY not set in .env")
        sys.exit(1)

    client = OpenAI(
        api_key=SETTINGS.openai.api_key,
        base_url=SETTINGS.openai.base_url,
    )

    # === Test 1: configured text model ===
    print(f"[1/2] Testing text model: {SETTINGS.models.qwen_text}")
    print(f"      base_url: {SETTINGS.openai.base_url}")
    try:
        r = client.chat.completions.create(
            model=SETTINGS.models.qwen_text,
            messages=[{"role": "user", "content": "Reply with exactly: pong"}],
        )
        reply = r.choices[0].message.content
        print(f"      OK Reply: {reply}")
        if reply and "pong" in reply.lower():
            print("      OK Text endpoint")
        else:
            print("      WARN Unexpected reply (but API works)")
    except Exception as e:
        print(f"      ERROR Text endpoint failed: {e}")
        sys.exit(1)

    # === Test 2: configured VLM model ===
    print(f"\n[2/2] Testing VLM model: {SETTINGS.models.qwen_vl}")
    try:
        # Qwen-VL requires images with both dimensions > 10px, so build a 16x16 white PNG.
        import base64
        import struct
        import zlib

        def _white_png(size: int = 16) -> str:
            def chunk(tag: bytes, data: bytes) -> bytes:
                crc = zlib.crc32(tag + data) & 0xFFFFFFFF
                return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

            sig = b"\x89PNG\r\n\x1a\n"
            ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
            raw = b"".join(b"\x00" + b"\xff\xff\xff" * size for _ in range(size))
            idat = zlib.compress(raw)
            png = sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
            return base64.b64encode(png).decode("ascii")

        white_png_b64 = _white_png(16)
        r = client.chat.completions.create(
            model=SETTINGS.models.qwen_vl,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{white_png_b64}"
                            },
                        },
                        {"type": "text", "text": "What color is this image? One word."},
                    ],
                }
            ],
        )
        reply = r.choices[0].message.content
        print(f"      OK VLM reply: {reply}")
        print("      OK VLM endpoint")
    except Exception as e:
        print(f"      ERROR VLM endpoint failed: {e}")
        sys.exit(1)

    print("\nOK All Qwen API smoke tests passed")


if __name__ == "__main__":
    main()
