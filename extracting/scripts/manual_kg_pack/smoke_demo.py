"""
Minimal end-to-end demo: extract 1 sample (Model paint 1) from Table 1 + the
test panel preparation method paragraph, produce the 10 JSONL files and a CSV.
Run the §6 gates and report.

This is NOT the production extractor. It is a deterministic skeleton that shows
the schema and gate logic work. The real production loop is what Claude
performs across 347 conversations driven by EXTRACT_PROMPT.md.

Usage:
    python smoke_demo.py <prep_dir> <out_dir>
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# -------- canonical role classifier ---------------------------------------

# Lower-case substring → (slot, role, role_detail)
ROLE_KEYWORDS: list[tuple[str, str, str, str | None]] = [
    # binders / resins
    ("epoxy resin (bisphenol", "resin", "binder", None),
    ("epoxy resin", "resin", "binder", None),
    ("aliphatic epoxy resin", "resin", "binder", None),
    ("reactive epoxy diluent", "resin", "resin", "reactive epoxy diluent"),
    ("cardolite", "resin", "resin", "reactive epoxy diluent"),
    ("silicate binder", "resin", "binder", "silicate binder"),
    ("ethylsilicate", "resin", "binder", "silicate binder"),
    ("ethyl silicate", "resin", "binder", "silicate binder"),
    ("hydroxyfunctional", "resin", "binder", "hydroxyfunctional acrylic"),
    ("acrylic resin", "resin", "binder", None),
    ("polysiloxane", "resin", "binder", "polysiloxane"),
    ("siloxane resin", "resin", "binder", None),
    ("amino functional siloxane", "resin", "binder", "amino siloxane"),
    ("urea/aldehyde", "resin", "resin", "urea/aldehyde resin"),
    ("polyaminoamide", "resin", "curing_agent", "polyaminoamide curing agent"),
    ("polyisocyanate", "resin", "curing_agent", "polyisocyanate crosslinker"),
    # pigments
    ("zinc particles", "component", "pigment", "zinc particles / metallic conductive pigment"),
    ("zinc dust", "component", "pigment", "metallic zinc pigment"),
    ("graphite", "component", "pigment", "conductive pigment"),
    ("graphene", "component", "pigment", "conductive pigment / graphene"),
    ("carbon nano-tube", "component", "pigment", "conductive pigment / carbon nanotube"),
    ("carbon nanotube", "component", "pigment", "conductive pigment / carbon nanotube"),
    ("carbon black", "component", "pigment", "conductive pigment / carbon black"),
    ("carbon fibre", "component", "pigment", "conductive pigment / carbon fibre"),
    ("antimony-doped tin oxide", "component", "pigment", "conductive pigment"),
    ("mica coated", "component", "pigment", "conductive pigment"),
    ("zinc phosphate", "component", "other", "corrosion inhibitor"),
    ("zinc chloride", "component", "other", "accelerator / catalyst"),
    # fillers
    ("microspheres", "component", "filler", "microsphere filler"),
    ("microsphere", "component", "filler", "microsphere filler"),
    ("nepheline syenite", "component", "filler", "filler"),
    ("silica aerogel", "component", "filler", "silica aerogel"),
    ("kaolin", "component", "filler", "extender pigment"),
    ("ceramic", "component", "filler", "ceramic microsphere"),
    ("expancel", "component", "filler", "polymeric microsphere filler"),
    ("polyethylene spherical", "component", "filler", "polymeric microsphere filler"),
    ("acrylic ester spherical", "component", "filler", "polymeric microsphere filler"),
    ("polymethylmethacrylate spherical", "component", "filler", "polymeric microsphere filler"),
    ("spherical silica", "component", "filler", "microsphere filler"),
    # additives
    ("organo clay", "component", "rheology_additive", None),
    ("amide wax", "component", "rheology_additive", None),
    ("polyamide wax", "component", "rheology_additive", None),
    ("wetting and suspending", "component", "dispersant", None),
    ("wetting and dispersing", "component", "dispersant", None),
    ("wetting dispersing", "component", "dispersant", None),
    ("dispersing agent", "component", "dispersant", None),
    ("soya lecithin", "component", "dispersant", None),
    ("fluoro silicone, defoamer", "component", "surface_additive", "defoamer"),
    ("defoamer", "component", "surface_additive", "defoamer"),
    ("slip and flow", "component", "surface_additive", "slip and flow additive"),
    ("slip/flow", "component", "surface_additive", "slip and flow additive"),
    ("light stabiliser", "component", "other", "light stabiliser"),
    ("hindered amine", "component", "other", "light stabiliser"),
    ("dibutyltin", "component", "other", "catalyst"),
    ("epoxy accelerator", "component", "other", "epoxy accelerator"),
    ("ancamine", "component", "other", "epoxy accelerator"),
    ("additives", "component", "other", "combined additives"),
    ("additive", "component", "other", None),
]


def classify_ingredient(name: str) -> tuple[str, str, str | None]:
    name_l = (name or "").lower()
    for kw, slot, role, role_detail in ROLE_KEYWORDS:
        if kw in name_l:
            return slot, role, role_detail
    return "component", "other", None


# -------- table parsing helpers -------------------------------------------

def find_table_on_page(kg_input: dict, page_num: int, idx: int = 0) -> dict | None:
    for p in kg_input["pages"]:
        if p["page"] == page_num:
            if idx < len(p["tables"]):
                return p["tables"][idx]
    return None


def find_paragraph_containing(kg_input: dict, page_num: int, substr: str) -> dict | None:
    for p in kg_input["pages"]:
        if p["page"] != page_num:
            continue
        for para in p["paragraphs"]:
            if substr.lower() in para["text"].lower():
                return para
    return None


def md5_12(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:12]


def slug(s: str) -> str:
    return re.sub(r"\W+", "_", s.lower()).strip("_")


# -------- main ------------------------------------------------------------

def main(prep_dir: Path, out_dir: Path) -> int:
    kg_input = json.loads((prep_dir / "kg_input.json").read_text(encoding="utf-8"))
    doc_id = kg_input["doc_id"]
    kg_source = f"{doc_id}_manual_strict_example_scope_kg_pack_v1"
    out_dir.mkdir(parents=True, exist_ok=True)

    # State accumulators
    canonical: dict[str, dict] = {}    # canonical_name → record
    canonical_order: list[str] = []
    seq_ent = 0

    def get_or_create_entity(canonical_name: str, entity_type: str,
                             category: str | None = None, role: str | None = None,
                             role_detail: str | None = None, alias: str | None = None) -> str:
        nonlocal seq_ent
        key = canonical_name
        if key in canonical:
            rec = canonical[key]
            if alias and alias not in rec["aliases"]:
                rec["aliases"].append(alias)
            return rec["entity_id"]
        seq_ent += 1
        ent_id = f"ENT_{entity_type.upper()}_{seq_ent:05d}"
        rec = {
            "record_type": "CanonicalEntityRecord",
            "schema_version": "kg_pack_canonical_entity_v1",
            "node_type": "CANONICAL_ENTITY",
            "node_id": ent_id,
            "entity_id": ent_id,
            "entity_type": entity_type,
            "canonical_name": canonical_name,
            "aliases": [alias] if alias else [canonical_name],
            "patent_id": doc_id,
            "doc_id": doc_id,
            "kg_source": kg_source,
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
    seq_fact = 0

    def new_fact_id() -> str:
        nonlocal seq_fact
        seq_fact += 1
        return f"FACT_{doc_id}_{seq_fact:06d}"

    def add_edge(edge_type: str, src: str, dst: str, **extras) -> dict:
        eid = "EDGE_" + md5_12(f"{edge_type}|{src}|{dst}")
        rec = {
            "record_type": "EdgeRecord",
            "schema_version": "kg_pack_edge_v1",
            "edge_id": eid,
            "edge_type": edge_type,
            "src": src,
            "dst": dst,
            "patent_id": doc_id,
            "doc_id": doc_id,
            "kg_source": kg_source,
        }
        rec.update(extras)
        edges.append(rec)
        return rec

    # 1. PATENT + PROFILE
    pat_id = f"PAT_{doc_id}"
    profile_id = f"PROFILE_{doc_id}"

    # 2. Pick Model paint 1 from Table 1 (page 46, first table)
    tbl = find_table_on_page(kg_input, 46, 0)
    assert tbl is not None, "Table 1 not found on page 46"
    rows = tbl["rows"]
    # header: row 0 = sample labels (Model paint 1..5 at col 3..7)
    # row 1 = ref labels (Reference paint 1..2 at col 1..2)
    # row 2 = unit (%SV) per column
    # data rows: 3..end
    samples_by_col = {
        1: "Reference paint 1",
        2: "Reference paint 2",
        3: "Model paint 1",
        4: "Model paint 2",
        5: "Model paint 3",
        6: "Model paint 4",
        7: "Model paint 5",
    }
    SAMPLE_COL = 3        # Model paint 1
    SAMPLE_ID = samples_by_col[SAMPLE_COL]
    EX_ID = "Example 1 / Table 1"

    # Build context for MP1 formulation
    ctx_slug = slug(f"{doc_id}__example_1_table_1__{SAMPLE_ID}__formulation")
    ctx_id = ctx_slug
    excxt_id = f"EXCTX_{ctx_slug}"
    example_contexts.append({
        "record_type": "ExampleContextRecord",
        "schema_version": "kg_pack_example_context_v1",
        "node_type": "EXAMPLE_CONTEXT",
        "node_id": excxt_id,
        "context_id": ctx_id,
        "patent_id": doc_id,
        "doc_id": doc_id,
        "example_id": EX_ID,
        "sample_id": SAMPLE_ID,
        "bundle_type": "formulation_context",
        "table_role": "Table 1",
        "evidence_ids": [],         # filled later
        "linked_hyperedge_ids": [],
        "kg_source": kg_source,
    })

    # canonical entities up front for slot mapping
    ent_app_tested = get_or_create_entity(
        "epoxy-based anti-corrosive zinc primer coating", "application")
    ent_app_claimed = get_or_create_entity(
        "anti-corrosive zinc primer coating compositions for protecting iron and steel structures", "application")
    ent_sub_tested = get_or_create_entity(
        "cold rolled mild steel panel (10 x 15 cm x 1.6 mm), abrasive blasted to Sa 2½ according to ISO 8501-1, surface profile equivalent to BN 9 (Rugotest No. 3)", "substrate")
    ent_sub_claimed = get_or_create_entity("iron and steel structures / metal structures", "substrate")
    ent_prop_formulation = get_or_create_entity(
        "basic epoxy-based paint formulation", "property", category="formulation")

    # -- evidence_unit for Model paint 1 column of Table 1 --
    evd_id = f"EVD_{doc_id}_p46_table1_{slug(SAMPLE_ID)}"
    direct_units: list[dict] = [
        {"slot": "sample_context", "source_field": "sample_id",
         "name": SAMPLE_ID, "raw_text": SAMPLE_ID, "unit_id": "u001"},
        {"slot": "example_context", "source_field": "example_id",
         "name": EX_ID, "raw_text": EX_ID, "unit_id": "u002"},
        {"slot": "property", "source_field": "property",
         "name": "basic epoxy-based paint formulation",
         "category": "formulation", "measured_or_claimed": "measured", "unit_id": "u003"},
    ]
    cell_excerpts: list[str] = []
    next_u = 4

    # Walk data rows (start at row 3 which is the first ingredient)
    for r in rows[3:]:
        if len(r["cells"]) <= SAMPLE_COL:
            continue
        ingr_name = (r["cells"][0]["text"] or "").strip()
        val_cell = r["cells"][SAMPLE_COL]
        val_text = (val_cell["text"] or "").strip()
        if not ingr_name or not val_text:
            continue
        # Classify ingredient
        slot, role, role_detail = classify_ingredient(ingr_name)
        # Try to detect metrics rows (Total, PVC, SVR, Ratio, Mixing ratio)
        if any(m in ingr_name.lower() for m in ("total", "pvc", "svr", "ratio")):
            metric_name = ingr_name
            unit = "%SV" if "%SV" in val_text else ("%" if "PVC" in ingr_name or "SVR" in ingr_name else None)
            # Strip unit token from value
            v = re.sub(r"\s*%SV\s*$", "", val_text).strip()
            ent_id = get_or_create_entity(metric_name, "formulation_metric")
            unit_rec = {
                "slot": "formulation_metric", "source_field": "result",
                "metric": metric_name, "value": v, "unit": unit,
                "raw_text": val_text, "scale": "formulation metric",
                "unit_id": f"u{next_u:03d}",
            }
            direct_units.append(unit_rec)
            cell_excerpts.append(f"{metric_name}: {val_text}")
            next_u += 1
            # FactRecord
            f = {
                "record_type": "FactRecord", "schema_version": "kg_pack_fact_v1",
                "node_type": "FACT", "node_id": new_fact_id(),
                "fact_id": "", "patent_id": doc_id, "doc_id": doc_id,
                "evidence_id": evd_id, "context_id": ctx_id, "hyperedge_id": "",
                "slot": "formulation_metric", "source_field": "result",
                "name": None, "metric": metric_name, "role": None, "role_detail": None,
                "value": v, "unit": unit, "raw_text": val_text,
                "amount_basis": None, "scale": "formulation metric",
                "step_type": None,
                "linked_canonical_entity_id": ent_id, "kg_source": kg_source,
            }
            f["fact_id"] = f["node_id"]
            facts.append(f)
            continue

        ent_id = get_or_create_entity(ingr_name, "material",
                                       role=role if slot == "resin" else role,
                                       role_detail=role_detail,
                                       alias=ingr_name)
        unit_rec = {
            "slot": slot, "source_field": "resin" if slot == "resin" else "additives",
            "name": ingr_name, "role": role,
            "value": val_text, "unit": "%SV",
            "raw_text": f"{val_text} %SV",
            "amount_basis": "formulation_amount",
            "evidence_ref": f"Table 1 / {SAMPLE_ID} / {ingr_name}",
            "unit_id": f"u{next_u:03d}",
        }
        if role_detail: unit_rec["role_detail"] = role_detail
        direct_units.append(unit_rec)
        cell_excerpts.append(f"{ingr_name}: {val_text} %SV")
        next_u += 1
        # FactRecord
        f = {
            "record_type": "FactRecord", "schema_version": "kg_pack_fact_v1",
            "node_type": "FACT", "node_id": new_fact_id(),
            "fact_id": "", "patent_id": doc_id, "doc_id": doc_id,
            "evidence_id": evd_id, "context_id": ctx_id, "hyperedge_id": "",
            "slot": slot, "source_field": "resin" if slot == "resin" else "additives",
            "name": ingr_name, "metric": None,
            "role": role, "role_detail": role_detail,
            "value": val_text, "unit": "%SV", "raw_text": f"{val_text} %SV",
            "amount_basis": "formulation_amount", "scale": None,
            "step_type": None,
            "linked_canonical_entity_id": ent_id, "kg_source": kg_source,
        }
        f["fact_id"] = f["node_id"]
        facts.append(f)

    # Get row_bbox for evidence (use Model paint 1's column? we only have row-level — use table bbox)
    text_excerpt = f"Table 1 row '{SAMPLE_ID}'; " + " | ".join(cell_excerpts)
    evidence_units.append({
        "record_type": "EvidenceRecord",
        "schema_version": "kg_pack_l1_evidence_unit_v1",
        "node_type": "EVD", "node_id": evd_id, "evidence_id": evd_id,
        "patent_id": doc_id, "doc_id": doc_id,
        "layer": "L1_evidence_unit",
        "source": "mineru_layout_plus_llm_decomposition",
        "source_pdf": kg_input["source_pdf_name"],
        "page": 46, "printed_page": 45,
        "section": "Example 1 / Table 1 - Basic formulation of epoxy-based paints",
        "evidence_type": "formulation_table",
        "source_block_type": "formulation_table_row",
        "table": "Table 1", "row": SAMPLE_ID, "column": "%SV",
        "caption": "Table 1 - Basic formulation of epoxy-based paints",
        "bbox": None,
        "asset_path": "pages/page_046.png",
        "asset_bbox_image": tbl["bbox_image"],
        "asset_bbox_pdf": tbl["bbox_pdf"],
        "text_excerpt": text_excerpt,
        "quote_style": "structured_row_transcription",
        "direct_extracted_units": direct_units,
        "context_bindings": {
            "linked_l2_context_ids": [ctx_id],
            "primary_l2_context_ids": [ctx_id],
            "inherited_by_l2_context_ids": [],
            "linked_bundle_types": ["formulation_context"],
            "linked_sample_ids": [SAMPLE_ID],
        },
        "coverage_scope": "examples_section_tables_and_related_method_paragraphs",
        "confidence": 0.94,
        "qa_flags": ["mineru_layout_based", "row_level_bbox_only", "examples_section_only"],
        "unresolved_fields": ["cell_bbox"],
        "kg_source": kg_source,
        "trace_role": "L1_retrieval_context",
        "source_scope": "examples_pages_43_62",
    })
    example_contexts[-1]["evidence_ids"].append(evd_id)

    # Hyperedge for MP1 formulation
    hedge_id = f"HEDGE_{doc_id}_0001"
    example_contexts[-1]["linked_hyperedge_ids"].append(hedge_id)
    resin_list = [
        {"name": f["name"], "role": f["role"], "amount": f["value"], "unit": f["unit"]}
        for f in facts if f["evidence_id"] == evd_id and f["slot"] == "resin"
    ]
    addl_list = [
        {"name": f["name"], "role": f["role"], "amount": f["value"], "unit": f["unit"]}
        for f in facts if f["evidence_id"] == evd_id and f["slot"] == "component"
    ]
    hyperedges.append({
        "record_type": "HyperedgeRecord",
        "schema_version": "kg_pack_l2_hyperedge_v1",
        "node_type": "HYPEREDGE", "node_id": hedge_id, "hyperedge_id": hedge_id,
        "patent_id": doc_id, "doc_id": doc_id,
        "context_id": ctx_id, "bundle_type": "formulation_context",
        "sample_id": SAMPLE_ID, "example_id": EX_ID,
        "application": {
            "tested": "epoxy-based anti-corrosive zinc primer coating",
            "claimed": "anti-corrosive zinc primer coating compositions for protecting iron and steel structures"},
        "substrate": {
            "tested": "cold rolled mild steel panel (10 x 15 cm x 1.6 mm), abrasive blasted to Sa 2½ according to ISO 8501-1, surface profile equivalent to BN 9 (Rugotest No. 3)",
            "claimed": "iron and steel structures / metal structures"},
        "resin": resin_list,
        "additives": addl_list,
        "process": [],
        "property": {"name": "basic epoxy-based paint formulation",
                     "category": "formulation", "measured_or_claimed": "measured"},
        "test_method": {"name": None, "standard_id": [],
                        "condition": {"exposure": None, "immersion": None, "rating_scale": None, "score_max": None}},
        "result": [],
        "baseline": {"baseline_sample_id": None, "baseline_context_id": None,
                     "baseline_result": None, "comparison_text": None},
        "evidence": [{"evidence_id": evd_id, "page": 46,
                      "section": "Example 1 / Table 1 - Basic formulation of epoxy-based paints",
                      "table": "Table 1", "row": SAMPLE_ID, "column": "%SV",
                      "evidence_type": "formulation_table", "printed_page": 45}],
        "confidence": {"overall": 0.94, "sample_binding": 0.94,
                       "formulation_binding": 0.94, "result_binding": 0.0},
        "qa_flags": ["mineru_layout_based", "role_normalized", "examples_section_only"],
        "unresolved_fields": ["cell_bbox"],
        "kg_source": kg_source,
        "source_scope": "examples_pages_43_62",
        "evidence_ids": [evd_id],
        "answerability": "context_bundle",
        "projection_note": "Use this L2 record as fact source; CSV should be a view only.",
    })

    # backfill fact.hyperedge_id
    for f in facts:
        if f["evidence_id"] == evd_id:
            f["hyperedge_id"] = hedge_id

    # ----- edges -----
    add_edge("HAS_PROFILE", pat_id, profile_id)
    add_edge("HAS_EVIDENCE", pat_id, evd_id)
    add_edge("HAS_EXAMPLE_CONTEXT", pat_id, excxt_id)
    add_edge("HAS_HYPEREDGE", pat_id, hedge_id)
    add_edge("CONTEXT_HAS_HYPEREDGE", excxt_id, hedge_id, bundle_type="formulation_context")
    add_edge("SUPPORTED_BY_EVIDENCE", hedge_id, evd_id)
    add_edge("EVIDENCE_SUPPORTS_HYPEREDGE", evd_id, hedge_id)
    add_edge("HAS_APPLICATION", hedge_id, ent_app_tested, relation_scope="tested")
    add_edge("HAS_APPLICATION", hedge_id, ent_app_claimed, relation_scope="claimed")
    add_edge("HAS_SUBSTRATE", hedge_id, ent_sub_tested, relation_scope="tested")
    add_edge("HAS_SUBSTRATE", hedge_id, ent_sub_claimed, relation_scope="claimed")
    add_edge("HAS_PROPERTY", hedge_id, ent_prop_formulation)
    for r in resin_list:
        # find ent_id
        ent = canonical[r["name"]]
        add_edge("HAS_RESIN", hedge_id, ent["entity_id"], amount=f"{r['amount']} {r['unit']}", role=r["role"])
    for c in addl_list:
        ent = canonical[c["name"]]
        add_edge("HAS_COMPONENT", hedge_id, ent["entity_id"], amount=f"{c['amount']} {c['unit']}", role=c["role"])
    # facts edges
    for f in facts:
        add_edge("HAS_FACT", pat_id, f["fact_id"])
        add_edge("CONTEXT_HAS_FACT", excxt_id, f["fact_id"])
        add_edge("FACT_PART_OF_HYPEREDGE", f["fact_id"], hedge_id)
        add_edge("FACT_SUPPORTED_BY_EVIDENCE", f["fact_id"], f["evidence_id"])
        add_edge("EVIDENCE_SUPPORTS_FACT", f["evidence_id"], f["fact_id"])
        if f.get("linked_canonical_entity_id"):
            add_edge("MENTIONS_CANONICAL_ENTITY", f["fact_id"], f["linked_canonical_entity_id"])

    # ----- PATENT + PROFILE records -----
    patents_rec = {
        "record_type": "PatentRecord", "schema_version": "kg_pack_patent_v1",
        "node_type": "PATENT", "node_id": pat_id,
        "patent_id": doc_id, "doc_id": doc_id,
        "publication_number": doc_id, "title": "Anti-corrosive zinc primer coating compositions",
        "application_number": "PCT/EP2015/054689",
        "publication_date": "2015-09-11", "filing_date": "2015-03-05", "priority_date": "2014-03-05",
        "applicants": ["Hempel A/S"], "jurisdiction": "WO",
        "source_pdf": kg_input["source_pdf_name"], "page_count": kg_input["page_count"],
        "kg_source": kg_source, "review_status": "generated_for_user_review",
        "scope_note": "Smoke demo: Examples-scope KG pack scaffolding for Model paint 1 (Table 1) only.",
    }
    profile_rec = {
        "record_type": "PatentProfileRecord", "schema_version": "kg_pack_patent_profile_v1",
        "node_type": "PATENT_PROFILE", "node_id": profile_id,
        "profile_id": profile_id, "patent_id": doc_id, "doc_id": doc_id,
        "technology_area": "anti-corrosive zinc primer coating",
        "application": "anti-corrosive primer coatings for iron and steel structures",
        "coating_system": "zinc-rich primer; epoxy-based example shown",
        "core_innovation": "Smoke demo subset of full pack — not the full innovation summary.",
        "systems_covered": ["epoxy-based paints"],
        "examples_scope": {"pages": "PDF page 46", "tables": ["Table 1"], "paragraphs": []},
        "summary": "Demo subset showing schema for one sample column of Table 1.",
        "kg_source": kg_source,
    }

    # ----- write JSONL -----
    def write_jsonl(name: str, records: list[dict]) -> int:
        with (out_dir / name).open("w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        return len(records)

    counts = {}
    counts["patents"] = write_jsonl("patents.jsonl", [patents_rec])
    counts["patent_profiles"] = write_jsonl("patent_profiles.jsonl", [profile_rec])
    counts["evidence_units"] = write_jsonl("evidence_units.jsonl", evidence_units)
    counts["example_contexts"] = write_jsonl("example_contexts.jsonl", example_contexts)
    counts["facts"] = write_jsonl("facts.jsonl", facts)
    counts["canonical_entities"] = write_jsonl(
        "canonical_entities.jsonl", [canonical[k] for k in canonical_order])
    counts["canonical_relations"] = write_jsonl("canonical_relations.jsonl", [])
    counts["edges"] = write_jsonl("edges.jsonl", edges)
    counts["hyperedges"] = write_jsonl("hyperedges.jsonl", hyperedges)

    # manifest
    manifest = {
        "record_type": "ManifestRecord", "schema_version": "kg_pack_manifest_v1",
        "patent_id": doc_id, "doc_id": doc_id, "title": patents_rec["title"],
        "source_pdf": patents_rec["source_pdf"],
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kg_source": kg_source,
        "scope": "Smoke demo: Examples scope PDF page 46 only (Table 1 / Model paint 1)",
        "files": {
            "patents": "patents.jsonl", "patent_profiles": "patent_profiles.jsonl",
            "evidence_units": "evidence_units.jsonl", "example_contexts": "example_contexts.jsonl",
            "facts": "facts.jsonl", "canonical_entities": "canonical_entities.jsonl",
            "canonical_relations": "canonical_relations.jsonl", "edges": "edges.jsonl",
            "hyperedges": "hyperedges.jsonl", "manifest": "manifest.json",
        },
        "counts": counts,
        "coverage": {
            "source_blocks_covered": ["Table 1 / Model paint 1"],
            "by_bundle_type": {"formulation_context": len(hyperedges)},
            "by_evidence_type": {"formulation_table": len(evidence_units)},
            "by_fact_slot": {},
            "trace_rate": {"facts_with_evidence_id": 1.0,
                           "hyperedges_with_evidence_ids": 1.0,
                           "evidence_units_with_asset_path": 1.0},
        },
        "qa": {"review_status": "generated_for_user_review",
               "missing_obligation_count": 0,
               "known_limitations": ["Demo subset — only 1 sample column extracted."]},
    }
    slot_counter = {}
    for f in facts:
        slot_counter[f["slot"]] = slot_counter.get(f["slot"], 0) + 1
    manifest["coverage"]["by_fact_slot"] = slot_counter
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                                            encoding="utf-8")

    # CSV projection
    csv_path = out_dir / "boss_preview.csv"
    cols = [
        "patent_id","doc_id","hyperedge_id","context_id","example_id","sample_id","bundle_type",
        "polarity","application_tested","application_claimed","substrate_tested","substrate_claimed",
        "resin_binder","resin_other","crosslinker_curing_agent","pigment_zinc","pigment_conductive",
        "pigment_other","filler_microsphere","filler_other","additive_corrosion_inhibitor",
        "additive_dispersant","additive_rheology","additive_surface","additive_other","solvent",
        "process_steps","process_film_thickness","process_cure_temp","process_cure_time",
        "test_method","test_standards","property_name","property_category","result_main","result_all",
        "baseline_sample_id","baseline_result","evidence_ids","evidence_pages",
        "extraction_confidence","qa_flags",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
        w.writerow(cols)
        for h in hyperedges:
            polarity = "positive" if h["sample_id"].lower().startswith("model paint") else \
                       ("negative" if h["sample_id"].lower().startswith("reference paint") else "context")
            sj = lambda items: ";".join(items)
            res = h.get("resin", [])
            add = h.get("additives", [])
            fmt = lambda r: f"{r['name']}:{r['amount']} {r['unit']}"
            row = [
                h["patent_id"], h["doc_id"], h["node_id"], h["context_id"], h["example_id"], h["sample_id"],
                h["bundle_type"], polarity,
                h["application"]["tested"], h["application"]["claimed"],
                h["substrate"]["tested"], h["substrate"]["claimed"],
                sj(fmt(r) for r in res if r["role"] == "binder"),
                sj(fmt(r) for r in res if r["role"] in ("resin", "hardener")),
                sj(fmt(r) for r in (res + add) if r["role"] == "curing_agent"),
                sj(fmt(r) for r in add if r["role"] == "pigment" and "zinc" in r["name"].lower()),
                sj(fmt(r) for r in add if r["role"] == "pigment" and re.search(r"graphite|carbon|graphene|tin oxide|mica", r["name"], re.I)),
                sj(fmt(r) for r in add if r["role"] == "pigment" and "zinc" not in r["name"].lower() and not re.search(r"graphite|carbon|graphene|tin oxide|mica", r["name"], re.I)),
                sj(fmt(r) for r in add if r["role"] == "filler" and re.search(r"microsphere|sphere|expancel|cenosphere", r["name"], re.I)),
                sj(fmt(r) for r in add if r["role"] == "filler" and not re.search(r"microsphere|sphere|expancel|cenosphere", r["name"], re.I)),
                "", # corrosion_inhibitor placeholder
                sj(fmt(r) for r in add if r["role"] == "dispersant"),
                sj(fmt(r) for r in add if r["role"] == "rheology_additive"),
                sj(fmt(r) for r in add if r["role"] == "surface_additive"),
                sj(fmt(r) for r in add if r["role"] == "other"),
                "", # solvent
                ";".join(p["step_type"] for p in h.get("process", [])),
                "", "", "",  # film, cure_temp, cure_time
                h["test_method"]["name"] or "",
                ";".join(h["test_method"]["standard_id"]),
                h["property"]["name"], h["property"]["category"],
                "", "",  # result_main, result_all (no result in formulation hyperedge)
                h["baseline"]["baseline_sample_id"] or "",
                h["baseline"]["baseline_result"] or "",
                ";".join(h["evidence_ids"]),
                ";".join(str(e["page"]) for e in h["evidence"]),
                h["confidence"]["overall"],
                ";".join(h["qa_flags"]),
            ]
            w.writerow(row)

    # ---------------- §6 Gates ----------------
    EDGE_RULES = {
        "HAS_PROFILE": ("PATENT", "PATENT_PROFILE", None),
        "HAS_EVIDENCE": ("PATENT", "EVD", None),
        "HAS_EXAMPLE_CONTEXT": ("PATENT", "EXAMPLE_CONTEXT", None),
        "HAS_HYPEREDGE": ("PATENT", "HYPEREDGE", None),
        "HAS_FACT": ("PATENT", "FACT", None),
        "HAS_RESIN": ("HYPEREDGE", "CANONICAL_ENTITY",
                       lambda e: e.get("entity_type") == "material" and e.get("role") in {"binder","resin","curing_agent","hardener"}),
        "HAS_COMPONENT": ("HYPEREDGE", "CANONICAL_ENTITY",
                          lambda e: e.get("entity_type") == "material" and e.get("role") in {"pigment","filler","curing_agent","dispersant","rheology_additive","surface_additive","other"}),
        "HAS_PROPERTY": ("HYPEREDGE", "CANONICAL_ENTITY",
                          lambda e: e.get("entity_type") == "property"),
        "HAS_SUBSTRATE": ("HYPEREDGE", "CANONICAL_ENTITY",
                          lambda e: e.get("entity_type") == "substrate"),
        "HAS_APPLICATION": ("HYPEREDGE", "CANONICAL_ENTITY",
                            lambda e: e.get("entity_type") == "application"),
        "HAS_TEST_METHOD": ("HYPEREDGE", "CANONICAL_ENTITY",
                            lambda e: e.get("entity_type") == "test_method"),
        "HAS_TEST_STANDARD": ("HYPEREDGE", "CANONICAL_ENTITY",
                              lambda e: e.get("entity_type") == "test_standard"),
        "HAS_BASELINE_HYPEREDGE": ("HYPEREDGE", "HYPEREDGE", None),
        "CONTEXT_HAS_HYPEREDGE": ("EXAMPLE_CONTEXT", "HYPEREDGE", None),
        "CONTEXT_HAS_FACT": ("EXAMPLE_CONTEXT", "FACT", None),
        "SUPPORTED_BY_EVIDENCE": ("HYPEREDGE", "EVD", None),
        "EVIDENCE_SUPPORTS_HYPEREDGE": ("EVD", "HYPEREDGE", None),
        "FACT_SUPPORTED_BY_EVIDENCE": ("FACT", "EVD", None),
        "EVIDENCE_SUPPORTS_FACT": ("EVD", "FACT", None),
        "FACT_PART_OF_HYPEREDGE": ("FACT", "HYPEREDGE", None),
        "FACT_INHERITED_BY_HYPEREDGE": ("FACT", "HYPEREDGE", None),
        "MENTIONS_CANONICAL_ENTITY": (None, "CANONICAL_ENTITY", None),
    }
    NODE_INDEX: dict[str, dict] = {}
    for r in [patents_rec, profile_rec] + evidence_units + example_contexts + facts + \
              list(canonical.values()) + hyperedges:
        NODE_INDEX[r["node_id"]] = r

    violations: list[str] = []
    for e in edges:
        rule = EDGE_RULES.get(e["edge_type"])
        if not rule:
            violations.append(f"unknown edge_type {e['edge_type']}")
            continue
        src_type_req, dst_type_req, dst_role_check = rule
        src_node = NODE_INDEX.get(e["src"])
        dst_node = NODE_INDEX.get(e["dst"])
        if not src_node:
            violations.append(f"missing src {e['src']} for {e['edge_type']}")
            continue
        if not dst_node:
            violations.append(f"missing dst {e['dst']} for {e['edge_type']}")
            continue
        if src_type_req and src_node["node_type"] != src_type_req:
            violations.append(f"{e['edge_type']}: src is {src_node['node_type']} expected {src_type_req}")
        if dst_type_req and dst_node["node_type"] != dst_type_req:
            violations.append(f"{e['edge_type']}: dst is {dst_node['node_type']} expected {dst_type_req}")
        if dst_role_check and not dst_role_check(dst_node):
            violations.append(f"{e['edge_type']}: dst role check failed for {dst_node['node_id']} "
                              f"(entity_type={dst_node.get('entity_type')}, role={dst_node.get('role')})")

    facts_with_ev = sum(1 for f in facts if f["evidence_id"])
    hedges_with_ev = sum(1 for h in hyperedges if h["evidence_ids"])
    evds_with_asset = sum(1 for e in evidence_units if e["asset_path"])
    trace = {
        "facts_with_evidence_id": facts_with_ev / max(1, len(facts)),
        "hyperedges_with_evidence_ids": hedges_with_ev / max(1, len(hyperedges)),
        "evidence_units_with_asset_path": evds_with_asset / max(1, len(evidence_units)),
    }

    print(f"[done] {doc_id}: "
          f"{counts['patents']}/{counts['patent_profiles']}/"
          f"{counts['evidence_units']}/{counts['example_contexts']}/"
          f"{counts['facts']}/{counts['canonical_entities']}/"
          f"{counts['canonical_relations']}/{counts['edges']}/"
          f"{counts['hyperedges']} "
          f"CSV: {len(hyperedges)} rows")
    print(f"[gates] trace={trace}")
    print(f"[gates] edge_violations={len(violations)} (first 3: {violations[:3]})")
    return 0 if not violations else 1


if __name__ == "__main__":
    prep_dir = Path(sys.argv[1]).resolve()
    out_dir = Path(sys.argv[2]).resolve()
    sys.exit(main(prep_dir, out_dir))
