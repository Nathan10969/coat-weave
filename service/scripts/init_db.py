from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.config import CoatingApiSettings  # noqa: E402
from storage.repository import ServiceRepository  # noqa: E402


def main() -> None:
    settings = CoatingApiSettings.from_env()
    ServiceRepository(settings).init_schema()
    print("coating_api schema initialized")


if __name__ == "__main__":
    main()

