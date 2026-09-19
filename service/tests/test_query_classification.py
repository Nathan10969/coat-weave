from __future__ import annotations

import copy
import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


CORE = Path(__file__).resolve().parents[1] / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

LEGACY = "/root/coating/embedding/data/kg_286_aggregate"
PPG = "/root/coating/work/auto_refinish_300_20260731/kg_ingest_20260803/ppg/merged_kg_pack"
FULLER = "/root/coating/work/hb_fuller_122_20260813/merged_kg_pack"
FULLER_V2 = "/root/coating/work/track_b6_20260817/accepted_packs/hb_fuller"
AXALTA_V2 = "/root/coating/work/track_b6_20260817/accepted_packs/axalta"
AXES = (
    "application_domains", "coating_functions", "coating_layers",
    "supply_forms", "film_processes",
)


def test_module_is_available():
    assert importlib.util.find_spec("query_classification") is not None, "registry module not implemented"


@pytest.fixture
def qc():
    assert importlib.util.find_spec("query_classification") is not None, "registry module not implemented"
    module = importlib.import_module("query_classification")
    module._load_registry.cache_clear()
    yield module
    module._load_registry.cache_clear()


def test_registry_defines_exactly_five_axes_and_is_copy_safe(qc):
    registry = qc.load_registry()
    definitions = qc.axis_definitions()
    assert tuple(definitions) == AXES
    assert registry["version"]
    for axis, definition in definitions.items():
        assert definition["definition"]
        assert definition["values"] == list(definition["definitions"])
        assert definition["aliases"]
        assert set(definition["values"]) == set(registry["axes"][axis]["values"])
    registry["rules"].clear()
    definitions["coating_functions"]["values"].clear()
    assert qc.load_registry()["rules"]
    assert "anticorrosion" in qc.axis_definitions()["coating_functions"]["values"]


@pytest.mark.parametrize("raw", ["anticorrosion", " ANTICORROSION ", "Anti-Corrosion", "\u9632\u8150"])
def test_query_exact_aliases_only_are_casefolded(qc, raw):
    result = qc.normalize_axis("coating_functions", raw)
    assert result["values"] == ["anticorrosion"]
    assert result["unsupported"] == []
    if raw != "anticorrosion":
        assert result["adjustments"]


@pytest.mark.parametrize("raw", ["corrosion", "anti", "not anticorrosive", "anticorrosion primer", "anticorrosion,primer", "protective_anticorrosive"])
def test_query_does_not_use_containment_or_split_compounds(qc, raw):
    result = qc.normalize_axis("coating_functions", raw)
    assert result["values"] == []
    assert result["unsupported"] == [raw]


def test_normalization_preserves_invalid_values_and_suggests_only_exact_other_axis(qc):
    raw = ["anti-corrosion", "anticorrosion", "primer", {"value": "antifouling"}, ["antifouling"], 5, False, ""]
    original = copy.deepcopy(raw)
    result = qc.normalize_axis("coating_functions", raw)
    assert raw == original
    assert result["values"] == ["anticorrosion"]
    assert result["unsupported"] == raw[2:]
    assert [(c["dimension"], c["value"]) for c in result["candidates"]] == [("coating_layers", "primer")]


def test_unknown_axis_is_reported_without_cross_axis_migration(qc):
    result = qc.normalize_axis("functions", "anticorrosion")
    assert result["values"] == []
    assert result["unsupported"] == ["anticorrosion"]
    assert result["adjustments"][0]["kind"] == "unknown_axis"


@pytest.mark.parametrize("raw", [None, []])
def test_omitted_axis_is_empty(qc, raw):
    assert qc.normalize_axis("coating_functions", raw) == {
        "values": [], "unsupported": [], "adjustments": [], "candidates": [],
    }


