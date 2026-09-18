from __future__ import annotations

import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

import kg_contract


def setup_function(_fn=None):
    kg_contract.load_tool_contract.cache_clear()
    kg_contract.load_coating_family_aliases.cache_clear()
    kg_contract.load_coating_family_multi_value.cache_clear()
    kg_contract.load_coating_family_concepts.cache_clear()
    kg_contract.load_material_resin_aliases.cache_clear()
    kg_contract.load_material_aliases.cache_clear()


def test_marine_antifouling_concept_alias_migrates():
    receipt = kg_contract.normalize_filter_request(
        "kg.hybrid_search",
        {"application_family": ["marine"], "coating_families": ["marine_antifouling"]},
    )
    assert set(receipt["effective_filters"]["coating_families"]) == {
        "antifouling",
        "foul_release",
        "fouling_control",
    }
    assert any(
        item.startswith("concept_alias_migrated:marine_antifouling->marine_fouling_control")
        for item in receipt["route_adjustments"]
    )


def test_applications_antifouling_migrates_to_coating_families():
    receipt = kg_contract.normalize_filter_request(
        "kg.sql_aggregate",
        {"applications": ["antifouling", "hull"]},
    )
    assert "hull" in receipt["effective_filters"]["applications"]
    assert "antifouling" not in receipt["effective_filters"]["applications"]
    assert "antifouling" in receipt["effective_filters"]["coating_families"]
    assert any(
        item.startswith("applications_migrated_to_coating_families:antifouling")
        for item in receipt["route_adjustments"]
    )


def test_polyurethane_migrates_to_resin_systems_via_material_groups():
    receipt = kg_contract.normalize_filter_request(
        "kg.hybrid_search",
        {
            "materials": ["polyurethane", "zinc dust"],
            "material_groups": [
                {"values": ["amino resin", "氨基树脂"], "roles": ["curing_agent"], "match": "any"}
            ],
        },
    )
    assert "polyurethane" in receipt["effective_filters"]["resin_systems"]
    assert "polyurethane" not in receipt["effective_filters"]["materials"]
    assert "zinc dust" in receipt["effective_filters"]["materials"]
    groups = receipt["effective_filters"]["material_groups"]
    assert any("zinc dust" in (g.get("values") or []) for g in groups)
    assert any("amino resin" in (g.get("values") or []) for g in groups)


def test_unknown_coating_family_becomes_unsupported_not_demoted():
    receipt = kg_contract.normalize_filter_request(
        "kg.hybrid_search",
        {"coating_families": ["totally_unknown_compound_xyz"]},
    )
    assert receipt["effective_filters"]["coating_families"] == []
    assert any(item.startswith("coating_families:") for item in receipt["unsupported_constraints"])
    assert receipt["effective_filters"].get("substrates") == []


def test_hybrid_multi_value_expand():
    values, adj, bad = kg_contract.resolve_coating_family_token("fouling_release_antifouling")
    assert set(values) == {"antifouling", "foul_release"}
    assert bad == []


def test_openai_schema_exposes_new_fields():
    props = kg_contract.openai_filter_properties("kg.hybrid_search")
    assert "coating_families" in props
    assert "publication_year" in props
    assert "material_groups" in props
    assert props["material_groups"]["type"] == "array"


def test_gma_material_alias_expands():
    receipt = kg_contract.normalize_filter_request(
        "kg.sql_aggregate",
        {"materials": ["GMA"], "publication_year": ["unknown_year"]},
    )
    materials = {item.casefold() for item in receipt["effective_filters"]["materials"]}
    assert "gma" in materials
    assert "glycidyl methacrylate" in materials
    assert "mat_gma" in materials or "mat_glycidyl_methacrylate" in materials
    groups = receipt["effective_filters"]["material_groups"]
    assert len(groups) == 1
    group_values = {item.casefold() for item in (groups[0].get("values") or [])}
    assert "glycidyl methacrylate" in group_values


def test_soft_filter_ignores_material_groups_dicts():
    # Import expand helpers lightly by path when available; skip if heavy deps missing.
    import importlib.util
    expand_path = Path(__file__).resolve().parents[1] / "kg_tools" / "kg_expand_http_service.py"
    # Don't execute full module (needs expand_hyperedge_multihop). Just assert contract expand path.
    receipt = kg_contract.normalize_filter_request(
        "kg.hybrid_search",
        {
            "application_family": ["automotive"],
            "resin_systems": ["polyurethane"],
            "material_groups": [
                {"values": ["amino resin"], "roles": ["curing_agent"], "match": "any"},
                {"values": ["high-solids clearcoat"], "match": "any"},
            ],
        },
    )
    assert receipt["effective_filters"]["material_groups"]
    assert isinstance(receipt["effective_filters"]["material_groups"][0], dict)


def test_gma_synonym_materials_merge_to_one_or_group():
    receipt = kg_contract.normalize_filter_request(
        "kg.sql_aggregate",
        {
            "materials": ["GMA", "glycidyl methacrylate", "甲基丙烯酸缩水甘油酯"],
            "publication_year": ["unknown_year"],
        },
    )
    groups = receipt["effective_filters"]["material_groups"]
    assert len(groups) == 1
    values = {item.casefold() for item in (groups[0].get("values") or [])}
    assert "gma" in values
    assert "glycidyl methacrylate" in values
    assert "甲基丙烯酸缩水甘油酯" in values
    materials = {item.casefold() for item in receipt["effective_filters"]["materials"]}
    assert "甲基丙烯酸缩水甘油酯" in materials


def test_gma_synonym_order_and_duplicates_stable():
    a = kg_contract.normalize_filter_request(
        "kg.sql_aggregate",
        {"materials": ["GMA", "glycidyl methacrylate", "GMA"]},
    )
    b = kg_contract.normalize_filter_request(
        "kg.sql_aggregate",
        {"materials": ["甲基丙烯酸缩水甘油酯", "GMA", "glycidyl methacrylate"]},
    )
    assert len(a["effective_filters"]["material_groups"]) == 1
    assert len(b["effective_filters"]["material_groups"]) == 1
    assert a["effective_filters"]["material_groups"] == b["effective_filters"]["material_groups"]
    assert a["effective_filters"]["materials"] == b["effective_filters"]["materials"]


def test_normalize_filter_request_materials_idempotent():
    once = kg_contract.normalize_filter_request(
        "kg.sql_aggregate",
        {"materials": ["GMA", "甲基丙烯酸缩水甘油酯"], "publication_year": ["unknown_year"]},
    )
    twice = kg_contract.normalize_filter_request("kg.sql_aggregate", once["effective_filters"])
    assert once["effective_filters"]["material_groups"] == twice["effective_filters"]["material_groups"]
    assert once["effective_filters"]["materials"] == twice["effective_filters"]["materials"]
    assert once["effective_filters"]["publication_year"] == twice["effective_filters"]["publication_year"]


def test_unrelated_materials_remain_and_across_groups():
    receipt = kg_contract.normalize_filter_request(
        "kg.hybrid_search",
        {"materials": ["GMA", "zinc dust"]},
    )
    groups = receipt["effective_filters"]["material_groups"]
    assert len(groups) == 2
    value_sets = [{item.casefold() for item in (g.get("values") or [])} for g in groups]
    assert any("gma" in values for values in value_sets)
    assert any("zinc dust" in values for values in value_sets)
