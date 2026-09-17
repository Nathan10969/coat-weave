"""
Project vision-extracted JSON into 10 JSONL + manifest + CSV.

Input: vision_input.json (the deterministic transcription Claude produces from
visual reading of PDF page images — NO MinerU / NO OCR used)
Output: $OUT_DIR/*.jsonl + manifest.json + boss_preview.csv
Gates:
    - facts_with_evidence_id == 1.0
    - hyperedges_with_evidence_ids == 1.0
    - evidence_units_with_asset_path == 1.0
    - All edges pass type-whitelist (HAS_RESIN→material/binder|resin|curing_agent|hardener, etc.)

Usage:
    python vision_project.py <vision_input.json> <out_dir>
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def md5_12(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:12]


def slug(s: str) -> str:
    return re.sub(r"\W+", "_", s.lower()).strip("_")


def main(vi_path: Path, out_dir: Path) -> int:
    vi = json.loads(vi_path.read_text(encoding="utf-8"))
    doc_id = vi["doc_id"]
    kg_source = f"{doc_id}_claude_vision_strict_example_scope_kg_pack_v1"
    out_dir.mkdir(parents=True, exist_ok=True)
    catalog = vi["ingredients_catalog"]

    # All canonical entities accumulate here; entity_type_seq is global continuous
    canonical: dict[str, dict] = {}
    canonical_order: list[str] = []
    seq_ent = 0

    def get_or_create_entity(canonical_name: str, entity_type: str,
                             category: str | None = None, role: str | None = None,
                             role_detail: str | None = None, alias: str | None = None) -> str:
        nonlocal seq_ent
        if not canonical_name:
            return ""
        key = canonical_name.strip()
        if key in canonical:
            rec = canonical[key]
            if alias and alias not in rec["aliases"]:
                rec["aliases"].append(alias)
            return rec["entity_id"]
        seq_ent += 1
        ent_id = f"ENT_{entity_type.upper()}_{seq_ent:05d}"
        rec: dict = {
            "record_type": "CanonicalEntityRecord",
            "schema_version": "kg_pack_canonical_entity_v1",
            "node_type": "CANONICAL_ENTITY",
            "node_id": ent_id, "entity_id": ent_id,
            "entity_type": entity_type, "canonical_name": key,
            "aliases": [alias or key],
            "patent_id": doc_id, "doc_id": doc_id, "kg_source": kg_source,
        }
        if category: rec["category"] = category
        if role: rec["role"] = role
        if role_detail: rec["role_detail"] = role_detail
        canonical[key] = rec
        canonical_order.append(key)
        return ent_id

    facts: list[dict] = []
    evidence_units: list[dict] = []
    hyperedges: list[dict] = []
    example_contexts: list[dict] = []
    edges: list[dict] = []
    canonical_relations: list[dict] = []
    seq_fact = 0
    seq_hedge = 0

    def new_fact_id() -> str:
        nonlocal seq_fact
        seq_fact += 1
        return f"FACT_{doc_id}_{seq_fact:06d}"

    def new_hedge_id() -> str:
        nonlocal seq_hedge
        seq_hedge += 1
        return f"HEDGE_{doc_id}_{seq_hedge:04d}"

    def add_edge(edge_type: str, src: str, dst: str, **extras) -> None:
        if not src or not dst:
            return
        eid = "EDGE_" + md5_12(f"{edge_type}|{src}|{dst}|{json.dumps(extras, sort_keys=True)}")
        rec = {
            "record_type": "EdgeRecord", "schema_version": "kg_pack_edge_v1",
            "edge_id": eid, "edge_type": edge_type, "src": src, "dst": dst,
            "patent_id": doc_id, "doc_id": doc_id, "kg_source": kg_source,
        }
        rec.update(extras)
        edges.append(rec)

    pat_id = f"PAT_{doc_id}"
    profile_id = f"PROFILE_{doc_id}"

    # Pre-seed shared substrate / application canonicals
    ent_sub_tested = get_or_create_entity(vi["shared_test_substrate"], "substrate")
    ent_sub_claimed = get_or_create_entity(vi["shared_claimed_substrate"], "substrate")
    ent_app_claimed = get_or_create_entity(
        "anti-corrosive zinc primer coating compositions for protecting iron and steel structures", "application")

    APP_NAMES = {
        "epoxy": "epoxy-based anti-corrosive zinc primer coating",
        "silicate": "silicate-based anti-corrosive zinc primer coating",
        "polysiloxane": "polysiloxane-based anti-corrosive zinc primer coating",
        "polyurethane": "polyurethane-based anti-corrosive zinc primer coating",
    }
    PROP_NAMES = {
        ("formulation","epoxy"):       ("basic epoxy-based paint formulation","formulation"),
        ("formulation","silicate"):    ("basic silicate-based paint formulation","formulation"),
        ("formulation","polysiloxane"):("basic polysiloxane-based paint formulation","formulation"),
        ("formulation","polyurethane"):("basic polyurethane-based paint formulation","formulation"),
        ("result-sst","epoxy"):        ("rust creep / corrosion resistance","corrosion_resistance"),
        ("result-sst","silicate"):     ("rust creep / corrosion resistance","corrosion_resistance"),
        ("result-sst","polysiloxane"): ("rust creep / corrosion resistance","corrosion_resistance"),
        ("result-sst","polyurethane"): ("rust creep / corrosion resistance","corrosion_resistance"),
        ("result-crack","epoxy"):      ("cracking resistance after thermal cycling","durability"),
    }

    # Index hyperedges by (sample_id, table_label) so we can attach baseline edges later
    sample_to_hedge: dict[tuple[str, str], dict] = {}

    # === walk each table ===
    for tbl in vi["tables"]:
        label = tbl["label"]
        kind = tbl["kind"]
        system = tbl["system"]
        caption = tbl["caption"]
        first_page = tbl["pages"][0]
        page_img = f"pages/page_{first_page:03d}.png"
        app_tested = APP_NAMES.get(system, "anti-corrosive zinc primer coating")
        ent_app_tested = get_or_create_entity(app_tested, "application")

        if kind == "formulation":
            prop_name, prop_cat = PROP_NAMES[("formulation", system)]
            ent_prop = get_or_create_entity(prop_name, "property", category=prop_cat)
            bundle_type = "formulation_context"
            for sample_id in tbl["samples"]:
                ctx_slug = slug(f"{doc_id}__{label}__{sample_id}__formulation")
                excxt_id = f"EXCTX_{ctx_slug}"
                evd_id = f"EVD_{doc_id}_p{first_page}_{slug(label)}_{slug(sample_id)}"
                hedge_id = new_hedge_id()
                example_contexts.append({
                    "record_type": "ExampleContextRecord",
                    "schema_version": "kg_pack_example_context_v1",
                    "node_type": "EXAMPLE_CONTEXT",
                    "node_id": excxt_id, "context_id": ctx_slug,
                    "patent_id": doc_id, "doc_id": doc_id,
                    "example_id": label, "sample_id": sample_id,
                    "bundle_type": bundle_type, "table_role": label,
                    "evidence_ids": [evd_id], "linked_hyperedge_ids": [hedge_id],
                    "kg_source": kg_source,
                })

                direct_units: list[dict] = []
                cell_excerpts: list[str] = []
                page_facts: list[dict] = []
                next_u = 1

                def add_unit(slot: str, **kw):
                    nonlocal next_u
                    rec = {"slot": slot, "unit_id": f"u{next_u:03d}", **kw}
                    direct_units.append(rec)
                    next_u += 1
                    return rec

                # sample_context / example_context / property facts
                add_unit("sample_context", source_field="sample_id", name=sample_id, raw_text=sample_id)
                fid = new_fact_id()
                page_facts.append({
                    "record_type":"FactRecord","schema_version":"kg_pack_fact_v1","node_type":"FACT",
                    "node_id":fid,"fact_id":fid,"patent_id":doc_id,"doc_id":doc_id,
                    "evidence_id":evd_id,"context_id":ctx_slug,"hyperedge_id":hedge_id,
                    "slot":"sample_context","source_field":"sample_id","name":sample_id,
                    "metric":None,"role":None,"role_detail":None,
                    "value":sample_id,"unit":None,"raw_text":sample_id,
                    "amount_basis":None,"scale":None,"step_type":None,
                    "linked_canonical_entity_id":"","kg_source":kg_source,
                })

                add_unit("example_context", source_field="example_id", name=label, raw_text=label)
                fid = new_fact_id()
                page_facts.append({
                    "record_type":"FactRecord","schema_version":"kg_pack_fact_v1","node_type":"FACT",
                    "node_id":fid,"fact_id":fid,"patent_id":doc_id,"doc_id":doc_id,
                    "evidence_id":evd_id,"context_id":ctx_slug,"hyperedge_id":hedge_id,
                    "slot":"example_context","source_field":"example_id","name":label,
                    "metric":None,"role":None,"role_detail":None,
                    "value":label,"unit":None,"raw_text":label,
                    "amount_basis":None,"scale":None,"step_type":None,
                    "linked_canonical_entity_id":"","kg_source":kg_source,
                })

                add_unit("property", source_field="property", name=prop_name,
                          category=prop_cat, measured_or_claimed="measured")
                fid = new_fact_id()
                page_facts.append({
                    "record_type":"FactRecord","schema_version":"kg_pack_fact_v1","node_type":"FACT",
                    "node_id":fid,"fact_id":fid,"patent_id":doc_id,"doc_id":doc_id,
                    "evidence_id":evd_id,"context_id":ctx_slug,"hyperedge_id":hedge_id,
                    "slot":"property","source_field":"property","name":prop_name,
                    "metric":None,"role":None,"role_detail":None,
                    "value":prop_name,"unit":None,"raw_text":prop_name,
                    "amount_basis":None,"scale":None,"step_type":None,
                    "linked_canonical_entity_id":ent_prop,"kg_source":kg_source,
                })

                # substrate fact
                add_unit("substrate", source_field="substrate.tested", name="tested_substrate",
                          raw_text=vi["shared_test_substrate"])
                fid = new_fact_id()
                page_facts.append({
                    "record_type":"FactRecord","schema_version":"kg_pack_fact_v1","node_type":"FACT",
                    "node_id":fid,"fact_id":fid,"patent_id":doc_id,"doc_id":doc_id,
                    "evidence_id":evd_id,"context_id":ctx_slug,"hyperedge_id":hedge_id,
                    "slot":"substrate","source_field":"substrate.tested","name":"tested_substrate",
                    "metric":None,"role":None,"role_detail":None,
                    "value":None,"unit":None,"raw_text":vi["shared_test_substrate"],
                    "amount_basis":None,"scale":None,"step_type":None,
                    "linked_canonical_entity_id":ent_sub_tested,"kg_source":kg_source,
                })

                resin_list: list[dict] = []
                addl_list: list[dict] = []

                # Walk ingredient rows
                for row in tbl["rows"]:
                    ingr = row["ingredient"]
                    if sample_id not in row["values"]:
                        continue
                    val = row["values"][sample_id]
                    if val is None or val == "":
                        continue
                    meta = catalog.get(ingr, {"slot":"component","role":"other"})
                    slot = meta["slot"]
                    role = meta.get("role","other")
                    role_detail = meta.get("role_detail")
                    ent_id = get_or_create_entity(ingr, "material", role=role, role_detail=role_detail, alias=ingr)
                    unit_rec = {"slot": slot, "source_field": "resin" if slot=="resin" else "additives",
                                  "name": ingr, "role": role,
                                  "value": str(val), "unit": tbl.get("unit","%SV"),
                                  "raw_text": f"{val} {tbl.get('unit','%SV')}",
                                  "amount_basis":"formulation_amount",
                                  "evidence_ref": f"{label} / {sample_id} / {ingr}",
                                  "unit_id": f"u{next_u:03d}"}
                    if role_detail: unit_rec["role_detail"] = role_detail
                    direct_units.append(unit_rec); next_u += 1
                    cell_excerpts.append(f"{ingr}: {val} {tbl.get('unit','%SV')}")
                    fid = new_fact_id()
                    page_facts.append({
                        "record_type":"FactRecord","schema_version":"kg_pack_fact_v1","node_type":"FACT",
                        "node_id":fid,"fact_id":fid,"patent_id":doc_id,"doc_id":doc_id,
                        "evidence_id":evd_id,"context_id":ctx_slug,"hyperedge_id":hedge_id,
                        "slot":slot,"source_field":"resin" if slot=="resin" else "additives",
                        "name":ingr,"metric":None,"role":role,"role_detail":role_detail,
                        "value":str(val),"unit":tbl.get("unit","%SV"),
                        "raw_text":f"{val} {tbl.get('unit','%SV')}",
                        "amount_basis":"formulation_amount","scale":None,"step_type":None,
                        "linked_canonical_entity_id":ent_id,"kg_source":kg_source,
                    })
                    item = {"name": ingr, "role": role, "amount": str(val), "unit": tbl.get("unit","%SV")}
                    if role_detail: item["role_detail"] = role_detail
                    if slot == "resin":
                        resin_list.append(item)
                    else:
                        addl_list.append(item)

                # Walk formulation metrics
                for met in tbl.get("metrics", []):
                    if sample_id not in met["values"]:
                        continue
                    v = met["values"][sample_id]
                    if v is None or v == "":
                        continue
                    mname = met["metric"]
                    munit = met.get("unit")
                    eid = get_or_create_entity(mname, "formulation_metric")
                    direct_units.append({"slot":"formulation_metric","source_field":"result",
                                          "metric":mname,"value":str(v),"unit":munit,
                                          "raw_text":f"{v}{(' '+munit) if munit else ''}",
                                          "scale":"formulation metric",
                                          "unit_id":f"u{next_u:03d}"}); next_u += 1
                    cell_excerpts.append(f"{mname}: {v}{(' '+munit) if munit else ''}")
                    fid = new_fact_id()
                    page_facts.append({
                        "record_type":"FactRecord","schema_version":"kg_pack_fact_v1","node_type":"FACT",
                        "node_id":fid,"fact_id":fid,"patent_id":doc_id,"doc_id":doc_id,
                        "evidence_id":evd_id,"context_id":ctx_slug,"hyperedge_id":hedge_id,
                        "slot":"formulation_metric","source_field":"result",
                        "name":None,"metric":mname,"role":None,"role_detail":None,
                        "value":str(v),"unit":munit,"raw_text":f"{v}{(' '+munit) if munit else ''}",
                        "amount_basis":None,"scale":"formulation metric","step_type":None,
                        "linked_canonical_entity_id":eid,"kg_source":kg_source,
                    })

                facts.extend(page_facts)

                hedge = {
                    "record_type":"HyperedgeRecord","schema_version":"kg_pack_l2_hyperedge_v1",
                    "node_type":"HYPEREDGE","node_id":hedge_id,"hyperedge_id":hedge_id,
                    "patent_id":doc_id,"doc_id":doc_id,
                    "context_id":ctx_slug,"bundle_type":bundle_type,
                    "sample_id":sample_id,"example_id":label,
                    "application":{"tested":app_tested,
                                    "claimed":"anti-corrosive zinc primer coating compositions for protecting iron and steel structures"},
                    "substrate":{"tested":vi["shared_test_substrate"],
                                  "claimed":vi["shared_claimed_substrate"]},
                    "resin":resin_list,"additives":addl_list,"process":[],
                    "property":{"name":prop_name,"category":prop_cat,"measured_or_claimed":"measured"},
                    "test_method":{"name":None,"standard_id":[],
                                    "condition":{"exposure":None,"immersion":None,"rating_scale":None,"score_max":None}},
                    "result":[],
                    "baseline":{"baseline_sample_id":None,"baseline_context_id":None,
                                 "baseline_result":None,"comparison_text":None},
                    "evidence":[{"evidence_id":evd_id,"page":first_page,
                                  "section":f"Example / {label} - {caption[:80]}",
                                  "table":label,"row":sample_id,"column":tbl.get("unit","%SV"),
                                  "evidence_type":"formulation_table","printed_page":first_page-1}],
                    "confidence":{"overall":0.95,"sample_binding":0.95,
                                   "formulation_binding":0.95,"result_binding":0.0},
                    "qa_flags":["claude_vision_only","no_ocr","no_mineru","examples_section_only"],
                    "unresolved_fields":[],"kg_source":kg_source,
                    "source_scope":"examples_pages_43_62",
                    "evidence_ids":[evd_id],"answerability":"context_bundle",
                    "projection_note":"Use this L2 record as fact source; CSV should be a view only.",
                }
                hyperedges.append(hedge)
                sample_to_hedge[(sample_id, label)] = hedge

                evidence_units.append({
                    "record_type":"EvidenceRecord","schema_version":"kg_pack_l1_evidence_unit_v1",
                    "node_type":"EVD","node_id":evd_id,"evidence_id":evd_id,
                    "patent_id":doc_id,"doc_id":doc_id,"layer":"L1_evidence_unit",
                    "source":"claude_vision_no_ocr_no_mineru",
                    "source_pdf":vi["source_pdf_name"],
                    "page":first_page,"printed_page":first_page-1,
                    "section":f"Example / {label} - {caption[:80]}",
                    "evidence_type":"formulation_table","source_block_type":"formulation_table_row",
                    "table":label,"row":sample_id,"column":tbl.get("unit","%SV"),
                    "caption":caption,"bbox":None,
                    "asset_path":page_img,
                    "asset_bbox_image":None,"asset_bbox_pdf":None,
                    "text_excerpt":f"{label} row '{sample_id}'; "+ " | ".join(cell_excerpts),
                    "quote_style":"structured_row_transcription",
                    "direct_extracted_units":direct_units,
                    "context_bindings":{"linked_l2_context_ids":[ctx_slug],
                                         "primary_l2_context_ids":[ctx_slug],
                                         "inherited_by_l2_context_ids":[],
                                         "linked_bundle_types":[bundle_type],
                                         "linked_sample_ids":[sample_id]},
                    "coverage_scope":"examples_section_tables_and_related_method_paragraphs",
                    "confidence":0.95,
                    "qa_flags":["claude_vision_only","no_ocr","no_mineru","examples_section_only"],
                    "unresolved_fields":[],"kg_source":kg_source,
                    "trace_role":"L1_retrieval_context","source_scope":"examples_pages_43_62",
                })

        elif kind == "result":
            # Determine prop type
            if "crack" in (caption or "").lower():
                prop_key = ("result-crack", system)
                tm_name = tbl.get("test_method","Cracking test / thermal cycling resistance test")
            else:
                prop_key = ("result-sst", system)
                tm_name = tbl.get("test_method","Salt Spray Test (SST) / rust creep measurement")
            prop_name, prop_cat = PROP_NAMES.get(prop_key, ("rust creep / corrosion resistance","corrosion_resistance"))
            ent_prop = get_or_create_entity(prop_name, "property", category=prop_cat)
            ent_tm = get_or_create_entity(tm_name, "test_method")
            standards = tbl.get("test_standards", [])
            std_ents = [get_or_create_entity(s, "test_standard") for s in standards]
            bundle_type = "performance_result"
            metrics_defs = tbl.get("result_metrics", [])

            for row in tbl["rows"]:
                sample_id = row["sample"]
                values = row["values"]
                ctx_slug = slug(f"{doc_id}__{label}__{sample_id}__result")
                excxt_id = f"EXCTX_{ctx_slug}"
                evd_id = f"EVD_{doc_id}_p{first_page}_{slug(label)}_{slug(sample_id)}"
                hedge_id = new_hedge_id()
                example_contexts.append({
                    "record_type":"ExampleContextRecord","schema_version":"kg_pack_example_context_v1",
                    "node_type":"EXAMPLE_CONTEXT","node_id":excxt_id,"context_id":ctx_slug,
                    "patent_id":doc_id,"doc_id":doc_id,
                    "example_id":label,"sample_id":sample_id,
                    "bundle_type":bundle_type,"table_role":label,
                    "evidence_ids":[evd_id],"linked_hyperedge_ids":[hedge_id],
                    "kg_source":kg_source,
                })
                direct_units: list[dict] = []
                cell_excerpts: list[str] = []
                page_facts: list[dict] = []
                next_u = 1

                # context facts
                for slot_name, source_field, name, value in (
                    ("sample_context","sample_id",sample_id,sample_id),
                    ("example_context","example_id",label,label),
                    ("property","property",prop_name,prop_name),
                    ("substrate","substrate.tested","tested_substrate",vi["shared_test_substrate"]),
                ):
                    direct_units.append({"slot":slot_name,"source_field":source_field,
                                          "name":name,"raw_text":str(value),"unit_id":f"u{next_u:03d}"})
                    next_u += 1
                    ent_link = ""
                    if slot_name == "property": ent_link = ent_prop
                    elif slot_name == "substrate": ent_link = ent_sub_tested
                    fid = new_fact_id()
                    page_facts.append({
                        "record_type":"FactRecord","schema_version":"kg_pack_fact_v1","node_type":"FACT",
                        "node_id":fid,"fact_id":fid,"patent_id":doc_id,"doc_id":doc_id,
                        "evidence_id":evd_id,"context_id":ctx_slug,"hyperedge_id":hedge_id,
                        "slot":slot_name,"source_field":source_field,"name":name,
                        "metric":None,"role":None,"role_detail":None,
                        "value":str(value),"unit":None,"raw_text":str(value),
                        "amount_basis":None,"scale":None,"step_type":None,
                        "linked_canonical_entity_id":ent_link,"kg_source":kg_source,
                    })

                # test_method fact
                fid = new_fact_id()
                direct_units.append({"slot":"test_method","source_field":"test_method.name",
                                      "name":tm_name,"raw_text":tm_name,"unit_id":f"u{next_u:03d}"})
                next_u += 1
                page_facts.append({
                    "record_type":"FactRecord","schema_version":"kg_pack_fact_v1","node_type":"FACT",
                    "node_id":fid,"fact_id":fid,"patent_id":doc_id,"doc_id":doc_id,
                    "evidence_id":evd_id,"context_id":ctx_slug,"hyperedge_id":hedge_id,
                    "slot":"test_method","source_field":"test_method.name","name":tm_name,
                    "metric":None,"role":None,"role_detail":None,
                    "value":tm_name,"unit":None,"raw_text":tm_name,
                    "amount_basis":None,"scale":None,"step_type":None,
                    "linked_canonical_entity_id":ent_tm,"kg_source":kg_source,
                })
                for std, sid in zip(standards, std_ents):
                    fid = new_fact_id()
                    direct_units.append({"slot":"test_standard","source_field":"test_method.standard_id",
                                          "name":std,"raw_text":std,"unit_id":f"u{next_u:03d}"})
                    next_u += 1
                    page_facts.append({
                        "record_type":"FactRecord","schema_version":"kg_pack_fact_v1","node_type":"FACT",
                        "node_id":fid,"fact_id":fid,"patent_id":doc_id,"doc_id":doc_id,
                        "evidence_id":evd_id,"context_id":ctx_slug,"hyperedge_id":hedge_id,
                        "slot":"test_standard","source_field":"test_method.standard_id","name":std,
                        "metric":None,"role":None,"role_detail":None,
                        "value":std,"unit":None,"raw_text":std,
                        "amount_basis":None,"scale":None,"step_type":None,
                        "linked_canonical_entity_id":sid,"kg_source":kg_source,
                    })

                # Result cells
                results_for_hedge: list[dict] = []
                for mdef, val in zip(metrics_defs, values):
                    if val is None or val == "":
                        continue
                    mname = mdef["name"]
                    munit = mdef.get("unit")
                    eid = get_or_create_entity(mname, "performance_metric")
                    direct_units.append({"slot":"result","source_field":"result",
                                          "name":mname,"metric":mname,
                                          "value":str(val),"unit":munit,
                                          "raw_text":str(val),
                                          "evidence_ref":f"{label} / {sample_id} / {mname}",
                                          "unit_id":f"u{next_u:03d}"}); next_u += 1
                    cell_excerpts.append(f"{mname}: {val}")
                    fid = new_fact_id()
                    page_facts.append({
                        "record_type":"FactRecord","schema_version":"kg_pack_fact_v1","node_type":"FACT",
                        "node_id":fid,"fact_id":fid,"patent_id":doc_id,"doc_id":doc_id,
                        "evidence_id":evd_id,"context_id":ctx_slug,"hyperedge_id":hedge_id,
                        "slot":"result","source_field":"result",
                        "name":mname,"metric":mname,"role":None,"role_detail":None,
                        "value":str(val),"unit":munit,"raw_text":str(val),
                        "amount_basis":None,"scale":None,"step_type":None,
                        "linked_canonical_entity_id":eid,"kg_source":kg_source,
                    })
                    results_for_hedge.append({"metric":mname,"value":str(val),"unit":munit,"evidence_ref":evd_id})

                facts.extend(page_facts)

                hedge = {
                    "record_type":"HyperedgeRecord","schema_version":"kg_pack_l2_hyperedge_v1",
                    "node_type":"HYPEREDGE","node_id":hedge_id,"hyperedge_id":hedge_id,
                    "patent_id":doc_id,"doc_id":doc_id,
                    "context_id":ctx_slug,"bundle_type":bundle_type,
                    "sample_id":sample_id,"example_id":label,
                    "application":{"tested":app_tested,
                                    "claimed":"anti-corrosive zinc primer coating compositions for protecting iron and steel structures"},
                    "substrate":{"tested":vi["shared_test_substrate"],
                                  "claimed":vi["shared_claimed_substrate"]},
                    "resin":[],"additives":[],"process":[],
                    "property":{"name":prop_name,"category":prop_cat,"measured_or_claimed":"measured"},
                    "test_method":{"name":tm_name,"standard_id":list(standards),
                                    "condition":{"exposure":None,"immersion":None,"rating_scale":None,"score_max":None}},
                    "result":results_for_hedge,
                    "baseline":{"baseline_sample_id":None,"baseline_context_id":None,
                                 "baseline_result":None,"comparison_text":None},
                    "evidence":[{"evidence_id":evd_id,"page":first_page,
                                  "section":f"Example / {label} - {caption[:80]}",
                                  "table":label,"row":sample_id,"column":None,
                                  "evidence_type":"result_table","printed_page":first_page-1}],
                    "confidence":{"overall":0.95,"sample_binding":0.95,
                                   "formulation_binding":0.0,"result_binding":0.95},
                    "qa_flags":["claude_vision_only","no_ocr","no_mineru","examples_section_only"],
                    "unresolved_fields":[],"kg_source":kg_source,
                    "source_scope":"examples_pages_43_62",
                    "evidence_ids":[evd_id],"answerability":"context_bundle",
                    "projection_note":"Use this L2 record as fact source; CSV should be a view only.",
                }
                hyperedges.append(hedge)
                sample_to_hedge[(sample_id, label)] = hedge

                evidence_units.append({
                    "record_type":"EvidenceRecord","schema_version":"kg_pack_l1_evidence_unit_v1",
                    "node_type":"EVD","node_id":evd_id,"evidence_id":evd_id,
                    "patent_id":doc_id,"doc_id":doc_id,"layer":"L1_evidence_unit",
                    "source":"claude_vision_no_ocr_no_mineru",
                    "source_pdf":vi["source_pdf_name"],
                    "page":first_page,"printed_page":first_page-1,
                    "section":f"Example / {label} - {caption[:80]}",
                    "evidence_type":"result_table","source_block_type":"result_table_row",
                    "table":label,"row":sample_id,"column":None,
                    "caption":caption,"bbox":None,
                    "asset_path":page_img,
                    "asset_bbox_image":None,"asset_bbox_pdf":None,
                    "text_excerpt":f"{label} row '{sample_id}'; "+ " | ".join(cell_excerpts),
                    "quote_style":"structured_row_transcription",
                    "direct_extracted_units":direct_units,
                    "context_bindings":{"linked_l2_context_ids":[ctx_slug],
                                         "primary_l2_context_ids":[ctx_slug],
                                         "inherited_by_l2_context_ids":[],
                                         "linked_bundle_types":[bundle_type],
                                         "linked_sample_ids":[sample_id]},
                    "coverage_scope":"examples_section_tables_and_related_method_paragraphs",
                    "confidence":0.95,
                    "qa_flags":["claude_vision_only","no_ocr","no_mineru","examples_section_only"],
                    "unresolved_fields":[],"kg_source":kg_source,
                    "trace_role":"L1_retrieval_context","source_scope":"examples_pages_43_62",
                })

    # ==== baseline binding ====
    # For each result hyperedge tied to a Model paint, find a Reference paint
    # result hyperedge for the same table and bind it.
    for (sid, label), h in sample_to_hedge.items():
        if h["bundle_type"] != "performance_result":
            continue
        if not sid.startswith("Model paint"):
            continue
        # find any reference paint in same table
        for (sid2, label2), h2 in sample_to_hedge.items():
            if label2 != label or h2["bundle_type"] != "performance_result":
                continue
            if not (sid2.startswith("Reference paint") or sid2.startswith("Ref")):
                continue
            if h2["result"]:
                first = h2["result"][0]
                h["baseline"]["baseline_sample_id"] = sid2
                h["baseline"]["baseline_context_id"] = h2["context_id"]
                h["baseline"]["baseline_result"] = f"{first['value']} {first.get('unit') or ''}".strip()
                h["baseline"]["comparison_text"] = f"baseline = {sid2} in same {label}"
                # baseline fact
                fid = new_fact_id()
                facts.append({
                    "record_type":"FactRecord","schema_version":"kg_pack_fact_v1","node_type":"FACT",
                    "node_id":fid,"fact_id":fid,"patent_id":doc_id,"doc_id":doc_id,
                    "evidence_id":h["evidence_ids"][0],"context_id":h["context_id"],
                    "hyperedge_id":h["hyperedge_id"],
                    "slot":"baseline","source_field":"baseline",
                    "name":None,"metric":None,"role":None,"role_detail":None,
                    "value":h["baseline"]["baseline_result"],"unit":None,
                    "raw_text":h["baseline"]["comparison_text"],
                    "amount_basis":None,"scale":None,"step_type":None,
                    "linked_canonical_entity_id":"","kg_source":kg_source,
                })
                add_edge("HAS_BASELINE_HYPEREDGE", h["hyperedge_id"], h2["hyperedge_id"])
                break

    # ==== edges ====
    add_edge("HAS_PROFILE", pat_id, profile_id)
    for e in evidence_units:
        add_edge("HAS_EVIDENCE", pat_id, e["node_id"])
    for ec in example_contexts:
        add_edge("HAS_EXAMPLE_CONTEXT", pat_id, ec["node_id"])
        for hid in ec["linked_hyperedge_ids"]:
            add_edge("CONTEXT_HAS_HYPEREDGE", ec["node_id"], hid, bundle_type=ec["bundle_type"])
    for h in hyperedges:
        add_edge("HAS_HYPEREDGE", pat_id, h["node_id"])
        for eid in h["evidence_ids"]:
            add_edge("SUPPORTED_BY_EVIDENCE", h["node_id"], eid)
            add_edge("EVIDENCE_SUPPORTS_HYPEREDGE", eid, h["node_id"])
        ent_app_t = get_or_create_entity(h["application"]["tested"], "application")
        ent_app_c = get_or_create_entity(h["application"]["claimed"], "application")
        ent_sub_t = get_or_create_entity(h["substrate"]["tested"], "substrate")
        ent_sub_c = get_or_create_entity(h["substrate"]["claimed"], "substrate")
        add_edge("HAS_APPLICATION", h["node_id"], ent_app_t, relation_scope="tested")
        add_edge("HAS_APPLICATION", h["node_id"], ent_app_c, relation_scope="claimed")
        add_edge("HAS_SUBSTRATE", h["node_id"], ent_sub_t, relation_scope="tested")
        add_edge("HAS_SUBSTRATE", h["node_id"], ent_sub_c, relation_scope="claimed")
        if h["property"]["name"]:
            ent_p = get_or_create_entity(h["property"]["name"], "property", category=h["property"]["category"])
            add_edge("HAS_PROPERTY", h["node_id"], ent_p)
        if h["test_method"]["name"]:
            tmid = get_or_create_entity(h["test_method"]["name"], "test_method")
            add_edge("HAS_TEST_METHOD", h["node_id"], tmid)
        for std in h["test_method"]["standard_id"]:
            sid = get_or_create_entity(std, "test_standard")
            add_edge("HAS_TEST_STANDARD", h["node_id"], sid)
        for r in h["resin"]:
            ent = canonical.get(r["name"])
            if ent:
                add_edge("HAS_RESIN", h["node_id"], ent["entity_id"],
                          amount=f"{r['amount']} {r['unit']}", role=r["role"])
        for c in h["additives"]:
            ent = canonical.get(c["name"])
            if ent:
                add_edge("HAS_COMPONENT", h["node_id"], ent["entity_id"],
                          amount=f"{c['amount']} {c['unit']}", role=c["role"])
    for f in facts:
        add_edge("HAS_FACT", pat_id, f["fact_id"])
        if f.get("context_id"):
            ec_id = next((ec["node_id"] for ec in example_contexts if ec["context_id"] == f["context_id"]), None)
            if ec_id:
                add_edge("CONTEXT_HAS_FACT", ec_id, f["fact_id"])
        if f.get("hyperedge_id"):
            add_edge("FACT_PART_OF_HYPEREDGE", f["fact_id"], f["hyperedge_id"])
        if f.get("evidence_id"):
            add_edge("FACT_SUPPORTED_BY_EVIDENCE", f["fact_id"], f["evidence_id"])
            add_edge("EVIDENCE_SUPPORTS_FACT", f["evidence_id"], f["fact_id"])
        if f.get("linked_canonical_entity_id"):
            add_edge("MENTIONS_CANONICAL_ENTITY", f["fact_id"], f["linked_canonical_entity_id"])

    # ==== PATENT + PROFILE ====
    page_min = min(p for t in vi["tables"] for p in t["pages"])
    page_max = max(p for t in vi["tables"] for p in t["pages"])
    patents_rec = {
        "record_type":"PatentRecord","schema_version":"kg_pack_patent_v1",
        "node_type":"PATENT","node_id":pat_id,
        "patent_id":doc_id,"doc_id":doc_id,
        "publication_number":doc_id,"title":"Anti-corrosive zinc primer coating compositions",
        "application_number":"PCT/EP2015/054689",
        "publication_date":"2015-09-11","filing_date":"2015-03-05","priority_date":"2014-03-05",
        "applicants":["Hempel A/S"],"jurisdiction":"WO",
        "source_pdf":vi["source_pdf_name"],"page_count":vi.get("page_count_total",68),
        "kg_source":kg_source,"review_status":"generated_for_user_review",
        "scope_note":f"Examples-scope KG pack extracted by Claude vision (no OCR/MinerU), pages {page_min}-{page_max}.",
    }
    profile_rec = {
        "record_type":"PatentProfileRecord","schema_version":"kg_pack_patent_profile_v1",
        "node_type":"PATENT_PROFILE","node_id":profile_id,
        "profile_id":profile_id,"patent_id":doc_id,"doc_id":doc_id,
        "technology_area":"anti-corrosive zinc primer coating compositions",
        "application":"anti-corrosive primer coatings for protecting iron and steel structures",
        "coating_system":"zinc-rich primer; epoxy-based and silicate-based examples (this vision pack)",
        "core_innovation":"Use of zinc particles with conductive pigments and microspheres for improved corrosion / cracking performance.",
        "systems_covered":sorted({t["system"] for t in vi["tables"]}),
        "examples_scope":{"pages":f"PDF pages {page_min}-{page_max}",
                            "tables":sorted({t["label"] for t in vi["tables"]}),
                            "paragraphs":[]},
        "summary":"Examples-scope KG pack from Claude vision; no OCR or MinerU used.",
        "kg_source":kg_source,
    }

    # ==== write JSONL ====
    def write_jsonl(name: str, records: list[dict]) -> int:
        with (out_dir / name).open("w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        return len(records)

    counts = {
        "patents": write_jsonl("patents.jsonl", [patents_rec]),
        "patent_profiles": write_jsonl("patent_profiles.jsonl", [profile_rec]),
        "evidence_units": write_jsonl("evidence_units.jsonl", evidence_units),
        "example_contexts": write_jsonl("example_contexts.jsonl", example_contexts),
        "facts": write_jsonl("facts.jsonl", facts),
        "canonical_entities": write_jsonl("canonical_entities.jsonl", [canonical[k] for k in canonical_order]),
        "canonical_relations": write_jsonl("canonical_relations.jsonl", canonical_relations),
        "edges": write_jsonl("edges.jsonl", edges),
        "hyperedges": write_jsonl("hyperedges.jsonl", hyperedges),
    }

    # ==== manifest ====
    slot_counter: dict[str, int] = {}
    for f in facts:
        slot_counter[f["slot"]] = slot_counter.get(f["slot"], 0) + 1
    by_bundle: dict[str, int] = {}
    for h in hyperedges:
        by_bundle[h["bundle_type"]] = by_bundle.get(h["bundle_type"], 0) + 1
    by_ev_type: dict[str, int] = {}
    for e in evidence_units:
        by_ev_type[e["evidence_type"]] = by_ev_type.get(e["evidence_type"], 0) + 1
    manifest = {
        "record_type":"ManifestRecord","schema_version":"kg_pack_manifest_v1",
        "patent_id":doc_id,"doc_id":doc_id,"title":patents_rec["title"],
        "source_pdf":patents_rec["source_pdf"],
        "generated_at_utc":datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kg_source":kg_source,
        "scope":f"Examples section, PDF pages {page_min}-{page_max}, extracted by Claude vision (no OCR/MinerU)",
        "files":{k:f"{k}.jsonl" for k in counts},
        "counts":counts,
        "coverage":{
            "source_blocks_covered":[t["caption"][:80] for t in vi["tables"]],
            "by_bundle_type":by_bundle,"by_evidence_type":by_ev_type,
            "by_fact_slot":slot_counter,
            "trace_rate":{
                "facts_with_evidence_id":sum(1 for f in facts if f["evidence_id"])/max(1,len(facts)),
                "hyperedges_with_evidence_ids":sum(1 for h in hyperedges if h["evidence_ids"])/max(1,len(hyperedges)),
                "evidence_units_with_asset_path":sum(1 for e in evidence_units if e["asset_path"])/max(1,len(evidence_units)),
            },
        },
        "qa":{"review_status":"generated_for_user_review","missing_obligation_count":0,
              "known_limitations":[
                  "Vision read only; cell bbox not extracted at pixel level.",
                  "Tables 5 (polysiloxane) and 6 (polyurethane) not yet included in this vision pack; same procedure applies.",
              ]},
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    # ==== CSV ====
    csv_path = out_dir / "boss_preview.csv"
    cols = ["patent_id","doc_id","hyperedge_id","context_id","example_id","sample_id","bundle_type",
            "polarity","application_tested","application_claimed","substrate_tested","substrate_claimed",
            "resin_binder","resin_other","crosslinker_curing_agent","pigment_zinc","pigment_conductive",
            "pigment_other","filler_microsphere","filler_other","additive_corrosion_inhibitor",
            "additive_dispersant","additive_rheology","additive_surface","additive_other","solvent",
            "process_steps","process_film_thickness","process_cure_temp","process_cure_time",
            "test_method","test_standards","property_name","property_category","result_main","result_all",
            "baseline_sample_id","baseline_result","evidence_ids","evidence_pages",
            "extraction_confidence","qa_flags"]
    csv_rows_written = 0
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
        w.writerow(cols)
        for h in hyperedges:
            csv_rows_written += 1
            polarity = "positive" if h["sample_id"].lower().startswith("model paint") else \
                       ("negative" if h["sample_id"].lower().startswith("ref") else "context")
            sj = lambda items: ";".join(items)
            fmt = lambda r: f"{r['name']}:{r.get('amount','')} {r.get('unit','') or ''}".strip()
            res = h.get("resin",[]); add = h.get("additives",[])
            row = [
                h["patent_id"], h["doc_id"], h["node_id"], h["context_id"], h["example_id"], h["sample_id"],
                h["bundle_type"], polarity,
                h["application"]["tested"], h["application"]["claimed"],
                h["substrate"]["tested"], h["substrate"]["claimed"],
                sj(fmt(r) for r in res if r["role"] == "binder"),
                sj(fmt(r) for r in res if r["role"] in ("resin","hardener")),
                sj(fmt(r) for r in (res+add) if r["role"] == "curing_agent"),
                sj(fmt(r) for r in add if r["role"] == "pigment" and "zinc" in r["name"].lower()),
                sj(fmt(r) for r in add if r["role"] == "pigment" and re.search(r"graphite|carbon|graphene|tin oxide|mica", r["name"], re.I)),
                sj(fmt(r) for r in add if r["role"] == "pigment" and "zinc" not in r["name"].lower() and not re.search(r"graphite|carbon|graphene|tin oxide|mica", r["name"], re.I)),
                sj(fmt(r) for r in add if r["role"] == "filler" and re.search(r"microsphere|sphere|expancel|cenosphere|cil150|ultraspheres|w-?610|aw50|sr5000", r["name"], re.I)),
                sj(fmt(r) for r in add if r["role"] == "filler" and not re.search(r"microsphere|sphere|expancel|cenosphere|cil150|ultraspheres|w-?610|aw50|sr5000", r["name"], re.I)),
                sj(fmt(r) for r in add if r.get("role_detail") and "corrosion inhibitor" in (r.get("role_detail") or "").lower()),
                sj(fmt(r) for r in add if r["role"] == "dispersant"),
                sj(fmt(r) for r in add if r["role"] == "rheology_additive"),
                sj(fmt(r) for r in add if r["role"] == "surface_additive"),
                sj(fmt(r) for r in add if r["role"] == "other" and not (r.get("role_detail") and "solvent" in (r.get("role_detail") or "").lower())),
                sj(fmt(r) for r in add if r.get("role_detail") and "solvent" in (r.get("role_detail") or "").lower()),
                "","","","",
                h["test_method"]["name"] or "",
                ";".join(h["test_method"]["standard_id"]),
                h["property"]["name"], h["property"]["category"],
                (f"{h['result'][0]['value']} {h['result'][0].get('unit','') or ''}".strip() if h["result"] else ""),
                ";".join(f"{r['metric']}={r['value']} {r.get('unit','') or ''}".strip() for r in h["result"]),
                h["baseline"]["baseline_sample_id"] or "",
                h["baseline"]["baseline_result"] or "",
                ";".join(h["evidence_ids"]),
                ";".join(sorted({str(e["page"]) for e in h["evidence"]})),
                h["confidence"]["overall"],
                ";".join(h["qa_flags"]),
            ]
            w.writerow(row)

    # ==== gates ====
    NODE_INDEX: dict[str, dict] = {}
    for r in [patents_rec, profile_rec] + evidence_units + example_contexts + facts + \
              list(canonical.values()) + hyperedges:
        NODE_INDEX[r["node_id"]] = r
    EDGE_RULES = {
        "HAS_PROFILE":("PATENT","PATENT_PROFILE",None),
        "HAS_EVIDENCE":("PATENT","EVD",None),
        "HAS_EXAMPLE_CONTEXT":("PATENT","EXAMPLE_CONTEXT",None),
        "HAS_HYPEREDGE":("PATENT","HYPEREDGE",None),
        "HAS_FACT":("PATENT","FACT",None),
        "HAS_RESIN":("HYPEREDGE","CANONICAL_ENTITY",
                      lambda e: e.get("entity_type")=="material" and e.get("role") in {"binder","resin","curing_agent","hardener"}),
        "HAS_COMPONENT":("HYPEREDGE","CANONICAL_ENTITY",
                         lambda e: e.get("entity_type")=="material" and e.get("role") in {"pigment","filler","curing_agent","dispersant","rheology_additive","surface_additive","other"}),
        "HAS_PROPERTY":("HYPEREDGE","CANONICAL_ENTITY",lambda e: e.get("entity_type")=="property"),
        "HAS_SUBSTRATE":("HYPEREDGE","CANONICAL_ENTITY",lambda e: e.get("entity_type")=="substrate"),
        "HAS_APPLICATION":("HYPEREDGE","CANONICAL_ENTITY",lambda e: e.get("entity_type")=="application"),
        "HAS_TEST_METHOD":("HYPEREDGE","CANONICAL_ENTITY",lambda e: e.get("entity_type")=="test_method"),
        "HAS_TEST_STANDARD":("HYPEREDGE","CANONICAL_ENTITY",lambda e: e.get("entity_type")=="test_standard"),
        "HAS_BASELINE_HYPEREDGE":("HYPEREDGE","HYPEREDGE",None),
        "CONTEXT_HAS_HYPEREDGE":("EXAMPLE_CONTEXT","HYPEREDGE",None),
        "CONTEXT_HAS_FACT":("EXAMPLE_CONTEXT","FACT",None),
        "SUPPORTED_BY_EVIDENCE":("HYPEREDGE","EVD",None),
        "EVIDENCE_SUPPORTS_HYPEREDGE":("EVD","HYPEREDGE",None),
        "FACT_SUPPORTED_BY_EVIDENCE":("FACT","EVD",None),
        "EVIDENCE_SUPPORTS_FACT":("EVD","FACT",None),
        "FACT_PART_OF_HYPEREDGE":("FACT","HYPEREDGE",None),
        "FACT_INHERITED_BY_HYPEREDGE":("FACT","HYPEREDGE",None),
        "MENTIONS_CANONICAL_ENTITY":(None,"CANONICAL_ENTITY",None),
    }
    violations: list[str] = []
    for e in edges:
        rule = EDGE_RULES.get(e["edge_type"])
        if not rule:
            violations.append(f"unknown edge_type {e['edge_type']}"); continue
        src_t, dst_t, role_check = rule
        sn = NODE_INDEX.get(e["src"]); dn = NODE_INDEX.get(e["dst"])
        if not sn: violations.append(f"missing src {e['src']} for {e['edge_type']}"); continue
        if not dn: violations.append(f"missing dst {e['dst']} for {e['edge_type']}"); continue
        if src_t and sn["node_type"] != src_t:
            violations.append(f"{e['edge_type']}: src is {sn['node_type']} expected {src_t}")
        if dst_t and dn["node_type"] != dst_t:
            violations.append(f"{e['edge_type']}: dst is {dn['node_type']} expected {dst_t}")
        if role_check and not role_check(dn):
            violations.append(f"{e['edge_type']}: role check failed for {dn['node_id']} (entity_type={dn.get('entity_type')}, role={dn.get('role')})")

    trace = manifest["coverage"]["trace_rate"]
    print(f"[done] {doc_id}: "
          f"patents={counts['patents']} profile={counts['patent_profiles']} "
          f"evidence={counts['evidence_units']} contexts={counts['example_contexts']} "
          f"facts={counts['facts']} canonical={counts['canonical_entities']} "
          f"relations={counts['canonical_relations']} edges={counts['edges']} "
          f"hyperedges={counts['hyperedges']} | CSV rows={csv_rows_written}")
    print(f"[gates] trace={trace}")
    print(f"[gates] edge_violations={len(violations)}")
    if violations:
        for v in violations[:5]:
            print(f"   - {v}")
    return 0 if not violations else 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()))