@pytest.mark.parametrize("source,field,raw,expected", [
    (LEGACY, "application_family", "protective_anticorrosive", {"application_domains": ["protective"]}),
    (LEGACY, "coating_family", "intumescent_fire_protection", {"coating_functions": ["fire_protection"]}),
    (LEGACY, "coating_family", "anti_corrosion_primer", {"coating_functions": ["anticorrosion"], "coating_layers": ["primer"]}),
    (LEGACY, "coating_family", "anti_corrosive_primer", {"coating_functions": ["anticorrosion"], "coating_layers": ["primer"]}),
    (LEGACY, "coating_family", "anticorrosive_primer", {"coating_functions": ["anticorrosion"], "coating_layers": ["primer"]}),
    (LEGACY, "coating_family", "anticorrosive_coating", {"coating_functions": ["anticorrosion"]}),
    (LEGACY, "coating_family", "primer", {"coating_layers": ["primer"]}),
    (LEGACY, "coating_family", "powder", {"supply_forms": ["powder"]}),
    (LEGACY, "coating_family", "electrocoat", {"film_processes": ["electrodeposition"]}),
    (LEGACY, "coating_family", "antifouling_marine", {"application_domains": ["marine"], "coating_functions": ["antifouling"]}),
    (LEGACY, "coating_family", "fouling_release", {"coating_functions": ["foul_release"]}),
    (LEGACY, "coating_family", "optical_fiber_primary_coating", {"application_domains": ["optical_fiber"], "coating_layers": ["optical_fiber_primary"]}),
    (LEGACY, "application_family", "coil_coating", {"film_processes": ["coil_coating"]}),
    (PPG, "application_family", "ambient_cure", {"film_processes": ["ambient_cure"]}),
    (PPG, "application_family", "clearcoat", {"coating_layers": ["clearcoat"]}),
    (PPG, "application_family", "basecoat_clearcoat", {"coating_layers": ["basecoat", "clearcoat"]}),
    (PPG, "application_family", "automotive_refinish", {"application_domains": ["automotive", "automotive_refinish"]}),
    (PPG, "coating_family", "UV_curable_automotive_refinish_primer", {"application_domains": ["automotive", "automotive_refinish"], "coating_functions": [], "coating_layers": ["primer"], "film_processes": ["uv_cure"]}),
    (FULLER, "binder_family", "hot_melt_adhesive", {"application_domains": ["adhesives_sealants"], "film_processes": ["hot_melt"]}),
    (FULLER_V2, "coating_family", "one_part_moisture_curable_hot_melt_sealant", {"application_domains": ["adhesives_sealants"], "film_processes": ["moisture_cure", "hot_melt"]}),
])
def test_reviewed_exact_assignments(qc, source, field, raw, expected):
    profile = {"doc_id": "SYNTHETIC", field: raw}
    receipt = qc.project_profile(profile, source)
    assert receipt["version"] == qc.load_registry()["version"]
    assert receipt["values"] == {axis: expected.get(axis, []) for axis in AXES}
    assert receipt["missing_axes"] == [axis for axis in AXES if not expected.get(axis)]
    assert receipt["unmapped"] == []
    mapping = receipt["mappings"][0]
    assert mapping["source_id"] == source
    assert mapping["field"] == field
    assert mapping["table"] == "patent_profiles.jsonl"
    assert mapping["raw"] == raw
    assert mapping["rule_id"]
    assert mapping["review_status"] == "reviewed_label_mapping"


@pytest.mark.parametrize("raw", ["Anticorrosive_primer", " anticorrosive_primer", "anticorrosive_primer ", "not_anticorrosive", "anticorrosive_primer_extra", "anticorrosive_primer,topcoat"])
def test_source_values_are_not_casefolded_trimmed_or_split(qc, raw):
    result = qc.project_profile({"coating_family": raw}, LEGACY)
    assert not any(result["values"].values())
    assert result["unmapped"][0]["raw"] == raw


