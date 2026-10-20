"""Edge collection helpers for KG projection records."""

from __future__ import annotations

import hashlib
import json
from typing import Any

JSON = dict[str, Any]


class _EdgeSink:
    def __init__(self) -> None:
        self._records: dict[str, JSON] = {}

    def add(self, src: str, edge_type: str, dst: str, **properties: Any) -> None:
        if not src or not dst:
            return
        rec: JSON = {"src": src, "edge_type": edge_type, "dst": dst}
        clean_props = {k: v for k, v in properties.items() if v not in (None, "", [], {})}
        if clean_props:
            rec["properties"] = clean_props
        key = json.dumps(rec, ensure_ascii=False, sort_keys=True)
        rec["edge_id"] = "EDGE_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        self._records[rec["edge_id"]] = rec

    def records(self) -> list[JSON]:
        return sorted(self._records.values(), key=lambda row: str(row["edge_id"]))
