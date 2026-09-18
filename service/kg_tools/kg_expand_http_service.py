from __future__ import annotations

import json
import os
import re
import sys
import threading
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


JOBS_DIR = Path(__file__).resolve().parent
if str(JOBS_DIR) not in sys.path:
    sys.path.insert(0, str(JOBS_DIR))
CORE_DIR = JOBS_DIR.parent / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

import expand_hyperedge_multihop as expand  # noqa: E402
from kg_contract import load_tool_contract, normalize_filter_request  # noqa: E402
from demo_text import doc_id_lookup_key  # noqa: E402


HOST = os.environ.get("KG_EXPAND_HOST", "0.0.0.0")
PORT = int(os.environ.get("KG_EXPAND_PORT", "8021"))
AUTH_TOKEN = os.environ.get("KG_EXPAND_TOKEN", "").strip()
MAX_OBJECT_IDS = int(os.environ.get("KG_EXPAND_MAX_OBJECT_IDS", "30"))
MAX_QUOTE_CHARS = int(os.environ.get("KG_EXPAND_MAX_QUOTE_CHARS", "900"))
EMBED_URL = os.environ.get("KG_HYBRID_EMBED_URL", "http://127.0.0.1:8010/embed")
KG_AGGREGATE_DIR = Path(
    os.environ.get(
        "KG_AGGREGATE_DIR",
        str(getattr(expand, "DEFAULT_KG_DIR", Path(__file__).resolve().parents[1] / "data" / "kg_aggregate")),
    )
)
KG_SOURCE_MANIFEST_PATH = Path(os.environ["KG_SOURCE_MANIFEST"]) if os.environ.get("KG_SOURCE_MANIFEST") else None
RETRIEVAL_CONFIG_PATH = Path(
    os.environ.get(
        "KG_HYBRID_CONFIG",
        str(Path(__file__).resolve().parents[1] / "config" / "hyperedge_retrieval_config.json"),
    )
)


