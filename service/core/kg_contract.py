from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any


DEFAULT_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "config" / "kg_tool_contract.json"
CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


@lru_cache(maxsize=1)
def load_tool_contract() -> dict[str, Any]:
    path = Path(os.environ.get("KG_TOOL_CONTRACT_PATH", str(DEFAULT_CONTRACT_PATH)))
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("tools"), dict):
        raise ValueError(f"invalid KG tool contract: {path}")
    return value


def _load_json_config(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


@lru_cache(maxsize=1)
def load_coating_family_aliases() -> dict[str, str]:
    raw = _load_json_config("coating_family_aliases.json")
    aliases = raw.get("aliases") if isinstance(raw.get("aliases"), dict) else raw
    return {
        str(k).strip().casefold(): str(v).strip().casefold()
        for k, v in (aliases or {}).items()
        if str(k).strip() and str(v).strip()
    }


@lru_cache(maxsize=1)
def load_coating_family_multi_value() -> dict[str, list[str]]:
    raw = _load_json_config("coating_family_multi_value.json")
    expand = raw.get("expand") if isinstance(raw.get("expand"), dict) else {}
    out: dict[str, list[str]] = {}
    for key, values in expand.items():
        cleaned = [str(v).strip().casefold() for v in (values or []) if str(v).strip()]
        if cleaned:
            out[str(key).strip().casefold()] = cleaned
    return out


@lru_cache(maxsize=1)
def load_coating_family_concepts() -> tuple[dict[str, list[str]], dict[str, str]]:
    raw = _load_json_config("coating_family_concepts.json")
    concepts_raw = raw.get("concepts") if isinstance(raw.get("concepts"), dict) else {}
    alias_raw = raw.get("concept_aliases") if isinstance(raw.get("concept_aliases"), dict) else {}
    concepts = {
        str(k).strip().casefold(): [str(v).strip().casefold() for v in (vals or []) if str(v).strip()]
        for k, vals in concepts_raw.items()
        if str(k).strip()
    }
    aliases = {
        str(k).strip().casefold(): str(v).strip().casefold()
        for k, v in alias_raw.items()
        if str(k).strip() and str(v).strip()
    }
    return concepts, aliases


@lru_cache(maxsize=1)
def load_material_resin_aliases() -> dict[str, str]:
    raw = _load_json_config("material_resin_aliases.json")
    aliases = raw.get("resin_system_aliases") if isinstance(raw.get("resin_system_aliases"), dict) else {}
    return {
        str(k).strip().casefold(): str(v).strip().casefold()
        for k, v in aliases.items()
        if str(k).strip() and str(v).strip()
    }


@lru_cache(maxsize=1)
def load_material_aliases() -> dict[str, list[str]]:
    raw = _load_json_config("material_aliases.json")
    aliases = raw.get("aliases") if isinstance(raw.get("aliases"), dict) else {}
    out: dict[str, list[str]] = {}
    for key, values in aliases.items():
        cleaned = normalize_string_list(values)
        if str(key).strip() and cleaned:
            out[str(key).strip().casefold()] = cleaned
    return out


def expand_material_values(values: list[str]) -> list[str]:
    """Expand closed material synonym table; always keep originals; no regex invention."""
    aliases = load_material_aliases()
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item).strip()
        if not text:
            continue
        candidates = aliases.get(text.casefold())
        if candidates:
            ordered = list(candidates)
            if text not in ordered:
                ordered = [text, *ordered]
        else:
            ordered = [text]
        for value in ordered:
            if value not in seen:
                seen.add(value)
                out.append(value)
    return out


def material_synonym_family_id(value: str) -> frozenset[str]:
    """Stable id for a closed synonym family; unknowns are singletons."""
    text = str(value or "").strip()
    if not text:
        return frozenset()
    aliases = load_material_aliases()
    members = aliases.get(text.casefold())
    if members:
        return frozenset(item.casefold() for item in members if str(item).strip())
    return frozenset([text.casefold()])


def material_group_family_ids(group: dict[str, Any]) -> frozenset[frozenset[str]]:
    families: set[frozenset[str]] = set()
    for item in group.get("values") or []:
        family = material_synonym_family_id(str(item))
        if family:
            families.add(family)
    return frozenset(families)


