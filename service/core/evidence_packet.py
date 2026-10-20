"""Lossless, source-scoped evidence interning and whole-sample batching."""
from __future__ import annotations

import copy
import json
from collections import OrderedDict
from typing import Any


def json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def source_key(item: dict) -> tuple[str, str]:
    metadata = item.get("metadata") or {}
    object_id = str(item.get("object_id") or "")
    parts = object_id.split("::")
    collection = item.get("collection_id") or metadata.get("collection_id")
    if not collection:
        collection = parts[0] if len(parts) >= 3 else "legacy"
    provenance = (item.get("provenance") or {}).get("hyperedge") or {}
    source = provenance.get("source_id")
    scope = json.dumps([collection, source], ensure_ascii=False) if source else str(collection)
    return scope, str(item.get("doc_id") or "")


def sample_key(item: dict) -> tuple[str, ...]:
    summary = item.get("hyperedge_summary") or {}
    raw = item.get("hyperedge_raw") or {}
    for field in ("sample_id", "context_id"):
        value = item.get(field) or summary.get(field) or raw.get(field)
        if value is not None and value != "":
            return (*source_key(item), field, str(value))
    # Unresolved identity stays separate; it must not count as a known sample.
    return (*source_key(item), "unresolved", str(item.get("object_id") or ""))


def expanded_items(packet: dict):
    for observation in packet.get("tool_observations") or []:
        if observation.get("tool") == "kg.expand_hyperedge_multihop":
            yield from observation.get("result", {}).get("items", [])


def pack_evidence(packet: dict) -> dict:
    result = copy.deepcopy(packet)
    registry: dict[str, dict] = {"facts": {}, "evidence": {}}
    owners: dict[tuple[str, str], set[str]] = {}
    conflicts: set[tuple[str, str]] = set()
    rows = list(expanded_items(result))
    for observation in result.get("tool_observations") or []:
        payload = observation.get("result") or {}
        if payload.get("exact_items") == payload.get("items"):
            payload.pop("exact_items", None)
    for item in rows:
        oid = str(item.get("object_id") or "")
        for kind, id_field in (("facts", "fact_id"), ("evidence", "evidence_id")):
            refs = []
            bindings = []
            for index, record in enumerate(item.pop(kind, []) or []):
                record_id = record.get(id_field) if isinstance(record, dict) else None
                identity = str(record_id) if record_id else f"anonymous:{oid}:{index}"
                record_source = (record.get("provenance") or {}).get("source_id") if isinstance(record, dict) else None
                ref = json.dumps([*source_key(item), record_source, identity], ensure_ascii=False, separators=(",", ":"))
                key = (kind, ref)
                owners.setdefault(key, set()).add(oid)
                normalized = copy.deepcopy(record)
                if isinstance(record, dict) and isinstance(record.get("raw"), dict):
                    # Source content defines equality; join decorations belong to
                    # each object's binding and may differ for the same source row.
                    normalized = copy.deepcopy(record["raw"])
                    bindings.append({"ref": ref, **{key: copy.deepcopy(record[key]) for key in
                        (id_field, "match_mode", "provenance", "origins") if key in record}})
                elif isinstance(normalized, dict) and "match_mode" in normalized:
                    bindings.append({"ref": ref, "match_mode": normalized.pop("match_mode")})
                if ref in registry[kind] and registry[kind][ref] != normalized:
                    conflicts.add(key)
                else:
                    registry[kind][ref] = normalized
                if ref not in refs:
                    refs.append(ref)
            item[kind + "_refs"] = refs
            if bindings:
                item[kind + "_bindings"] = bindings
    failed = set().union(*(owners[key] for key in conflicts)) if conflicts else set()
    for observation in result.get("tool_observations") or []:
        if observation.get("tool") != "kg.expand_hyperedge_multihop":
            continue
        payload = observation.get("result") or {}
        payload["items"] = [i for i in payload.get("items", []) if str(i.get("object_id") or "") not in failed]
    result["source_records"] = registry
    prior_failures = [copy.deepcopy(failure) for observation in result.get("tool_observations") or []
                      for failure in (observation.get("result") or {}).get("failed_objects", [])]
    result["evidence_delivery"] = {
        "input_objects": len(rows) + len(prior_failures),
        "verified_objects": len(rows) - sum(str(i.get("object_id") or "") in failed for i in rows),
        "distinct_samples": len({sample_key(i) for i in rows if sample_key(i)[2] != "unresolved" and str(i.get("object_id") or "") not in failed}),
        "failed_objects": prior_failures + [{"object_id": oid, "reason": "reference_conflict"} for oid in sorted(failed)],
    }
    return _subset(result, list(expanded_items(result)))


