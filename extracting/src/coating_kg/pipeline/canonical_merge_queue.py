"""Build a review queue for proposed canonical IDs.

The queue follows the conservative merge path:
exact/alias/trade-name/test-standard rules first, embedding/LLM only as
candidate generation/adjudication surfaces later, and human confirmation before
seed files or DB aliases are updated.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .canonical_resolver import CanonicalResolver


@dataclass
class MergeQueueItem:
    proposed_id: str
    canonical_name: str
    kind: str
    sub_type: str | None
    occurrences: int
    suggested_action: str
    matched_canonical_id: str | None = None
    rule: str | None = None
    confidence: str = "low"
    evidence_units: list[str] | None = None


def build_merge_queue(
    units_dir: Path,
    *,
    resolver: CanonicalResolver | None = None,
) -> dict[str, Any]:
    resolver = resolver or CanonicalResolver()
    proposed = _collect_proposed(units_dir)
    items: list[MergeQueueItem] = []
    for proposed_id, rows in proposed.items():
        best = _best_row(rows)
        name = str(best.get("canonical_name") or proposed_id)
        kind = str(best.get("kind") or _kind_from_id(proposed_id) or "")
        sub_type = best.get("sub_type")
        matched, rule = _rule_match(name, proposed_id, kind, resolver)
        if matched:
            action = "alias_candidate"
            confidence = "high" if rule in {"exact_id", "exact_name_or_alias", "test_standard"} else "medium"
        else:
            action = "new_canonical_candidate"
            confidence = "low"
        items.append(
            MergeQueueItem(
                proposed_id=proposed_id,
                canonical_name=name,
                kind=kind,
                sub_type=sub_type,
                occurrences=len(rows),
                suggested_action=action,
                matched_canonical_id=matched,
                rule=rule,
                confidence=confidence,
                evidence_units=sorted({str(r.get("unit_id") or "") for r in rows if r.get("unit_id")})[:20],
            )
        )

    return {
        "schema_version": "canonical_merge_queue_v1",
        "policy": {
            "exact_alias_trade_standard_rules": "auto-suggest only",
            "embedding": "candidate recall only",
            "llm": "pair adjudication only",
            "human": "required before alias/must_merge/forbidden_merge seed update",
        },
        "items": [asdict(item) for item in sorted(items, key=lambda x: (-x.occurrences, x.proposed_id))],
    }


def save_merge_queue(queue: dict[str, Any], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def _collect_proposed(units_dir: Path) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in sorted(units_dir.glob("U_*/proposed_canonicals.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        unit_id = path.parent.name
        for row in data.get("proposed_canonicals", []) or []:
            if not isinstance(row, dict):
                continue
            proposed_id = row.get("proposed_id") or row.get("canonical_id")
            if not proposed_id:
                continue
            enriched = dict(row)
            enriched["unit_id"] = unit_id
            out[str(proposed_id)].append(enriched)
    return out


def _best_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def score(row: dict[str, Any]) -> float:
        try:
            return float(row.get("confidence") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    return sorted(rows, key=score, reverse=True)[0]


def _kind_from_id(value: str) -> str | None:
    head = value.split("_", 1)[0]
    return head if head in {"MAT", "APP", "SUB", "PROP", "PROC", "TEST"} else None


def _rule_match(
    name: str,
    proposed_id: str,
    kind: str,
    resolver: CanonicalResolver,
) -> tuple[str | None, str | None]:
    if proposed_id in resolver.nodes_by_id:
        return proposed_id, "exact_id"
    resolved = resolver.resolve(name, kind=kind or None)
    if resolved:
        return resolved, "exact_name_or_alias"
    standard = _normalize_test_standard(name)
    if standard:
        resolved = resolver.resolve(standard, kind="TEST")
        if resolved:
            return resolved, "test_standard"
    trade = _trade_name_stem(name)
    if trade and trade != name.casefold():
        resolved = resolver.resolve(trade, kind=kind or None)
        if resolved:
            return resolved, "trade_name_stem"
    return None, None


def _normalize_test_standard(name: str) -> str | None:
    m = re.search(r"\b(ASTM|ISO|DIN)\s*-?\s*([A-Z]?\d{3,5})\b", name, re.I)
    if not m:
        return None
    return f"{m.group(1).upper()} {m.group(2).upper()}"


def _trade_name_stem(name: str) -> str | None:
    low = name.casefold()
    low = re.sub(r"\b(?:basf|dow|evonik|byk|tinuvin|irgacure)\b", "", low)
    low = re.sub(r"[^a-z0-9]+", " ", low).strip()
    return low or None