def merge_synonym_material_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse match=any groups that share a synonym family and the same roles.

    OR stays inside a group; AND remains across unrelated concepts/roles.
    """
    if not groups:
        return []
    merged: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        values = normalize_string_list(group.get("values"))
        roles = normalize_string_list(group.get("roles"))
        match = str(group.get("match") or "any").strip().casefold() or "any"
        candidate = {"values": values, "roles": roles, "match": match}
        if match != "any" or not values:
            merged.append(candidate)
            continue
        families = material_group_family_ids(candidate)
        role_key = tuple(roles)
        did_merge = False
        for existing in merged:
            if str(existing.get("match") or "any").casefold() != "any":
                continue
            if tuple(existing.get("roles") or []) != role_key:
                continue
            existing_families = material_group_family_ids(existing)
            if families.intersection(existing_families):
                existing["values"] = expand_material_values(
                    list(existing.get("values") or []) + values
                )
                did_merge = True
                break
        if not did_merge:
            candidate["values"] = expand_material_values(values)
            merged.append(candidate)
    return merged


def materials_from_material_groups(groups: list[dict[str, Any]]) -> list[str]:
    """Rebuild flat materials as a stable union of group values (idempotent)."""
    out: list[str] = []
    seen: set[str] = set()
    for group in groups:
        if not isinstance(group, dict):
            continue
        for item in group.get("values") or []:
            text = str(item).strip()
            if text and text not in seen:
                seen.add(text)
                out.append(text)
    return out


def normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def has_value(value: Any) -> bool:
    if isinstance(value, dict):
        return any(has_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(has_value(item) for item in value)
    return value not in (None, "", False)


def _alias_index(contract: dict[str, Any]) -> dict[str, str]:
    index: dict[str, str] = {}
    for canonical, aliases in (contract.get("filter_aliases") or {}).items():
        for alias in aliases or []:
            index[str(alias)] = str(canonical)
    return index


def normalize_material_roles(value: Any, contract: dict[str, Any] | None = None) -> tuple[list[str], bool]:
    contract = contract or load_tool_contract()
    config = contract.get("material_roles") or {}
    valid = {str(item).casefold() for item in config.get("values") or []}
    aliases = {str(key).casefold(): str(item).casefold() for key, item in (config.get("aliases") or {}).items()}
    out: list[str] = []
    rejected = False
    for item in normalize_string_list(value):
        role = aliases.get(item.casefold(), item.casefold())
        if role not in valid:
            rejected = True
            continue
        if role not in out:
            out.append(role)
    return out, rejected


def resolve_coating_family_token(token: str) -> tuple[list[str], list[str], list[str]]:
    """Return (canonical_values, route_adjustments, unsupported_tokens)."""
    raw = str(token or "").strip()
    if not raw:
        return [], [], []
    key = raw.casefold()
    adjustments: list[str] = []
    concepts, concept_aliases = load_coating_family_concepts()
    multi = load_coating_family_multi_value()
    aliases = load_coating_family_aliases()

    if key in concept_aliases:
        target = concept_aliases[key]
        adjustments.append(f"concept_alias_migrated:{key}->{target}")
        key = target
    if key in concepts:
        return list(concepts[key]), adjustments, []
    if key in multi:
        return list(multi[key]), adjustments, []
    if key in aliases:
        return [aliases[key]], adjustments, []
    canonical_values = set(aliases.values())
    for values in concepts.values():
        canonical_values.update(values)
    if key in canonical_values:
        return [key], adjustments, []
    return [], adjustments, [raw]


def normalize_coating_families(value: Any) -> tuple[list[str], list[str], list[str]]:
    values: list[str] = []
    adjustments: list[str] = []
    unsupported: list[str] = []
    for item in normalize_string_list(value):
        resolved, adj, bad = resolve_coating_family_token(item)
        adjustments.extend(adj)
        unsupported.extend(bad)
        for token in resolved:
            if token not in values:
                values.append(token)
    return values, adjustments, unsupported


_CN_COATING_CLASS_MAP = {
    "防污": "antifouling",
    "污损释放": "foul_release",
}


def _application_coating_class_tokens() -> set[str]:
    tokens = set(load_coating_family_aliases().keys())
    tokens.update(load_coating_family_multi_value().keys())
    tokens.update(load_coating_family_concepts()[0].keys())
    tokens.update(load_coating_family_aliases().values())
    tokens.update(_CN_COATING_CLASS_MAP.keys())
    return tokens


def migrate_applications_coating_classes(
    applications: list[str],
) -> tuple[list[str], list[str], list[str]]:
    kept: list[str] = []
    migrated: list[str] = []
    adjustments: list[str] = []
    class_tokens = _application_coating_class_tokens()
    for item in applications:
        key = item.casefold()
        token = _CN_COATING_CLASS_MAP.get(item, item)
        token_key = token.casefold()
        if key in class_tokens or token_key in class_tokens:
            resolved, adj, bad = resolve_coating_family_token(token)
            if resolved and not bad:
                for value in resolved:
                    if value not in migrated:
                        migrated.append(value)
                adjustments.extend(adj)
                adjustments.append(f"applications_migrated_to_coating_families:{item}")
                continue
        kept.append(item)
    return kept, migrated, adjustments


def normalize_material_groups(
    value: Any,
    contract: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str], list[str], list[str]]:
    """Return (groups, resin_systems_migrated, adjustments, unsupported)."""
    contract = contract or load_tool_contract()
    resin_aliases = load_material_resin_aliases()
    groups_out: list[dict[str, Any]] = []
    resin_migrated: list[str] = []
    adjustments: list[str] = []
    unsupported: list[str] = []

    if value is None:
        raw_groups: list[Any] = []
    elif isinstance(value, list):
        raw_groups = value
    else:
        raw_groups = [value]

    for group in raw_groups:
        if isinstance(group, str):
            group = {"values": [group], "match": "any"}
        if not isinstance(group, dict):
            unsupported.append("material_groups")
            continue
        values = normalize_string_list(group.get("values") or group.get("value"))
        roles, rejected = normalize_material_roles(group.get("roles") or group.get("role"), contract)
        if rejected:
            adjustments.append("material_groups_invalid_roles_removed")
        match = str(group.get("match") or "any").strip().casefold() or "any"
        if match not in {"any", "all"}:
            match = "any"
            adjustments.append("material_groups_match_defaulted_any")
        kept_values: list[str] = []
        for item in values:
            resin = resin_aliases.get(item.casefold())
            if resin:
                if resin not in resin_migrated:
                    resin_migrated.append(resin)
                adjustments.append(f"material_value_migrated_to_resin_systems:{item}->{resin}")
                continue
            kept_values.append(item)
        if not kept_values and not roles:
            if values:
                continue
            unsupported.append("material_groups")
            continue
        groups_out.append({"values": kept_values, "roles": roles, "match": match})
    groups_out = merge_synonym_material_groups(groups_out)
    return groups_out, resin_migrated, adjustments, unsupported


def legacy_materials_to_groups(materials: list[str]) -> list[dict[str, Any]]:
    # Keep one string per group here; synonym OR-merge happens in normalize_material_groups.
    return [{"values": [item], "roles": [], "match": "any"} for item in materials]


def normalize_filter_request(tool: str, value: Any) -> dict[str, Any]:
    contract = load_tool_contract()
    raw = value if isinstance(value, dict) else {}
    tool_config = (contract.get("tools") or {}).get(tool) or {}
    allowed = {str(item) for item in tool_config.get("filter_fields") or []}
    aliases = _alias_index(contract)
    field_types = contract.get("field_types") or {}

    requested: dict[str, Any] = {}
    unsupported: list[str] = []
    adjustments: list[str] = []
    canonical_values: dict[str, Any] = {}
    for key, raw_value in raw.items():
        if not has_value(raw_value):
            continue
        canonical = aliases.get(str(key), str(key))
        requested[str(key)] = raw_value
        if canonical not in allowed:
            if str(key) not in unsupported:
                unsupported.append(str(key))
            continue
        if canonical not in canonical_values:
            canonical_values[canonical] = raw_value

    effective: dict[str, Any] = {}
    for field in tool_config.get("filter_fields") or []:
        field = str(field)
        raw_value = canonical_values.get(field)
        field_type = field_types.get(field, "string_list")
        if field_type == "string":
            text = str(raw_value or "").strip()
            if field == "qa_policy":
                valid_qa = set(contract.get("qa_policy") or [])
                effective[field] = text if text in valid_qa else "include_all"
                if text and text not in valid_qa:
                    adjustments.append("qa_policy_invalid_defaulted")
            else:
                effective[field] = text
        elif field_type == "material_role_list":
            roles, rejected = normalize_material_roles(raw_value, contract)
            effective[field] = roles
            if rejected:
                adjustments.append("material_roles_invalid_values_removed")
        elif field_type == "material_group_list":
            groups, resin_migrated, group_adj, group_unsup = normalize_material_groups(raw_value, contract)
            effective[field] = groups
            adjustments.extend(group_adj)
            for item in group_unsup:
                if item not in unsupported:
                    unsupported.append(item)
            if resin_migrated:
                existing = normalize_string_list(
                    effective.get("resin_systems") or canonical_values.get("resin_systems")
                )
                for resin in resin_migrated:
                    if resin not in existing:
                        existing.append(resin)
                effective["resin_systems"] = existing
        else:
            effective[field] = normalize_string_list(raw_value)

    coating_values, coating_adj, coating_bad = normalize_coating_families(effective.get("coating_families"))
    adjustments.extend(coating_adj)
    effective["coating_families"] = coating_values
    for bad in coating_bad:
        token = f"coating_families:{bad}"
        if token not in unsupported:
            unsupported.append(token)
        adjustments.append(f"coating_families_unresolved:{bad}")

    apps = list(effective.get("applications") or [])
    kept_apps, migrated_cf, app_adj = migrate_applications_coating_classes(apps)
    if migrated_cf:
        adjustments.extend(app_adj)
        for value in migrated_cf:
            if value not in effective["coating_families"]:
                effective["coating_families"].append(value)
        effective["applications"] = kept_apps

    materials = list(effective.get("materials") or [])
    existing_groups = list(effective.get("material_groups") or [])
    if materials or existing_groups:
        legacy_groups = legacy_materials_to_groups(materials) if materials else []
        # Re-normalize existing groups + legacy materials together so synonym families
        # collapse to one OR group and a second normalize stays idempotent.
        groups, resin_migrated, group_adj, group_unsup = normalize_material_groups(
            existing_groups + legacy_groups,
            contract,
        )
        adjustments.extend(group_adj)
        for item in group_unsup:
            if item not in unsupported:
                unsupported.append(item)
        effective["material_groups"] = groups
        resin_aliases = load_material_resin_aliases()
        if resin_migrated:
            existing = list(effective.get("resin_systems") or [])
            for resin in resin_migrated:
                if resin not in existing:
                    existing.append(resin)
            effective["resin_systems"] = existing
            if materials:
                adjustments.append("legacy_materials_resolved_via_material_groups")
        if materials:
            # Flat materials mirror merged groups so re-feeding effective_filters is a no-op.
            effective["materials"] = materials_from_material_groups(groups)

    for key in ("coating_families", "publication_year", "material_groups"):
        if key in allowed and key not in effective:
            effective[key] = []

    return {
        "requested_filters": requested,
        "effective_filters": effective,
        "unsupported_constraints": sorted(unsupported),
        "route_adjustments": list(dict.fromkeys(adjustments)),
    }


def openai_filter_properties(tool: str) -> dict[str, Any]:
    contract = load_tool_contract()
    config = (contract.get("tools") or {}).get(tool) or {}
    properties: dict[str, Any] = {}
    role_values = (contract.get("material_roles") or {}).get("values") or []
    descriptions = contract.get("filter_descriptions") or {}
    for field in config.get("filter_fields") or []:
        field_type = (contract.get("field_types") or {}).get(field, "string_list")
        if field_type == "string":
            schema: dict[str, Any] = {"type": "string"}
        elif field_type == "material_group_list":
            schema = {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "values": {"type": "array", "items": {"type": "string"}},
                        "roles": {"type": "array", "items": {"type": "string", "enum": role_values}},
                        "match": {"type": "string", "enum": ["any", "all"]},
                    },
                    "additionalProperties": False,
                },
            }
        else:
            item_schema: dict[str, Any] = {"type": "string"}
            if field_type == "material_role_list":
                item_schema["enum"] = role_values
            schema = {"type": "array", "items": item_schema}
        if descriptions.get(field):
            schema["description"] = str(descriptions[field])
        properties[str(field)] = schema
    return properties


def openai_tool_parameters(tool: str) -> dict[str, Any]:
    """Build an OpenAI function parameter schema from the shared contract."""
    contract = load_tool_contract()
    tool_config = (contract.get("tools") or {}).get(tool) or {}
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, spec in (tool_config.get("parameters") or {}).items():
        field_type = str((spec or {}).get("type") or "string")
        if field_type == "filters":
            schema: dict[str, Any] = {
                "type": "object",
                "properties": openai_filter_properties(tool),
                "additionalProperties": False,
            }
        elif field_type == "string_list":
            schema = {"type": "array", "items": {"type": "string"}}
        elif field_type == "aggregate_group_list":
            schema = {
                "type": "array",
                "items": {"type": "string", "enum": list(contract["aggregate"]["group_by"])},
            }
        elif field_type == "doc_field_group_list":
            schema = {
                "type": "array",
                "items": {"type": "string", "enum": list(tool_config.get("allowed_groups") or [])},
            }
        elif field_type == "aggregate_intent":
            schema = {"type": "string", "enum": list(contract["aggregate"]["intents"])}
        elif field_type == "aggregate_target":
            schema = {"type": "string", "enum": list(contract["aggregate"]["targets"])}
        else:
            schema = {"type": field_type}
        for bound in ("minimum", "maximum"):
            if bound in spec:
                schema[bound] = spec[bound]
        properties[str(name)] = schema
        if spec.get("required"):
            required.append(str(name))
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