@pytest.mark.parametrize("source,field,raw", [
    (LEGACY, "coating_family", "protective_anticorrosive"),
    (LEGACY, "coating_family", "protective_coating"),
    (LEGACY, "coating_family", "zinc_primer"),
    (LEGACY, "coating_family", "primer_or_finish"),
    (LEGACY, "coating_family", "clearcoat_or_topcoat"),
    (LEGACY, "coating_family", "primary"),
    (LEGACY, "coating_family", "anticorrosion_PEM"),
    (FULLER, "coating_family", "adhesive_or_functional_layer"),
    (FULLER_V2, "application_family", "industrial_adhesive_or_material"),
    (FULLER, "binder_family", "hot_melt_or_AirFlex_EN1165_latex_printed_on_tissue"),
    (LEGACY, "binder_family", "epoxy"),
    (LEGACY, "binder_family", "silicone"),
])
def test_ambiguous_unknown_and_chemistry_values_stay_visible(qc, source, field, raw):
    profile = {field: raw, "doc_id": "SYNTHETIC", "metadata": {"keep": [1]}}
    before = copy.deepcopy(profile)
    result = qc.project_profile(profile, source)
    assert profile == before
    assert not any(result["values"].values())
    assert result["unmapped"][0]["raw"] == raw
    assert result["unmapped"][0]["reason"]


def test_missing_ambiguous_excluded_and_unassessable_are_distinct(qc):
    reasons = []
    for raw in [None, "adhesive_or_functional_layer", "not_a_coating_formulation", "not_assessable_from_supplied_edition"]:
        reasons.append(qc.project_profile({"coating_family": raw}, FULLER)["unmapped"][0]["reason"])
    assert len(set(reasons)) == 4


def test_source_and_field_isolation_no_global_doc_id_or_collection_defaults(qc):
    for source in [PPG, AXALTA_V2, LEGACY + "/", LEGACY.upper(), "unknown"]:
        result = qc.project_profile({"doc_id": "SAME", "coating_family": "anticorrosive_primer"}, source)
        assert not any(result["values"].values())
        assert result["unmapped"]
    result = qc.project_profile({"application_family": "anticorrosive_primer"}, LEGACY)
    assert not any(result["values"].values())
    assert not any(qc.project_profile({"doc_id": "SAME"}, PPG)["values"].values())


def test_no_cross_call_cohort_or_mutable_output_leakage(qc):
    first = qc.project_profile({"doc_id": "SAME", "application_family": "protective_anticorrosive"}, LEGACY)
    first["values"]["coating_functions"].append("anticorrosion")
    second = qc.project_profile({"doc_id": "SAME", "coating_family": "anticorrosive_primer"}, FULLER_V2)
    assert not any(second["values"].values())
    assert qc.project_profile({}, LEGACY)["missing_axes"] == list(AXES)


def test_profile_list_provenance_dedupes_values_not_source_occurrences(qc):
    profile = {"coating_family": ["primer", "primer", "mystery", {"label": "antifouling"}, ["topcoat"]]}
    result = qc.project_profile(profile, LEGACY)
    assert result["values"]["coating_layers"] == ["primer"]
    assert [m["index"] for m in result["mappings"]] == [0, 1]
    assert [u["raw"] for u in result["unmapped"]] == profile["coating_family"][2:]
    result["unmapped"][1]["raw"]["label"] = "mutated"
    assert profile["coating_family"][3] == {"label": "antifouling"}


def test_historical_and_unapproved_values_are_not_effective_aliases(qc):
    profile = {"application_family": [], "application_family_raw": ["anticorrosive"], "cure_mechanism": "uv_cure", "application_domains": ["marine"]}
    result = qc.project_profile(profile, LEGACY)
    assert not any(result["values"].values())
    reasons = {u["field"]: u["reason"] for u in result["unmapped"]}
    assert reasons["application_family_raw"] == "historical_not_projected"
    assert reasons["cure_mechanism"] == "unreviewed_value"
    assert reasons["application_domains"] == "unreviewed_value"


def test_lookup_classifications_only_exact_bounded_and_deterministic(qc):
    result = qc.lookup(" ANTI-CORROSION ")
    assert result["total"] == 1
    assert result["items"][0]["dimension"] == "coating_functions"
    assert result["items"][0]["value"] == "anticorrosion"
    assert "doc_id" not in result["items"][0]
    assert qc.lookup("anti")["items"] == []
    assert qc.lookup("primer", "coating_functions")["items"] == []
    all_values = qc.lookup("", limit=1000)
    first = qc.lookup("", limit=2)
    assert first == qc.lookup("", limit=2)
    assert first["items"] == all_values["items"][:2]
    assert first["truncated"] is True
    assert first["total"] == len(all_values["items"])
    assert qc.lookup("", limit=0)["items"] == []


