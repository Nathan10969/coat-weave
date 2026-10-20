from __future__ import annotations

import os
from pathlib import Path


ROOT = Path("/root/coating/coating_api_service")
REQUIRED = [
    ROOT / "api" / "main.py",
    ROOT / "core" / "routing.py",
    ROOT / "core" / "tool_runtime.py",
    ROOT / "kg_tools" / "kg_expand_http_service.py",
    ROOT / "systemd" / "coating-api.service",
    ROOT / "systemd" / "coating-kg-tools.service",
    ROOT / ".env",
]


def main() -> None:
    missing = [str(path) for path in REQUIRED if not path.exists()]
    if missing:
        print("missing required files:")
        for item in missing:
            print(f"- {item}")
        raise SystemExit(1)
    print("required coating_api_service files are present")
    print(f"COATING_API_TOKEN set: {bool(os.environ.get('COATING_API_TOKEN'))}")


if __name__ == "__main__":
    main()
