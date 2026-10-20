"""Finite, source-qualified profile classification; no source writes or inference.

Query tokens alone use strip/casefold. Profile values and manifest source roots
use exact equality. Callers must handle unsupported inputs before applying any
partial normalization result, and must bind profiles to source-local candidates.
"""

from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any


REGISTRY_PATH = Path(__file__).resolve().parents[1] / "config" / "query_classification_registry.json"
AXES = (
    "application_domains", "coating_functions", "coating_layers",
    "supply_forms", "film_processes",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(f"invalid query classification registry: {message}")


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _token_index(tokens: dict[str, list[str]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for token, values in tokens.items():
        _require(_text(token), "empty query token")
        _require(isinstance(values, list) and bool(values) and all(_text(v) for v in values), f"invalid token outputs {token!r}")
        key = token.strip().casefold()
        _require(key not in result or result[key] == values, f"ambiguous query alias {token!r}")
        result[key] = values
    return result


def _axis_tokens(definition: dict[str, Any]) -> dict[str, list[str]]:
    tokens = {value: [value] for value in definition["values"]}
    for alias, value in definition["aliases"].items():
        _require(_text(value) and value in definition["values"], f"unknown alias output {value!r}")
        _require(alias not in tokens or tokens[alias] == [value], f"canonical alias collision {alias!r}")
        tokens[alias] = [value]
    return _token_index(tokens)


def _legacy_tokens(registry: dict[str, Any], field: str) -> dict[str, list[str]]:
    definition = registry["legacy_filters"][field]
    if definition.get("table_source") != "legacy_coating_tables":
        _require(isinstance(definition.get("tokens"), dict) and bool(definition["tokens"]), f"legacy tokens for {field}")
        return _token_index(definition["tokens"])
    tables = registry.get("legacy_coating_tables")
    _require(isinstance(tables, dict), "legacy coating tables")
    for name, key in (("aliases", "aliases"), ("multi_value", "expand"), ("concepts", "concepts")):
        _require(isinstance(tables.get(name), dict) and isinstance(tables[name].get(key), dict), f"legacy {name}")
    aliases = tables["aliases"]["aliases"]
    _require(all(_text(v) for v in aliases.values()), "legacy spelling outputs")
    concepts = tables["concepts"]["concepts"]
    concept_aliases = tables["concepts"].get("concept_aliases")
    _require(isinstance(concept_aliases, dict), "legacy concept aliases")
    for alias, target in concept_aliases.items():
        _require(isinstance(target, str) and target in concepts, "legacy concept alias target")
    result: dict[str, list[str]] = {}
    # Validate each table before merging so exact-key conflicts cannot disappear.
    for tokens in (
        {key: [value] for key, value in aliases.items()},
        tables["multi_value"]["expand"],
        concepts,
        {alias: concepts[target] for alias, target in concept_aliases.items()},
    ):
        for token, values in _token_index(tokens).items():
            _require(token not in result or result[token] == values, f"ambiguous query alias {token!r}")
            result[token] = values
    canonical = {v for values in result.values() for v in values}
    for value in canonical:
        result.setdefault(value.strip().casefold(), [value])
    applications = tables.get("applications_aliases")
    _require(isinstance(applications, dict) and all(_text(k) and isinstance(v, str) and v in canonical for k, v in applications.items()), "legacy applications aliases")
    return result


def _validate_registry(registry: Any) -> None:
    _require(isinstance(registry, dict), "expected an object")
    _require(type(registry.get("schema_version")) is int and registry["schema_version"] == 1, "schema_version")
    _require(_text(registry.get("version")), "version")
    axes = registry.get("axes")
    _require(isinstance(axes, dict) and set(axes) == set(AXES), "exactly five query axes required")
    for axis, definition in axes.items():
        _require(isinstance(definition, dict) and _text(definition.get("definition")), f"definition for {axis}")
        values = definition.get("values")
        _require(isinstance(values, dict) and bool(values), f"values for {axis}")
        _require(all(_text(k) and k == k.strip().casefold() and _text(v) for k, v in values.items()), f"canonical definitions for {axis}")
        _require(isinstance(definition.get("aliases"), dict), f"aliases for {axis}")
        _axis_tokens(definition)
    _require(isinstance(registry.get("property_families"), dict) and bool(registry["property_families"]), "nonempty property_families map required")
    for family, ids in registry["property_families"].items():
        _require(isinstance(ids, list) and bool(ids) and all(_text(value) for value in ids), f"invalid property family IDs for {family!r}")
    _require(_text(registry.get("property_families_definition")), "property family definition")
    sources = registry.get("sources")
    _require(isinstance(sources, dict) and bool(sources), "sources")
    _require(all(_text(k) and _text(v) and v.startswith("/") for k, v in sources.items()), "stable source roots")
    _require(len(set(sources.values())) == len(sources), "duplicate source roots")
    fields = registry.get("profile_fields")
    _require(isinstance(fields, dict) and bool(fields), "profile_fields")
    _require(all(_text(k) and v in ("effective", "historical", "unreviewed") for k, v in fields.items()), "profile field tiers")
    seen_ids: set[str] = set()
    seen_keys: set[tuple[str, str, str]] = set()
    backed = {axis: set() for axis in AXES}
    for collection, status in (("rules", "reviewed_label_mapping"), ("unmapped_rules", "reviewed_unmapped_decision")):
        rules = registry.get(collection)
        _require(isinstance(rules, list), collection)
        for rule in rules:
            _require(isinstance(rule, dict), f"invalid {collection} entry")
            rule_id = rule.get("id")
            _require(_text(rule_id) and rule_id not in seen_ids, "unique rule ID required")
            seen_ids.add(rule_id)
            _require(rule.get("review_status") == status, f"unreviewed rule {rule_id}")
            _require(_text(rule.get("reason")), f"reason for {rule_id}")
            field, raw = rule.get("field"), rule.get("raw")
            _require(_text(field) and fields.get(field) == "effective" and _text(raw), f"source field/value for {rule_id}")
            selected = rule.get("sources")
            _require(isinstance(selected, list) and bool(selected) and all(isinstance(s, str) and s in sources for s in selected), f"sources for {rule_id}")
            for source in selected:
                key = (sources[source], field, raw)
                _require(key not in seen_keys, f"duplicate/conflicting source rule {rule_id}")
                seen_keys.add(key)
            if collection == "unmapped_rules":
                _require("values" not in rule, f"unmapped rule cannot assign values: {rule_id}")
                continue
            outputs = rule.get("values")
            _require(isinstance(outputs, dict) and bool(outputs), f"outputs for {rule_id}")
            for axis, values in outputs.items():
                _require(axis in axes, f"unknown output axis {axis}")
                _require(isinstance(values, list) and bool(values) and all(isinstance(v, str) and v in axes[axis]["values"] for v in values), f"unknown output for {rule_id}")
                _require(len(set(values)) == len(values), f"duplicate outputs for {rule_id}")
                backed[axis].update(values)
    for axis in AXES:
        _require(backed[axis] == set(axes[axis]["values"]), f"unbacked canonical in {axis}")
    legacy = registry.get("legacy_filters")
    _require(isinstance(legacy, dict), "legacy_filters")
    for field, definition in legacy.items():
        _require(_text(field) and isinstance(definition, dict) and _text(definition.get("definition")), "legacy definition")
        _legacy_tokens(registry, field)


@lru_cache(maxsize=1)
def _load_registry() -> dict[str, Any]:
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    _validate_registry(registry)
    return registry


def load_registry() -> dict[str, Any]:
    """Return an independent copy of the validated, process-cached registry.

    Missing files, malformed JSON and invalid/conflicting entries raise errors.
    The file is package-relative, with no cwd, environment or remote dependency.
    """
    return deepcopy(_load_registry())


def axis_definitions() -> dict[str, Any]:
    """Return five axes, each with definition, values, definitions and aliases.

    values is list[str]; definitions is {canonical: description}; aliases is
    {exact_query_alias: canonical}. Canonical tokens also accept themselves.
    Property families and legacy inputs are separate namespaces, not new axes.
    """
    return {
        axis: {
            "definition": definition["definition"],
            "values": list(definition["values"]),
            "definitions": dict(definition["values"]),
            "aliases": dict(definition["aliases"]),
        }
        for axis in AXES
        for definition in [_load_registry()["axes"][axis]]
    }


def _catalogue(dimension: str | None) -> list[dict[str, Any]]:
    registry = _load_registry()
    dimensions = (*AXES, "property_families")
    if dimension is not None and dimension not in dimensions:
        raise ValueError(f"unknown classification dimension: {dimension!r}")
    items = []
    for axis in dimensions if dimension is None else (dimension,):
        if axis == "property_families":
            items.extend({
                "dimension": axis, "value": value,
                "definition": registry["property_families_definition"],
                "aliases": [], "ids": deepcopy(ids),
            } for value, ids in registry["property_families"].items())
        else:
            definition = registry["axes"][axis]
            items.extend({
                "dimension": axis, "value": value, "definition": description,
                "aliases": [alias for alias, target in definition["aliases"].items() if target == value],
            } for value, description in definition["values"].items())
    return items


def lookup(query: str, dimension: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Exact classification/alias lookup, or bounded catalogue browsing for ''.

    Returns {version, query, dimension, items, total, limit, truncated}. Items
    contain dimension/value/definition/aliases and property groups also have ids.
    No records, search execution, domain inference or partial-string suggestions.
    """
    if not isinstance(query, str):
        raise ValueError("classification query must be a string")
    if type(limit) is not int or limit < 0:
        raise ValueError("classification lookup limit must be a nonnegative integer")
    key = query.strip().casefold()
    items = [
        item for item in _catalogue(dimension)
        if not key or key in {v.strip().casefold() for v in [item["value"], *item["aliases"]]}
    ]
    return {
        "version": _load_registry()["version"], "query": query, "dimension": dimension,
        "items": items[:limit], "total": len(items), "limit": limit,
        "truncated": len(items) > limit,
    }


def _normalize(value: Any, tokens: dict[str, list[str]]) -> dict[str, Any]:
    result: dict[str, Any] = {"values": [], "unsupported": [], "adjustments": [], "candidates": []}
    if value is None:
        return result
    items = value if isinstance(value, list) else [value]
    for item in items:
        if not isinstance(item, str):
            result["unsupported"].append(deepcopy(item))
            result["adjustments"].append({"kind": "unsupported_shape", "input": deepcopy(item)})
            continue
        key = item.strip().casefold()
        resolved = tokens.get(key)
        if not resolved:
            result["unsupported"].append(item)
            if key:
                for candidate in lookup(item, limit=len(_catalogue(None)))["items"]:
                    if candidate not in result["candidates"]:
                        result["candidates"].append(candidate)
            continue
        if item != key:
            result["adjustments"].append({"kind": "query_normalized", "input": item, "normalized": key})
        if resolved != [key]:
            result["adjustments"].append({"kind": "alias_resolved", "input": item, "values": list(resolved)})
        for canonical in resolved:
            if canonical not in result["values"]:
                result["values"].append(canonical)
    return result


def normalize_axis(axis: str, value: Any) -> dict[str, Any]:
    """Normalize one new axis without migrating invalid input to another axis.

    unsupported preserves original values/types; adjustments is list[dict].
    candidates are exact matches in other registry dimensions, never automatic
    corrections. A nonempty unsupported list requires caller error handling.
    """
    axes = _load_registry()["axes"]
    if not isinstance(axis, str) or axis not in axes:
        return {
            "values": [], "unsupported": deepcopy(value if isinstance(value, list) else [value]),
            "adjustments": [{"kind": "unknown_axis", "axis": deepcopy(axis)}], "candidates": [],
        }
    return _normalize(value, _axis_tokens(axes[axis]))


def normalize_legacy_filter(field: str, value: Any) -> dict[str, Any]:
    """Optional compatibility receipt; outputs remain in the original old field.

    No old-to-new-axis equivalence is asserted. The caller must retain its legacy
    predicate and report mode='legacy'; unsupported values must not be dropped.
    """
    registry = _load_registry()
    legacy = registry["legacy_filters"]
    legacy_adjustments = []
    if not isinstance(field, str) or field not in legacy:
        result = {"values": [], "unsupported": deepcopy(value if isinstance(value, list) else [value]),
                  "adjustments": [{"kind": "unknown_legacy_field", "field": deepcopy(field)}], "candidates": []}
    else:
        result = _normalize(value, _legacy_tokens(registry, field))
        if legacy[field].get("table_source") == "legacy_coating_tables":
            aliases = registry["legacy_coating_tables"]["concepts"]["concept_aliases"]
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, str) and item.strip().casefold() in aliases:
                    key = item.strip().casefold()
                    legacy_adjustments.append(f"concept_alias_migrated:{key}->{aliases[key]}")
    return {"version": registry["version"], "mode": "legacy", "field": deepcopy(field),
            "legacy_adjustments": legacy_adjustments, **result}


def project_profile(profile: dict[str, Any], source_id: str) -> dict[str, Any]:
    """Project one profile from an exact manifest root, without mutating it.

    mappings/unmapped retain source_id, table, field, raw and list index. Matched
    rules add rule_id, review_status and reason; output values are stable unions.
    missing_axes means no positive reviewed assignment, not proven absence.
    No doc-ID joins, historical fallback or source-name defaults are performed.
    """
    if not isinstance(profile, dict) or not isinstance(source_id, str):
        raise TypeError("profile must be a dict and source_id must be a stable root string")
    registry = _load_registry()
    values: dict[str, list[str]] = {axis: [] for axis in AXES}
    mappings, unmapped = [], []
    known_source = source_id in registry["sources"].values()
    # Source keys are registry references; the caller supplies the manifest root.
    rules = {
        (registry["sources"][source], rule["field"], rule["raw"]): rule
        for rule in registry["rules"] + registry["unmapped_rules"]
        for source in rule["sources"]
    }
    if not known_source:
        unmapped.append({"source_id": source_id, "table": "patent_profiles.jsonl", "field": None,
                         "raw": None, "index": None, "reason": "unknown_source"})
    for field, tier in registry["profile_fields"].items():
        if field not in profile:
            continue
        raw = profile[field]
        base = {"source_id": source_id, "table": "patent_profiles.jsonl", "field": field}
        if tier != "effective":
            unmapped.append({**base, "raw": deepcopy(raw), "index": None,
                             "reason": "historical_not_projected" if tier == "historical" else "unreviewed_field"})
            continue
        entries = list(enumerate(raw)) if isinstance(raw, list) and raw else [(None, raw)]
        for index, item in entries:
            provenance = {**base, "raw": deepcopy(item), "index": index}
            reason = None
            if item is None or item == "" or item == []:
                reason = "missing_value"
            elif not isinstance(item, str):
                reason = "unsupported_shape"
            elif not known_source:
                reason = "unknown_source"
            if reason:
                unmapped.append({**provenance, "reason": reason})
                continue
            rule = rules.get((source_id, field, item))
            if rule is None:
                unmapped.append({**provenance, "reason": "unreviewed_value"})
                continue
            provenance.update(rule_id=rule["id"], review_status=rule["review_status"], reason=rule["reason"])
            if "values" not in rule:
                unmapped.append({**provenance, "detail": rule.get("detail", "")})
                continue
            for axis, outputs in rule["values"].items():
                for output in outputs:
                    if output not in values[axis]:
                        values[axis].append(output)
            mappings.append({**provenance, "values": deepcopy(rule["values"])})
    return {"version": registry["version"], "values": values, "mappings": mappings,
            "missing_axes": [axis for axis in AXES if not values[axis]], "unmapped": unmapped}