@pytest.mark.parametrize("kwargs", [{"dimension": "bad"}, {"limit": -1}, {"limit": True}, {"limit": "2"}])
def test_lookup_invalid_parameters_fail_explicitly(qc, kwargs):
    with pytest.raises(ValueError):
        qc.lookup("", **kwargs)


def test_every_rule_is_reachable_in_only_its_source_fields(qc):
    registry = qc.load_registry()
    for rule in registry["rules"]:
        for source_key in rule["sources"]:
            result = qc.project_profile({rule["field"]: rule["raw"]}, registry["sources"][source_key])
            assert result["values"] == {axis: rule["values"].get(axis, []) for axis in AXES}
            assert result["mappings"][0]["rule_id"] == rule["id"]


@pytest.mark.parametrize("corruption", ["axis", "unknown_output", "source", "duplicate_rule", "unmapped_collision", "alias_collision", "alias_target", "review_status", "version"])
def test_registry_validation_fails_closed(qc, monkeypatch, corruption):
    registry = qc.load_registry()
    if corruption == "axis":
        registry["axes"].pop("supply_forms")
    elif corruption == "unknown_output":
        registry["rules"][0]["values"] = {"coating_functions": ["invented"]}
    elif corruption == "source":
        registry["rules"][0]["sources"] = ["invented"]
    elif corruption == "duplicate_rule":
        duplicate = copy.deepcopy(registry["rules"][0])
        duplicate["id"] = "duplicate"
        registry["rules"].append(duplicate)
    elif corruption == "unmapped_collision":
        registry["unmapped_rules"][0].update({key: copy.deepcopy(registry["rules"][0][key]) for key in ("sources", "field", "raw")})
    elif corruption == "alias_collision":
        registry["axes"]["coating_functions"]["aliases"][" ANTI-CORROSION "] = "fire_protection"
    elif corruption == "alias_target":
        registry["axes"]["coating_functions"]["aliases"]["broken"] = "invented"
    elif corruption == "review_status":
        registry["rules"][0]["review_status"] = "candidate_unreviewed"
    else:
        registry["version"] = ""
    qc._load_registry.cache_clear()
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: json.dumps(registry))
    with pytest.raises(ValueError):
        qc.load_registry()


def test_missing_registry_is_not_a_silent_empty_registry(qc, monkeypatch):
    qc._load_registry.cache_clear()
    def missing(*args, **kwargs):
        raise FileNotFoundError("synthetic missing registry")
    monkeypatch.setattr(Path, "read_text", missing)
    with pytest.raises(FileNotFoundError):
        qc.load_registry()


def test_runtime_has_no_file_write_side_effects(qc, monkeypatch):
    import builtins
    import io

    original_open = builtins.open
    original_io_open = io.open
    def guard(original):
        def read_only(file, mode="r", *args, **kwargs):
            assert not any(flag in mode for flag in "wax+"), "runtime attempted a file write"
            return original(file, mode, *args, **kwargs)
        return read_only
    monkeypatch.setattr(builtins, "open", guard(original_open))
    monkeypatch.setattr(io, "open", guard(original_io_open))
    qc.load_registry()
    qc.axis_definitions()
    qc.normalize_axis("coating_functions", "anti-corrosion")
    qc.project_profile({"coating_family": "primer"}, LEGACY)
    qc.lookup("primer")