def _subset(packet: dict, selected: list[dict]) -> dict:
    output = copy.deepcopy(packet)
    identities = {str(i.get("object_id")) for i in selected}
    used = {"facts": set(), "evidence": set()}
    for observation in output.get("tool_observations") or []:
        if observation.get("tool") != "kg.expand_hyperedge_multihop":
            continue
        payload = observation.get("result") or {}
        payload["items"] = [i for i in payload.get("items", []) if str(i.get("object_id")) in identities]
        payload.pop("exact_items", None)
        for item in payload["items"]:
            for kind in used:
                used[kind].update(item.get(kind + "_refs") or [])
    output["source_records"] = {
        kind: {key: value for key, value in packet.get("source_records", {}).get(kind, {}).items() if key in used[kind]}
        for kind in used
    }
    return output


def synthesis_context(packet: dict) -> dict:
    """Keep current counts/scope/filters without a second copy of expanded data."""
    return _subset(packet, [])


def batch_transport(packet: dict, tag: str = "b") -> tuple[dict, dict]:
    """Use reversible short reference handles, never change source record IDs."""
    output = copy.deepcopy(packet)
    aliases = {}
    for kind, prefix in (("facts", "f"), ("evidence", "e")):
        records = output.get("source_records", {}).get(kind, {})
        forward = {ref: f"{tag}_{prefix}{index}" for index, ref in enumerate(records)}
        aliases[kind] = {short: ref for ref, short in forward.items()}
        output["source_records"][kind] = {forward[ref]: record for ref, record in records.items()}
        for item in expanded_items(output):
            item[kind + "_refs"] = [forward[ref] for ref in item.get(kind + "_refs", [])]
            for binding in item.get(kind + "_bindings", []):
                binding["ref"] = forward[binding["ref"]]
    output["source_ref_map"] = aliases
    return output, aliases


def estimated_summary_tokens(packet: dict) -> int:
    transport, _ = batch_transport(packet)
    rows = list(expanded_items(transport))
    minimum = {"summaries": [{"object_id": r["object_id"], "summary": "",
                             "facts_refs": r.get("facts_refs", []), "evidence_refs": r.get("evidence_refs", [])}
                            for r in rows]}
    # Conservative sizing heuristic, not a provider tokenizer guarantee.
    # Reserve prose per object, including multiple results for one sample.
    return (json_size(minimum) + 1) // 2 + 512 * len(rows)


def plan_batches(packet: dict, budget: int, output_tokens: int | None = None) -> dict:
    if budget < 0:
        raise ValueError("packet budget must be nonnegative")
    failed = copy.deepcopy(packet.get("evidence_delivery", {}).get("failed_objects") or [])
    # Zero delegates input capacity to the provider; bytes are not model tokens.
    if budget == 0 or json_size(packet) <= budget:
        return {"batches": [copy.deepcopy(packet)], "failed_objects": failed, "requires_batching": False}
    groups: OrderedDict[tuple, list] = OrderedDict()
    for item in expanded_items(packet):
        groups.setdefault(sample_key(item), []).append(item)
    batches, current = [], []
    def fits(candidate):
        return (json_size(candidate) <= budget
                and json_size(batch_transport(candidate)[0]) <= budget
                and (output_tokens is None or estimated_summary_tokens(candidate) <= output_tokens))
    for group in groups.values():
        single = _subset(packet, group)
        if not fits(single):
            failed.extend({"object_id": row.get("object_id"), "reason": "oversized_item",
                           "budget_axis": "input_bytes" if json_size(single) > budget else "summary_output_tokens"} for row in group)
            continue
        candidate = _subset(packet, current + group)
        if current and not fits(candidate):
            batches.append(_subset(packet, current))
            current = []
        current.extend(group)
    if current:
        batches.append(_subset(packet, current))
    if not groups:
        failed.append({"object_id": None, "reason": "oversized_context"})
    return {"batches": batches, "failed_objects": failed, "requires_batching": True}
