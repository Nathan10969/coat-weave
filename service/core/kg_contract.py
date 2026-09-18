from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any


DEFAULT_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "config" / "kg_tool_contract.json"


@lru_cache(maxsize=1)
def load_tool_contract() -> dict[str, Any]:
    path = Path(os.environ.get("KG_TOOL_CONTRACT_PATH", str(DEFAULT_CONTRACT_PATH)))
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("tools"), dict):
        raise ValueError(f"invalid KG tool contract: {path}")
    return value


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
    source_keys: dict[str, list[str]] = {}
    for key, raw_value in raw.items():
        if not has_value(raw_value):
            continue
        canonical = aliases.get(str(key), str(key))
        requested[str(key)] = raw_value
        if canonical not in allowed:
            if str(key) not in unsupported:
                unsupported.append(str(key))
            continue
        source_keys.setdefault(canonical, []).append(str(key))
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
        else:
            effective[field] = normalize_string_list(raw_value)

    return {
        "requested_filters": requested,
        "effective_filters": effective,
        "unsupported_constraints": sorted(unsupported),
        "route_adjustments": adjustments,
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