def test_property_families_are_literal_migration_with_no_domain_inference(qc):
    import hashlib

    families = qc.load_registry()["property_families"]
    assert len(families) == 9
    assert sum(map(len, families.values())) == 70
    assert len({item for ids in families.values() for item in ids}) == 68
    digest = hashlib.sha256(json.dumps(families, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert digest == "dc674ccbbce8b7f814a482b817f7062208fc42cc28f35ed7692ecf0e3137c03f"
    result = qc.lookup(" MARINE_CORROSION ", dimension="property_families")
    assert result["total"] == 1
    item = result["items"][0]
    assert item["definition"]
    assert item["ids"] == families["marine_corrosion"]
    assert item["dimension"] == "property_families"
    assert "application_domains" not in item
    assert qc.lookup("marine", dimension="property_families")["items"] == []
    assert qc.normalize_axis("application_domains", "marine_corrosion")["values"] == []
    assert not any(qc.project_profile({"coating_family": "marine_corrosion"}, LEGACY)["values"].values())


def test_empty_property_map_is_rejected(qc, monkeypatch):
    registry = qc.load_registry()
    registry["property_families"] = {}
    qc._load_registry.cache_clear()
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: json.dumps(registry))
    with pytest.raises(ValueError):
        qc.load_registry()


@pytest.mark.parametrize("ids", ["PROP_test", 123, None, [], [123], [""], ["  "], ["PROP_test", None]])
def test_property_family_invalid_ids_block_activation(qc, monkeypatch, ids):
    registry = qc.load_registry()
    registry["property_families"]["marine_corrosion"] = ids
    qc._load_registry.cache_clear()
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: json.dumps(registry))
    with pytest.raises(ValueError, match="property family"):
        qc.load_registry()


@pytest.mark.parametrize("table", ["multi_value", "concepts", "concept_aliases"])
@pytest.mark.parametrize("token", ["anti-fouling", " ANTI-FOULING "])
@pytest.mark.parametrize("conflicting", [True, False])
def test_legacy_cross_table_overlap_checked_before_merge(qc, monkeypatch, table, token, conflicting):
    registry = qc.load_registry()
    tables = registry["legacy_coating_tables"]
    expected = [tables["aliases"]["aliases"]["anti-fouling"]]
    outputs = ["foul_release"] if conflicting else expected
    if table == "multi_value":
        tables[table]["expand"][token] = outputs
    elif table == "concepts":
        tables[table]["concepts"][token] = outputs
    else:
        tables["concepts"]["concepts"]["guard_test_concept"] = outputs
        tables["concepts"]["concept_aliases"][token] = "guard_test_concept"
    qc._load_registry.cache_clear()
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: json.dumps(registry))
    if conflicting:
        with pytest.raises(ValueError, match="ambiguous query alias"):
            qc.load_registry()
    else:
        assert qc.normalize_legacy_filter("coating_families", token)["values"] == expected


def test_invalid_legacy_table_entry_cannot_be_masked_by_later_table(qc, monkeypatch):
    registry = qc.load_registry()
    tables = registry["legacy_coating_tables"]
    tables["multi_value"]["expand"]["guard_test_token"] = []
    tables["concepts"]["concepts"]["guard_test_token"] = ["antifouling"]
    qc._load_registry.cache_clear()
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: json.dumps(registry))
    with pytest.raises(ValueError, match="invalid token outputs"):
        qc.load_registry()


def test_legacy_query_lookup_stays_legacy_and_does_not_guess_new_axes(qc):
    result = qc.normalize_legacy_filter("application_family", "protective_anticorrosive")
    assert result["mode"] == "legacy"
    assert result["field"] == "application_family"
    assert result["values"] == ["protective_anticorrosive"]
    assert result["unsupported"] == []
    assert "anticorrosion" not in result["values"]
    assert qc.normalize_axis("application_domains", "protective_anticorrosive")["unsupported"]
    compound = qc.normalize_legacy_filter("coating_families", "marine_antifouling")
    assert compound["values"] == ["antifouling", "foul_release", "fouling_control"]
    assert compound["mode"] == "legacy"
    for raw in ["primer_or_finish", "not_anticorrosive", "unknown"]:
        bad = qc.normalize_legacy_filter("coating_families", raw)
        assert bad["values"] == []
        assert bad["unsupported"] == [raw]
    assert qc.normalize_legacy_filter("application_family", "anticorrosive")["unsupported"] == ["anticorrosive"]


def test_legacy_coating_tables_are_exact_structural_copies(qc):
    config = CORE.parent / "config"
    tables = qc.load_registry()["legacy_coating_tables"]
    for key, filename in (
        ("aliases", "coating_family_aliases.json"),
        ("multi_value", "coating_family_multi_value.json"),
        ("concepts", "coating_family_concepts.json"),
    ):
        assert tables[key] == json.loads((config / filename).read_text(encoding="utf-8"))
    assert tables["applications_aliases"] == {"\u9632\u6c61": "antifouling", "\u6c61\u635f\u91ca\u653e": "foul_release"}
    assert "tokens" not in qc.load_registry()["legacy_filters"]["coating_families"]