def load_retrieval_config() -> dict[str, Any]:
    if not RETRIEVAL_CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(RETRIEVAL_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        traceback.print_exc()
        return {}


RETRIEVAL_CONFIG = load_retrieval_config()
FUSION_CONFIG = RETRIEVAL_CONFIG.get("fusion") if isinstance(RETRIEVAL_CONFIG.get("fusion"), dict) else {}
RETRIEVAL_DEFAULTS = (
    RETRIEVAL_CONFIG.get("retrieval") if isinstance(RETRIEVAL_CONFIG.get("retrieval"), dict) else {}
)
DEFAULT_FUSION_NAME = os.environ.get("KG_HYBRID_FUSION", str(FUSION_CONFIG.get("name") or "hybrid_weighted_d10_s90"))
DEFAULT_FUSION_MODE = os.environ.get("KG_HYBRID_FUSION_MODE", str(FUSION_CONFIG.get("mode") or "hybrid_weighted"))
DEFAULT_DENSE_WEIGHT = float(os.environ.get("KG_HYBRID_DENSE_WEIGHT", FUSION_CONFIG.get("dense_weight", 0.1)))
DEFAULT_SPARSE_WEIGHT = float(os.environ.get("KG_HYBRID_SPARSE_WEIGHT", FUSION_CONFIG.get("sparse_weight", 0.9)))
DEFAULT_SEARCH_TOP_K = int(os.environ.get("KG_HYBRID_TOP_K", str(RETRIEVAL_DEFAULTS.get("top_k", 20))))
DEFAULT_SEARCH_CANDIDATE_K = int(os.environ.get("KG_HYBRID_CANDIDATE_K", str(RETRIEVAL_DEFAULTS.get("candidate_k", 100))))
DEFAULT_RRF_K = int(os.environ.get("KG_HYBRID_RRF_K", str(FUSION_CONFIG.get("rrf_k") or 60)))
MAX_SEARCH_TOP_K = int(os.environ.get("KG_HYBRID_MAX_TOP_K", "50"))
MAX_SEARCH_CANDIDATE_K = int(os.environ.get("KG_HYBRID_MAX_CANDIDATE_K", "500"))
MAX_AGGREGATE_EXAMPLES_PER_ITEM = int(os.environ.get("KG_AGGREGATE_MAX_EXAMPLES_PER_ITEM", "4"))
KG_TOOL_CONTRACT = load_tool_contract()
AGGREGATE_INTENTS = set(KG_TOOL_CONTRACT["aggregate"]["intents"])
AGGREGATE_TARGETS = set(KG_TOOL_CONTRACT["aggregate"]["targets"])
AGGREGATE_GROUP_BY = set(KG_TOOL_CONTRACT["aggregate"]["group_by"])
_ROLE_CONFIG = KG_TOOL_CONTRACT["material_roles"]
VALID_MATERIAL_ROLES = frozenset(_ROLE_CONFIG["values"])
MATERIAL_ROLES = tuple(dict.fromkeys([*_ROLE_CONFIG["values"], *_ROLE_CONFIG["aliases"].keys()]))
MAX_AGGREGATE_LIMIT = int(os.environ.get("KG_AGGREGATE_MAX_LIMIT", "200"))

_STORE = None
_STORE_LOCK = threading.Lock()
_FACTS_BY_CONTEXT_CACHE: dict[int, tuple[int, dict[tuple[str, str], list[dict[str, Any]]]]] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_kg_sources() -> tuple[tuple[expand.KgDirectorySource, ...], tuple[Path, ...]]:
    if KG_SOURCE_MANIFEST_PATH is not None:
        return load_kg_source_manifest(KG_SOURCE_MANIFEST_PATH), ()
    if KG_AGGREGATE_DIR.exists():
        return (expand.KgDirectorySource(KG_AGGREGATE_DIR),), ()
    default_dir = getattr(expand, "DEFAULT_KG_DIR", None)
    if isinstance(default_dir, Path) and default_dir.exists():
        return (expand.KgDirectorySource(default_dir),), ()
    return (), tuple(expand.DEFAULT_KG_ZIPS)


def load_kg_source_manifest(path: Path) -> tuple[expand.KgDirectorySource, ...]:
    if not path.is_file():
        raise FileNotFoundError(f"KG source manifest not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        raise ValueError(f"KG source manifest must contain a sources list: {path}")
    sources: list[expand.KgDirectorySource] = []
    for item in data["sources"]:
        if not isinstance(item, dict):
            raise ValueError(f"invalid KG source entry: {item!r}")
        source_path = Path(str(item.get("root") or "").strip())
        collection_id = str(item.get("collection_id") or "").strip() or None
        company = str(item.get("company") or "").strip() or None
        id_schema_version = str(item.get("id_schema_version") or "").strip() or None
        if not source_path.is_dir():
            raise FileNotFoundError(f"KG source root not found: {source_path}")
        if company and not collection_id:
            raise ValueError(f"company requires collection_id: {item!r}")
        if id_schema_version == "retrieval_object_v2":
            if not collection_id or company:
                raise ValueError(f"v2 KG source requires collection_id and forbids company: {item!r}")
        elif collection_id and not company:
            raise ValueError(f"legacy KG source requires collection_id and company: {item!r}")
        elif id_schema_version:
            raise ValueError(f"unsupported id_schema_version: {item!r}")
        sources.append(expand.KgDirectorySource(source_path, collection_id, company, id_schema_version))
    if not sources:
        raise ValueError(f"KG source manifest has no sources: {path}")
    return tuple(sources)


def get_store() -> Any:
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                kg_dirs, kg_zips = resolve_kg_sources()
                _STORE = expand.load_kg_store(kg_dirs, kg_zips)
    return _STORE


def unique_strings(values: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    if not isinstance(values, list):
        return out
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def compact_text(value: Any, limit: int = MAX_QUOTE_CHARS) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def pick(raw: dict[str, Any] | None, *keys: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {key: raw.get(key) for key in keys if raw.get(key) is not None}


def compact_materials(raw_hyperedge: dict[str, Any]) -> list[dict[str, Any]]:
    materials: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for role in MATERIAL_ROLES:
        rows = raw_hyperedge.get(role) or []
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            amount_raw = row.get("amount")
            amount = amount_raw if isinstance(amount_raw, dict) else {}
            value = amount.get("value") if amount else amount_raw
            if value is None:
                value = row.get("value")
            unit = amount.get("unit") if amount else row.get("unit")
            material = (
                row.get("material")
                or row.get("source_label")
                or row.get("name")
                or row.get("label")
            )
            normalized_role = normalize_material_role_name(row.get("role") or role)
            item = {
                "role": normalized_role,
                "material": material,
                "canonical_id": row.get("canonical_id"),
                "value": value,
                "unit": unit,
            }
            marker = (normalized_role, material, row.get("canonical_id"), value, unit)
            if marker not in seen:
                seen.add(marker)
                materials.append(item)
    return materials


def compact_entity_label(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("label") or value.get("name") or value.get("value") or value.get("id")
    if isinstance(value, list):
        return [label for label in (compact_entity_label(item) for item in value) if label is not None]
    return value


def compact_entity_canonical_id(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("canonical_id") or value.get("id")
    if isinstance(value, list):
        return [
            canonical_id
            for canonical_id in (compact_entity_canonical_id(item) for item in value)
            if canonical_id is not None
        ]
    return None


def compact_test_standard(raw: dict[str, Any], db_meta: dict[str, Any], test_method: Any) -> Any:
    direct = raw.get("test_standard") or raw.get("test_standards") or db_meta.get("test_standard")
    if direct:
        return direct
    if isinstance(test_method, dict):
        return test_method.get("standard_id") or test_method.get("standards")
    return None


def compact_hyperedge_summary(item: dict[str, Any]) -> dict[str, Any]:
    raw = ((item.get("hyperedge") or {}).get("raw") or {}) if isinstance(item.get("hyperedge"), dict) else {}
    db_meta = ((item.get("db_hyperedge") or {}).get("metadata") or {}) if isinstance(item.get("db_hyperedge"), dict) else {}
    profile = ((item.get("patent_profile") or {}).get("raw") or {}) if isinstance(item.get("patent_profile"), dict) else {}
    substrate = raw.get("substrate") if isinstance(raw.get("substrate"), dict) else {}
    test_method = raw.get("test_method")
    application = raw.get("application") or raw.get("applications") or db_meta.get("application")
    result = raw.get("result")
    return {
        "sample_id": raw.get("sample_id") or db_meta.get("sample_id"),
        "context_id": raw.get("context_id") or db_meta.get("context_id"),
        "example_id": raw.get("example_id") or db_meta.get("example_id"),
        "example_kind": raw.get("example_kind") or db_meta.get("example_kind"),
        "polarity": raw.get("polarity") or db_meta.get("polarity"),
        "application": compact_entity_label(application),
        "application_canonical_id": compact_entity_canonical_id(application),
        "application_family": raw.get("application_family") or db_meta.get("application_family") or profile.get("application_family"),
        "materials": compact_materials(raw),
        "resin_system": raw.get("resin_system") or raw.get("binder_family") or profile.get("binder_family"),
        "substrate": substrate.get("label") or raw.get("substrate") or db_meta.get("substrate"),
        "substrate_canonical_id": substrate.get("canonical_id"),
        "property": raw.get("property") or db_meta.get("property"),
        "property_canonical_id": raw.get("property_canonical_id") or db_meta.get("property_canonical_id"),
        "test_method": test_method,
        "test_standard": compact_test_standard(raw, db_meta, test_method),
        "test_condition": raw.get("test_condition") or db_meta.get("test_condition"),
        "result": result,
        "process": raw.get("process"),
        "baseline": raw.get("baseline"),
        "evidence_ids": raw.get("evidence_ids") or db_meta.get("evidence_ids") or [],
        "fact_ids": raw.get("fact_ids") or db_meta.get("fact_ids") or [],
        "context_provenance": raw.get("context_provenance") or raw.get("provenance"),
        "qa_flags": raw.get("qa_flags") or [],
        "unresolved_fields": raw.get("unresolved_fields") or raw.get("unresolved") or [],
    }


def compact_facts(item: dict[str, Any], max_facts: int) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for fact in (item.get("facts") or [])[: max(max_facts, 0)]:
        raw = fact.get("raw") if isinstance(fact, dict) else {}
        if not isinstance(raw, dict):
            raw = {}
        facts.append(
            {
                "fact_id": fact.get("fact_id") or raw.get("fact_id") or raw.get("id"),
                "match_mode": fact.get("match_mode"),
                "slot": raw.get("slot"),
                "material": raw.get("material"),
                "material_role": raw.get("material_role"),
                "material_canonical_id": raw.get("material_canonical_id"),
                "value": raw.get("value"),
                "value_text": raw.get("value_text"),
                "unit": raw.get("unit"),
                "property": raw.get("property"),
                "evidence_id": raw.get("evidence_id"),
                "context_id": raw.get("context_id"),
                "qa_flags": raw.get("qa_flags") or [],
            }
        )
    return facts


def compact_evidence(item: dict[str, Any], max_evidence: int) -> list[dict[str, Any]]:
    evidence_rows: list[dict[str, Any]] = []
    for evidence in (item.get("evidence") or [])[: max(max_evidence, 0)]:
        raw = evidence.get("raw") if isinstance(evidence, dict) else {}
        if not isinstance(raw, dict):
            raw = {}
        evidence_rows.append(
            {
                "evidence_id": evidence.get("evidence_id") or raw.get("evidence_id") or raw.get("id"),
                "match_mode": evidence.get("match_mode"),
                "page": raw.get("page"),
                "section": raw.get("section"),
                "table": raw.get("table"),
                "row_label": raw.get("row_label"),
                "source_block_id": raw.get("source_block_id"),
                "evidence_type": raw.get("evidence_type"),
                "quote": compact_text(raw.get("quote")),
                "source_image": raw.get("source_image"),
                "asset_path": raw.get("asset_path"),
                "bbox": raw.get("bbox"),
                "qa_flags": raw.get("qa_flags") or [],
            }
        )
    return evidence_rows


def compact_patent(raw: dict[str, Any] | None) -> dict[str, Any]:
    data = pick(
        raw,
        "title",
        "publication_number",
        "publication_date",
        "doc_id",
        "patent_id",
        "wo_id",
        "language",
        "ipc_codes",
        "examples_page_range",
    )
    applicants = raw.get("applicants") if isinstance(raw, dict) else None
    assignee = raw.get("assignee") if isinstance(raw, dict) else None
    if applicants is not None:
        data["applicants"] = applicants
        data["assignee"] = applicants[0] if isinstance(applicants, list) and applicants else applicants
    elif assignee is not None:
        data["assignee"] = assignee
    return data


def compact_patent_profile(raw: dict[str, Any] | None) -> dict[str, Any]:
    return pick(
        raw,
        "profile_id",
        "binder_family",
        "coating_family",
        "cure_mechanism",
        "application_family",
        "substrate_scope",
    )


def compact_output(raw_payload: dict[str, Any], *, max_context_facts: int, max_evidence_per_item: int) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    partial = bool(raw_payload.get("summary", {}).get("db_missing"))
    for item in raw_payload.get("results") or []:
        hyperedge = item.get("hyperedge") if isinstance(item.get("hyperedge"), dict) else {}
        raw_hyperedge = hyperedge.get("raw") if isinstance(hyperedge.get("raw"), dict) else {}
        patent = item.get("patent") if isinstance(item.get("patent"), dict) else {}
        patent_profile = item.get("patent_profile") if isinstance(item.get("patent_profile"), dict) else {}
        unresolved = item.get("unresolved") or {"fact_ids": [], "evidence_ids": []}
        if unresolved.get("fact_ids") or unresolved.get("evidence_ids") or not hyperedge.get("found"):
            partial = True
        items.append(
            {
                "object_id": item.get("object_id"),
                "doc_id": item.get("doc_id"),
                "hyperedge_id": raw_hyperedge.get("hyperedge_id") or hyperedge.get("raw_hyperedge_id"),
                "hyperedge_summary": compact_hyperedge_summary(item),
                "facts": compact_facts(item, max_context_facts),
                "evidence": compact_evidence(item, max_evidence_per_item),
                "patent": compact_patent(patent.get("raw") if isinstance(patent, dict) else None),
                "patent_profile": compact_patent_profile(
                    patent_profile.get("raw") if isinstance(patent_profile, dict) else None
                ),
                "unresolved": {
                    "fact_ids": unresolved.get("fact_ids") or [],
                    "evidence_ids": unresolved.get("evidence_ids") or [],
                },
            }
        )

    summary = raw_payload.get("summary") or {}
    if summary.get("db_found", 0) == 0:
        status = "not_found"
    elif partial:
        status = "partial"
    else:
        status = "ok"
    return {
        "tool": "kg.expand_hyperedge_multihop",
        "status": status,
        "generated_at": now_iso(),
        "summary": summary,
        "items": items,
    }


def run_expand(request: dict[str, Any]) -> dict[str, Any]:
    object_ids = unique_strings(request.get("object_ids"))
    if not object_ids:
        return {
            "tool": "kg.expand_hyperedge_multihop",
            "status": "error",
            "error": "object_ids must be a non-empty list",
            "summary": {"requested": 0, "db_found": 0, "db_missing": 0},
            "items": [],
        }
    object_ids = object_ids[:MAX_OBJECT_IDS]
    max_context_facts = int(request.get("max_context_facts") or 20)
    max_evidence_per_item = int(request.get("max_evidence_per_item") or 5)
    pg_env = expand.load_pg_env(expand.DEFAULT_PG_ENV)
    kg_dirs, kg_zips = resolve_kg_sources()
    store = get_store()
    with expand.pg_connect(pg_env) as conn:
        db_rows = expand.fetch_db_hyperedges(conn, object_ids)
    raw_payload = expand.build_output(
        object_ids=object_ids,
        db_rows=db_rows,
        store=store,
        kg_dirs=tuple(source.path for source in kg_dirs),
        kg_zips=kg_zips,
        pg_env=expand.DEFAULT_PG_ENV,
        max_context_facts=max_context_facts,
    )
    return compact_output(
        raw_payload,
        max_context_facts=max_context_facts,
        max_evidence_per_item=max_evidence_per_item,
    )


def int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clamp_int(value: Any, default: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, int_or_default(value, default)))


def normalize_filter_values(value: Any) -> list[str]:
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


SEARCH_SOFT_FILTER_ALIASES: dict[str, tuple[str, ...]] = {
    key: tuple(values)
    for key, values in (KG_TOOL_CONTRACT.get("filter_aliases") or {}).items()
}
AGGREGATE_APPLIED_FILTER_ORDER = tuple(KG_TOOL_CONTRACT["tools"]["kg.sql_aggregate"]["filter_fields"])
AGGREGATE_FILTER_INPUT_KEYS = {
    alias for aliases in SEARCH_SOFT_FILTER_ALIASES.values() for alias in aliases
}
AGGREGATE_FIELD_FILTER_CONFIG = {
    "applications": {
        "keys": ("application", "applications"),
        "context_field": "application",
    },
    "substrates": {
        "keys": ("substrate", "substrates"),
        "context_field": "substrate",
    },
    "test_methods": {
        "keys": ("test_method", "test_methods"),
        "context_field": "test_method",
    },
    "test_standards": {
        "keys": ("test_standard", "test_standards", "standards"),
        "context_field": "test_standard",
    },
}

PROPERTY_FAMILY_CANONICAL_IDS: dict[str, tuple[str, ...]] = {
    "marine_antifouling": (
        "PROP_antifouling_raft_singapore",
        "PROP_antifouling_performance",
        "PROP_anti_fouling_performance",
        "PROP_antifouling_performance_raft",
        "PROP_antifouling_raft_test",
        "PROP_long_term_antifouling",
        "PROP_biofouling_coverage",
        "PROP_antifouling_static",
        "PROP_antifouling_dynamic",
        "PROP_biofouling_resistance",
        "PROP_antifouling_raft_spain",
        "PROP_antifouling_rating_florida_16weeks",
        "PROP_antifouling_rating_florida_8weeks",
        "PROP_antifouling_rating_sandefjord_rotor_18weeks",
        "PROP_antifouling_animal_fouling_score_spain_23wk",
        "PROP_antifouling_animal_fouling_score_singapore_16wk",
        "PROP_antifouling_slime_rating_singapore_32weeks",
        "PROP_antifouling_algae_rating_singapore_32weeks",
        "PROP_antifouling_animals_rating_singapore_32weeks",
        "PROP_efficacy_against_macro_fouling",
        "PROP_marine_fouling_resistance",
        "PROP_weed_fouling_coverage",
        "PROP_antifouling_microfouling_coverage_6mo",
        "PROP_antifouling_microfouling_coverage_12mo",
        "PROP_animal_fouling_coverage",
        "PROP_antifouling_coverage",
        "PROP_biofouling_field_performance",
    ),
    "marine_fouling_release": (
        "PROP_fouling_release_performance",
        "PROP_fouling_rating",
        "PROP_antifouling_total_fouling",
        "PROP_antifouling_soft_fouling",
        "PROP_antifouling_hard_fouling",
        "PROP_fouling_release_performance_soft",
        "PROP_fouling_release_performance_hard",
        "PROP_fouling_coverage_changi_44wk",
        "PROP_fouling_coverage_changi_35wk",
        "PROP_fouling_release_at_1p5_mps_80d",
        "PROP_fouling_release_at_2p6_mps_80d",
        "PROP_fouling_release_at_1p5_mps_7d",
        "PROP_fouling_release_at_2p6_mps_7d",
        "PROP_fouling_release_performance_raft_singapore_40weeks",
        "PROP_fouling_release_performance_raft_singapore_63weeks",
        "PROP_fouling_release_performance_raft_sandefjord_65weeks",
    ),
    "marine_corrosion": (
        "PROP_corrosion_resistance",
        "PROP_corrosion_resistance_salt_spray",
        "PROP_salt_spray_resistance",
        "PROP_salt_spray_corrosion_resistance",
        "PROP_nss_corrosion_width",
        "PROP_cathodic_delamination",
        "PROP_corrosion_protection",
        "PROP_corrosion_creep",
        "PROP_corrosion_rating",
    ),
    "marine_immersion_durability": (
        "PROP_blistering_fw_immersion",
        "PROP_cracking_sw_immersion",
        "PROP_wet_adhesion_seawater",
        "PROP_water_immersion_pass",
        "PROP_immersion_adhesion",
    ),
    "marine_adhesion": (
        "PROP_adhesion",
        "PROP_ADHESION",
        "PROP_cross_hatch_adhesion",
        "PROP_adhesion_rating",
        "PROP_wet_adhesion_seawater",
        "PROP_immersion_adhesion",
    ),
    "marine_mechanical_durability": (
        "PROP_abrasion_resistance",
        "PROP_CHIPPING_RESISTANCE",
    ),
    "marine_weathering_uv": (
        "PROP_gloss_60",
        "PROP_GLOSS",
        "PROP_total_solar_reflectance",
    ),
    "marine_repair_constructability": (
        "PROP_tack_free_time",
    ),
    "marine_tank_chemical": (
        "PROP_acid_resistance",
    ),
}


INVALID_EXAMPLE_KIND_FILTER_VALUES = {
    "formulation",
    "formula",
    "recipe",
    "coating system",
    "coating_system",
    "paint system",
    "paint_system",
}


def normalize_example_kind_filter(value: Any) -> list[str]:
    return [
        item
        for item in normalize_filter_values(value)
        if item.strip().lower() not in INVALID_EXAMPLE_KIND_FILTER_VALUES
    ]


def first_present_filter_value(raw: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for key in aliases:
        if raw.get(key) is not None:
            return raw.get(key)
    return None


def normalize_search_filters(raw: Any) -> dict[str, Any]:
    filters = normalize_filter_request("kg.hybrid_search", raw)["effective_filters"]
    filters["example_kind"] = normalize_example_kind_filter(filters.get("example_kind"))
    return filters


def normalize_aggregate_filters(raw: Any) -> dict[str, Any]:
    filters = normalize_filter_request("kg.sql_aggregate", raw)["effective_filters"]
    filters["example_kind"] = normalize_example_kind_filter(filters.get("example_kind"))
    return filters


def has_filter_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, list):
        return any(has_filter_value(item) for item in value)
    if isinstance(value, dict):
        return any(has_filter_value(item) for item in value.values())
    return bool(str(value).strip())


def applied_aggregate_filters(filters: dict[str, Any]) -> list[str]:
    return [key for key in AGGREGATE_APPLIED_FILTER_ORDER if has_filter_value(filters.get(key))]


def ignored_aggregate_filters(raw: Any, filters: dict[str, Any]) -> list[str]:
    raw = raw if isinstance(raw, dict) else {}
    ignored: list[str] = []
    if filters.get("qa_policy") != "include_all":
        ignored.append("qa_policy")
    for key in sorted(raw):
        if key == "qa_policy":
            continue
        if has_filter_value(raw.get(key)) and key not in AGGREGATE_FILTER_INPUT_KEYS:
            ignored.append(key)
    return ignored


def slug_id(prefix: str, value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    slug = "".join(ch if ch.isalnum() else "_" for ch in text.upper()).strip("_")
    return f"{prefix}_{slug}" if slug else None


def lower_set(values: list[str]) -> set[str]:
    return {str(value).strip().casefold() for value in values if str(value).strip()}


MARINE_APPLICATION_ALIASES = {
    "marine",
    "marine_antifouling",
    "ship_hull",
    "vessel_hull",
    "boat_hull",
    "yacht_hull",
    "underwater_structure",
    "underwater structures",
    "fixed_marine_structure",
    "marine_vessel_hull",
    "fouling_release_marine",
    "marine foul release coating",
    "marine biofouling control",
    "shipbuilding",
    "offshore",
}


def application_family_matches(wanted: set[str], actual_items: list[dict[str, Any]]) -> bool:
    actual = {
        str(item.get("value") or item.get("canonical_id") or "").strip().lower()
        for item in actual_items
        if str(item.get("value") or item.get("canonical_id") or "").strip()
    }
    if not wanted:
        return True
    if actual.intersection(wanted):
        return True
    if "marine" in wanted:
        return any(value in MARINE_APPLICATION_ALIASES or "marine" in value for value in actual)
    return False


def normalize_match_text(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    text = re.sub(r"[_\W]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def match_text_variants(value: Any) -> list[str]:
    normalized = normalize_match_text(value)
    if not normalized:
        return []
    compacted = re.sub(r"\s+", "", normalized)
    if compacted == normalized:
        return [normalized]
    return [normalized, compacted]


def item_matches_filter(item: dict[str, Any], wanted: set[str]) -> bool:
    actual: list[str] = []
    for key in ("value", "canonical_id"):
        actual.extend(match_text_variants(item.get(key)))
    wanted_norm: list[str] = []
    for value in wanted:
        wanted_norm.extend(match_text_variants(value))
    if not wanted_norm:
        return True
    if not wanted:
        return True
    for value in actual:
        if any(needle == value or needle in value or value in needle for needle in wanted_norm):
            return True
    return False


def values_match_filter(values: list[Any], wanted: set[str]) -> bool:
    return any(item_matches_filter({"value": value}, wanted) for value in values)


def append_structured_values(out: list[Any], value: Any) -> None:
    if value is None:
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            append_structured_values(out, item)
        return
    if isinstance(value, dict):
        for key in ("label", "name", "value", "canonical_name", "canonical_id", "node_id", "id", "standard_id"):
            append_structured_values(out, value.get(key))
        for key in ("aliases", "standards"):
            append_structured_values(out, value.get(key))
        return
    text = str(value).strip()
    if text:
        out.append(text)


def expand_canonical_values(values: list[Any]) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for value in values:
        expanded: list[Any] = [value]
        for item in expanded:
            text = str(item).strip() if item is not None else ""
            key = normalize_match_text(text)
            if text and key and key not in seen:
                seen.add(key)
                out.append(text)
    return out


def collect_record_filter_values(record: dict[str, Any], filter_key: str) -> list[Any]:
    config = AGGREGATE_FIELD_FILTER_CONFIG[filter_key]
    keys = config["keys"]
    values: list[Any] = []
    for key in keys:
        append_structured_values(values, record.get(key))
    if filter_key == "test_standards" and isinstance(record.get("test_method"), dict):
        method = record["test_method"]
        append_structured_values(values, method.get("standard_id"))
        append_structured_values(values, method.get("standards"))
    for container_key in ("primary_context", "use_context", "sample"):
        container = record.get(container_key)
        if not isinstance(container, dict):
            continue
        for key in keys:
            append_structured_values(values, container.get(key))
    context_fields = record.get("context_fields")
    if isinstance(context_fields, dict):
        append_structured_values(values, context_fields.get(config["context_field"]))
    return expand_canonical_values(values)


def associated_records(raw: dict[str, Any], key: str, record_map: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    ids = raw.get(key)
    ids = ids if isinstance(ids, list) else [ids] if ids else []
    return [record_map.get(str(item), {}) for item in ids if record_map.get(str(item))]


def embedded_records(raw: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in keys:
        value = raw.get(key)
        rows = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
        out.extend(row for row in rows if isinstance(row, dict))
    return out


def aggregate_filter_values(
    raw: dict[str, Any],
    filter_key: str,
    facts_map: dict[str, dict[str, Any]],
    evidence_map: dict[str, dict[str, Any]],
) -> list[Any]:
    values = collect_record_filter_values(raw, filter_key)
    for record in embedded_records(raw, "facts", "fact_records"):
        values.extend(collect_record_filter_values(record, filter_key))
    for record in embedded_records(raw, "evidence", "evidence_units"):
        values.extend(collect_record_filter_values(record, filter_key))
    for record in associated_records(raw, "fact_ids", facts_map):
        values.extend(collect_record_filter_values(record, filter_key))
    for record in associated_records(raw, "evidence_ids", evidence_map):
        values.extend(collect_record_filter_values(record, filter_key))
    return expand_canonical_values(values)


def raw_field_values(raw: dict[str, Any], *keys: str) -> list[Any]:
    values: list[Any] = []
    for key in keys:
        value = raw.get(key)
        if value is None:
            continue
        if isinstance(value, list):
            values.extend(value)
        else:
            values.append(value)
    return values


def test_method_items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    method = raw.get("test_method")
    values: list[Any] = []
    if isinstance(method, dict):
        values.extend(raw_field_values(method, "name", "label", "canonical_id", "id", "standard_id", "standards"))
    else:
        values.extend(raw_field_values(raw, "test_method", "test_methods"))
    return [item for item in (normalize_value_item(value, as_canonical(value, "TEST")) for value in values) if item]


def append_unique_values(out: list[str], values: tuple[str, ...] | list[str]) -> None:
    seen = {value.lower() for value in out}
    for value in values:
        text = str(value).strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        out.append(text)


def property_canonical_ids_for_filters(filters: dict[str, Any]) -> list[str]:
    canonical_ids = normalize_filter_values(filters.get("property_canonical_ids_soft"))
    for family in normalize_filter_values(filters.get("property_families")):
        append_unique_values(canonical_ids, PROPERTY_FAMILY_CANONICAL_IDS.get(family, ()))
    return canonical_ids


def metadata_property_canonical_ids(metadata: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if metadata.get("property_canonical_id") is not None:
        append_unique_values(out, [str(metadata.get("property_canonical_id"))])
    prop = metadata.get("property")
    if isinstance(prop, dict):
        for key in ("canonical_id", "id"):
            if prop.get(key) is not None:
                append_unique_values(out, [str(prop.get(key))])
    return out


def store_get(store: Any, *keys: str) -> Any:
    for key in keys:
        if isinstance(store, dict) and key in store:
            return store.get(key)
        if hasattr(store, key):
            return getattr(store, key)
    return None


def indexed_rows(rows: Any, *id_keys: str) -> dict[str, dict[str, Any]]:
    if isinstance(rows, dict):
        indexed: dict[str, dict[str, Any]] = {}
        for key, value in rows.items():
            if not isinstance(value, dict):
                continue
            indexed[str(key)] = value
            if isinstance(key, tuple):
                for part in key:
                    if part:
                        indexed[str(part)] = value
            for id_key in id_keys:
                if value.get(id_key):
                    indexed[str(value.get(id_key))] = value
        return indexed
    indexed: dict[str, dict[str, Any]] = {}
    if not isinstance(rows, list):
        return indexed
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in id_keys:
            value = row.get(key)
            if value:
                indexed[str(value)] = row
                break
    return indexed


def raw_hyperedge_object_id(raw: dict[str, Any]) -> str:
    object_id = raw.get("object_id") or raw.get("id")
    if object_id:
        return str(object_id)
    doc_id = raw.get("doc_id") or raw.get("patent_id") or raw.get("publication_number")
    hyperedge_id = raw.get("hyperedge_id") or raw.get("raw_hyperedge_id")
    if doc_id and hyperedge_id:
        return f"{doc_id}::{hyperedge_id}"
    return str(hyperedge_id or doc_id or "")


def raw_hyperedge_doc_id(raw: dict[str, Any]) -> str:
    doc_id = raw.get("doc_id") or raw.get("patent_id") or raw.get("publication_number")
    if doc_id:
        return str(doc_id)
    object_id = raw_hyperedge_object_id(raw)
    if "::" in object_id:
        return object_id.split("::", 1)[0]
    return ""


def raw_hyperedge_id(raw: dict[str, Any]) -> str:
    return str(raw.get("hyperedge_id") or raw.get("raw_hyperedge_id") or raw_hyperedge_object_id(raw))


def formulation_identity(raw: dict[str, Any]) -> tuple[str, str, str] | None:
    doc_id = doc_id_lookup_key(raw_hyperedge_doc_id(raw)) or normalize_match_text(raw_hyperedge_doc_id(raw))
    if not doc_id:
        return None
    sample_id = normalize_match_text(raw.get("sample_id"))
    if sample_id:
        return doc_id, "sample", sample_id
    context_id = normalize_match_text(raw.get("context_id"))
    if context_id:
        return doc_id, "context", context_id
    return None


def object_id_candidates(raw: dict[str, Any]) -> set[str]:
    candidates = {raw_hyperedge_object_id(raw), raw_hyperedge_id(raw)}
    doc_id = raw_hyperedge_doc_id(raw)
    hyperedge_id = raw_hyperedge_id(raw)
    if doc_id and hyperedge_id:
        candidates.add(f"{doc_id}::{hyperedge_id}")
    return {candidate for candidate in candidates if candidate}


def iter_store_hyperedges(store: Any) -> list[dict[str, Any]]:
    sources = [
        store_get(store, "hyperedges"),
        store_get(store, "hyperedges_by_object_id"),
        store_get(store, "hyperedges_by_id"),
        store_get(store, "raw_hyperedges"),
        store_get(store, "hyperedge_by_id"),
    ]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        rows = source.values() if isinstance(source, dict) else source
        if not isinstance(rows, list) and not hasattr(rows, "__iter__"):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw = row.get("raw") if isinstance(row.get("raw"), dict) else row
            object_id = raw_hyperedge_object_id(raw)
            if not object_id or object_id in seen:
                continue
            seen.add(object_id)
            out.append(raw)
    return out


def get_evidence_map(store: Any) -> dict[str, dict[str, Any]]:
    source = store_get(store, "evidence", "evidence_units", "evidence_by_id", "evidence_units_by_id")
    rows = indexed_rows(source, "evidence_id", "id")
    return {key: (value.get("raw") if isinstance(value.get("raw"), dict) else value) for key, value in rows.items()}


def get_facts_map(store: Any) -> dict[str, dict[str, Any]]:
    source = store_get(store, "facts", "facts_by_id", "fact_by_id", "raw_facts")
    rows = indexed_rows(source, "fact_id", "id")
    return {key: (value.get("raw") if isinstance(value.get("raw"), dict) else value) for key, value in rows.items()}


def get_patent_map(store: Any) -> dict[str, dict[str, Any]]:
    sources = [
        store_get(store, key)
        for key in (
            "patents",
            "patents_by_doc",
            "patents_by_doc_id",
            "patents_by_id",
            "patent_profiles_by_doc",
            "patent_profiles",
        )
    ]
    out: dict[str, dict[str, Any]] = {}
    for source in sources:
        rows = indexed_rows(source, "doc_id", "patent_id", "publication_number", "wo_id")
        for key, value in rows.items():
            raw = value.get("raw") if isinstance(value.get("raw"), dict) else value
            candidates = [
                key,
                raw.get("doc_id"),
                raw.get("patent_id"),
                raw.get("publication_number"),
                raw.get("wo_id"),
                raw.get("profile_id", "").removeprefix("PROFILE_") if isinstance(raw.get("profile_id"), str) else None,
            ]
            for candidate in candidates:
                if not candidate:
                    continue
                target = out.setdefault(str(candidate), {})
                target.update({k: v for k, v in raw.items() if v is not None})
    return out


def patent_for_doc(patents: dict[str, dict[str, Any]], doc_id: str) -> dict[str, Any]:
    if doc_id in patents:
        return patents[doc_id]
    lookup_key = doc_id_lookup_key(doc_id)
    if not lookup_key:
        return {}
    for candidate, patent in patents.items():
        if doc_id_lookup_key(candidate) == lookup_key:
            return patent
    return {}


def nested_labels(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        preferred = [value.get(key) for key in ("name", "label", "value", "organization", "company") if value.get(key)]
        source = preferred or list(value.values())
        return [label for item in source for label in nested_labels(item)]
    if isinstance(value, (list, tuple, set)):
        return [label for item in value for label in nested_labels(item)]
    text = str(value).strip()
    return [text] if text else []


def resolve_assignee_doc_ids(store: Any, assignees: Any) -> list[str]:
    wanted = lower_set(normalize_filter_values(assignees))
    if not wanted:
        return []
    resolved: set[str] = set()
    for map_key, patent in get_patent_map(store).items():
        labels: list[str] = []
        for key in (
            "assignee",
            "assignees",
            "applicant",
            "applicants",
            "current_assignee",
            "owner",
        ):
            labels.extend(nested_labels(patent.get(key)))
        if not labels or not item_matches_filter({"value": " ; ".join(labels)}, wanted):
            continue
        doc_id = str(
            patent.get("doc_id")
            or patent.get("patent_id")
            or patent.get("publication_number")
            or patent.get("wo_id")
            or map_key
        ).strip()
        if doc_id:
            resolved.add(doc_id)
    return sorted(resolved)


def available_store_doc_ids(store: Any) -> list[str]:
    return sorted(
        {
            *{raw_hyperedge_doc_id(raw) for raw in iter_store_hyperedges(store) if raw_hyperedge_doc_id(raw)},
            *{str(doc_id) for doc_id in get_patent_map(store) if str(doc_id).strip()},
        }
    )


def resolve_requested_doc_ids(store: Any, requested_doc_ids: Any) -> list[str]:
    requested = normalize_filter_values(requested_doc_ids)
    if not requested:
        return []
    requested_keys = {doc_id_lookup_key(value) or normalize_match_text(value) for value in requested}
    return [
        doc_id for doc_id in available_store_doc_ids(store)
        if (doc_id_lookup_key(doc_id) or normalize_match_text(doc_id)) in requested_keys
    ]


def apply_hard_search_scopes(store: Any, raw_filters: Any) -> tuple[dict[str, Any], list[str]]:
    filters = normalize_search_filters(raw_filters)
    warnings: list[str] = []
    requested_doc_ids = filters.get("doc_ids") or []
    if requested_doc_ids:
        filters["doc_ids"] = resolve_requested_doc_ids(store, requested_doc_ids) or ["__NO_DOC_ID_MATCH__"]
    assignees = filters.get("assignees") or []
    if not assignees:
        return filters, warnings
    resolved = resolve_assignee_doc_ids(store, assignees)
    if not resolved:
        filters["doc_ids"] = ["__NO_ASSIGNEE_MATCH__"]
        warnings.append("assignee_hard_scope_resolved_empty")
        return filters, warnings
    explicit = filters.get("doc_ids") or []
    if explicit:
        resolved_keys = {doc_id_lookup_key(value) or normalize_match_text(value) for value in resolved}
        filters["doc_ids"] = [
            doc_id for doc_id in explicit
            if (doc_id_lookup_key(doc_id) or normalize_match_text(doc_id)) in resolved_keys
        ]
        if not filters["doc_ids"]:
            filters["doc_ids"] = ["__NO_ASSIGNEE_DOC_INTERSECTION__"]
            warnings.append("assignee_and_doc_id_hard_scopes_do_not_intersect")
    else:
        filters["doc_ids"] = resolved
    return filters, warnings


def as_label(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("name", "label", "standard_id", "id", "canonical_id", "value"):
            if value.get(key):
                return str(value.get(key)).strip()
        return None
    if isinstance(value, list):
        labels = [as_label(item) for item in value]
        labels = [label for label in labels if label]
        return "; ".join(labels) if labels else None
    text = str(value).strip() if value is not None else ""
    return text or None


def as_canonical(value: Any, fallback_prefix: str | None = None) -> str | None:
    if isinstance(value, dict):
        for key in ("canonical_id", "standard_id", "id"):
            if value.get(key):
                return str(value.get(key)).strip()
    if fallback_prefix:
        return slug_id(fallback_prefix, as_label(value))
    return None


def normalize_value_item(value: Any, canonical_id: Any = None, **extra: Any) -> dict[str, Any] | None:
    label = as_label(value)
    if not label:
        return None
    item = {"value": label, "canonical_id": str(canonical_id).strip() if canonical_id else None}
    item.update({key: val for key, val in extra.items() if val is not None})
    return item


def material_rows(raw: dict[str, Any], role_filter: set[str] | None = None) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for role in MATERIAL_ROLES:
        if role_filter and not material_role_matches_filter(role, role_filter):
            continue
        rows = raw.get(role) or []
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                out.append((role, row))
    return out


MATERIAL_ROLE_ALIASES = dict(_ROLE_CONFIG["aliases"])


def normalize_material_role_name(value: Any) -> str:
    text = str(value or "").strip().casefold()
    return MATERIAL_ROLE_ALIASES.get(text, text)


def normalize_material_role_values(value: Any) -> list[str]:
    roles: list[str] = []
    for item in normalize_filter_values(value):
        role = normalize_material_role_name(item)
        if role in VALID_MATERIAL_ROLES and role not in roles:
            roles.append(role)
    return roles


def material_role_matches_filter(role: Any, role_filter: set[str]) -> bool:
    role_text = str(role or "").strip().casefold()
    normalized_role = normalize_material_role_name(role_text)
    normalized_filter = {normalize_material_role_name(item) for item in role_filter}
    return role_text in role_filter or normalized_role in normalized_filter


def facts_by_doc_context(facts_map: dict[str, dict[str, Any]] | None) -> dict[tuple[str, str], list[dict[str, Any]]]:
    facts_map = facts_map or {}
    cache_key = id(facts_map)
    cached = _FACTS_BY_CONTEXT_CACHE.get(cache_key)
    if cached and cached[0] == len(facts_map):
        return cached[1]
    by_context: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for fact in facts_map.values():
        if not isinstance(fact, dict):
            continue
        doc_id = str(fact.get("doc_id") or "").strip()
        context_id = str(fact.get("context_id") or "").strip()
        if doc_id and context_id:
            by_context.setdefault((doc_id, context_id), []).append(fact)
    _FACTS_BY_CONTEXT_CACHE.clear()
    _FACTS_BY_CONTEXT_CACHE[cache_key] = (len(facts_map), by_context)
    return by_context


def fact_material_rows_for_raw(
    raw: dict[str, Any],
    facts_map: dict[str, dict[str, Any]] | None,
    role_filter: set[str] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    facts_map = facts_map or {}
    out: list[tuple[str, dict[str, Any]]] = []
    fact_rows = raw_fact_rows(raw, facts_map)
    seen = {fact_identity(fact) for fact in fact_rows}
    doc_id = raw_hyperedge_doc_id(raw)
    context_id = str(raw.get("context_id") or "").strip()
    if doc_id and context_id:
        for fact in facts_by_doc_context(facts_map).get((doc_id, context_id), []):
            if not isinstance(fact, dict):
                continue
            fact_key = fact_identity(fact)
            if fact_key in seen:
                continue
            fact_rows.append(fact)
            seen.add(fact_key)
    for fact in fact_rows:
        material = fact.get("material") or fact.get("material_name") or fact.get("name") or fact.get("label")
        if not material:
            continue
        role = fact.get("material_role") or fact.get("role")
        if not role:
            continue
        role_text = normalize_material_role_name(role)
        if role_filter and not material_role_matches_filter(role_text, role_filter):
            continue
        row: dict[str, Any] = {
            "material": material,
            "canonical_id": fact.get("material_canonical_id") or fact.get("canonical_id"),
            "fact_id": fact_identity(fact),
        }
        if fact.get("value") is not None or fact.get("unit") is not None:
            row["value"] = fact.get("value")
            row["unit"] = fact.get("unit")
            row["amount"] = {"value": fact.get("value"), "unit": fact.get("unit")}
        out.append((role_text, row))
    return out


def aggregate_material_rows(
    raw: dict[str, Any],
    facts_map: dict[str, dict[str, Any]] | None,
    role_filter: set[str] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    rows = material_rows(raw, role_filter)
    rows.extend(fact_material_rows_for_raw(raw, facts_map, role_filter))
    return rows


def normalize_resin_system(value: Any) -> str:
    text = as_label(value)
    lower = str(text or "").strip().lower()
    if not lower:
        return "unknown"
    if any(term in lower for term in ("silyl acrylate", "silyl_ester", "silyl ester", "acrylic_silyl", "silylated acrylic")):
        return "silyl acrylate / silyl ester acrylic"
    if any(term in lower for term in ("silicone", "siloxane", "polysiloxane", "pdms", "organosiloxane")):
        return "silicone/polysiloxane"
    if any(term in lower for term in ("acrylic", "acrylate", "methacrylate", "mma", "bma")):
        return "acrylic"
    if any(term in lower for term in ("epoxy", "epoxide")):
        return "epoxy"
    if any(term in lower for term in ("polyurethane", "polyurea", "isocyanate", "urethane")):
        return "polyurethane/polyurea"
    if "polyester" in lower:
        return "polyester"
    if any(term in lower for term in ("fluoro", "fluoropolymer", "fluorinated", "ptfe")):
        return "fluoropolymer"
    if any(term in lower for term in ("classifier", "mixed", "hybrid", "copolymer")):
        return "mixed/other"
    return "mixed/other"


def resin_system_candidates(raw: dict[str, Any], patent: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for key in ("resin_system", "binder_family"):
        value = raw.get(key)
        values = value if isinstance(value, list) else [value]
        for item in values:
            category = normalize_resin_system(item)
            if category != "unknown":
                candidates.append(
                    {
                        "value": category,
                        "raw_value": as_label(item),
                        "source": "explicit_hyperedge",
                    }
                )
    patent = patent or {}
    for key in ("resin_system", "binder_family"):
        value = patent.get(key)
        values = value if isinstance(value, list) else [value]
        for item in values:
            category = normalize_resin_system(item)
            if category != "unknown":
                candidates.append(
                    {
                        "value": category,
                        "raw_value": as_label(item),
                        "source": "patent_profile",
                    }
                )
    for role, row in material_rows(raw, {"resin"}):
        name = row.get("material") or row.get("name") or row.get("label") or row.get("canonical_id")
        category = normalize_resin_system(name)
        if category != "unknown":
            candidates.append(
                {
                    "value": category,
                    "raw_value": as_label(name),
                    "source": "inferred_from_material",
                    "material_role": role,
                }
            )
    return candidates


def aggregate_target_values(
    raw: dict[str, Any],
    target: str,
    patents: dict[str, dict[str, Any]],
    filters: dict[str, Any],
    facts_map: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    role_filter = lower_set(filters.get("material_roles") or []) or None
    doc_id = raw_hyperedge_doc_id(raw)
    patent = patent_for_doc(patents, doc_id)
    if target == "test_method":
        return test_method_items(raw)
    if target == "property":
        prop = raw.get("property")
        canonical_id = raw.get("property_canonical_id") or as_canonical(prop)
        item = normalize_value_item(prop, canonical_id)
        return [item] if item else []
    if target == "material":
        items: list[dict[str, Any]] = []
        for role, row in aggregate_material_rows(raw, facts_map, role_filter):
            item = normalize_value_item(row.get("material") or row.get("name") or row.get("label"), row.get("canonical_id"), material_role=role)
            if item:
                items.append(item)
        return items
    if target == "material_role":
        return [
            {"value": role, "canonical_id": slug_id("ROLE", role)}
            for role, _ in aggregate_material_rows(raw, facts_map, role_filter)
        ]
    if target == "resin_system":
        return [
            normalize_value_item(
                item["value"],
                slug_id("RESIN_SYSTEM", item["value"]),
                source=item.get("source"),
                raw_value=item.get("raw_value"),
            )
            for item in resin_system_candidates(raw, patent)
            if normalize_value_item(item["value"], slug_id("RESIN_SYSTEM", item["value"]))
        ]
    if target == "substrate":
        return [
            item
            for item in (
                normalize_value_item(value, as_canonical(value, "SUBSTRATE"))
                for value in raw_field_values(raw, "substrate", "substrates")
            )
            if item
        ]
    if target == "assignee":
        assignee = patent.get("assignee")
        if assignee is None:
            assignee = patent.get("applicants")
        values = assignee if isinstance(assignee, list) else [assignee]
        return [item for item in (normalize_value_item(value, slug_id("ASSIGNEE", value)) for value in values) if item]
    if target == "doc_id":
        item = normalize_value_item(doc_id, slug_id("DOC", doc_id))
        return [item] if item else []
    if target == "publication_year":
        publication_date = str(patent.get("publication_date") or "").strip()
        match = re.search(r"(?:19|20)\d{2}", publication_date)
        year = match.group(0) if match else "unknown_year"
        item = normalize_value_item(year, slug_id("PUBLICATION_YEAR", year))
        return [item] if item else []
    if target == "application_family":
        values = patent.get("application_family")
        values = values if isinstance(values, list) else [values]
        return [
            item
            for item in (normalize_value_item(value, slug_id("APPLICATION", value)) for value in values)
            if item
        ]
    if target == "formulation":
        identity = formulation_identity(raw)
        if identity is None:
            return []
        label = raw.get("sample_id") or raw.get("context_id")
        item = normalize_value_item(
            label,
            "::".join(identity),
            doc_id=doc_id,
            object_id=raw_hyperedge_object_id(raw),
            hyperedge_id=raw_hyperedge_id(raw),
            sample_id=raw.get("sample_id"),
            property=raw.get("property"),
        )
        return [item] if item else []
    if target in {"example_kind", "polarity"}:
        item = normalize_value_item(raw.get(target), slug_id(target.upper(), raw.get(target)))
        return [item] if item else []
    if target == "amount":
        items = []
        for role, row in aggregate_material_rows(raw, facts_map, role_filter):
            amount = row.get("amount") if isinstance(row.get("amount"), dict) else {}
            value = amount.get("value", row.get("value"))
            unit = amount.get("unit", row.get("unit"))
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            material = row.get("material") or row.get("name") or row.get("label")
            items.append(
                {
                    "value": numeric,
                    "unit": str(unit or "").strip() or None,
                    "canonical_id": row.get("canonical_id"),
                    "material": material,
                    "material_role": role,
                }
            )
        return items
    return []


def raw_matches_aggregate_filters(
    raw: dict[str, Any],
    patents: dict[str, dict[str, Any]],
    filters: dict[str, Any],
    facts_map: dict[str, dict[str, Any]] | None = None,
    evidence_map: dict[str, dict[str, Any]] | None = None,
) -> bool:
    facts_map = facts_map or {}
    evidence_map = evidence_map or {}
    doc_ids = set(filters.get("doc_ids") or [])
    if doc_ids and raw_hyperedge_doc_id(raw) not in doc_ids:
        return False
    if filters.get("properties"):
        wanted = lower_set(filters["properties"])
        values = aggregate_target_values(raw, "property", patents, filters)
        if not any((item.get("value") or "").casefold() in wanted or (item.get("canonical_id") or "").casefold() in wanted for item in values):
            return False
    soft_property_ids = lower_set(property_canonical_ids_for_filters(filters))
    if soft_property_ids:
        values = aggregate_target_values(raw, "property", patents, filters)
        if not any((item.get("canonical_id") or "").casefold() in soft_property_ids for item in values):
            return False
    if filters.get("polarity") and str(raw.get("polarity") or "").casefold() not in lower_set(filters["polarity"]):
        return False
    if filters.get("example_kind") and str(raw.get("example_kind") or "").casefold() not in lower_set(filters["example_kind"]):
        return False
    if filters.get("material_roles"):
        role_filter = lower_set(filters["material_roles"])
        if not aggregate_material_rows(raw, facts_map, role_filter):
            return False
    if filters.get("materials") or filters.get("material_canonical_ids"):
        role_filter = lower_set(filters.get("material_roles") or []) or None
        material_rows_for_filter = aggregate_material_rows(raw, facts_map, role_filter)
        if filters.get("materials"):
            wanted_materials = lower_set(filters["materials"])
            material_items = [
                normalize_value_item(
                    row.get("material") or row.get("name") or row.get("label"),
                    row.get("canonical_id"),
                )
                for _, row in material_rows_for_filter
            ]
            if not any(item and item_matches_filter(item, wanted_materials) for item in material_items):
                return False
        if filters.get("material_canonical_ids"):
            wanted_ids = lower_set(filters["material_canonical_ids"])
            actual_ids = {
                str(row.get("canonical_id") or "").strip().casefold()
                for _, row in material_rows_for_filter
                if str(row.get("canonical_id") or "").strip()
            }
            if not wanted_ids.intersection(actual_ids):
                return False
    if filters.get("resin_systems"):
        wanted_systems = lower_set(filters["resin_systems"])
        systems = resin_system_candidates(raw, patent_for_doc(patents, raw_hyperedge_doc_id(raw)))
        if not any(item_matches_filter({"value": item.get("value")}, wanted_systems) for item in systems):
            return False
    if filters.get("assignees"):
        wanted = lower_set(filters["assignees"])
        assignees = aggregate_target_values(raw, "assignee", patents, filters)
        if not any(item_matches_filter(item, wanted) for item in assignees):
            return False
    if filters.get("application_family"):
        wanted = lower_set(filters["application_family"])
        families = aggregate_target_values(raw, "application_family", patents, filters)
        if not application_family_matches(wanted, families):
            return False
    if filters.get("applications"):
        wanted = lower_set(filters["applications"])
        applications = aggregate_filter_values(raw, "applications", facts_map, evidence_map)
        if not values_match_filter(applications, wanted):
            return False
    if filters.get("substrates"):
        wanted = lower_set(filters["substrates"])
        substrates = aggregate_filter_values(raw, "substrates", facts_map, evidence_map)
        if not values_match_filter(substrates, wanted):
            return False
    if filters.get("test_methods"):
        wanted = lower_set(filters["test_methods"])
        methods = aggregate_filter_values(raw, "test_methods", facts_map, evidence_map)
        if not values_match_filter(methods, wanted):
            return False
    if filters.get("test_standards"):
        wanted = lower_set(filters["test_standards"])
        standards = aggregate_filter_values(raw, "test_standards", facts_map, evidence_map)
        if not values_match_filter(standards, wanted):
            return False
    return True


def fetch_allowed_hyperedge_keys(filters: dict[str, Any], warnings: list[str]) -> set[tuple[str, str]] | None:
    sql_filters = normalize_search_filters(filters)
    if not any(sql_filters.get(key) for key in ("doc_ids", "properties", "polarity", "example_kind")):
        return None
    try:
        pg_env = expand.load_pg_env(expand.DEFAULT_PG_ENV)
        clauses, params = search_filter_sql("o", sql_filters)
        sql = f"""
            SELECT o.doc_id,
                   COALESCE(
                       NULLIF(o.metadata->>'raw_hyperedge_id', ''),
                       NULLIF(o.metadata->>'hyperedge_id', ''),
                       regexp_replace(o.object_id, '^.*::', '')
                   ) AS raw_hyperedge_id
            FROM retrieval_objects o
            WHERE {' AND '.join(clauses)}
            ORDER BY o.doc_id, o.object_id
        """
        with expand.pg_connect(pg_env) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return {
                    (str(doc_id), str(hyperedge_id))
                    for doc_id, hyperedge_id in cur.fetchall()
                    if doc_id and hyperedge_id
                }
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"retrieval_objects filter unavailable; used raw KG filtering only: {exc}")
        return None


def first_evidence_example(raw: dict[str, Any], evidence_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    evidence_ids = raw.get("evidence_ids")
    if not isinstance(evidence_ids, list):
        evidence_ids = [raw.get("evidence_id")] if raw.get("evidence_id") else []
    evidence_id = str(evidence_ids[0]) if evidence_ids else None
    evidence = evidence_map.get(evidence_id, {}) if evidence_id else {}
    return {
        "doc_id": raw_hyperedge_doc_id(raw),
        "object_id": raw_hyperedge_object_id(raw),
        "hyperedge_id": raw_hyperedge_id(raw),
        "evidence_id": evidence_id,
        "page": evidence.get("page"),
        "section": evidence.get("section"),
        "table": evidence.get("table"),
        "row_label": evidence.get("row_label"),
        "quote": compact_text(evidence.get("quote")),
    }


def raw_assignees(raw: dict[str, Any], patents: dict[str, dict[str, Any]]) -> list[str]:
    patent = patent_for_doc(patents, raw_hyperedge_doc_id(raw))
    assignee = patent.get("assignee")
    if assignee is None:
        assignee = patent.get("applicants")
    values = assignee if isinstance(assignee, list) else [assignee]
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def add_bucket_assignees(bucket: dict[str, Any], raw: dict[str, Any], patents: dict[str, dict[str, Any]]) -> None:
    assignees = bucket.setdefault("assignees", set())
    for assignee in raw_assignees(raw, patents):
        assignees.add(assignee)


def add_bucket_example(
    bucket: dict[str, Any],
    raw: dict[str, Any],
    evidence_map: dict[str, dict[str, Any]],
    value_item: dict[str, Any],
    patents: dict[str, dict[str, Any]],
) -> None:
    examples = bucket.setdefault("examples", [])
    if len(examples) >= MAX_AGGREGATE_EXAMPLES_PER_ITEM:
        return
    example = first_evidence_example(raw, evidence_map)
    assignees = raw_assignees(raw, patents)
    if assignees:
        example["assignee"] = assignees[0]
    for key in ("material", "material_role", "unit"):
        if value_item.get(key) is not None:
            example[key] = value_item.get(key)
    examples.append(example)


DOC_FIELD_SCAN_DEFAULT_GROUPS = {"test_method", "test_condition", "property", "result", "facts", "materials", "evidence"}
DOC_FIELD_SCAN_ALLOWED_GROUPS = DOC_FIELD_SCAN_DEFAULT_GROUPS | {"baseline", "process", "substrate"}


def normalize_doc_scan_groups(value: Any) -> set[str]:
    groups = set(normalize_filter_values(value))
    groups = {group for group in groups if group in DOC_FIELD_SCAN_ALLOWED_GROUPS}
    return groups or set(DOC_FIELD_SCAN_DEFAULT_GROUPS)


def recursive_scalar_fields(prefix: str, value: Any) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            out.extend(recursive_scalar_fields(f"{prefix}.{key}" if prefix else str(key), item))
        return out
    if isinstance(value, list):
        for index, item in enumerate(value):
            out.extend(recursive_scalar_fields(f"{prefix}[{index}]", item))
        return out
    text = str(value or "").strip()
    if text:
        out.append((prefix, text))
    return out


def raw_evidence_rows(raw: dict[str, Any], evidence_map: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    evidence_ids = raw.get("evidence_ids")
    if not isinstance(evidence_ids, list):
        evidence_ids = [raw.get("evidence_id")] if raw.get("evidence_id") else []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for evidence_id in evidence_ids:
        key = str(evidence_id or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        evidence = evidence_map.get(key, {})
        if evidence:
            rows.append(evidence)
    return rows


def raw_fact_rows(raw: dict[str, Any], facts_map: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    inline = raw.get("facts")
    if isinstance(inline, list):
        rows.extend(item for item in inline if isinstance(item, dict))
    fact_ids = raw.get("fact_ids")
    if not isinstance(fact_ids, list):
        fact_ids = [raw.get("fact_id")] if raw.get("fact_id") else []
    seen = {str(row.get("fact_id") or row.get("id") or "") for row in rows}
    for fact_id in fact_ids:
        key = str(fact_id or "").strip()
        if not key or key in seen:
            continue
        fact = facts_map.get(key)
        if fact:
            rows.append(fact)
            seen.add(key)
    return rows


def doc_scan_query_terms(query: str) -> list[str]:
    lower = str(query or "").lower()
    terms: list[str] = []
    for phrase in ("protocol 1", "protocol 2", "thermal exposure", "thermal cycling", "cold-water quench", "pass/fail"):
        if phrase in lower:
            terms.append(phrase)
    terms.extend(re.findall(r"[a-z0-9][a-z0-9_./-]*|[\u4e00-\u9fff]{2,}", lower))
    out: list[str] = []
    seen: set[str] = set()
    stop = {"the", "and", "or", "for", "with", "this", "that", "have", "has", "does", "do", "we", "in"}
    for term in terms:
        cleaned = term.strip().lower()
        if not cleaned or cleaned in stop:
            continue
        if cleaned.isdigit():
            continue
        if cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return out


def doc_scan_field_texts(
    raw: dict[str, Any],
    field_groups: set[str],
    facts_map: dict[str, dict[str, Any]],
    evidence_map: dict[str, dict[str, Any]],
) -> tuple[dict[str, str], list[dict[str, Any]], list[dict[str, Any]]]:
    fields: dict[str, str] = {}
    for key in ("test_method", "test_condition", "property", "property_canonical_id", "result", "baseline", "process", "substrate"):
        group = key if key in {"test_method", "test_condition", "property", "result", "baseline", "process", "substrate"} else "property"
        if group not in field_groups:
            continue
        for path, text in recursive_scalar_fields(key, raw.get(key)):
            fields[path] = text
    if "materials" in field_groups:
        for role in MATERIAL_ROLES:
            for path, text in recursive_scalar_fields(role, raw.get(role)):
                fields[path] = text
    facts = raw_fact_rows(raw, facts_map) if "facts" in field_groups else []
    for fact in facts:
        fact_id = str(fact.get("fact_id") or fact.get("id") or "fact")
        for path, text in recursive_scalar_fields(f"facts.{fact_id}", fact):
            fields[path] = text
    evidence_rows = raw_evidence_rows(raw, evidence_map) if "evidence" in field_groups else []
    for evidence in evidence_rows:
        evidence_id = str(evidence.get("evidence_id") or evidence.get("id") or "evidence")
        for key in ("quote", "section", "table", "row_label"):
            if evidence.get(key):
                fields[f"evidence.{evidence_id}.{key}"] = str(evidence.get(key))
    return fields, facts, evidence_rows


def doc_scan_score(fields: dict[str, str], query_terms: list[str]) -> tuple[float, list[str]]:
    haystack_by_path = {path: text.lower() for path, text in fields.items()}
    matched_fields: list[str] = []
    score = 0.0
    for path, text in haystack_by_path.items():
        field_score = 0.0
        for term in query_terms:
            if term in text:
                field_score += 3.0 if " " in term else 1.0
        if field_score:
            matched_fields.append(path)
            score += field_score
    if any(path.startswith("test_method") for path in matched_fields):
        score += 2.0
    if any(path.startswith("test_condition") for path in matched_fields):
        score += 2.0
    if any(path.startswith("evidence") for path in matched_fields):
        score += 0.5
    return score, matched_fields[:20]


def compact_doc_scan_fact(fact: dict[str, Any]) -> dict[str, Any]:
    out = {
        "fact_id": fact.get("fact_id") or fact.get("id"),
        "slot": fact.get("slot") or fact.get("field") or fact.get("predicate"),
        "material": fact.get("material") or fact.get("material_name"),
        "material_role": fact.get("material_role") or fact.get("role"),
        "property": fact.get("property"),
        "test_method": fact.get("test_method"),
        "test_condition": fact.get("test_condition"),
        "result": fact.get("result"),
        "value": fact.get("value") or fact.get("value_text"),
        "unit": fact.get("unit"),
    }
    return {key: value for key, value in out.items() if value not in (None, "", [], {})}


def compact_doc_scan_evidence(evidence_rows: list[dict[str, Any]], max_rows: int = 4) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for evidence in evidence_rows[:max_rows]:
        out.append(
            {
                "evidence_id": evidence.get("evidence_id") or evidence.get("id"),
                "page": evidence.get("page"),
                "section": evidence.get("section"),
                "table": evidence.get("table"),
                "row_label": evidence.get("row_label"),
                "quote": compact_text(evidence.get("quote")),
            }
        )
    return out


def compact_doc_scan_item(
    raw: dict[str, Any],
    *,
    score: float,
    matched_fields: list[str],
    facts: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "doc_id": raw_hyperedge_doc_id(raw),
        "object_id": raw_hyperedge_object_id(raw),
        "hyperedge_id": raw_hyperedge_id(raw),
        "sample_id": raw.get("sample_id"),
        "context_id": raw.get("context_id"),
        "example_id": raw.get("example_id"),
        "example_kind": raw.get("example_kind"),
        "property": raw.get("property"),
        "property_canonical_id": raw.get("property_canonical_id"),
        "test_method": raw.get("test_method"),
        "test_condition": raw.get("test_condition"),
        "result": raw.get("result"),
        "baseline": raw.get("baseline"),
        "materials": compact_materials(raw),
        "facts": [compact_doc_scan_fact(fact) for fact in facts[:20]],
        "evidence": compact_doc_scan_evidence(evidence_rows),
        "score": round(score, 3),
        "matched_fields": matched_fields,
    }


def run_doc_field_scan(request: dict[str, Any]) -> dict[str, Any]:
    requested_doc_ids = normalize_filter_values(request.get("doc_ids"))
    if not requested_doc_ids:
        return {
            "tool": "kg.doc_field_scan",
            "status": "error",
            "error": "doc_ids is required",
            "summary": {},
            "items": [],
        }
    query = str(request.get("query") or "")
    field_groups = normalize_doc_scan_groups(request.get("field_groups"))
    limit = clamp_int(request.get("limit"), 200, 1, 500)
    include_evidence = bool(request.get("include_evidence", True))
    store = get_store()
    doc_ids = resolve_requested_doc_ids(store, requested_doc_ids)
    doc_id_set = set(doc_ids)
    evidence_map = get_evidence_map(store) if include_evidence else {}
    facts_map = get_facts_map(store)
    query_terms = doc_scan_query_terms(query)
    scanned: list[tuple[float, int, dict[str, Any], list[str], list[dict[str, Any]], list[dict[str, Any]]]] = []
    hyperedges_scanned = 0
    for index, raw in enumerate(iter_store_hyperedges(store)):
        if raw_hyperedge_doc_id(raw) not in doc_id_set:
            continue
        hyperedges_scanned += 1
        fields, facts, evidence_rows = doc_scan_field_texts(raw, field_groups, facts_map, evidence_map)
        score, matched_fields = doc_scan_score(fields, query_terms)
        scanned.append((score, index, raw, matched_fields, facts, evidence_rows))
    matched = [row for row in scanned if row[0] > 0]
    ranked = matched if matched else scanned
    ranked = sorted(ranked, key=lambda row: (-row[0], row[1], raw_hyperedge_object_id(row[2])))
    items = [
        compact_doc_scan_item(raw, score=score, matched_fields=matched_fields, facts=facts, evidence_rows=evidence_rows)
        for score, _index, raw, matched_fields, facts, evidence_rows in ranked[:limit]
    ]
    status = "ok" if items else "empty"
    warnings: list[str] = []
    if scanned and not matched:
        warnings.append("no lexical field match; returned doc-scoped candidates")
    return {
        "tool": "kg.doc_field_scan",
        "status": status,
        "requested_doc_ids": requested_doc_ids,
        "doc_ids": doc_ids,
        "query": query,
        "field_groups": sorted(field_groups),
        "summary": {
            "docs_scanned": len(doc_id_set),
            "hyperedges_scanned": hyperedges_scanned,
            "matched_hyperedges": len(matched),
            "returned": len(items),
        },
        "items": items,
        "warnings": warnings,
    }


def choose_doc_resin_system(
    doc_id: str,
    doc_rows: list[dict[str, Any]],
    patent: dict[str, Any],
) -> dict[str, Any]:
    scored: dict[str, dict[str, Any]] = {}
    source_priority = {"explicit_hyperedge": 3, "patent_profile": 2, "inferred_from_material": 1}
    for raw in doc_rows:
        for candidate in resin_system_candidates(raw, patent):
            value = candidate["value"]
            bucket = scored.setdefault(
                value,
                {
                    "value": value,
                    "score": 0,
                    "sources": {},
                    "raw_values": set(),
                },
            )
            source = str(candidate.get("source") or "unknown")
            bucket["score"] += source_priority.get(source, 0)
            bucket["sources"][source] = bucket["sources"].get(source, 0) + 1
            raw_value = candidate.get("raw_value")
            if raw_value:
                bucket["raw_values"].add(str(raw_value))
    if not scored:
        for key in ("resin_system", "binder_family"):
            value = patent.get(key)
            values = value if isinstance(value, list) else [value]
            for item in values:
                category = normalize_resin_system(item)
                if category == "unknown":
                    continue
                bucket = scored.setdefault(
                    category,
                    {"value": category, "score": 0, "sources": {}, "raw_values": set()},
                )
                bucket["score"] += source_priority["patent_profile"]
                bucket["sources"]["patent_profile"] = bucket["sources"].get("patent_profile", 0) + 1
                label = as_label(item)
                if label:
                    bucket["raw_values"].add(label)
    if not scored:
        return {
            "doc_id": doc_id,
            "value": "unknown",
            "source": "unknown",
            "source_breakdown": {"unknown": 1},
            "raw_values": [],
        }
    chosen = sorted(scored.values(), key=lambda row: (-int(row["score"]), str(row["value"])))[0]
    source_breakdown = dict(chosen["sources"])
    source = sorted(source_breakdown, key=lambda key: (-source_breakdown[key], key))[0]
    return {
        "doc_id": doc_id,
        "value": chosen["value"],
        "source": source,
        "source_breakdown": source_breakdown,
        "raw_values": sorted(chosen["raw_values"])[:12],
    }


def run_resin_system_aggregate(
    *,
    matched: list[dict[str, Any]],
    all_hyperedges: list[dict[str, Any]],
    patents: dict[str, dict[str, Any]],
    evidence_map: dict[str, dict[str, Any]],
    target: str,
    group_by: list[str],
    filters: dict[str, Any],
    limit: int,
    include_examples: bool,
    warnings: list[str],
    applied_filters: list[str] | None = None,
    ignored_filters: list[str] | None = None,
) -> dict[str, Any]:
    matched_doc_ids = sorted({raw_hyperedge_doc_id(raw) for raw in matched if raw_hyperedge_doc_id(raw)})
    total_docs = len(matched_doc_ids)
    rows_by_doc: dict[str, list[dict[str, Any]]] = {doc_id: [] for doc_id in matched_doc_ids}
    matched_example_by_doc: dict[str, dict[str, Any]] = {}
    for raw in matched:
        doc_id = raw_hyperedge_doc_id(raw)
        if doc_id and doc_id not in matched_example_by_doc:
            matched_example_by_doc[doc_id] = raw
    for raw in all_hyperedges:
        doc_id = raw_hyperedge_doc_id(raw)
        if doc_id in rows_by_doc:
            rows_by_doc[doc_id].append(raw)

    buckets: dict[str, dict[str, Any]] = {}
    classified_docs = 0
    for doc_id in matched_doc_ids:
        classification = choose_doc_resin_system(doc_id, rows_by_doc.get(doc_id, []), patents.get(doc_id, {}))
        value = classification["value"]
        if value != "unknown":
            classified_docs += 1
        bucket = buckets.setdefault(
            value,
            {
                "value": value,
                "canonical_id": slug_id("RESIN_SYSTEM", value),
                "count": 0,
                "doc_ids": [],
                "assignees": set(),
                "examples": [],
                "source_breakdown": {},
                "raw_values": set(),
            },
        )
        bucket["count"] += 1
        bucket["doc_ids"].append(doc_id)
        for source, count in classification.get("source_breakdown", {}).items():
            bucket["source_breakdown"][source] = bucket["source_breakdown"].get(source, 0) + int(count)
        for raw_value in classification.get("raw_values", []):
            bucket["raw_values"].add(raw_value)
        example_raw = matched_example_by_doc.get(doc_id) or (rows_by_doc.get(doc_id) or [{}])[0]
        add_bucket_assignees(bucket, example_raw, patents)
        if include_examples and len(bucket["examples"]) < MAX_AGGREGATE_EXAMPLES_PER_ITEM:
            example = first_evidence_example(example_raw, evidence_map)
            example["doc_id"] = doc_id
            example["resin_system"] = value
            example["resin_system_source"] = classification.get("source")
            bucket["examples"].append(example)

    items: list[dict[str, Any]] = []
    unknown_docs = total_docs - classified_docs
    for bucket in buckets.values():
        doc_ids = sorted(set(bucket["doc_ids"]))
        assignees = bucket.pop("assignees")
        raw_values = bucket.pop("raw_values")
        bucket["doc_ids"] = doc_ids
        bucket["doc_count"] = len(doc_ids)
        bucket["assignee_count"] = len(assignees)
        bucket["assignees"] = sorted(assignees)[:20]
        bucket["raw_values"] = sorted(raw_values)[:20]
        bucket["percentage_of_total_matched_docs"] = round((len(doc_ids) / total_docs * 100), 2) if total_docs else 0.0
        bucket["percentage_of_classified_docs"] = (
            round((len(doc_ids) / classified_docs * 100), 2)
            if classified_docs and bucket["value"] != "unknown"
            else 0.0
        )
        bucket["evidence_coverage"] = {
            "matched_doc_count": total_docs,
            "classified_doc_count": classified_docs,
            "unknown_doc_count": unknown_docs,
        }
        if not include_examples:
            bucket["examples"] = []
        items.append(bucket)
    items = sorted(items, key=lambda row: (-int(row.get("doc_count") or 0), str(row.get("value") or "")))[:limit]
    return {
        "tool": "kg.sql_aggregate",
        "status": "ok" if items else "empty",
        "query_interpretation": {"intent": "group_count", "target": target, "group_by": group_by, "filters": filters},
        "summary": {
            "matched_hyperedges": len(matched),
            "matched_doc_count": total_docs,
            "classified_doc_count": classified_docs,
            "unknown_doc_count": unknown_docs,
            "returned": len(items),
            "applied_filters": applied_filters or [],
            "ignored_filters": ignored_filters or [],
        },
        "items": items,
        "warnings": warnings,
        "generated_at": now_iso(),
    }


def run_sql_aggregate(request: dict[str, Any]) -> dict[str, Any]:
    intent = str(request.get("intent") or "").strip()
    target = str(request.get("target") or "").strip()
    group_by = normalize_filter_values(request.get("group_by"))
    raw_filters = request.get("requested_filters") if isinstance(request.get("requested_filters"), dict) else request.get("filters")
    receipt = normalize_filter_request("kg.sql_aggregate", raw_filters)
    receipt["route_adjustments"] = list(
        dict.fromkeys([*(request.get("route_adjustments") or []), *receipt["route_adjustments"]])
    )
    invalid_group_by = [value for value in group_by if value not in AGGREGATE_GROUP_BY]
    receipt["unsupported_constraints"] = sorted(
        set([*receipt["unsupported_constraints"], *invalid_group_by, *(request.get("unsupported_constraints") or [])])
    )

    def finish(payload: dict[str, Any], *, aggregate_target: str | None = None) -> dict[str, Any]:
        resolved_target = aggregate_target or target
        payload["requested_filters"] = receipt["requested_filters"]
        payload["effective_filters"] = receipt["effective_filters"]
        payload["unsupported_constraints"] = receipt["unsupported_constraints"]
        payload["route_adjustments"] = receipt["route_adjustments"]
        payload["cohort_mode"] = "document" if resolved_target in {"doc_id", "publication_year", "resin_system"} else "hyperedge"
        payload["count_unit"] = (
            "patent" if resolved_target in {"doc_id", "publication_year", "resin_system"}
            else "formulation" if resolved_target == "formulation"
            else "hyperedge"
        )
        if receipt["unsupported_constraints"]:
            payload["status"] = "unsupported"
            payload["items"] = []
        return payload

    if intent not in AGGREGATE_INTENTS:
        return finish({
            "tool": "kg.sql_aggregate",
            "status": "error",
            "error": f"unsupported intent: {intent}",
            "query_interpretation": {"intent": intent, "target": target, "filters": {}},
            "summary": {},
            "items": [],
            "warnings": [],
        })
    if target not in AGGREGATE_TARGETS:
        return finish({
            "tool": "kg.sql_aggregate",
            "status": "error",
            "error": f"unsupported target: {target}",
            "query_interpretation": {"intent": intent, "target": target, "filters": {}},
            "summary": {},
            "items": [],
            "warnings": [],
        })
    aggregate_target = group_by[0] if intent == "group_count" and group_by and group_by[0] in AGGREGATE_GROUP_BY else target
    if receipt["unsupported_constraints"]:
        return finish(
            {
                "tool": "kg.sql_aggregate",
                "status": "unsupported",
                "query_interpretation": {"intent": intent, "target": target, "group_by": group_by, "filters": receipt["effective_filters"]},
                "summary": {},
                "items": [],
                "warnings": ["one or more requested constraints are not supported by this tool contract"],
            },
            aggregate_target=aggregate_target,
        )
    if intent == "numeric_distribution" and aggregate_target != "amount":
        return finish({
            "tool": "kg.sql_aggregate",
            "status": "error",
            "error": "numeric_distribution currently supports target=amount only",
            "query_interpretation": {"intent": intent, "target": target, "group_by": group_by, "filters": {}},
            "summary": {},
            "items": [],
            "warnings": [],
        }, aggregate_target=aggregate_target)

    filters = receipt["effective_filters"]
    limit = clamp_int(request.get("limit"), 50, 1, MAX_AGGREGATE_LIMIT)
    include_examples = bool(request.get("include_examples", True))
    applied_filters = applied_aggregate_filters(filters)
    ignored_filters: list[str] = []
    warnings: list[str] = []
    if filters["qa_policy"] != "include_all":
        warnings.append("qa_policy is recorded only in v4 and is not used for aggregate filtering")
    if ignored_filters:
        warnings.append(f"ignored aggregate filters: {', '.join(ignored_filters)}")

    store = get_store()
    filters_before_scope = dict(filters)
    filters, scope_warnings = apply_hard_search_scopes(store, filters)
    receipt["effective_filters"] = filters
    if filters.get("doc_ids") != filters_before_scope.get("doc_ids"):
        receipt["route_adjustments"].append("doc_id_scope_resolved_for_lookup")
    warnings.extend(scope_warnings)
    evidence_map = get_evidence_map(store)
    facts_map = get_facts_map(store)
    patents = get_patent_map(store)
    allowed_keys = fetch_allowed_hyperedge_keys(filters, warnings)
    all_hyperedges = iter_store_hyperedges(store)
    requested_material_ids = lower_set(filters.get("material_canonical_ids") or [])
    if requested_material_ids:
        known_material_ids = {
            str(row.get("canonical_id") or "").strip().casefold()
            for raw in all_hyperedges
            for _, row in aggregate_material_rows(raw, facts_map, None)
            if str(row.get("canonical_id") or "").strip()
        }
        unknown_material_ids = sorted(requested_material_ids - known_material_ids)
        if unknown_material_ids:
            receipt["unsupported_constraints"] = ["material_canonical_ids"]
            receipt["route_adjustments"].append("unknown_material_canonical_ids_rejected")
            return finish(
                {
                    "tool": "kg.sql_aggregate",
                    "status": "unsupported",
                    "query_interpretation": {"intent": intent, "target": target, "group_by": group_by, "filters": filters},
                    "summary": {},
                    "items": [],
                    "warnings": [f"unknown material canonical IDs: {', '.join(unknown_material_ids)}"],
                },
                aggregate_target=aggregate_target,
            )
    match_filters = dict(filters)
    matched: list[dict[str, Any]] = []
    for raw in all_hyperedges:
        if allowed_keys is not None and hyperedge_natural_key(raw) not in allowed_keys:
            continue
        if raw_matches_aggregate_filters(raw, patents, match_filters, facts_map, evidence_map):
            matched.append(raw)

    target_identities: set[tuple[Any, ...]] = set()
    missing_target_identity = 0
    for raw in matched:
        if target == "doc_id":
            doc_id = raw_hyperedge_doc_id(raw)
            identity = doc_id_lookup_key(doc_id) or normalize_match_text(doc_id)
            if identity:
                target_identities.add(("doc", identity))
        elif target == "formulation":
            identity = formulation_identity(raw)
            if identity is None:
                missing_target_identity += 1
            else:
                target_identities.add(("formulation", *identity))
        else:
            for target_item in aggregate_target_values(raw, target, patents, filters, facts_map):
                value = normalize_match_text(target_item.get("value"))
                canonical_id = normalize_match_text(target_item.get("canonical_id"))
                if value or canonical_id:
                    target_identities.add(("value", value, canonical_id))

    if aggregate_target == "resin_system":
        return finish(run_resin_system_aggregate(
            matched=matched,
            all_hyperedges=all_hyperedges,
            patents=patents,
            evidence_map=evidence_map,
            target=target,
            group_by=group_by,
            filters=filters,
            limit=limit,
            include_examples=include_examples,
            warnings=warnings,
            applied_filters=applied_filters,
            ignored_filters=ignored_filters,
        ), aggregate_target=aggregate_target)

    if intent == "numeric_distribution":
        buckets: dict[str, dict[str, Any]] = {}
        amount_count = 0
        for raw in matched:
            for item in aggregate_target_values(raw, "amount", patents, filters, facts_map):
                amount_count += 1
                unit = item.get("unit") or "unspecified"
                bucket = buckets.setdefault(
                    unit,
                    {
                        "value": "amount",
                        "unit": unit,
                        "count": 0,
                        "doc_ids": set(),
                        "assignees": set(),
                        "values": [],
                        "examples": [],
                    },
                )
                numeric = float(item["value"])
                bucket["count"] += 1
                bucket["doc_ids"].add(raw_hyperedge_doc_id(raw))
                add_bucket_assignees(bucket, raw, patents)
                bucket["values"].append(numeric)
                if include_examples:
                    add_bucket_example(bucket, raw, evidence_map, item, patents)
        items = []
        for bucket in buckets.values():
            values = bucket.pop("values")
            doc_ids = bucket.pop("doc_ids")
            assignees = bucket.pop("assignees")
            bucket["doc_count"] = len(doc_ids)
            bucket["assignee_count"] = len(assignees)
            bucket["assignees"] = sorted(assignees)[:20]
            bucket["min"] = min(values) if values else None
            bucket["max"] = max(values) if values else None
            bucket["avg"] = round(sum(values) / len(values), 6) if values else None
            if not include_examples:
                bucket["examples"] = []
            items.append(bucket)
        items = sorted(items, key=lambda row: (-int(row.get("count") or 0), str(row.get("unit") or "")))[:limit]
        return finish({
            "tool": "kg.sql_aggregate",
            "status": "ok" if items else "empty",
            "query_interpretation": {"intent": intent, "target": target, "group_by": group_by, "filters": filters},
            "summary": {
                "matched_hyperedges": len(matched),
                "amount_count": amount_count,
                "returned": len(items),
                "applied_filters": applied_filters,
                "ignored_filters": ignored_filters,
            },
            "items": items,
            "warnings": warnings,
            "generated_at": now_iso(),
        }, aggregate_target=aggregate_target)

    buckets: dict[tuple[str, str | None], dict[str, Any]] = {}
    missing_formulation_identity = 0
    for raw in matched:
        if aggregate_target == "formulation" and formulation_identity(raw) is None:
            missing_formulation_identity += 1
            continue
        for item in aggregate_target_values(raw, aggregate_target, patents, filters, facts_map):
            value = str(item.get("value") or "").strip()
            if not value:
                continue
            canonical_id = item.get("canonical_id")
            identity = formulation_identity(raw) if aggregate_target == "formulation" else None
            key = (
                "::".join(identity) if identity else value.lower(),
                str(canonical_id).lower() if canonical_id else None,
            )
            bucket = buckets.setdefault(
                key,
                {
                    "value": value,
                    "canonical_id": canonical_id,
                    "count": 0,
                    "count_identities": set(),
                    "doc_ids": set(),
                    "assignees": set(),
                    "examples": [],
                },
            )
            doc_id = raw_hyperedge_doc_id(raw)
            if aggregate_target in {"doc_id", "publication_year"}:
                count_identity = ("doc", doc_id_lookup_key(doc_id) or normalize_match_text(doc_id))
            elif aggregate_target == "formulation":
                count_identity = identity
            else:
                count_identity = ("hyperedge", *hyperedge_natural_key(raw))
            bucket["count_identities"].add(count_identity)
            bucket["count"] = len(bucket["count_identities"])
            if doc_id:
                bucket["doc_ids"].add(doc_id_lookup_key(doc_id) or normalize_match_text(doc_id))
            add_bucket_assignees(bucket, raw, patents)
            for extra_key in ("doc_id", "object_id", "hyperedge_id", "sample_id", "property", "material_role"):
                if item.get(extra_key) is not None and bucket.get(extra_key) is None:
                    bucket[extra_key] = item.get(extra_key)
            if include_examples:
                add_bucket_example(bucket, raw, evidence_map, item, patents)

    items: list[dict[str, Any]] = []
    for bucket in buckets.values():
        bucket.pop("count_identities", None)
        doc_ids = bucket.pop("doc_ids")
        assignees = bucket.pop("assignees")
        bucket["doc_count"] = len(doc_ids)
        bucket["assignee_count"] = len(assignees)
        bucket["assignees"] = sorted(assignees)[:20]
        if not include_examples:
            bucket["examples"] = []
        items.append(bucket)
    items = sorted(items, key=lambda row: (-int(row.get("count") or 0), str(row.get("value") or "")))[:limit]
    total_count = len(target_identities)
    group_count = len(buckets) if intent == "group_count" else None
    if missing_formulation_identity:
        warnings.append(f"excluded_formulations_missing_sample_and_context:{missing_formulation_identity}")
    if missing_target_identity and target == "formulation" and not missing_formulation_identity:
        warnings.append(f"excluded_formulations_missing_sample_and_context:{missing_target_identity}")
    return finish({
        "tool": "kg.sql_aggregate",
        "status": "ok" if items else "empty",
        "query_interpretation": {"intent": intent, "target": target, "group_by": group_by, "filters": filters},
        "summary": {
            "matched_hyperedges": len(matched),
            "total_count": total_count,
            "distinct_count": total_count,
            "group_count": group_count,
            "returned": len(items),
            "applied_filters": applied_filters,
            "ignored_filters": ignored_filters,
        },
        "items": items,
        "warnings": warnings,
        "generated_at": now_iso(),
    }, aggregate_target=aggregate_target)


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(value):.8f}" for value in values) + "]"


def embed_query(query: str) -> tuple[list[float], dict[str, float]]:
    body = {
        "texts": [query],
        "return_sparse": True,
        "sparse_max_terms": 256,
        "normalize": True,
    }
    req = urllib.request.Request(
        EMBED_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    dense_rows = payload.get("dense") or []
    if not dense_rows or not isinstance(dense_rows[0], list):
        raise ValueError("embedding service returned no dense vector")
    sparse_rows = payload.get("sparse") or []
    sparse_raw = sparse_rows[0] if sparse_rows and isinstance(sparse_rows[0], dict) else {}
    sparse: dict[str, float] = {}
    for token_id, weight in sparse_raw.items():
        try:
            value = float(weight)
        except (TypeError, ValueError):
            continue
        token_text = str(token_id).strip()
        if token_text and value:
            sparse[token_text] = value
    return [float(x) for x in dense_rows[0]], sparse


def search_filter_sql(alias: str, filters: dict[str, Any]) -> tuple[list[str], list[Any]]:
    clauses = [f"{alias}.object_type = 'hyperedge'"]
    params: list[Any] = []
    if filters["doc_ids"]:
        clauses.append(f"{alias}.doc_id = ANY(%s)")
        params.append(filters["doc_ids"])
    if filters["properties"]:
        clauses.append(
            f"(({alias}.metadata->>'property') = ANY(%s) "
            f"OR ({alias}.metadata->>'property_canonical_id') = ANY(%s) "
            f"OR ({alias}.metadata->'property'->>'name') = ANY(%s) "
            f"OR ({alias}.metadata->'property'->>'canonical_id') = ANY(%s))"
        )
        params.extend([filters["properties"], filters["properties"], filters["properties"], filters["properties"]])
    if filters["polarity"]:
        clauses.append(f"({alias}.metadata->>'polarity') = ANY(%s)")
        params.append(filters["polarity"])
    if filters["example_kind"]:
        clauses.append(f"({alias}.metadata->>'example_kind') = ANY(%s)")
        params.append(filters["example_kind"])
    return clauses, params


def row_dicts(cursor: Any, rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


def optional_score(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def search_dense(conn: Any, query_vec: list[float], filters: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    vector = vector_literal(query_vec)
    clauses, params = search_filter_sql("o", filters)
    sql = f"""
        SELECT
            o.object_type,
            o.object_id,
            o.doc_id,
            o.text_for_embedding,
            o.metadata,
            1 - (o.dense_embedding <=> %s::vector) AS dense_score
        FROM retrieval_objects o
        WHERE {' AND '.join(clauses)}
        ORDER BY o.dense_embedding <=> %s::vector, o.object_id
        LIMIT %s
    """
    with conn.cursor() as cur:
        try:
            cur.execute("SET LOCAL hnsw.ef_search = 100")
        except Exception:
            conn.rollback()
        cur.execute(sql, [vector, *params, vector, limit])
        return row_dicts(cur, cur.fetchall())


def search_sparse(conn: Any, lexical_weights: dict[str, float], filters: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    if not lexical_weights:
        return []
    terms = sorted(lexical_weights.items(), key=lambda item: (-item[1], item[0]))[:256]
    values_sql = ", ".join(["(%s, %s)"] * len(terms))
    term_params: list[Any] = []
    for token_id, weight in terms:
        term_params.extend([token_id, float(weight)])
    clauses, filter_params = search_filter_sql("o", filters)
    sql = f"""
        WITH query_terms(token_id, query_weight) AS (
            VALUES {values_sql}
        )
        SELECT
            o.object_type,
            o.object_id,
            o.doc_id,
            o.text_for_embedding,
            o.metadata,
            SUM(p.weight * q.query_weight) AS sparse_score
        FROM query_terms q
        JOIN retrieval_sparse_postings p
          ON p.token_id = q.token_id
         AND p.object_type = 'hyperedge'
        JOIN retrieval_objects o
          ON o.object_type = p.object_type
         AND o.object_id = p.object_id
        WHERE {' AND '.join(clauses)}
        GROUP BY o.object_type, o.object_id, o.doc_id, o.text_for_embedding, o.metadata
        ORDER BY sparse_score DESC, o.object_id
        LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, [*term_params, *filter_params, limit])
        return row_dicts(cur, cur.fetchall())


def search_soft_property_candidates(conn: Any, filters: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    property_ids = property_canonical_ids_for_filters(filters)
    if not property_ids:
        return []
    clauses, filter_params = search_filter_sql("o", filters)
    clauses.append(
        "(o.metadata->>'property_canonical_id' = ANY(%s) "
        "OR o.metadata->'property'->>'canonical_id' = ANY(%s))"
    )
    sql = f"""
        SELECT
            o.object_type,
            o.object_id,
            o.doc_id,
            o.text_for_embedding,
            o.metadata,
            1.0 AS property_soft_score
        FROM retrieval_objects o
        WHERE {' AND '.join(clauses)}
        ORDER BY o.object_id
        LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, [*filter_params, property_ids, property_ids, limit])
        return row_dicts(cur, cur.fetchall())


MATERIAL_QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "by",
    "for",
    "in",
    "of",
    "the",
    "to",
    "use",
    "used",
    "uses",
    "using",
    "where",
    "which",
    "with",
    "coating",
    "coatings",
    "composition",
    "compositions",
    "formula",
    "formulation",
    "formulations",
    "paint",
    "paints",
    "catalyst",
    "catalysts",
    "component",
    "components",
    "material",
    "materials",
    "配方",
    "涂料",
    "催化剂",
    "哪些",
    "哪个",
    "使用",
    "用了",
    "作",
}


def material_recall_compact(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").casefold())


def material_query_terms(query: str) -> set[str]:
    text = str(query or "").casefold()
    words = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}", text)
    useful = [word for word in words if word not in MATERIAL_QUERY_STOPWORDS]
    terms: set[str] = set()
    dbto_specific = (
        "dbto" in text
        or "二丁基氧化锡" in text
        or ("dibutyl" in useful and "tin" in useful and ("oxide" in useful or "dioxide" in useful))
        or (("dibutyltin" in useful or "dibutyltinoxide" in text) and ("oxide" in useful or "dioxide" in useful))
    )
    if dbto_specific:
        return {
            "dbto",
            "dibutyltinoxide",
            "dibutyltindioxide",
            "dibutyltinoxidee6278",
            "dibutyltindioxidee6278",
            "二丁基氧化锡",
        }
    if len(useful) == 1:
        compacted = material_recall_compact(useful[0])
        if len(compacted) >= 3:
            terms.add(compacted)
    for size in (2, 3, 4):
        for index in range(0, max(0, len(useful) - size + 1)):
            compacted = material_recall_compact("".join(useful[index : index + size]))
            if len(compacted) >= 6:
                terms.add(compacted)
    compact_query = material_recall_compact(text)
    if len(compact_query) >= 6:
        terms.add(compact_query)
    if "dbto" in terms or "dbto" in text or "二丁基氧化锡" in text:
        terms.update(
            {
                "dbto",
                "dibutyltinoxide",
                "dibutyltindioxide",
                "dibutyltinoxidee6278",
                "dibutyltindioxidee6278",
                "二丁基氧化锡",
            }
        )
    return terms


def material_fact_text(fact: dict[str, Any]) -> str:
    return " ".join(
        str(fact.get(key) or "")
        for key in (
            "material",
            "material_canonical_id",
            "material_role",
            "canonical_id",
            "role",
            "value_text",
        )
    )


def material_fact_match_score(fact: dict[str, Any], query_terms: set[str]) -> float:
    if not query_terms:
        return 0.0
    haystack = material_recall_compact(material_fact_text(fact))
    if not haystack:
        return 0.0
    score = 0.0
    for term in query_terms:
        if not term or term not in haystack:
            continue
        if term == "dbto":
            score = max(score, 1.3)
        elif term in {"dibutyltinoxide", "dibutyltindioxide", "dibutyltinoxidee6278", "dibutyltindioxidee6278"}:
            score = max(score, 1.25)
        elif len(term) >= 8:
            score = max(score, 1.15)
        else:
            score = max(score, 1.0)
    return score


def material_fact_matches_query(fact: dict[str, Any], query_terms: set[str]) -> bool:
    return material_fact_match_score(fact, query_terms) > 0.0


def fact_identity(fact: dict[str, Any], fallback: str = "") -> str:
    fact_id = fact.get("fact_id") or fact.get("id") or fallback
    doc_id = fact.get("doc_id") or fact.get("patent_id") or fact.get("publication_number") or ""
    return f"{doc_id}::{fact_id}" if doc_id and fact_id else str(fact_id or fallback)


def fact_hyperedge_keys(fact: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for key in ("source_hyperedge_id", "hyperedge_id", "raw_hyperedge_id", "object_id"):
        value = str(fact.get(key) or "").strip()
        if value:
            keys.add(value)
            doc_id = str(fact.get("doc_id") or "").strip()
            if doc_id and "::" not in value:
                keys.add(f"{doc_id}::{value}")
    return keys


def raw_matches_search_hard_filters(
    raw: dict[str, Any],
    filters: dict[str, Any],
    facts_map: dict[str, dict[str, Any]] | None = None,
    patents: dict[str, dict[str, Any]] | None = None,
) -> bool:
    doc_ids = set(filters.get("doc_ids") or [])
    if doc_ids and raw_hyperedge_doc_id(raw) not in doc_ids:
        return False
    for filter_key, raw_key in (
        ("properties", "property"),
        ("polarity", "polarity"),
        ("example_kind", "example_kind"),
    ):
        wanted = lower_set(filters.get(filter_key) or [])
        if wanted and not item_matches_filter(
            {"value": raw.get(raw_key), "canonical_id": raw.get(f"{raw_key}_canonical_id")},
            wanted,
        ):
            return False
    role_filter = lower_set(filters.get("material_roles") or [])
    if role_filter and not aggregate_material_rows(raw, facts_map, role_filter):
        return False
    material_rows_for_filter = aggregate_material_rows(raw, facts_map, role_filter or None)
    wanted_materials = lower_set(filters.get("materials") or [])
    if wanted_materials:
        material_items = [
            normalize_value_item(
                row.get("material") or row.get("name") or row.get("label"),
                row.get("canonical_id"),
            )
            for _, row in material_rows_for_filter
        ]
        if not any(item and item_matches_filter(item, wanted_materials) for item in material_items):
            return False
    wanted_material_ids = lower_set(filters.get("material_canonical_ids") or [])
    if wanted_material_ids:
        actual_ids = {
            str(row.get("canonical_id") or "").strip().casefold()
            for _, row in material_rows_for_filter
            if str(row.get("canonical_id") or "").strip()
        }
        if not wanted_material_ids.intersection(actual_ids):
            return False
    wanted_resin_systems = lower_set(filters.get("resin_systems") or [])
    if wanted_resin_systems:
        patent = patent_for_doc(patents or {}, raw_hyperedge_doc_id(raw))
        systems = resin_system_candidates(raw, patent)
        if not any(item_matches_filter({"value": item.get("value")}, wanted_resin_systems) for item in systems):
            return False
    return True


def filter_search_items_by_hard_material_roles(
    items: list[dict[str, Any]],
    store: Any,
    filters: dict[str, Any],
) -> list[dict[str, Any]]:
    if not any(filters.get(key) for key in ("material_roles", "materials", "material_canonical_ids", "resin_systems")):
        return items
    facts_map = get_facts_map(store)
    patents = get_patent_map(store)
    raw_by_natural_key: dict[tuple[str, str], dict[str, Any]] = {}
    raw_by_object_id: dict[str, dict[str, Any]] = {}
    for raw in iter_store_hyperedges(store):
        doc_id = raw_hyperedge_doc_id(raw)
        hyperedge_id = raw_hyperedge_id(raw)
        if doc_id and hyperedge_id:
            raw_by_natural_key[(doc_id, hyperedge_id)] = raw
        for object_id in object_id_candidates(raw):
            raw_by_object_id[object_id] = raw
    filtered: list[dict[str, Any]] = []
    for item in items:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        object_id = str(item.get("object_id") or "")
        doc_id = str(item.get("doc_id") or "")
        hyperedge_id = str(
            metadata.get("raw_hyperedge_id")
            or metadata.get("hyperedge_id")
            or item.get("hyperedge_id")
            or ""
        )
        raw = raw_by_object_id.get(object_id) or raw_by_natural_key.get((doc_id, hyperedge_id))
        if raw and raw_matches_search_hard_filters(raw, filters, facts_map, patents):
            filtered.append(item)
    return filtered


def compact_material_fact(fact: dict[str, Any]) -> dict[str, Any]:
    return {
        "fact_id": fact.get("fact_id") or fact.get("id"),
        "material": fact.get("material"),
        "material_canonical_id": fact.get("material_canonical_id") or fact.get("canonical_id"),
        "material_role": fact.get("material_role") or fact.get("role"),
        "value": fact.get("value"),
        "unit": fact.get("unit"),
        "context_id": fact.get("context_id"),
        "evidence_id": fact.get("evidence_id"),
    }


def material_fact_recall_text(raw: dict[str, Any], facts: list[dict[str, Any]]) -> str:
    parts = [
        f"object: hyperedge",
        f"doc_id: {raw_hyperedge_doc_id(raw)}",
        f"hyperedge_id: {raw_hyperedge_id(raw)}",
        f"context_id: {raw.get('context_id') or ''}",
        f"sample: {raw.get('sample_id') or raw.get('sample_label') or ''}",
    ]
    material_parts = []
    for fact in facts:
        compact = compact_material_fact(fact)
        material_parts.append(
            " ".join(
                str(compact.get(key) or "")
                for key in ("material", "material_canonical_id", "material_role", "value", "unit")
            ).strip()
        )
    if material_parts:
        parts.append("material_facts: " + "; ".join(part for part in material_parts if part))
    raw_materials = compact_materials(raw)
    if raw_materials:
        parts.append(
            "materials: "
            + "; ".join(
                " ".join(
                    str(item.get(key) or "")
                    for key in ("role", "material", "canonical_id", "value", "unit")
                ).strip()
                for item in raw_materials[:20]
            )
        )
    return " | ".join(part for part in parts if part.strip())


def material_fact_row_sort_key(item: dict[str, Any]) -> tuple[float, float, str, str, str]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return (
        -float(item.get("score") or 0.0),
        -float(item.get("material_fact_score") or 0.0),
        str(item.get("doc_id") or ""),
        str(metadata.get("context_id") or ""),
        str(item.get("object_id") or ""),
    )


def diversify_material_fact_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    doc_order: list[str] = []
    for row in sorted(rows, key=material_fact_row_sort_key):
        doc_key = str(row.get("doc_id") or row.get("object_id") or "")
        if doc_key not in buckets:
            buckets[doc_key] = []
            doc_order.append(doc_key)
        buckets[doc_key].append(row)

    out: list[dict[str, Any]] = []
    indexes = {doc_key: 0 for doc_key in doc_order}
    while True:
        appended = False
        for doc_key in doc_order:
            bucket = buckets[doc_key]
            index = indexes[doc_key]
            if index >= len(bucket):
                continue
            out.append(bucket[index])
            indexes[doc_key] = index + 1
            appended = True
        if not appended:
            break
    return out


def has_material_fact_channel(item: dict[str, Any]) -> bool:
    channels = item.get("channels") if isinstance(item.get("channels"), list) else []
    return "material_fact" in channels


def diversify_material_fact_search_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    material_items = [item for item in items if has_material_fact_channel(item)]
    if not material_items:
        return items
    other_items = [item for item in items if not has_material_fact_channel(item)]
    return diversify_material_fact_rows(material_items) + other_items


MATERIAL_FACT_PRIORITY_QUERY_MARKERS = (
    "dbto",
    "dibutyl tin oxide",
    "dibutyltin oxide",
    "zinc-rich",
    "zinc rich",
    "zinc powder",
    "zinc dust",
    "zinc pigment",
    "graphene",
    "phosphate",
    "\u4e8c\u4e01\u57fa\u6c27\u5316\u9521",
    "\u5bcc\u950c",
    "\u950c\u7c89",
    "\u77f3\u58a8\u70ef",
    "\u78f7\u9178\u76d0",
)


def material_fact_priority_query(query: str) -> bool:
    text = str(query or "").casefold()
    return any(marker.casefold() in text for marker in MATERIAL_FACT_PRIORITY_QUERY_MARKERS)

SUBSTRATE_FACT_QUERY_MARKERS = (
    "substrate",
    "base material",
    "base panel",
    "cfrp",
    "pa-cf",
    "carbon fiber reinforced",
    "carbon fibre reinforced",
    "carbon-fiber reinforced",
    "carbon-fibre reinforced",
    "基底",
    "基材",
    "底材",
    "碳纤维复合",
    "碳纤维增强",
)


def substrate_query_terms(query: str) -> set[str]:
    text = str(query or "").casefold()
    if not any(marker.casefold() in text for marker in SUBSTRATE_FACT_QUERY_MARKERS):
        return set()
    terms: set[str] = set()
    if any(marker in text for marker in ("cfrp", "pa-cf")) or "碳纤维" in text:
        terms.update(
            {
                "cfrp",
                "carbonfiberreinforcedplastic",
                "carbonfiberreinforcedcomposite",
                "carbonfibercomposite",
                "碳纤维复合材料",
                "碳纤维增强复合材料",
            }
        )
    for phrase in (
        "carbon fiber reinforced plastic",
        "carbon fibre reinforced plastic",
        "carbon fiber reinforced composite",
        "carbon fibre reinforced composite",
        "carbon fiber composite",
        "carbon fibre composite",
    ):
        if phrase in text:
            terms.add(material_recall_compact(phrase))
    compact_query = material_recall_compact(text)
    if len(compact_query) >= 6:
        terms.add(compact_query)
    return terms


def substrate_fact_match_score(values: list[Any], query_terms: set[str]) -> float:
    if not query_terms:
        return 0.0
    haystack = material_recall_compact(" ".join(str(value or "") for value in values))
    if not haystack:
        return 0.0
    score = 0.0
    for term in query_terms:
        if not term:
            continue
        compacted = material_recall_compact(term)
        if compacted and (compacted in haystack or haystack in compacted):
            score = max(score, 1.25 if compacted in {"cfrp", "碳纤维复合材料", "碳纤维增强复合材料"} else 1.0)
    return score


def compact_substrate_fact(value: Any) -> dict[str, Any]:
    return {"slot": "substrate", "substrate": str(value or "").strip()}


def substrate_fact_recall_text(raw: dict[str, Any], values: list[Any]) -> str:
    return " | ".join(
        [
            "object: hyperedge",
            f"doc_id: {raw_hyperedge_doc_id(raw)}",
            f"sample: {raw.get('sample_id') or raw.get('sample_label') or ''}",
            "slot: substrate",
            "substrates: " + "; ".join(str(value or "") for value in values[:12]),
        ]
    )


def substrate_fact_row_sort_key(item: dict[str, Any]) -> tuple[float, float, str, str]:
    return (
        -float(item.get("score") or 0.0),
        -float(item.get("substrate_fact_score") or 0.0),
        str(item.get("doc_id") or ""),
        str(item.get("object_id") or ""),
    )


def diversify_substrate_fact_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    doc_order: list[str] = []
    for row in sorted(rows, key=substrate_fact_row_sort_key):
        doc_key = str(row.get("doc_id") or row.get("object_id") or "")
        if doc_key not in buckets:
            buckets[doc_key] = []
            doc_order.append(doc_key)
        buckets[doc_key].append(row)
    out: list[dict[str, Any]] = []
    indexes = {doc_key: 0 for doc_key in doc_order}
    while True:
        appended = False
        for doc_key in doc_order:
            bucket = buckets[doc_key]
            index = indexes[doc_key]
            if index >= len(bucket):
                continue
            out.append(bucket[index])
            indexes[doc_key] = index + 1
            appended = True
        if not appended:
            break
    return out


def has_substrate_fact_channel(item: dict[str, Any]) -> bool:
    channels = item.get("channels") if isinstance(item.get("channels"), list) else []
    return "substrate_fact" in channels


def diversify_substrate_fact_search_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    substrate_items = [item for item in items if has_substrate_fact_channel(item)]
    if not substrate_items:
        return items
    other_items = [item for item in items if not has_substrate_fact_channel(item)]
    return diversify_substrate_fact_rows(substrate_items) + other_items


TEST_FACT_IMMERSION_QUERY_MARKERS = (
    "immersion",
    "submersion",
    "immersed",
    "soak",
    "soaking",
    "seawater",
    "sea water",
    "salt water",
    "saltwater",
    "marine immersion",
    "浸泡",
    "浸水",
    "海水",
    "挂板",
)

TEST_FACT_SEAWATER_TEXT_MARKERS = (
    "seawater",
    "sea water",
    "salt water",
    "saltwater",
    "natural seawater",
    "marine immersion",
    "submersion",
    "海水",
)

TEST_FACT_IMMERSION_TEXT_MARKERS = TEST_FACT_SEAWATER_TEXT_MARKERS + (
    "immersion",
    "immersed",
    "soak",
    "soaked",
    "soaking",
    "water immersion",
    "freshwater",
    "fresh water",
    "fw immersion",
    "sw immersion",
    "浸泡",
    "浸水",
)


def query_requests_test_fact_recall(query: str) -> bool:
    text = str(query or "").casefold()
    return any(marker in text for marker in TEST_FACT_IMMERSION_QUERY_MARKERS)


def test_fact_recall_text(
    raw: dict[str, Any],
    facts: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
) -> str:
    parts = [
        f"object: hyperedge",
        f"doc_id: {raw_hyperedge_doc_id(raw)}",
        f"hyperedge_id: {raw_hyperedge_id(raw)}",
        f"context_id: {raw.get('context_id') or ''}",
        f"sample: {raw.get('sample_id') or raw.get('sample_label') or ''}",
        f"property: {raw.get('property') or ''}",
        f"property_canonical_id: {raw.get('property_canonical_id') or ''}",
        f"test_method: {json.dumps(raw.get('test_method'), ensure_ascii=False)}",
        f"test_condition: {json.dumps(raw.get('test_condition'), ensure_ascii=False)}",
        f"result: {json.dumps(raw.get('result'), ensure_ascii=False)}",
    ]
    if facts:
        parts.append("facts: " + "; ".join(json.dumps(fact, ensure_ascii=False) for fact in facts[:8]))
    if evidence_rows:
        evidence_bits = []
        for evidence in evidence_rows[:4]:
            evidence_bits.append(
                " ".join(
                    str(evidence.get(key) or "")
                    for key in ("section", "table", "row_label", "quote")
                ).strip()
            )
        parts.append("evidence: " + "; ".join(bit for bit in evidence_bits if bit))
    return " | ".join(part for part in parts if str(part).strip())


def property_canonical_id_from_raw(raw: dict[str, Any]) -> str:
    value = raw.get("property_canonical_id")
    if value:
        return str(value)
    prop = raw.get("property")
    if isinstance(prop, dict):
        return str(prop.get("canonical_id") or "")
    return ""


def test_fact_immersion_score(
    raw: dict[str, Any],
    facts: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
) -> float:
    text = test_fact_recall_text(raw, facts, evidence_rows).casefold()
    prop_id = property_canonical_id_from_raw(raw)
    immersion_ids = set(PROPERTY_FAMILY_CANONICAL_IDS.get("marine_immersion_durability", ()))
    score = 0.0
    if prop_id in immersion_ids:
        score = max(score, 2.4)
    if any(marker in text for marker in TEST_FACT_SEAWATER_TEXT_MARKERS):
        score = max(score, 2.2)
    if any(marker in text for marker in TEST_FACT_IMMERSION_TEXT_MARKERS):
        score = max(score, 2.0)
    if "raft" in text and any(marker in text for marker in ("sea", "marine", "singapore", "spain", "sandefjord")):
        score = max(score, 1.8)
    return score


def test_fact_row_sort_key(item: dict[str, Any]) -> tuple[float, float, str, str, str]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return (
        -float(item.get("score") or 0.0),
        -float(item.get("test_fact_score") or 0.0),
        str(item.get("doc_id") or ""),
        str(metadata.get("context_id") or ""),
        str(item.get("object_id") or ""),
    )


def diversify_test_fact_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    doc_order: list[str] = []
    for row in sorted(rows, key=test_fact_row_sort_key):
        doc_key = str(row.get("doc_id") or row.get("object_id") or "")
        if doc_key not in buckets:
            buckets[doc_key] = []
            doc_order.append(doc_key)
        buckets[doc_key].append(row)
    out: list[dict[str, Any]] = []
    indexes = {doc_key: 0 for doc_key in doc_order}
    while True:
        appended = False
        for doc_key in doc_order:
            bucket = buckets[doc_key]
            index = indexes[doc_key]
            if index >= len(bucket):
                continue
            out.append(bucket[index])
            indexes[doc_key] = index + 1
            appended = True
        if not appended:
            break
    return out


def has_test_fact_channel(item: dict[str, Any]) -> bool:
    channels = item.get("channels") if isinstance(item.get("channels"), list) else []
    return "test_fact" in channels


def diversify_test_fact_search_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    test_items = [item for item in items if has_test_fact_channel(item)]
    if not test_items:
        return items
    other_items = [item for item in items if not has_test_fact_channel(item)]
    return diversify_test_fact_rows(test_items) + other_items


def test_fact_recall_rows(
    store: Any,
    query: str,
    filters: dict[str, Any],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    if not query_requests_test_fact_recall(query):
        return []
    facts_map = get_facts_map(store)
    evidence_map = get_evidence_map(store)
    rows: list[dict[str, Any]] = []
    for raw in iter_store_hyperedges(store):
        if not raw_matches_search_hard_filters(raw, filters, facts_map):
            continue
        facts = raw_fact_rows(raw, facts_map)
        evidence_rows = raw_evidence_rows(raw, evidence_map)
        score = test_fact_immersion_score(raw, facts, evidence_rows)
        if score <= 0.0:
            continue
        object_id = raw_hyperedge_object_id(raw)
        if not object_id:
            continue
        metadata = {
            "hyperedge_id": raw_hyperedge_id(raw),
            "raw_hyperedge_id": raw_hyperedge_id(raw),
            "sample_id": raw.get("sample_id") or raw.get("sample_label"),
            "context_id": raw.get("context_id"),
            "property": raw.get("property"),
            "property_canonical_id": property_canonical_id_from_raw(raw),
            "test_method": raw.get("test_method"),
        }
        rows.append(
            {
                "object_type": "hyperedge",
                "object_id": object_id,
                "doc_id": raw_hyperedge_doc_id(raw),
                "text_for_embedding": test_fact_recall_text(raw, facts, evidence_rows),
                "metadata": metadata,
                "test_fact_score": round(score, 6),
                "score": round(1.0 + score, 6),
                "channels": ["test_fact"],
            }
        )
    return diversify_test_fact_rows(rows)[:limit]


NUMERIC_FACT_SALT_QUERY_MARKERS = (
    "salt spray",
    "salt fog",
    "neutral salt spray",
    "nss",
    "aass",
    "astm b117",
    "iso 9227",
    "jis k5600",
    "盐雾",
)

NUMERIC_FACT_SALT_TEXT_MARKERS = NUMERIC_FACT_SALT_QUERY_MARKERS + (
    "salt_spray",
    "salt_fog",
    "corrosion_resistance_salt",
    "corrosion resistance salt",
)

NUMERIC_FACT_THICKNESS_QUERY_MARKERS = (
    "film thickness",
    "dry film thickness",
    "coating thickness",
    "dft",
    "膜厚",
)

NUMERIC_GREATER_MARKERS = (
    ">=",
    ">",
    "超过",
    "大于",
    "高于",
    "以上",
    "不少于",
    "at least",
    "more than",
    "over",
    "greater than",
    "above",
)

NUMERIC_LESS_MARKERS = (
    "<=",
    "<",
    "小于",
    "低于",
    "少于",
    "以下",
    "不超过",
    "less than",
    "under",
    "below",
    "no more than",
)

DURATION_UNIT_TO_HOURS = {
    "h": 1.0,
    "hr": 1.0,
    "hrs": 1.0,
    "hour": 1.0,
    "hours": 1.0,
    "小时": 1.0,
    "day": 24.0,
    "days": 24.0,
    "d": 24.0,
    "天": 24.0,
    "week": 168.0,
    "weeks": 168.0,
    "wk": 168.0,
    "wks": 168.0,
    "周": 168.0,
}

THICKNESS_UNIT_TO_UM = {
    "um": 1.0,
    "µm": 1.0,
    "μm": 1.0,
    "micron": 1.0,
    "microns": 1.0,
    "micrometer": 1.0,
    "micrometers": 1.0,
    "微米": 1.0,
    "mm": 1000.0,
    "millimeter": 1000.0,
    "millimeters": 1000.0,
    "in": 25400.0,
    "inch": 25400.0,
    "inches": 25400.0,
    "mil": 25.4,
    "mils": 25.4,
}

DURATION_VALUE_RE = re.compile(
    r"(?<![A-Za-z0-9.])(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>hours?|hrs?|h|days?|d|weeks?|wks?|wk|小时|天|周)(?![A-Za-z0-9])",
    re.IGNORECASE,
)

THICKNESS_VALUE_RE = re.compile(
    r"(?<![A-Za-z0-9.])(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>µm|μm|um|mm|in|inch(?:es)?|microns?|micrometers?|millimeters?|mils?|微米)(?![A-Za-z0-9])",
    re.IGNORECASE,
)

NUMERIC_COMPARISON_VALUE_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>µm|μm|um|mm|in|inch(?:es)?|microns?|micrometers?|millimeters?|mils?|hours?|hrs?|h|days?|d|weeks?|wks?|wk|小时|天|周|微米)(?![A-Za-z0-9])",
    re.IGNORECASE,
)

THICKNESS_PATH_MARKERS = (
    "film_thickness",
    "film thickness",
    "dry_film_thickness",
    "dry film thickness",
    "coating_thickness",
    "coating thickness",
    "dft",
    "膜厚",
)

THICKNESS_EXCLUDED_PATH_MARKERS = (
    "loss",
    "change",
    "rate",
    "polish",
    "polishing",
    "erosion",
    "wear",
    "abrasion",
)


def normalized_numeric_text(value: Any) -> str:
    text = str(value or "").casefold()
    return text.replace("_", " ").replace("-", " ")


def numeric_query_kind(query: str) -> str | None:
    text = normalized_numeric_text(query)
    if any(marker in text for marker in NUMERIC_FACT_SALT_QUERY_MARKERS):
        return "duration_hours"
    if any(marker in text for marker in NUMERIC_FACT_THICKNESS_QUERY_MARKERS):
        return "film_thickness_um"
    return None


def hyperedge_natural_key(raw: dict[str, Any]) -> tuple[str, str] | None:
    doc_id = raw_hyperedge_doc_id(raw)
    hyperedge_id = raw_hyperedge_id(raw)
    return (doc_id, hyperedge_id) if doc_id and hyperedge_id else None


def unit_kind(unit: str) -> str | None:
    key = str(unit or "").casefold()
    if key in DURATION_UNIT_TO_HOURS:
        return "duration_hours"
    if key in THICKNESS_UNIT_TO_UM:
        return "film_thickness_um"
    return None


def comparison_op_from_window(text: str, start: int, end: int) -> str | None:
    before = text[max(0, start - 36) : start]
    after = text[end : min(len(text), end + 36)]
    window = before + " " + after
    if any(marker in window for marker in NUMERIC_LESS_MARKERS):
        return "le" if any(marker in window for marker in ("<=", "不超过", "no more than")) else "lt"
    if any(marker in window for marker in NUMERIC_GREATER_MARKERS):
        return "ge" if any(marker in window for marker in (">=", "不少于", "at least")) else "gt"
    return None


def parse_numeric_query_comparisons(query: str) -> list[tuple[str, str, float]]:
    text = normalized_numeric_text(query)
    wanted_kind = numeric_query_kind(query)
    out: list[tuple[str, str, float]] = []
    for match in NUMERIC_COMPARISON_VALUE_RE.finditer(text):
        kind = unit_kind(match.group("unit"))
        if not kind or (wanted_kind and kind != wanted_kind):
            continue
        op = comparison_op_from_window(text, match.start(), match.end())
        if not op:
            continue
        value = float(match.group("value"))
        if kind == "duration_hours":
            value *= DURATION_UNIT_TO_HOURS[str(match.group("unit")).casefold()]
        elif kind == "film_thickness_um":
            value *= THICKNESS_UNIT_TO_UM[str(match.group("unit")).casefold()]
        out.append((kind, op, value))
    return out


def compare_numeric_value(value: float, op: str, threshold: float) -> bool:
    if op == "gt":
        return value > threshold
    if op == "ge":
        return value >= threshold
    if op == "lt":
        return value < threshold
    if op == "le":
        return value <= threshold
    return False


def text_has_salt_spray_context(text: str) -> bool:
    blob = normalized_numeric_text(text)
    return any(marker in blob for marker in NUMERIC_FACT_SALT_TEXT_MARKERS)


def extract_duration_hours(text: str) -> list[tuple[float, str]]:
    out: list[tuple[float, str]] = []
    for match in DURATION_VALUE_RE.finditer(str(text or "")):
        unit = str(match.group("unit")).casefold()
        factor = DURATION_UNIT_TO_HOURS.get(unit)
        if factor is None:
            continue
        raw_value = float(match.group("value"))
        out.append((raw_value * factor, match.group(0)))
    return out


def extract_thickness_um_from_text(text: str) -> list[tuple[float, str]]:
    out: list[tuple[float, str]] = []
    for match in THICKNESS_VALUE_RE.finditer(str(text or "")):
        unit = str(match.group("unit")).casefold()
        factor = THICKNESS_UNIT_TO_UM.get(unit)
        if factor is None:
            continue
        raw_value = float(match.group("value"))
        out.append((raw_value * factor, match.group(0)))
    return out


def scalar_numeric_value(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if re.fullmatch(r"\d+(?:\.\d+)?", stripped):
            return float(stripped)
    return None


def path_has_thickness_exclusion(path_text: str) -> bool:
    tokens = {token for token in re.split(r"[^a-z0-9]+", path_text) if token}
    return bool(tokens & set(THICKNESS_EXCLUDED_PATH_MARKERS))


def extract_thickness_from_controlled_value(path: str, value: Any) -> list[tuple[float, str]]:
    path_text = normalized_numeric_text(path)
    if path_has_thickness_exclusion(path_text):
        return []
    out: list[tuple[float, str]] = []
    if isinstance(value, dict):
        numeric_value = scalar_numeric_value(value.get("value") or value.get("amount"))
        unit = str(value.get("unit") or value.get("units") or "").casefold()
        if numeric_value is not None and unit in THICKNESS_UNIT_TO_UM:
            out.append((numeric_value * THICKNESS_UNIT_TO_UM[unit], f"{numeric_value:g} {unit}"))
        elif numeric_value is not None and not unit and any(marker in path_text for marker in THICKNESS_PATH_MARKERS):
            out.append((numeric_value, f"{numeric_value:g} um"))
        for child_key, child_value in value.items():
            child_path = f"{path}.{child_key}" if path else str(child_key)
            out.extend(extract_thickness_from_controlled_value(child_path, child_value))
        return out
    if isinstance(value, list):
        for index, item in enumerate(value):
            out.extend(extract_thickness_from_controlled_value(f"{path}[{index}]", item))
        return out
    if any(marker in path_text for marker in THICKNESS_PATH_MARKERS):
        out.extend(extract_thickness_um_from_text(str(value or "")))
        last_token = next((token for token in reversed(re.split(r"[^a-z0-9]+", path_text)) if token), "")
        numeric_value = scalar_numeric_value(value)
        if numeric_value is not None and last_token not in {"value", "amount"}:
            out.append((numeric_value, f"{numeric_value:g} um"))
    return out


def extract_structured_film_thickness_um(raw: dict[str, Any]) -> list[tuple[float, str]]:
    out: list[tuple[float, str]] = []
    for key in ("film_thickness", "coating_thickness", "dry_film_thickness", "dft"):
        if key in raw:
            out.extend(extract_thickness_from_controlled_value(key, raw.get(key)))
    for key in ("substrate", "sample", "primary_context", "use_context", "context_fields"):
        if key in raw:
            out.extend(extract_thickness_from_controlled_value(key, raw.get(key)))
    deduped: list[tuple[float, str]] = []
    seen: set[tuple[float, str]] = set()
    for value, label in out:
        key = (round(float(value), 6), str(label))
        if key in seen:
            continue
        seen.add(key)
        deduped.append((float(value), str(label)))
    return deduped


def numeric_fact_row_sort_key(item: dict[str, Any]) -> tuple[float, float, str, str, str]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return (
        -float(item.get("score") or 0.0),
        -float(item.get("numeric_fact_score") or 0.0),
        str(item.get("doc_id") or ""),
        str(metadata.get("context_id") or ""),
        str(item.get("object_id") or ""),
    )


def diversify_numeric_fact_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    doc_order: list[str] = []
    for row in sorted(rows, key=numeric_fact_row_sort_key):
        doc_key = str(row.get("doc_id") or row.get("object_id") or "")
        if doc_key not in buckets:
            buckets[doc_key] = []
            doc_order.append(doc_key)
        buckets[doc_key].append(row)
    out: list[dict[str, Any]] = []
    indexes = {doc_key: 0 for doc_key in doc_order}
    while True:
        appended = False
        for doc_key in doc_order:
            bucket = buckets[doc_key]
            index = indexes[doc_key]
            if index >= len(bucket):
                continue
            out.append(bucket[index])
            indexes[doc_key] = index + 1
            appended = True
        if not appended:
            break
    return out


def has_numeric_fact_channel(item: dict[str, Any]) -> bool:
    channels = item.get("channels") if isinstance(item.get("channels"), list) else []
    return "numeric_fact" in channels


def diversify_numeric_fact_search_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    numeric_items = [item for item in items if has_numeric_fact_channel(item)]
    if not numeric_items:
        return items
    other_items = [item for item in items if not has_numeric_fact_channel(item)]
    return diversify_numeric_fact_rows(numeric_items) + other_items


def numeric_fact_recall_text(
    raw: dict[str, Any],
    facts: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    *,
    numeric_kind: str,
    numeric_value: float,
    numeric_label: str,
) -> str:
    base = test_fact_recall_text(raw, facts, evidence_rows)
    return (
        f"numeric_fact_kind: {numeric_kind} | "
        f"numeric_fact_value: {numeric_value:g} | "
        f"numeric_fact_label: {numeric_label} | "
        f"{base}"
    )


def numeric_candidates_for_raw(
    raw: dict[str, Any],
    facts: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    kind: str,
) -> list[tuple[float, str]]:
    if kind == "duration_hours":
        text = test_fact_recall_text(raw, facts, evidence_rows)
        if not text_has_salt_spray_context(text):
            return []
        return extract_duration_hours(text)
    if kind == "film_thickness_um":
        return extract_structured_film_thickness_um(raw)
    return []


def numeric_fact_recall_rows(
    store: Any,
    query: str,
    filters: dict[str, Any],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    comparisons = parse_numeric_query_comparisons(query)
    if not comparisons:
        return []
    facts_map = get_facts_map(store)
    evidence_map = get_evidence_map(store)
    rows: list[dict[str, Any]] = []
    for raw in iter_store_hyperedges(store):
        if not raw_matches_search_hard_filters(raw, filters, facts_map):
            continue
        facts = raw_fact_rows(raw, facts_map)
        evidence_rows = raw_evidence_rows(raw, evidence_map)
        for kind, op, threshold in comparisons:
            candidates = numeric_candidates_for_raw(raw, facts, evidence_rows, kind)
            matched = [(value, label) for value, label in candidates if compare_numeric_value(value, op, threshold)]
            if not matched:
                continue
            best_value, best_label = max(matched, key=lambda item: item[0])
            object_id = raw_hyperedge_object_id(raw)
            if not object_id:
                continue
            margin = abs(float(best_value) - float(threshold))
            score = 2.8 + min(margin / max(float(threshold), 1.0), 2.0)
            metadata = {
                "hyperedge_id": raw_hyperedge_id(raw),
                "raw_hyperedge_id": raw_hyperedge_id(raw),
                "sample_id": raw.get("sample_id") or raw.get("sample_label"),
                "context_id": raw.get("context_id"),
                "property": raw.get("property"),
                "property_canonical_id": property_canonical_id_from_raw(raw),
                "test_method": raw.get("test_method"),
                "numeric_fact_kind": kind,
                "numeric_fact_value": round(float(best_value), 6),
                "numeric_fact_unit": "h" if kind == "duration_hours" else "um",
                "numeric_fact_label": best_label,
                "numeric_fact_op": op,
                "numeric_fact_threshold": round(float(threshold), 6),
            }
            rows.append(
                {
                    "object_type": "hyperedge",
                    "object_id": object_id,
                    "doc_id": raw_hyperedge_doc_id(raw),
                    "text_for_embedding": numeric_fact_recall_text(
                        raw,
                        facts,
                        evidence_rows,
                        numeric_kind=kind,
                        numeric_value=float(best_value),
                        numeric_label=best_label,
                    ),
                    "metadata": metadata,
                    "numeric_fact_score": round(score, 6),
                    "score": round(1.0 + score, 6),
                    "channels": ["numeric_fact"],
                }
            )
    return diversify_numeric_fact_rows(rows)[:limit]


def material_fact_recall_rows(
    store: Any,
    query: str,
    filters: dict[str, Any],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    query_terms = material_query_terms(query)
    if not query_terms:
        return []
    facts_map = get_facts_map(store)
    all_hyperedges = iter_store_hyperedges(store)
    by_fact_id: dict[str, list[dict[str, Any]]] = {}
    by_context: dict[tuple[str, str], list[dict[str, Any]]] = {}
    by_object_key: dict[str, dict[str, Any]] = {}
    for raw in all_hyperedges:
        if not raw_matches_search_hard_filters(raw, filters, facts_map):
            continue
        for candidate in object_id_candidates(raw):
            by_object_key[candidate] = raw
        doc_id = raw_hyperedge_doc_id(raw)
        context_id = str(raw.get("context_id") or "").strip()
        if doc_id and context_id:
            by_context.setdefault((doc_id, context_id), []).append(raw)
        for fact in raw_fact_rows(raw, facts_map):
            key = fact_identity(fact)
            if key:
                by_fact_id.setdefault(key, []).append(raw)

    matched_by_object: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    seen_facts: set[str] = set()
    for fallback, fact in facts_map.items():
        if not isinstance(fact, dict):
            continue
        fact_key = fact_identity(fact, fallback)
        if fact_key in seen_facts:
            continue
        seen_facts.add(fact_key)
        if not material_fact_matches_query(fact, query_terms):
            continue
        doc_id = str(fact.get("doc_id") or "").strip()
        candidate_raws: list[dict[str, Any]] = []
        for key in fact_hyperedge_keys(fact):
            if key in by_object_key:
                candidate_raws.append(by_object_key[key])
        candidate_raws.extend(by_fact_id.get(fact_key, []))
        context_id = str(fact.get("context_id") or "").strip()
        if doc_id and context_id:
            candidate_raws.extend(by_context.get((doc_id, context_id), []))
        for raw in candidate_raws:
            if not raw_matches_search_hard_filters(raw, filters, facts_map):
                continue
            object_id = raw_hyperedge_object_id(raw)
            if not object_id:
                continue
            existing = matched_by_object.setdefault(object_id, (raw, []))[1]
            if fact_key not in {fact_identity(item) for item in existing}:
                existing.append(fact)

    rows: list[dict[str, Any]] = []
    for object_id, (raw, facts) in matched_by_object.items():
        best_match_score = max((material_fact_match_score(fact, query_terms) for fact in facts), default=1.0)
        row_score = 1.0 + best_match_score
        metadata = {
            "hyperedge_id": raw_hyperedge_id(raw),
            "raw_hyperedge_id": raw_hyperedge_id(raw),
            "sample_id": raw.get("sample_id") or raw.get("sample_label"),
            "context_id": raw.get("context_id"),
            "matched_material_facts": [compact_material_fact(fact) for fact in facts[:8]],
        }
        rows.append(
            {
                "object_type": "hyperedge",
                "object_id": object_id,
                "doc_id": raw_hyperedge_doc_id(raw),
                "text_for_embedding": material_fact_recall_text(raw, facts),
                "metadata": metadata,
                "material_fact_score": round(best_match_score, 6),
                "score": round(row_score, 6),
                "channels": ["material_fact"],
            }
        )
    return diversify_material_fact_rows(rows)[:limit]


def substrate_fact_recall_rows(
    store: Any,
    query: str,
    filters: dict[str, Any],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    query_terms = substrate_query_terms(query)
    if not query_terms:
        return []
    facts_map = get_facts_map(store)
    evidence_map = get_evidence_map(store)
    rows: list[dict[str, Any]] = []
    for raw in iter_store_hyperedges(store):
        if not raw_matches_search_hard_filters(raw, filters, facts_map):
            continue
        values = aggregate_filter_values(raw, "substrates", facts_map, evidence_map)
        score = substrate_fact_match_score(values, query_terms)
        if score <= 0.0:
            continue
        metadata = {
            "hyperedge_id": raw_hyperedge_id(raw),
            "raw_hyperedge_id": raw_hyperedge_id(raw),
            "sample_id": raw.get("sample_id") or raw.get("sample_label"),
            "context_id": raw.get("context_id"),
            "matched_substrate_facts": [compact_substrate_fact(value) for value in values[:8]],
        }
        rows.append(
            {
                "object_type": "hyperedge",
                "object_id": raw_hyperedge_object_id(raw),
                "doc_id": raw_hyperedge_doc_id(raw),
                "text_for_embedding": substrate_fact_recall_text(raw, values),
                "metadata": metadata,
                "substrate_fact_score": round(score, 6),
                "score": round(1.0 + score, 6),
                "channels": ["substrate_fact"],
            }
        )
    return diversify_substrate_fact_rows(rows)[:limit]


def merge_property_recall_candidates(
    fused: list[dict[str, Any]],
    property_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_object_id: dict[str, dict[str, Any]] = {}
    for item in fused:
        object_id = str(item.get("object_id") or "")
        if object_id:
            by_object_id[object_id] = dict(item)
    for row in property_rows:
        object_id = str(row.get("object_id") or "")
        if not object_id:
            continue
        item = by_object_id.setdefault(
            object_id,
            {
                "object_type": row.get("object_type"),
                "object_id": row.get("object_id"),
                "doc_id": row.get("doc_id"),
                "text_for_embedding": row.get("text_for_embedding"),
                "metadata": row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
                "score": 0.0,
                "dense_score": None,
                "sparse_score": None,
                "channels": [],
            },
        )
        if not item.get("doc_id"):
            item["doc_id"] = row.get("doc_id")
        if not item.get("text_for_embedding"):
            item["text_for_embedding"] = row.get("text_for_embedding")
        if not item.get("metadata") and isinstance(row.get("metadata"), dict):
            item["metadata"] = row.get("metadata")
        item["property_soft_score"] = row.get("property_soft_score", 1.0)
        channels = item.setdefault("channels", [])
        if "property_soft" not in channels:
            channels.append("property_soft")
    return list(by_object_id.values())


def merge_material_fact_recall_candidates(
    fused: list[dict[str, Any]],
    material_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not material_rows:
        return fused
    by_object_id: dict[str, dict[str, Any]] = {}
    for item in fused:
        object_id = str(item.get("object_id") or "")
        if object_id:
            by_object_id[object_id] = dict(item)
    for row in material_rows:
        object_id = str(row.get("object_id") or "")
        if not object_id:
            continue
        item = by_object_id.setdefault(
            object_id,
            {
                "object_type": row.get("object_type"),
                "object_id": row.get("object_id"),
                "doc_id": row.get("doc_id"),
                "text_for_embedding": row.get("text_for_embedding"),
                "metadata": row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
                "score": 0.0,
                "dense_score": None,
                "sparse_score": None,
                "property_soft_score": None,
                "material_fact_score": None,
                "channels": [],
            },
        )
        row_material_score = float(row.get("material_fact_score") or 1.0)
        item["material_fact_score"] = max(float(item.get("material_fact_score") or 0.0), row_material_score)
        item["score"] = max(float(item.get("score") or 0.0), float(row.get("score") or 1.05))
        channels = item.setdefault("channels", [])
        if "material_fact" not in channels:
            channels.append("material_fact")
        item["channels"] = sorted(channels)
        if not item.get("doc_id"):
            item["doc_id"] = row.get("doc_id")
        if not item.get("text_for_embedding"):
            item["text_for_embedding"] = row.get("text_for_embedding")
        existing_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        row_metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if row_metadata:
            merged_metadata = dict(existing_metadata)
            if row_metadata.get("matched_material_facts"):
                merged_metadata["matched_material_facts"] = row_metadata.get("matched_material_facts")
            for key in ("sample_id", "context_id", "hyperedge_id", "raw_hyperedge_id"):
                if not merged_metadata.get(key) and row_metadata.get(key) is not None:
                    merged_metadata[key] = row_metadata.get(key)
            item["metadata"] = merged_metadata
    merged = list(by_object_id.values())
    merged.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("object_id") or "")))
    return merged


def merge_substrate_fact_recall_candidates(
    fused: list[dict[str, Any]],
    substrate_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not substrate_rows:
        return fused
    by_object_id: dict[str, dict[str, Any]] = {}
    for item in fused:
        object_id = str(item.get("object_id") or "")
        if object_id:
            by_object_id[object_id] = dict(item)
    for row in substrate_rows:
        object_id = str(row.get("object_id") or "")
        if not object_id:
            continue
        item = by_object_id.setdefault(
            object_id,
            {
                "object_type": row.get("object_type"),
                "object_id": row.get("object_id"),
                "doc_id": row.get("doc_id"),
                "text_for_embedding": row.get("text_for_embedding"),
                "metadata": row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
                "score": 0.0,
                "dense_score": None,
                "sparse_score": None,
                "property_soft_score": None,
                "material_fact_score": None,
                "substrate_fact_score": None,
                "channels": [],
            },
        )
        row_substrate_score = float(row.get("substrate_fact_score") or 1.0)
        item["substrate_fact_score"] = max(float(item.get("substrate_fact_score") or 0.0), row_substrate_score)
        item["score"] = max(float(item.get("score") or 0.0), float(row.get("score") or (1.0 + row_substrate_score)))
        channels = item.setdefault("channels", [])
        if "substrate_fact" not in channels:
            channels.append("substrate_fact")
        item["channels"] = sorted(channels)
        if not item.get("doc_id"):
            item["doc_id"] = row.get("doc_id")
        if not item.get("text_for_embedding"):
            item["text_for_embedding"] = row.get("text_for_embedding")
        existing_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        row_metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if row_metadata:
            merged_metadata = dict(existing_metadata)
            if row_metadata.get("matched_substrate_facts"):
                merged_metadata["matched_substrate_facts"] = row_metadata.get("matched_substrate_facts")
            for key in ("sample_id", "context_id", "hyperedge_id", "raw_hyperedge_id"):
                if not merged_metadata.get(key) and row_metadata.get(key) is not None:
                    merged_metadata[key] = row_metadata.get(key)
            item["metadata"] = merged_metadata
    merged = list(by_object_id.values())
    merged.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("object_id") or "")))
    return merged


def merge_test_fact_recall_candidates(
    fused: list[dict[str, Any]],
    test_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not test_rows:
        return fused
    by_object_id: dict[str, dict[str, Any]] = {}
    for item in fused:
        object_id = str(item.get("object_id") or "")
        if object_id:
            by_object_id[object_id] = dict(item)
    for row in test_rows:
        object_id = str(row.get("object_id") or "")
        if not object_id:
            continue
        item = by_object_id.setdefault(
            object_id,
            {
                "object_type": row.get("object_type"),
                "object_id": row.get("object_id"),
                "doc_id": row.get("doc_id"),
                "text_for_embedding": row.get("text_for_embedding"),
                "metadata": row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
                "score": 0.0,
                "dense_score": None,
                "sparse_score": None,
                "property_soft_score": None,
                "material_fact_score": None,
                "test_fact_score": None,
                "channels": [],
            },
        )
        row_test_score = float(row.get("test_fact_score") or 1.0)
        item["test_fact_score"] = max(float(item.get("test_fact_score") or 0.0), row_test_score)
        item["score"] = max(float(item.get("score") or 0.0), float(row.get("score") or (1.0 + row_test_score)))
        channels = item.setdefault("channels", [])
        if "test_fact" not in channels:
            channels.append("test_fact")
        item["channels"] = sorted(channels)
        if not item.get("doc_id"):
            item["doc_id"] = row.get("doc_id")
        if not item.get("text_for_embedding"):
            item["text_for_embedding"] = row.get("text_for_embedding")
        existing_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        row_metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if row_metadata:
            merged_metadata = dict(existing_metadata)
            for key in ("sample_id", "context_id", "hyperedge_id", "raw_hyperedge_id", "property", "property_canonical_id", "test_method"):
                if not merged_metadata.get(key) and row_metadata.get(key) is not None:
                    merged_metadata[key] = row_metadata.get(key)
            item["metadata"] = merged_metadata
    merged = list(by_object_id.values())
    merged.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("object_id") or "")))
    return merged


def merge_numeric_fact_recall_candidates(
    fused: list[dict[str, Any]],
    numeric_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not numeric_rows:
        return fused
    by_object_id: dict[str, dict[str, Any]] = {}
    for item in fused:
        object_id = str(item.get("object_id") or "")
        if object_id:
            by_object_id[object_id] = dict(item)
    for row in numeric_rows:
        object_id = str(row.get("object_id") or "")
        if not object_id:
            continue
        item = by_object_id.setdefault(
            object_id,
            {
                "object_type": row.get("object_type"),
                "object_id": row.get("object_id"),
                "doc_id": row.get("doc_id"),
                "text_for_embedding": row.get("text_for_embedding"),
                "metadata": row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
                "score": 0.0,
                "dense_score": None,
                "sparse_score": None,
                "property_soft_score": None,
                "material_fact_score": None,
                "test_fact_score": None,
                "numeric_fact_score": None,
                "channels": [],
            },
        )
        row_numeric_score = float(row.get("numeric_fact_score") or 1.0)
        item["numeric_fact_score"] = max(float(item.get("numeric_fact_score") or 0.0), row_numeric_score)
        item["score"] = max(float(item.get("score") or 0.0), float(row.get("score") or (1.0 + row_numeric_score)))
        channels = item.setdefault("channels", [])
        if "numeric_fact" not in channels:
            channels.append("numeric_fact")
        item["channels"] = sorted(channels)
        if not item.get("doc_id"):
            item["doc_id"] = row.get("doc_id")
        if not item.get("text_for_embedding"):
            item["text_for_embedding"] = row.get("text_for_embedding")
        existing_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        row_metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if row_metadata:
            merged_metadata = dict(existing_metadata)
            for key in (
                "sample_id",
                "context_id",
                "hyperedge_id",
                "raw_hyperedge_id",
                "property",
                "property_canonical_id",
                "test_method",
                "numeric_fact_kind",
                "numeric_fact_value",
                "numeric_fact_unit",
                "numeric_fact_label",
                "numeric_fact_op",
                "numeric_fact_threshold",
            ):
                if not merged_metadata.get(key) and row_metadata.get(key) is not None:
                    merged_metadata[key] = row_metadata.get(key)
            item["metadata"] = merged_metadata
    merged = list(by_object_id.values())
    merged.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("object_id") or "")))
    return merged


def rrf_fuse(
    dense_rows: list[dict[str, Any]],
    sparse_rows: list[dict[str, Any]],
    *,
    top_k: int,
    rrf_k: int = DEFAULT_RRF_K,
) -> list[dict[str, Any]]:
    fused: dict[str, dict[str, Any]] = {}

    def add_rows(rows: list[dict[str, Any]], channel: str, score_key: str) -> None:
        for rank, row in enumerate(rows, start=1):
            object_id = row.get("object_id")
            if not object_id:
                continue
            item = fused.setdefault(
                object_id,
                {
                    "object_type": row.get("object_type"),
                    "object_id": object_id,
                    "doc_id": row.get("doc_id"),
                    "text_for_embedding": row.get("text_for_embedding"),
                    "metadata": row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
                    "score": 0.0,
                    "dense_score": None,
                    "sparse_score": None,
                    "channels": [],
                },
            )
            item["score"] += 1.0 / (rrf_k + rank)
            if channel not in item["channels"]:
                item["channels"].append(channel)
            if row.get(score_key) is not None:
                item[score_key] = float(row.get(score_key))
            if not item.get("doc_id"):
                item["doc_id"] = row.get("doc_id")
            if not item.get("text_for_embedding"):
                item["text_for_embedding"] = row.get("text_for_embedding")
            if not item.get("metadata") and isinstance(row.get("metadata"), dict):
                item["metadata"] = row.get("metadata")

    add_rows(dense_rows, "dense", "dense_score")
    add_rows(sparse_rows, "sparse", "sparse_score")
    ranked = sorted(fused.values(), key=lambda item: (-float(item["score"]), str(item["object_id"])))
    return ranked[:top_k]


def normalized_score_map(rows: list[dict[str, Any]], score_key: str) -> dict[str, float]:
    raw: dict[str, float] = {}
    for row in rows:
        object_id = row.get("object_id")
        if not object_id:
            continue
        try:
            raw[str(object_id)] = float(row.get(score_key) or 0.0)
        except (TypeError, ValueError):
            raw[str(object_id)] = 0.0
    if not raw:
        return {}
    low = min(raw.values())
    high = max(raw.values())
    if high <= low:
        return {key: 1.0 for key in raw}
    return {key: (score - low) / (high - low) for key, score in raw.items()}


def weighted_fuse(
    dense_rows: list[dict[str, Any]],
    sparse_rows: list[dict[str, Any]],
    *,
    top_k: int,
    dense_weight: float = DEFAULT_DENSE_WEIGHT,
    sparse_weight: float = DEFAULT_SPARSE_WEIGHT,
) -> list[dict[str, Any]]:
    dense_norm = normalized_score_map(dense_rows, "dense_score")
    sparse_norm = normalized_score_map(sparse_rows, "sparse_score")
    fused: dict[str, dict[str, Any]] = {}
    channels: dict[str, set[str]] = {}

    def add_rows(rows: list[dict[str, Any]], channel: str, score_key: str) -> None:
        for row in rows:
            object_id = row.get("object_id")
            if not object_id:
                continue
            key = str(object_id)
            item = fused.setdefault(
                key,
                {
                    "object_type": row.get("object_type"),
                    "object_id": object_id,
                    "doc_id": row.get("doc_id"),
                    "text_for_embedding": row.get("text_for_embedding"),
                    "metadata": row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
                    "score": 0.0,
                    "dense_score": None,
                    "sparse_score": None,
                    "channels": [],
                },
            )
            channels.setdefault(key, set()).add(channel)
            if row.get(score_key) is not None:
                item[score_key] = float(row.get(score_key))
            if not item.get("doc_id"):
                item["doc_id"] = row.get("doc_id")
            if not item.get("text_for_embedding"):
                item["text_for_embedding"] = row.get("text_for_embedding")
            if not item.get("metadata") and isinstance(row.get("metadata"), dict):
                item["metadata"] = row.get("metadata")

    add_rows(dense_rows, "dense", "dense_score")
    add_rows(sparse_rows, "sparse", "sparse_score")
    for key, item in fused.items():
        item["score"] = dense_weight * dense_norm.get(key, 0.0) + sparse_weight * sparse_norm.get(key, 0.0)
        item["channels"] = sorted(channels.get(key, set()))
    ranked = sorted(fused.values(), key=lambda item: (-float(item["score"]), str(item["object_id"])))
    return ranked[:top_k]


def fuse_search_results(
    dense_rows: list[dict[str, Any]],
    sparse_rows: list[dict[str, Any]],
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    if DEFAULT_FUSION_MODE == "hybrid_weighted" or DEFAULT_FUSION_NAME.startswith("hybrid_weighted"):
        return weighted_fuse(
            dense_rows,
            sparse_rows,
            top_k=top_k,
            dense_weight=DEFAULT_DENSE_WEIGHT,
            sparse_weight=DEFAULT_SPARSE_WEIGHT,
        )
    return rrf_fuse(dense_rows, sparse_rows, top_k=top_k, rrf_k=DEFAULT_RRF_K)


def soft_filter_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def soft_filter_haystack(item: dict[str, Any]) -> str:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return " ".join(
        [
            str(item.get("object_id") or ""),
            str(item.get("doc_id") or ""),
            str(item.get("text_for_embedding") or ""),
            soft_filter_text(metadata),
        ]
    ).lower()


def soft_filter_score(item: dict[str, Any], filters: dict[str, Any]) -> float:
    haystack = soft_filter_haystack(item)
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    score = 0.0
    for key in SEARCH_SOFT_FILTER_ALIASES:
        if key in {"property_families", "property_canonical_ids_soft"}:
            continue
        values = [value.lower() for value in filters.get(key, []) if str(value).strip()]
        if not values:
            continue
        if any(value in haystack for value in values):
            score += 0.25
    property_ids = {value.lower() for value in property_canonical_ids_for_filters(filters)}
    if property_ids:
        metadata_ids = {value.lower() for value in metadata_property_canonical_ids(metadata)}
        if metadata_ids.intersection(property_ids):
            score += 0.9
        elif any(value in haystack for value in property_ids):
            score += 0.45
    return score


def rerank_search_results_by_soft_filters(
    items: list[dict[str, Any]],
    filters: dict[str, Any],
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for item in items:
        copied = dict(item)
        boost = soft_filter_score(copied, filters)
        copied["soft_filter_score"] = round(boost, 6)
        copied["score"] = float(copied.get("score") or 0.0) + boost
        ranked.append(copied)
    ranked.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("object_id") or "")))
    return ranked[:top_k]


def compact_search_metadata(metadata: Any) -> dict[str, Any]:
    metadata = metadata if isinstance(metadata, dict) else {}
    out: dict[str, Any] = {}
    for key in [
        "sample_id",
        "context_id",
        "example_id",
        "example_kind",
        "polarity",
        "application",
        "application_family",
        "substrate",
        "test_method",
        "test_standard",
        "test_condition",
        "evidence_ids",
        "fact_ids",
        "numeric_fact_kind",
        "numeric_fact_value",
        "numeric_fact_unit",
        "numeric_fact_label",
        "numeric_fact_op",
        "numeric_fact_threshold",
        "matched_substrate_facts",
        "matched_material_facts",
    ]:
        if metadata.get(key) is not None:
            out[key] = metadata.get(key)
    prop = metadata.get("property")
    if isinstance(prop, dict):
        out["property"] = prop.get("name") or prop.get("label") or prop.get("id")
        out["property_canonical_id"] = metadata.get("property_canonical_id") or prop.get("canonical_id")
    else:
        if prop is not None:
            out["property"] = prop
        if metadata.get("property_canonical_id") is not None:
            out["property_canonical_id"] = metadata.get("property_canonical_id")
    return out


def compact_search_item(item: dict[str, Any], rank: int) -> dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    object_id = item.get("object_id")
    return {
        "rank": rank,
        "object_id": object_id,
        "doc_id": item.get("doc_id"),
        "hyperedge_id": metadata.get("hyperedge_id") or metadata.get("raw_hyperedge_id") or object_id,
        "score": round(float(item.get("score") or 0.0), 6),
        "score_parts": {
            "dense_score": optional_score(item.get("dense_score")),
            "sparse_score": optional_score(item.get("sparse_score")),
            "property_soft_score": optional_score(item.get("property_soft_score")),
            "material_fact_score": optional_score(item.get("material_fact_score")),
            "substrate_fact_score": optional_score(item.get("substrate_fact_score")),
            "test_fact_score": optional_score(item.get("test_fact_score")),
            "numeric_fact_score": optional_score(item.get("numeric_fact_score")),
            "soft_filter_score": optional_score(item.get("soft_filter_score")),
            "fusion": DEFAULT_FUSION_NAME,
            "fusion_mode": DEFAULT_FUSION_MODE,
            "dense_weight": DEFAULT_DENSE_WEIGHT if DEFAULT_FUSION_MODE == "hybrid_weighted" else None,
            "sparse_weight": DEFAULT_SPARSE_WEIGHT if DEFAULT_FUSION_MODE == "hybrid_weighted" else None,
            "rrf_k": DEFAULT_RRF_K if DEFAULT_FUSION_MODE == "hybrid_rrf" else None,
        },
        "channels": item.get("channels") or [],
        "text_preview": compact_text(item.get("text_for_embedding"), 1200),
        "metadata": compact_search_metadata(metadata),
    }


def paginate_search_items(
    items: list[dict[str, Any]],
    *,
    offset: int,
    top_k: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    safe_offset = max(0, int(offset))
    safe_top_k = max(1, int(top_k))
    page = items[safe_offset : safe_offset + safe_top_k]
    end = safe_offset + len(page)
    has_more = end < len(items)
    return page, {
        "offset": safe_offset,
        "page_size": len(page),
        "next_offset": end if has_more else None,
        "has_more": has_more,
    }


def run_hybrid_search(request: dict[str, Any]) -> dict[str, Any]:
    query = str(request.get("query") or "").strip()
    raw_filters = request.get("requested_filters") if isinstance(request.get("requested_filters"), dict) else request.get("filters")
    receipt = normalize_filter_request("kg.hybrid_search", raw_filters)
    receipt["unsupported_constraints"] = sorted(
        set([*receipt["unsupported_constraints"], *(request.get("unsupported_constraints") or [])])
    )
    receipt["route_adjustments"] = list(
        dict.fromkeys([*(request.get("route_adjustments") or []), *receipt["route_adjustments"]])
    )

    def finish(payload: dict[str, Any]) -> dict[str, Any]:
        payload["requested_filters"] = receipt["requested_filters"]
        payload["effective_filters"] = receipt["effective_filters"]
        payload["unsupported_constraints"] = receipt["unsupported_constraints"]
        payload["route_adjustments"] = receipt["route_adjustments"]
        payload["cohort_mode"] = "hyperedge"
        payload["count_unit"] = "hyperedge"
        if receipt["unsupported_constraints"]:
            payload["status"] = "unsupported"
            payload["items"] = []
        return payload

    if not query:
        return finish({
            "tool": "kg.hybrid_search",
            "status": "error",
            "query": query,
            "error": "query must be a non-empty string",
            "summary": {"candidate_k": 0, "dense_found": 0, "sparse_found": 0, "returned": 0},
            "items": [],
            "warnings": [],
        })
    if receipt["unsupported_constraints"]:
        return finish({
            "tool": "kg.hybrid_search",
            "status": "unsupported",
            "query": query,
            "summary": {"candidate_k": 0, "dense_found": 0, "sparse_found": 0, "returned": 0},
            "items": [],
            "warnings": ["one or more requested constraints are not supported by this tool contract"],
        })
    top_k = clamp_int(request.get("top_k"), DEFAULT_SEARCH_TOP_K, 1, MAX_SEARCH_TOP_K)
    candidate_k = clamp_int(request.get("candidate_k"), DEFAULT_SEARCH_CANDIDATE_K, top_k, MAX_SEARCH_CANDIDATE_K)
    offset = clamp_int(request.get("offset"), 0, 0, candidate_k)
    store = get_store()
    filters, warnings = apply_hard_search_scopes(store, receipt["effective_filters"])
    receipt["effective_filters"] = filters
    requested_material_ids = lower_set(filters.get("material_canonical_ids") or [])
    if requested_material_ids:
        facts_map = get_facts_map(store)
        known_material_ids = {
            str(row.get("canonical_id") or "").strip().casefold()
            for raw in iter_store_hyperedges(store)
            for _, row in aggregate_material_rows(raw, facts_map, None)
            if str(row.get("canonical_id") or "").strip()
        }
        unknown_material_ids = sorted(requested_material_ids - known_material_ids)
        if unknown_material_ids:
            receipt["unsupported_constraints"] = ["material_canonical_ids"]
            receipt["route_adjustments"].append("unknown_material_canonical_ids_rejected")
            return finish({
                "tool": "kg.hybrid_search",
                "status": "unsupported",
                "query": query,
                "summary": {"candidate_k": candidate_k, "dense_found": 0, "sparse_found": 0, "returned": 0},
                "items": [],
                "warnings": [f"unknown material canonical IDs: {', '.join(unknown_material_ids)}"],
            })
    if filters["qa_policy"] != "include_all":
        warnings.append("qa_policy is recorded only in v2 and is not used for filtering")
    if any(
        warning in {
            "assignee_hard_scope_resolved_empty",
            "assignee_and_doc_id_hard_scopes_do_not_intersect",
        }
        for warning in warnings
    ):
        return finish({
            "tool": "kg.hybrid_search",
            "status": "empty",
            "query": query,
            "applied_filters": filters,
            "pagination": {"offset": offset, "page_size": 0, "next_offset": None, "has_more": False},
            "summary": {
                "candidate_k": candidate_k,
                "dense_found": 0,
                "sparse_found": 0,
                "pool_size": 0,
                "returned": 0,
            },
            "items": [],
            "warnings": warnings,
            "generated_at": now_iso(),
        })

    dense_vec, sparse_weights = embed_query(query)
    pg_env = expand.load_pg_env(expand.DEFAULT_PG_ENV)
    with expand.pg_connect(pg_env) as conn:
        dense_rows = search_dense(conn, dense_vec, filters, candidate_k)
        sparse_rows = search_sparse(conn, sparse_weights, filters, candidate_k)
        property_rows = search_soft_property_candidates(conn, filters, candidate_k)
    numeric_rows = numeric_fact_recall_rows(store, query, filters, limit=candidate_k)
    test_rows = test_fact_recall_rows(store, query, filters, limit=candidate_k)
    material_rows = material_fact_recall_rows(store, query, filters, limit=candidate_k)
    substrate_rows = substrate_fact_recall_rows(store, query, filters, limit=candidate_k)
    fused = fuse_search_results(dense_rows, sparse_rows, top_k=candidate_k)
    fused = merge_property_recall_candidates(fused, property_rows)
    fused = merge_numeric_fact_recall_candidates(fused, numeric_rows)
    fused = merge_test_fact_recall_candidates(fused, test_rows)
    fused = merge_material_fact_recall_candidates(fused, material_rows)
    fused = merge_substrate_fact_recall_candidates(fused, substrate_rows)
    fused = rerank_search_results_by_soft_filters(fused, filters, top_k=candidate_k)
    if numeric_rows:
        fused = diversify_numeric_fact_search_items(fused)[:candidate_k]
    elif material_rows and material_fact_priority_query(query):
        fused = diversify_material_fact_search_items(fused)[:candidate_k]
    elif test_rows:
        fused = diversify_test_fact_search_items(fused)[:candidate_k]
    elif substrate_rows:
        fused = diversify_substrate_fact_search_items(fused)[:candidate_k]
    elif material_rows:
        fused = diversify_material_fact_search_items(fused)[:candidate_k]
    fused = filter_search_items_by_hard_material_roles(fused, store, filters)
    page_rows, pagination = paginate_search_items(fused, offset=offset, top_k=top_k)
    items = [compact_search_item(item, rank) for rank, item in enumerate(page_rows, start=offset + 1)]
    return finish({
        "tool": "kg.hybrid_search",
        "status": "ok" if items else "empty",
        "query": query,
        "applied_filters": filters,
        "pagination": pagination,
        "summary": {
            "candidate_k": candidate_k,
            "dense_found": len(dense_rows),
            "sparse_found": len(sparse_rows),
            "property_soft_found": len(property_rows),
            "numeric_fact_found": len(numeric_rows),
            "test_fact_found": len(test_rows),
            "substrate_fact_found": len(substrate_rows),
            "material_fact_found": len(material_rows),
            "pool_size": len(fused),
            "returned": len(items),
        },
        "items": items,
        "warnings": warnings,
        "generated_at": now_iso(),
    })


class Handler(BaseHTTPRequestHandler):
    server_version = "KgExpandTool/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{now_iso()}] {self.address_string()} {fmt % args}", flush=True)

    def send_json(self, payload: Any, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self) -> bool:
        if not AUTH_TOKEN:
            return True
        header = self.headers.get("Authorization", "")
        return header == f"Bearer {AUTH_TOKEN}"

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_json(
                {
                    "status": "ok",
                    "tool": "kg.expand_hyperedge_multihop",
                    "tools": [
                        "kg.expand_hyperedge_multihop",
                        "kg.hybrid_search",
                        "kg.sql_aggregate",
                        "kg.doc_field_scan",
                    ],
                    "schema_version": expand.SCHEMA_VERSION,
                    "kg_dirs": [str(source.path) for source in resolve_kg_sources()[0]],
                    "kg_zips": [str(path) for path in resolve_kg_sources()[1]],
                    "pg_env": str(expand.DEFAULT_PG_ENV),
                    "embed_url": EMBED_URL,
                    "retrieval_config": str(RETRIEVAL_CONFIG_PATH),
                    "fusion": DEFAULT_FUSION_NAME,
                    "fusion_mode": DEFAULT_FUSION_MODE,
                    "dense_weight": DEFAULT_DENSE_WEIGHT,
                    "sparse_weight": DEFAULT_SPARSE_WEIGHT,
                    "default_top_k": DEFAULT_SEARCH_TOP_K,
                    "default_candidate_k": DEFAULT_SEARCH_CANDIDATE_K,
                    "generated_at": now_iso(),
                }
            )
            return
        self.send_json({"status": "not_found", "path": self.path}, 404)

    def do_POST(self) -> None:
        if self.path not in {
            "/tools/kg.expand_hyperedge_multihop",
            "/tools/kg.hybrid_search",
            "/tools/kg.sql_aggregate",
            "/tools/kg.doc_field_scan",
        }:
            self.send_json({"status": "not_found", "path": self.path}, 404)
            return
        if not self.authorized():
            self.send_json({"status": "unauthorized"}, 401)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            request = json.loads(body) if body else {}
            if self.path == "/tools/kg.expand_hyperedge_multihop":
                payload = run_expand(request)
            elif self.path == "/tools/kg.hybrid_search":
                payload = run_hybrid_search(request)
            elif self.path == "/tools/kg.sql_aggregate":
                payload = run_sql_aggregate(request)
            else:
                payload = run_doc_field_scan(request)
            status_code = 400 if payload.get("status") == "error" else 200
            self.send_json(payload, status_code)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            tool_map = {
                "/tools/kg.hybrid_search": "kg.hybrid_search",
                "/tools/kg.sql_aggregate": "kg.sql_aggregate",
                "/tools/kg.doc_field_scan": "kg.doc_field_scan",
            }
            tool = tool_map.get(self.path, "kg.expand_hyperedge_multihop")
            self.send_json(
                {
                    "tool": tool,
                    "status": "error",
                    "error": str(exc),
                    "summary": {},
                    "items": [],
                },
                500,
            )


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"kg tools service at http://{HOST}:{PORT}", flush=True)
    print("tools=kg.expand_hyperedge_multihop,kg.hybrid_search,kg.sql_aggregate,kg.doc_field_scan", flush=True)
    print(f"auth={'on' if AUTH_TOKEN else 'off'}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