def test_legacy_concept_migration_receipt_is_preserved(qc):
    result = qc.normalize_legacy_filter("coating_families", "MARINE_ANTIFOULING")
    assert result["values"] == ["antifouling", "foul_release", "fouling_control"]
    assert result["legacy_adjustments"] == ["concept_alias_migrated:marine_antifouling->marine_fouling_control"]
    assert result["mode"] == "legacy"


def test_runtime_reads_only_one_registry_file(qc, monkeypatch):
    read = Path.read_text
    calls = []
    def only_registry(path, *args, **kwargs):
        calls.append(path)
        assert path == qc.REGISTRY_PATH
        return read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", only_registry)
    qc.load_registry()
    qc.axis_definitions()
    qc.normalize_legacy_filter("coating_families", "marine_antifouling")
    qc.project_profile({"coating_family": "primer"}, LEGACY)
    qc.lookup("marine_corrosion", "property_families")
    assert calls == [qc.REGISTRY_PATH]


SUPPLEMENTARY_PATH = CORE.parents[1] / "docs/verification/supplementary_profile_label_proposals_20260908.json"
SUPPLEMENTARY = json.loads(SUPPLEMENTARY_PATH.read_text(encoding="utf-8"))


def _approved_supplementary_mappings():
    approved = copy.deepcopy(SUPPLEMENTARY["rules"])
    automotive_only = copy.deepcopy(next(r for r in SUPPLEMENTARY["unmapped_decisions"] if r["id"] == "SUP-U014"))
    automotive_only.update(id="SUP-M059", values={"application_domains": ["automotive"]})
    approved.append(automotive_only)
    return [(source, rule) for rule in approved for source in rule["sources"]]


@pytest.mark.parametrize("source,rule", _approved_supplementary_mappings(),
                         ids=[f"{s}-{r['id']}" for s, r in _approved_supplementary_mappings()])
def test_boyle_approved_supplementary_exact_mappings(qc, source, rule):
    root = SUPPLEMENTARY["source_bindings"][source]
    profile = {"doc_id": "SYNTHETIC", rule["field"]: rule["raw"]}
    before = copy.deepcopy(profile)
    result = qc.project_profile(profile, root)
    assert result["values"] == {axis: rule["values"].get(axis, []) for axis in AXES}
    assert result["unmapped"] == []
    assert profile == before
    mapping = result["mappings"][0]
    assert (mapping["rule_id"], mapping["source_id"], mapping["field"], mapping["raw"]) == (
        rule["id"], root, rule["field"], rule["raw"])
    assert mapping["review_status"] == "reviewed_label_mapping"
    for variant in (" " + rule["raw"], rule["raw"] + " extra"):
        rejected = qc.project_profile({rule["field"]: variant}, root)
        assert not any(rejected["values"].values())
        assert rejected["unmapped"][0]["reason"] == "unreviewed_value"


@pytest.mark.parametrize("source,rule", [
    (source, rule) for rule in SUPPLEMENTARY["unmapped_decisions"]
    if rule["id"] != "SUP-U014" for source in rule["sources"]
], ids=[f"{source}-{rule['id']}" for rule in SUPPLEMENTARY["unmapped_decisions"]
        if rule["id"] != "SUP-U014" for source in rule["sources"]])
def test_boyle_supplementary_unmapped_reasons_are_preserved(qc, source, rule):
    result = qc.project_profile({rule["field"]: rule["raw"]}, SUPPLEMENTARY["source_bindings"][source])
    assert not any(result["values"].values())
    unresolved = result["unmapped"][0]
    assert unresolved["rule_id"] == rule["id"]
    assert unresolved["reason"] == rule["reason_code"]
    assert unresolved["detail"] == rule["reason"]
    assert unresolved["raw"] == rule["raw"]


def test_supplementary_activation_preserves_frozen_vocabularies_and_original_rules(qc):
    import hashlib

    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    assert hashlib.sha256(SUPPLEMENTARY_PATH.read_bytes()).hexdigest() == "3f77a94d891c5ced1c2da3bf93dfd373c20432be0d17f3f47386d4051b9f5ad4"
    registry = qc.load_registry()
    assert digest({k: registry[k] for k in ("axes", "legacy_filters", "legacy_coating_tables", "property_families", "sources")}) == "b8fe100bf5309ae55b6f67793a08d9d1cf5dd825f7d0f62f0d8505c62903cae7"
    original = {k: [r for r in registry[k] if not r["id"].startswith("SUP-")] for k in ("rules", "unmapped_rules")}
    assert digest(original) == "8a35e220da310f72d6b1769c6b2dd587e9f65173ae93985e279226b3f482bf04"
    assert len(registry["rules"]) == 113
    assert len(registry["unmapped_rules"]) == 51
    assert registry["version"] == "query_classification.v1.20260908.supplement1"
    review = registry["supplementary_review"]
    assert review["reviewer"] == "Boyle"
    assert review["census_sha256"] == SUPPLEMENTARY["census"]["sha256"]
    assert review["proposal_sha256"] == "3f77a94d891c5ced1c2da3bf93dfd373c20432be0d17f3f47386d4051b9f5ad4"
    m38 = next(r for r in registry["rules"] if r["id"] == "SUP-M038")
    assert m38["reason"] == "Hot melt and clear coat are explicit in the complete label; thermosetting/two-component details are not mapped to cure temperature or package/process facets."
    assert not any(r["id"] == "SUP-U014" for r in registry["unmapped_rules"])
    assert qc.normalize_axis("film_processes", "thermal")["unsupported"] == ["thermal"]


@pytest.mark.parametrize("field", ["cure_mechanism", "application_domain", "application_domains", "technical_domain", "domain", "coating_system"])
def test_supplementary_fields_preserve_missing_and_unsupported_shapes(qc, field):
    assert qc.load_registry()["profile_fields"][field] == "effective"
    for raw in (None, "", []):
        result = qc.project_profile({field: raw}, LEGACY)
        assert not any(result["values"].values())
        assert result["unmapped"][0]["reason"] == "missing_value"
    for raw in ({"label": "UV"}, 123, True, [["UV"]]):
        result = qc.project_profile({field: raw}, LEGACY)
        assert not any(result["values"].values())
        assert result["unmapped"][0]["reason"] == "unsupported_shape"


def test_supplementary_array_members_keep_independent_provenance_and_unknowns(qc):
    source = SUPPLEMENTARY["source_bindings"]["S3"]
    raw = ["adhesive", "car repair coating", "coating", "car repair coating", {"label": "adhesive"}]
    before = copy.deepcopy(raw)
    result = qc.project_profile({"application_domains": raw}, source)
    assert result["values"]["application_domains"] == ["adhesives_sealants", "automotive", "automotive_refinish"]
    assert [m["index"] for m in result["mappings"]] == [0, 1, 3]
    assert [u["index"] for u in result["unmapped"]] == [2, 4]
    assert result["unmapped"][0]["reason"] == "insufficient_specificity"
    assert not result["values"]["coating_layers"]
    assert raw == before


def test_supplementary_source_field_and_case_isolation(qc):
    source = SUPPLEMENTARY["source_bindings"]["S5"]
    for root, field, raw in (
        (source, "domain", "AUTOMOTIVE COATING COMPOSITIONS"),
        (source, "application_domain", "automotive coating compositions"),
        (FULLER, "domain", "automotive coating compositions"),
        (source + "/", "domain", "automotive coating compositions"),
    ):
        result = qc.project_profile({"doc_id": "SAME", field: raw}, root)
        assert not any(result["values"].values())
        assert result["unmapped"]
    s2 = SUPPLEMENTARY["source_bindings"]["S2"]
    automotive_only = qc.project_profile({"application_domain": "automotive OEM refinish applications"}, s2)
    assert automotive_only["values"]["application_domains"] == ["automotive"]
    assert automotive_only["mappings"][0]["rule_id"] == "SUP-M059"
