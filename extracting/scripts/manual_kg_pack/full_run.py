"""
Full per-patent extractor — produces the same 10 JSONL + manifest + CSV as the
master prompt, deterministically. Covers:

- multi-page formulation tables (Table 1/4/5/6): merge across pages by caption-chain
- result tables (Table 2/3/etc.): row=sample, col=metric, transposed handling
- method paragraphs (panel prep / cracking / SST / example process)
- canonical entity de-dup + role classification
- hyperedge bundling per (sample_id × context)
- baseline binding (model paint × matching reference paint in same table)
- type-strict edge whitelist (§4.8 of EXTRACT_PROMPT.md)
- §6 gates after writing

Usage:
    python full_run.py <prep_dir> <out_dir>
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ============ canonical role classifier (same as smoke_demo) ==============

ROLE_KEYWORDS: list[tuple[str, str, str, str | None]] = [
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
    ("crayamid", "resin", "curing_agent", "polyaminoamide curing agent"),
    ("polyisocyanate", "resin", "curing_agent", "polyisocyanate crosslinker"),
    ("tolonate", "resin", "curing_agent", "polyisocyanate crosslinker"),
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
    ("microspheres", "component", "filler", "microsphere filler"),
    ("microsphere", "component", "filler", "microsphere filler"),
    ("nepheline syenite", "component", "filler", "filler"),
    ("silica aerogel", "component", "filler", "silica aerogel"),
    ("aerosil", "component", "filler", "silica aerogel"),
    ("kaolin", "component", "filler", "extender pigment"),
    ("ceramic", "component", "filler", "ceramic microsphere"),
    ("expancel", "component", "filler", "polymeric microsphere filler"),
    ("polyethylene spherical", "component", "filler", "polymeric microsphere filler"),
    ("acrylic ester spherical", "component", "filler", "polymeric microsphere filler"),
    ("polymethylmethacrylate spherical", "component", "filler", "polymeric microsphere filler"),
    ("spherical silica", "component", "filler", "microsphere filler"),
    ("organo clay", "component", "rheology_additive", None),
    ("luvogel", "component", "rheology_additive", None),
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
    ("bisphenol a", "component", "other", "bisphenol A component"),
    ("thixotropic", "component", "rheology_additive", "thixotropic agent"),
    ("solvent", "component", "other", "solvent"),
    ("xylene", "component", "other", "solvent"),
    ("naphtha", "component", "other", "solvent"),
    ("isopropanol", "component", "other", "solvent"),
    ("butanol", "component", "other", "solvent"),
    ("butyl acetate", "component", "other", "solvent"),
    ("additives", "component", "other", "combined additives"),
    ("additive", "component", "other", None),
]

METRIC_KEYWORDS = ("total", "pvc", "svr", "ratio", "mixing")
RESULT_HEADER_HINTS = ("rust creep", "dft", "dry film", "cracking level", "panel", "crack")


def classify_ingredient(name: str) -> tuple[str, str, str | None]:
    n = (name or "").lower()
    for kw, slot, role, role_detail in ROLE_KEYWORDS:
        if kw in n:
            return slot, role, role_detail
    return "component", "other", None


def md5_12(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:12]


def slug(s: str) -> str:
    return re.sub(r"\W+", "_", s.lower()).strip("_")


SAMPLE_RE = re.compile(r"\b(?:model|reference|ref\.?)\s*paint\s*\d+", re.IGNORECASE)
SAMPLE_NUM_RE = re.compile(r"\b(model|reference|ref)\b[\s.]*(?:paint\s*)?(\d+)", re.IGNORECASE)


def parse_sample_label(text: str) -> str | None:
    """Convert 'Ref. paint 3' / 'Model paint 7' / 'Reference paint 1' → canonical."""
    if not text:
        return None
    m = re.search(r"\b(model|reference|ref)\b[\s.]*(?:paint\s*)?(\d+)", text, re.IGNORECASE)
    if not m:
        return None
    kind = m.group(1).lower()
    num = m.group(2)
    if kind == "model":
        return f"Model paint {num}"
    return f"Reference paint {num}"


# ============ table classification ========================================

def classify_table(table: dict) -> str:
    """Return 'formulation' | 'result' | 'unknown'.
    Formulation: row 0 contains 'Model paint' or similar with numbers in cells.
    Result: column 0 contains sample labels in data rows.
    """
    caption = (table.get("table_caption") or "").lower()
    if any(h in caption for h in RESULT_HEADER_HINTS):
        return "result"
    rows = table.get("rows", [])
    if not rows:
        return "unknown"
    # Check row 0 first cell text
    r0c0 = (rows[0]["cells"][0]["text"] if rows[0]["cells"] else "").lower()
    if "model paint" in r0c0 and any(c["text"] and c["text"].strip().isdigit() for c in rows[0]["cells"][1:]):
        # row 0 = "Model paint" + numeric col headers → formulation OR result
        # Distinguish by checking row 2+ — formulation: row 2 is "Component 1+ | %SV..." then row 3 ingredient.
        # result: row 2 onward has sample labels in col 0
        unit_or_metric_row = (rows[1]["cells"][0]["text"] if len(rows) > 1 and rows[1]["cells"] else "").lower()
        if "component" in unit_or_metric_row or "compound" in unit_or_metric_row or "%sv" in unit_or_metric_row:
            return "formulation"
        return "formulation"  # default for "Model paint" headers
    if "model paintcomposition" in r0c0 or "ref. paint" in r0c0 or re.match(r"^(model|ref|reference)\b", r0c0):
        return "result"
    # Look for sample labels in column 0 of data rows
    for r in rows[1:5]:
        if not r["cells"]:
            continue
        if parse_sample_label(r["cells"][0]["text"] or ""):
            return "result"
    return "unknown"


def extract_formulation_samples(table: dict) -> dict[int, str]:
    """Build {col_idx: sample_id} for formulation table by walking header rows 0-1."""
    rows = table.get("rows", [])
    samples: dict[int, str] = {}
    # Row 0: usually "Model paint" | nums..; Row 1: "Reference paint" | nums...
    if rows:
        r0 = rows[0]
        first = (r0["cells"][0]["text"] or "").lower() if r0["cells"] else ""
        if "model" in first:
            for c in r0["cells"][1:]:
                if c["text"] and c["text"].strip().isdigit():
                    samples[c["col_idx"]] = f"Model paint {c['text'].strip()}"
        elif "ref" in first or "reference" in first:
            for c in r0["cells"][1:]:
                if c["text"] and c["text"].strip().isdigit():
                    samples[c["col_idx"]] = f"Reference paint {c['text'].strip()}"
    if len(rows) > 1:
        r1 = rows[1]
        first = (r1["cells"][0]["text"] or "").lower() if r1["cells"] else ""
        if "model" in first:
            for c in r1["cells"][1:]:
                if c["text"] and c["text"].strip().isdigit() and c["col_idx"] not in samples:
                    samples[c["col_idx"]] = f"Model paint {c['text'].strip()}"
        elif "ref" in first or "reference" in first:
            for c in r1["cells"][1:]:
                if c["text"] and c["text"].strip().isdigit() and c["col_idx"] not in samples:
                    samples[c["col_idx"]] = f"Reference paint {c['text'].strip()}"
    return samples


def extract_result_metrics(table: dict) -> tuple[dict[int, str], int]:
    """Build {col_idx: metric_name} for result table from header rows.
    Returns (metric_map, first_data_row_idx)."""
    rows = table.get("rows", [])
    metrics: dict[int, str] = {}
    # Header rows have metric names. Walk until we hit a row whose col 0 looks like a sample.
    header_rows: list[list[str]] = []
    data_start = 0
    for r_i, r in enumerate(rows):
        cell0 = (r["cells"][0]["text"] or "").strip() if r["cells"] else ""
        if parse_sample_label(cell0) or cell0.lower().startswith("ref"):
            data_start = r_i
            break
        header_rows.append([(c["text"] or "").strip() for c in r["cells"]])
    if not header_rows:
        return metrics, 0
    # Combine all header rows column-wise
    max_cols = max(len(h) for h in header_rows)
    for col in range(1, max_cols):
        parts: list[str] = []
        for h in header_rows:
            if col < len(h) and h[col]:
                parts.append(h[col])
        if parts:
            metrics[col] = " ".join(parts)
    return metrics, data_start


# ============ method paragraph extraction =================================

ISO_RE = re.compile(r"\b(ISO|ASTM|NACE|DIN|EN|GB)\s*[\w./-]+", re.IGNORECASE)
TEMP_RE = re.compile(r"\d+(?:[.,±]\d+)?\s*°\s*C", re.IGNORECASE)
TIME_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:days?|h\b|hours?|min(?:utes?)?\b|seconds?\b)", re.IGNORECASE)
THICKNESS_RE = re.compile(r"\d+\s*[x×]\s*\d+\s*[µu]m|\d+\s*[µu]m\b", re.IGNORECASE)


def extract_method_paragraph(para: dict, page_num: int) -> dict | None:
    text = para.get("text", "")
    if len(text) < 30:
        return None
    tlow = text.lower()
    has_substrate = any(k in tlow for k in ("steel panel", "abrasive blasted", "substrate", "iso 8501"))
    has_test = any(k in tlow for k in ("test", "sst", "salt spray", "cracking", "rust creep", "test panel", "thermal cycling"))
    has_proc = any(k in tlow for k in ("preparation", "premixed", "mixing", "rpm", "stirring", "homogeniz"))
    if not (has_substrate or has_test or has_proc):
        return None
    return {
        "page": page_num,
        "text": text,
        "has_substrate": has_substrate,
        "has_test": has_test,
        "has_proc": has_proc,
    }


# ============ main =========================================================

def main(prep_dir: Path, out_dir: Path) -> int:
    kg_input = json.loads((prep_dir / "kg_input.json").read_text(encoding="utf-8"))
    doc_id = kg_input["doc_id"]
    kg_source = f"{doc_id}_manual_strict_example_scope_kg_pack_v1"
    out_dir.mkdir(parents=True, exist_ok=True)
    scope_pages = kg_input["examples_scope"]["detected_pages"]
    scope_first = min(scope_pages) if scope_pages else 1
    scope_last = max(scope_pages) if scope_pages else kg_input["page_count"]

    # ==== state ====
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
        eid = "EDGE_" + md5_12(f"{edge_type}|{src}|{dst}")
        rec = {
            "record_type": "EdgeRecord", "schema_version": "kg_pack_edge_v1",
            "edge_id": eid, "edge_type": edge_type, "src": src, "dst": dst,
            "patent_id": doc_id, "doc_id": doc_id, "kg_source": kg_source,
        }
        rec.update(extras)
        edges.append(rec)

    pat_id = f"PAT_{doc_id}"
    profile_id = f"PROFILE_{doc_id}"

    # ==== pre-seed shared canonicals ====
    ent_sub_tested = get_or_create_entity(
        "cold rolled mild steel panel (10 x 15 cm x 1.6 mm), abrasive blasted to Sa 2½ according to ISO 8501-1, surface profile equivalent to BN 9 (Rugotest No. 3)",
        "substrate", alias="cold rolled mild steel panel, abrasive blasted to Sa 2½")
    ent_sub_claimed = get_or_create_entity("iron and steel structures / metal structures", "substrate")
    ent_app_claimed = get_or_create_entity(
        "anti-corrosive zinc primer coating compositions for protecting iron and steel structures", "application")

    # ==== detect table CHAINS (multi-page) and methods ====
    # A chain is a list of (page, table_idx, table_dict) sharing a caption.
    # Continuation: subsequent pages with empty caption + a header row containing
    # "Model paint" / "Ref(erence) paint" — chain to previous.
    chains: list[dict] = []         # {caption, kind: formulation|result, pages: [...], tables: [...]}
    current_chain: dict | None = None
    for p in kg_input["pages"]:
        if not p["in_examples_scope"]:
            continue
        for t in p["tables"]:
            cap = (t.get("table_caption") or "").strip()
            kind = classify_table(t)
            if cap.lower().startswith("table"):
                # Start of a new chain
                current_chain = {
                    "caption": cap, "kind": kind, "tables": [(p["page"], t)],
                    "table_label": re.match(r"(Table\s*\d+)", cap, re.IGNORECASE).group(1)
                                   if re.match(r"(Table\s*\d+)", cap, re.IGNORECASE) else cap[:20],
                }
                chains.append(current_chain)
            else:
                # Continuation — append to current chain if compatible (same kind expected)
                if current_chain is not None:
                    # If kind is "unknown" treat as continuation of current chain's kind
                    current_chain["tables"].append((p["page"], t))
                else:
                    # Orphan table — start a new chain
                    current_chain = {
                        "caption": "(unlabeled)", "kind": kind,
                        "tables": [(p["page"], t)], "table_label": f"Table_p{p['page']}",
                    }
                    chains.append(current_chain)

    # ==== process each chain ====
    # Build hyperedges keyed by (sample_id, chain_id) so a sample's facts across
    # multi-page chain pages bundle into one hyperedge.
    sample_hedges: dict[tuple[str, int], dict] = {}

    def get_sample_hedge(sample_id: str, chain_idx: int, chain: dict,
                          bundle_type: str, application_tested: str,
                          prop_name: str, prop_category: str) -> dict:
        key = (sample_id, chain_idx)
        if key in sample_hedges:
            return sample_hedges[key]
        ctx_slug = slug(f"{doc_id}__{chain['table_label']}__{sample_id}__{bundle_type}")
        excxt_id = f"EXCTX_{ctx_slug}"
        example_contexts.append({
            "record_type": "ExampleContextRecord",
            "schema_version": "kg_pack_example_context_v1",
            "node_type": "EXAMPLE_CONTEXT",
            "node_id": excxt_id, "context_id": ctx_slug,
            "patent_id": doc_id, "doc_id": doc_id,
            "example_id": f"{chain['table_label']}",
            "sample_id": sample_id, "bundle_type": bundle_type,
            "table_role": chain["table_label"],
            "evidence_ids": [], "linked_hyperedge_ids": [],
            "kg_source": kg_source,
        })
        hedge_id = new_hedge_id()
        example_contexts[-1]["linked_hyperedge_ids"].append(hedge_id)
        hedge = {
            "record_type": "HyperedgeRecord",
            "schema_version": "kg_pack_l2_hyperedge_v1",
            "node_type": "HYPEREDGE",
            "node_id": hedge_id, "hyperedge_id": hedge_id,
            "patent_id": doc_id, "doc_id": doc_id,
            "context_id": ctx_slug, "bundle_type": bundle_type,
            "sample_id": sample_id, "example_id": f"{chain['table_label']}",
            "application": {"tested": application_tested,
                            "claimed": "anti-corrosive zinc primer coating compositions for protecting iron and steel structures"},
            "substrate": {
                "tested": "cold rolled mild steel panel (10 x 15 cm x 1.6 mm), abrasive blasted to Sa 2½ according to ISO 8501-1, surface profile equivalent to BN 9 (Rugotest No. 3)",
                "claimed": "iron and steel structures / metal structures"},
            "resin": [], "additives": [], "process": [],
            "property": {"name": prop_name, "category": prop_category, "measured_or_claimed": "measured"},
            "test_method": {"name": None, "standard_id": [],
                            "condition": {"exposure": None, "immersion": None, "rating_scale": None, "score_max": None}},
            "result": [], "baseline": {"baseline_sample_id": None, "baseline_context_id": None,
                                        "baseline_result": None, "comparison_text": None},
            "evidence": [], "confidence": {"overall": 0.92, "sample_binding": 0.94,
                                            "formulation_binding": 0.94 if bundle_type == "formulation_context" else 0.0,
                                            "result_binding": 0.92 if bundle_type == "performance_result" else 0.0},
            "qa_flags": ["mineru_layout_based", "role_normalized", "examples_section_only"],
            "unresolved_fields": ["cell_bbox"], "kg_source": kg_source,
            "source_scope": f"examples_pages_{scope_first}_{scope_last}",
            "evidence_ids": [], "answerability": "context_bundle",
            "projection_note": "Use this L2 record as fact source; CSV should be a view only.",
        }
        hyperedges.append(hedge)
        sample_hedges[key] = hedge
        return hedge

    # ---- detect resin family per chain for application_tested ----
    def chain_application(chain: dict) -> str:
        cap_l = chain["caption"].lower()
        if "epoxy" in cap_l: return "epoxy-based anti-corrosive zinc primer coating"
        if "silicate" in cap_l: return "silicate-based anti-corrosive zinc primer coating"
        if "polysiloxane" in cap_l or "siloxane" in cap_l: return "polysiloxane-based anti-corrosive zinc primer coating"
        if "polyurethane" in cap_l or "urethane" in cap_l: return "polyurethane-based anti-corrosive zinc primer coating"
        return "anti-corrosive zinc primer coating"

    def chain_prop(chain: dict) -> tuple[str, str]:
        cap_l = chain["caption"].lower()
        kind = chain["kind"]
        if kind == "formulation":
            if "epoxy" in cap_l: return "basic epoxy-based paint formulation", "formulation"
            if "silicate" in cap_l: return "basic silicate-based paint formulation", "formulation"
            if "polysiloxane" in cap_l or "siloxane" in cap_l: return "basic polysiloxane-based paint formulation", "formulation"
            if "polyurethane" in cap_l: return "basic polyurethane-based paint formulation", "formulation"
            return "basic paint formulation", "formulation"
        else:
            if "cracking" in cap_l: return "cracking resistance after thermal cycling", "durability"
            return "rust creep / corrosion resistance", "corrosion_resistance"

    # ==== walk chains ====
    for ci, chain in enumerate(chains):
        app_tested = chain_application(chain)
        prop_name, prop_cat = chain_prop(chain)
        ent_app_tested = get_or_create_entity(app_tested, "application")
        ent_prop = get_or_create_entity(prop_name, "property", category=prop_cat)

        if chain["kind"] == "formulation":
            # ---- Walk pages in this chain ----
            for page_num, tbl in chain["tables"]:
                samples = extract_formulation_samples(tbl)
                if not samples:
                    continue
                # Identify "data start" — first row with a non-empty col 0 that's an ingredient
                # Header rows are: Model paint row, Reference paint row, Component/%SV row
                # Walk rows; classify each by col 0 content.
                rows = tbl["rows"]
                # data_start = end of the CONTIGUOUS header block at the top.
                # Walk from row 0 — keep advancing while col 0 is a header marker; stop at first
                # non-header row. Do NOT advance past in-table "Component 2" separators.
                data_start = 0
                for r_i, r in enumerate(rows):
                    c0 = ((r["cells"][0]["text"] if r["cells"] else "") or "").lower()
                    is_header = (not c0) or any(
                        h in c0 for h in ("model paint", "reference paint", "ref. paint",
                                          "component 1+", "component 1:", "component 1.",
                                          "compound 1:", "compound 1+", "compound 1.",
                                          "component 1 +", "%sv"))
                    if is_header and r_i == data_start:
                        data_start = r_i + 1
                    else:
                        break
                # Treat in-table "Component 2" / "Compound 2" rows as separators (skip them but keep going)
                # We'll emit one evidence_unit per sample column on this page.
                for col_idx, sample_id in samples.items():
                    evd_id = f"EVD_{doc_id}_p{page_num}_{slug(chain['table_label'])}_{slug(sample_id)}"
                    hedge = get_sample_hedge(sample_id, ci, chain, "formulation_context",
                                              app_tested, prop_name, prop_cat)
                    direct_units: list[dict] = []
                    cell_excerpts: list[str] = []
                    page_facts: list[dict] = []
                    next_u = 1
                    # sample_context + example_context + property
                    direct_units.append({"slot": "sample_context", "source_field": "sample_id",
                                          "name": sample_id, "raw_text": sample_id, "unit_id": f"u{next_u:03d}"})
                    # also emit as fact
                    fid = new_fact_id()
                    page_facts.append({
                        "record_type": "FactRecord", "schema_version": "kg_pack_fact_v1",
                        "node_type": "FACT", "node_id": fid, "fact_id": fid,
                        "patent_id": doc_id, "doc_id": doc_id,
                        "evidence_id": evd_id, "context_id": hedge["context_id"],
                        "hyperedge_id": hedge["hyperedge_id"],
                        "slot": "sample_context", "source_field": "sample_id",
                        "name": sample_id, "metric": None, "role": None, "role_detail": None,
                        "value": sample_id, "unit": None, "raw_text": sample_id,
                        "amount_basis": None, "scale": None, "step_type": None,
                        "linked_canonical_entity_id": "", "kg_source": kg_source,
                    })
                    next_u += 1
                    direct_units.append({"slot": "example_context", "source_field": "example_id",
                                          "name": f"{chain['table_label']} (page {page_num})",
                                          "raw_text": f"{chain['table_label']} (page {page_num})", "unit_id": f"u{next_u:03d}"})
                    fid = new_fact_id()
                    page_facts.append({
                        "record_type": "FactRecord", "schema_version": "kg_pack_fact_v1",
                        "node_type": "FACT", "node_id": fid, "fact_id": fid,
                        "patent_id": doc_id, "doc_id": doc_id,
                        "evidence_id": evd_id, "context_id": hedge["context_id"],
                        "hyperedge_id": hedge["hyperedge_id"],
                        "slot": "example_context", "source_field": "example_id",
                        "name": f"{chain['table_label']} (page {page_num})",
                        "metric": None, "role": None, "role_detail": None,
                        "value": f"{chain['table_label']} (page {page_num})",
                        "unit": None, "raw_text": f"{chain['table_label']} (page {page_num})",
                        "amount_basis": None, "scale": None, "step_type": None,
                        "linked_canonical_entity_id": "", "kg_source": kg_source,
                    })
                    next_u += 1
                    direct_units.append({"slot": "property", "source_field": "property",
                                          "name": prop_name, "category": prop_cat,
                                          "measured_or_claimed": "measured", "unit_id": f"u{next_u:03d}"})
                    fid = new_fact_id()
                    page_facts.append({
                        "record_type": "FactRecord", "schema_version": "kg_pack_fact_v1",
                        "node_type": "FACT", "node_id": fid, "fact_id": fid,
                        "patent_id": doc_id, "doc_id": doc_id,
                        "evidence_id": evd_id, "context_id": hedge["context_id"],
                        "hyperedge_id": hedge["hyperedge_id"],
                        "slot": "property", "source_field": "property",
                        "name": prop_name, "metric": None, "role": None, "role_detail": None,
                        "value": prop_name, "unit": None, "raw_text": prop_name,
                        "amount_basis": None, "scale": None, "step_type": None,
                        "linked_canonical_entity_id": ent_prop, "kg_source": kg_source,
                    })
                    next_u += 1

                    for r in rows[data_start:]:
                        if col_idx >= len(r["cells"]):
                            continue
                        ingr_name = (r["cells"][0]["text"] or "").strip()
                        val_text = (r["cells"][col_idx]["text"] or "").strip()
                        if not ingr_name or not val_text:
                            continue
                        # Skip in-table separator rows like "Component 2:" / "Compound 2:"
                        ingr_low = ingr_name.lower()
                        if re.match(r"^(component|compound)\s*[12+.:]+\s*$", ingr_low):
                            continue
                        # Skip non-numeric values for ingredient rows (likely sub-headers)
                        if any(m in ingr_name.lower() for m in METRIC_KEYWORDS):
                            metric_name = ingr_name
                            v = re.sub(r"\s*%SV\s*$", "", val_text).strip()
                            unit = "%SV" if "%SV" in val_text or "%" in val_text else None
                            mid = get_or_create_entity(metric_name, "formulation_metric")
                            direct_units.append({"slot": "formulation_metric", "source_field": "result",
                                                  "metric": metric_name, "value": v, "unit": unit,
                                                  "raw_text": val_text, "scale": "formulation metric",
                                                  "unit_id": f"u{next_u:03d}"})
                            cell_excerpts.append(f"{metric_name}: {val_text}")
                            next_u += 1
                            fid = new_fact_id()
                            f = {
                                "record_type": "FactRecord", "schema_version": "kg_pack_fact_v1",
                                "node_type": "FACT", "node_id": fid, "fact_id": fid,
                                "patent_id": doc_id, "doc_id": doc_id,
                                "evidence_id": evd_id, "context_id": hedge["context_id"],
                                "hyperedge_id": hedge["hyperedge_id"],
                                "slot": "formulation_metric", "source_field": "result",
                                "name": None, "metric": metric_name, "role": None, "role_detail": None,
                                "value": v, "unit": unit, "raw_text": val_text,
                                "amount_basis": None, "scale": "formulation metric", "step_type": None,
                                "linked_canonical_entity_id": mid, "kg_source": kg_source,
                            }
                            page_facts.append(f)
                            continue
                        slot, role, role_detail = classify_ingredient(ingr_name)
                        # numeric check
                        if not re.match(r"^[<>~]?\s*\d", val_text):
                            continue
                        eid = get_or_create_entity(ingr_name, "material", role=role, role_detail=role_detail, alias=ingr_name)
                        unit_rec = {
                            "slot": slot,
                            "source_field": "resin" if slot == "resin" else "additives",
                            "name": ingr_name, "role": role,
                            "value": val_text, "unit": "%SV",
                            "raw_text": f"{val_text} %SV", "amount_basis": "formulation_amount",
                            "evidence_ref": f"{chain['table_label']} / {sample_id} / {ingr_name}",
                            "unit_id": f"u{next_u:03d}",
                        }
                        if role_detail: unit_rec["role_detail"] = role_detail
                        direct_units.append(unit_rec)
                        cell_excerpts.append(f"{ingr_name}: {val_text} %SV")
                        next_u += 1
                        fid = new_fact_id()
                        f = {
                            "record_type": "FactRecord", "schema_version": "kg_pack_fact_v1",
                            "node_type": "FACT", "node_id": fid, "fact_id": fid,
                            "patent_id": doc_id, "doc_id": doc_id,
                            "evidence_id": evd_id, "context_id": hedge["context_id"],
                            "hyperedge_id": hedge["hyperedge_id"],
                            "slot": slot, "source_field": "resin" if slot == "resin" else "additives",
                            "name": ingr_name, "metric": None, "role": role, "role_detail": role_detail,
                            "value": val_text, "unit": "%SV", "raw_text": f"{val_text} %SV",
                            "amount_basis": "formulation_amount", "scale": None, "step_type": None,
                            "linked_canonical_entity_id": eid, "kg_source": kg_source,
                        }
                        page_facts.append(f)
                        # Add to hyperedge.resin/additives lists
                        item = {"name": ingr_name, "role": role, "amount": val_text, "unit": "%SV"}
                        if role_detail: item["role_detail"] = role_detail
                        if slot == "resin":
                            hedge["resin"].append(item)
                        else:
                            hedge["additives"].append(item)
                    if not page_facts:
                        continue
                    facts.extend(page_facts)
                    text_excerpt = f"{chain['table_label']} row '{sample_id}'; " + " | ".join(cell_excerpts)
                    # Find table bbox for asset_bbox
                    evidence_units.append({
                        "record_type": "EvidenceRecord",
                        "schema_version": "kg_pack_l1_evidence_unit_v1",
                        "node_type": "EVD", "node_id": evd_id, "evidence_id": evd_id,
                        "patent_id": doc_id, "doc_id": doc_id,
                        "layer": "L1_evidence_unit",
                        "source": "mineru_layout_plus_llm_decomposition",
                        "source_pdf": kg_input["source_pdf_name"],
                        "page": page_num, "printed_page": page_num - 1,
                        "section": f"Example / {chain['table_label']} - {chain['caption'][:80]}",
                        "evidence_type": "formulation_table", "source_block_type": "formulation_table_row",
                        "table": chain["table_label"], "row": sample_id, "column": "%SV",
                        "caption": chain["caption"], "bbox": None,
                        "asset_path": f"pages/page_{page_num:03d}.png",
                        "asset_bbox_image": tbl["bbox_image"],
                        "asset_bbox_pdf": tbl["bbox_pdf"],
                        "text_excerpt": text_excerpt,
                        "quote_style": "structured_row_transcription",
                        "direct_extracted_units": direct_units,
                        "context_bindings": {
                            "linked_l2_context_ids": [hedge["context_id"]],
                            "primary_l2_context_ids": [hedge["context_id"]],
                            "inherited_by_l2_context_ids": [],
                            "linked_bundle_types": ["formulation_context"],
                            "linked_sample_ids": [sample_id],
                        },
                        "coverage_scope": "examples_section_tables_and_related_method_paragraphs",
                        "confidence": 0.92, "qa_flags": ["mineru_layout_based", "row_level_bbox_only", "examples_section_only"],
                        "unresolved_fields": ["cell_bbox"], "kg_source": kg_source,
                        "trace_role": "L1_retrieval_context",
                        "source_scope": f"examples_pages_{scope_first}_{scope_last}",
                    })
                    # link to hyperedge
                    hedge["evidence"].append({"evidence_id": evd_id, "page": page_num,
                                               "section": evidence_units[-1]["section"],
                                               "table": chain["table_label"], "row": sample_id, "column": "%SV",
                                               "evidence_type": "formulation_table", "printed_page": page_num - 1})
                    hedge["evidence_ids"].append(evd_id)
                    # find the corresponding example_context and append evidence
                    for c in example_contexts:
                        if c["context_id"] == hedge["context_id"]:
                            c["evidence_ids"].append(evd_id)
                            break

        elif chain["kind"] == "result":
            # Result tables: column 0 = sample label; columns 1.. = metric values
            for page_num, tbl in chain["tables"]:
                metrics, data_start = extract_result_metrics(tbl)
                if not metrics:
                    continue
                for r in tbl["rows"][data_start:]:
                    if not r["cells"]:
                        continue
                    sample_id = parse_sample_label((r["cells"][0]["text"] or "")) if r["cells"] else None
                    if not sample_id:
                        continue
                    evd_id = f"EVD_{doc_id}_p{page_num}_{slug(chain['table_label'])}_{slug(sample_id)}"
                    hedge = get_sample_hedge(sample_id, ci, chain, "performance_result",
                                              app_tested, prop_name, prop_cat)
                    direct_units: list[dict] = []
                    cell_excerpts: list[str] = []
                    page_facts: list[dict] = []
                    next_u = 1
                    direct_units.append({"slot": "sample_context", "source_field": "sample_id",
                                          "name": sample_id, "raw_text": sample_id, "unit_id": f"u{next_u:03d}"})
                    fid = new_fact_id()
                    page_facts.append({
                        "record_type": "FactRecord", "schema_version": "kg_pack_fact_v1",
                        "node_type": "FACT", "node_id": fid, "fact_id": fid,
                        "patent_id": doc_id, "doc_id": doc_id,
                        "evidence_id": evd_id, "context_id": hedge["context_id"],
                        "hyperedge_id": hedge["hyperedge_id"],
                        "slot": "sample_context", "source_field": "sample_id",
                        "name": sample_id, "metric": None, "role": None, "role_detail": None,
                        "value": sample_id, "unit": None, "raw_text": sample_id,
                        "amount_basis": None, "scale": None, "step_type": None,
                        "linked_canonical_entity_id": "", "kg_source": kg_source,
                    })
                    next_u += 1
                    direct_units.append({"slot": "example_context", "source_field": "example_id",
                                          "name": chain["table_label"],
                                          "raw_text": chain["table_label"], "unit_id": f"u{next_u:03d}"})
                    fid = new_fact_id()
                    page_facts.append({
                        "record_type": "FactRecord", "schema_version": "kg_pack_fact_v1",
                        "node_type": "FACT", "node_id": fid, "fact_id": fid,
                        "patent_id": doc_id, "doc_id": doc_id,
                        "evidence_id": evd_id, "context_id": hedge["context_id"],
                        "hyperedge_id": hedge["hyperedge_id"],
                        "slot": "example_context", "source_field": "example_id",
                        "name": chain["table_label"], "metric": None, "role": None, "role_detail": None,
                        "value": chain["table_label"], "unit": None, "raw_text": chain["table_label"],
                        "amount_basis": None, "scale": None, "step_type": None,
                        "linked_canonical_entity_id": "", "kg_source": kg_source,
                    })
                    next_u += 1
                    direct_units.append({"slot": "property", "source_field": "property",
                                          "name": prop_name, "category": prop_cat,
                                          "measured_or_claimed": "measured", "unit_id": f"u{next_u:03d}"})
                    fid = new_fact_id()
                    page_facts.append({
                        "record_type": "FactRecord", "schema_version": "kg_pack_fact_v1",
                        "node_type": "FACT", "node_id": fid, "fact_id": fid,
                        "patent_id": doc_id, "doc_id": doc_id,
                        "evidence_id": evd_id, "context_id": hedge["context_id"],
                        "hyperedge_id": hedge["hyperedge_id"],
                        "slot": "property", "source_field": "property",
                        "name": prop_name, "metric": None, "role": None, "role_detail": None,
                        "value": prop_name, "unit": None, "raw_text": prop_name,
                        "amount_basis": None, "scale": None, "step_type": None,
                        "linked_canonical_entity_id": ent_prop, "kg_source": kg_source,
                    })
                    next_u += 1
                    for col_idx, metric_name in metrics.items():
                        if col_idx >= len(r["cells"]):
                            continue
                        val_text = (r["cells"][col_idx]["text"] or "").strip()
                        if not val_text:
                            continue
                        # Unit detection
                        unit = "mm" if "rust creep" in metric_name.lower() else \
                               ("µm" if "dft" in metric_name.lower() or "thickness" in metric_name.lower() else None)
                        mid = get_or_create_entity(metric_name, "performance_metric")
                        unit_rec = {"slot": "result", "source_field": "result",
                                     "name": metric_name, "metric": metric_name,
                                     "value": val_text, "unit": unit, "raw_text": val_text,
                                     "evidence_ref": f"{chain['table_label']} / {sample_id} / {metric_name}",
                                     "unit_id": f"u{next_u:03d}"}
                        direct_units.append(unit_rec)
                        cell_excerpts.append(f"{metric_name}: {val_text}")
                        next_u += 1
                        fid = new_fact_id()
                        f = {
                            "record_type": "FactRecord", "schema_version": "kg_pack_fact_v1",
                            "node_type": "FACT", "node_id": fid, "fact_id": fid,
                            "patent_id": doc_id, "doc_id": doc_id,
                            "evidence_id": evd_id, "context_id": hedge["context_id"],
                            "hyperedge_id": hedge["hyperedge_id"],
                            "slot": "result", "source_field": "result",
                            "name": metric_name, "metric": metric_name, "role": None, "role_detail": None,
                            "value": val_text, "unit": unit, "raw_text": val_text,
                            "amount_basis": None, "scale": None, "step_type": None,
                            "linked_canonical_entity_id": mid, "kg_source": kg_source,
                        }
                        page_facts.append(f)
                        hedge["result"].append({"metric": metric_name, "value": val_text,
                                                  "unit": unit, "evidence_ref": evd_id})
                    if not page_facts:
                        continue
                    facts.extend(page_facts)
                    text_excerpt = f"{chain['table_label']} row '{sample_id}'; " + " | ".join(cell_excerpts)
                    evidence_units.append({
                        "record_type": "EvidenceRecord",
                        "schema_version": "kg_pack_l1_evidence_unit_v1",
                        "node_type": "EVD", "node_id": evd_id, "evidence_id": evd_id,
                        "patent_id": doc_id, "doc_id": doc_id,
                        "layer": "L1_evidence_unit",
                        "source": "mineru_layout_plus_llm_decomposition",
                        "source_pdf": kg_input["source_pdf_name"],
                        "page": page_num, "printed_page": page_num - 1,
                        "section": f"Example / {chain['table_label']} - {chain['caption'][:80]}",
                        "evidence_type": "result_table", "source_block_type": "result_table_row",
                        "table": chain["table_label"], "row": sample_id, "column": None,
                        "caption": chain["caption"], "bbox": None,
                        "asset_path": f"pages/page_{page_num:03d}.png",
                        "asset_bbox_image": tbl["bbox_image"],
                        "asset_bbox_pdf": tbl["bbox_pdf"],
                        "text_excerpt": text_excerpt,
                        "quote_style": "structured_row_transcription",
                        "direct_extracted_units": direct_units,
                        "context_bindings": {
                            "linked_l2_context_ids": [hedge["context_id"]],
                            "primary_l2_context_ids": [hedge["context_id"]],
                            "inherited_by_l2_context_ids": [],
                            "linked_bundle_types": ["performance_result"],
                            "linked_sample_ids": [sample_id],
                        },
                        "coverage_scope": "examples_section_tables_and_related_method_paragraphs",
                        "confidence": 0.92, "qa_flags": ["mineru_layout_based", "row_level_bbox_only", "examples_section_only"],
                        "unresolved_fields": ["cell_bbox"], "kg_source": kg_source,
                        "trace_role": "L1_retrieval_context",
                        "source_scope": f"examples_pages_{scope_first}_{scope_last}",
                    })
                    hedge["evidence"].append({"evidence_id": evd_id, "page": page_num,
                                               "section": evidence_units[-1]["section"],
                                               "table": chain["table_label"], "row": sample_id, "column": None,
                                               "evidence_type": "result_table", "printed_page": page_num - 1})
                    hedge["evidence_ids"].append(evd_id)
                    for c in example_contexts:
                        if c["context_id"] == hedge["context_id"]:
                            c["evidence_ids"].append(evd_id)
                            break

    # ==== method paragraphs ====
    # Walk in-scope paragraphs and extract methods. Build evidence + facts + a test_context hyperedge.
    method_para_count = 0
    for p in kg_input["pages"]:
        if not p["in_examples_scope"]:
            continue
        for para in p["paragraphs"]:
            mp = extract_method_paragraph(para, p["page"])
            if not mp:
                continue
            method_para_count += 1
            page_num = mp["page"]
            text = mp["text"]
            section_guess = "Examples / "
            if "panel" in text.lower() and "prep" in text.lower():
                descriptor, section_guess = "test_panel_preparation", "Examples / Preparation of test panels"
            elif "cracking" in text.lower():
                descriptor, section_guess = "cracking_test_method", "Examples / Test Methods / Cracking test"
            elif "salt spray" in text.lower() or "sst" in text.lower():
                descriptor, section_guess = "salt_spray_test_method", "Examples / Test Methods / Salt Spray Test (SST)"
            elif "example" in text.lower() and "preparation" in text.lower():
                descriptor, section_guess = f"example_process_p{page_num}", "Examples / Example process"
            else:
                descriptor = f"method_p{page_num}_{method_para_count}"
            evd_id = f"EVD_{doc_id}_p{page_num}_{descriptor}"

            # Build a test_context hyperedge (one per descriptor, on first page only)
            ctx_slug = slug(f"{doc_id}__examples__{descriptor}")
            existing = next((h for h in hyperedges if h["context_id"] == ctx_slug), None)
            if existing is None:
                example_contexts.append({
                    "record_type": "ExampleContextRecord",
                    "schema_version": "kg_pack_example_context_v1",
                    "node_type": "EXAMPLE_CONTEXT",
                    "node_id": f"EXCTX_{ctx_slug}", "context_id": ctx_slug,
                    "patent_id": doc_id, "doc_id": doc_id,
                    "example_id": section_guess,
                    "sample_id": f"shared {descriptor.replace('_', ' ')}",
                    "bundle_type": "test_context", "table_role": None,
                    "evidence_ids": [], "linked_hyperedge_ids": [],
                    "kg_source": kg_source,
                })
                hedge_id = new_hedge_id()
                example_contexts[-1]["linked_hyperedge_ids"].append(hedge_id)
                hedge = {
                    "record_type": "HyperedgeRecord", "schema_version": "kg_pack_l2_hyperedge_v1",
                    "node_type": "HYPEREDGE", "node_id": hedge_id, "hyperedge_id": hedge_id,
                    "patent_id": doc_id, "doc_id": doc_id,
                    "context_id": ctx_slug, "bundle_type": "test_context",
                    "sample_id": f"shared {descriptor.replace('_', ' ')}",
                    "example_id": section_guess,
                    "application": {"tested": "anti-corrosive zinc primer coating test",
                                    "claimed": "anti-corrosive zinc primer coating compositions for protecting iron and steel structures"},
                    "substrate": {
                        "tested": "cold rolled mild steel panel (10 x 15 cm x 1.6 mm), abrasive blasted to Sa 2½ according to ISO 8501-1, surface profile equivalent to BN 9 (Rugotest No. 3)",
                        "claimed": "iron and steel structures / metal structures"},
                    "resin": [], "additives": [], "process": [],
                    "property": {"name": "test panel preparation" if "panel" in descriptor else
                                ("cracking resistance" if "crack" in descriptor else
                                ("rust creep / corrosion resistance" if "salt" in descriptor else "process")),
                                "category": "durability" if "crack" in descriptor else
                                ("corrosion_resistance" if "salt" in descriptor else "other"),
                                "measured_or_claimed": "measured"},
                    "test_method": {"name": None, "standard_id": [],
                                    "condition": {"exposure": None, "immersion": None, "rating_scale": None, "score_max": None}},
                    "result": [],
                    "baseline": {"baseline_sample_id": None, "baseline_context_id": None,
                                 "baseline_result": None, "comparison_text": None},
                    "evidence": [], "confidence": {"overall": 0.92, "sample_binding": 0.92,
                                                    "formulation_binding": 0.0, "result_binding": 0.92},
                    "qa_flags": ["mineru_layout_based", "examples_section_only"],
                    "unresolved_fields": ["cell_bbox"], "kg_source": kg_source,
                    "source_scope": f"examples_pages_{scope_first}_{scope_last}",
                    "evidence_ids": [], "answerability": "context_bundle",
                    "projection_note": "Use this L2 record as fact source; CSV should be a view only.",
                }
                hyperedges.append(hedge)
                existing = hedge

            hedge = existing
            direct_units: list[dict] = []
            page_facts: list[dict] = []
            next_u = 1
            direct_units.append({"slot": "sample_context", "source_field": "sample_id",
                                  "name": hedge["sample_id"], "raw_text": hedge["sample_id"], "unit_id": f"u{next_u:03d}"})
            next_u += 1
            direct_units.append({"slot": "example_context", "source_field": "example_id",
                                  "name": section_guess, "raw_text": section_guess, "unit_id": f"u{next_u:03d}"})
            next_u += 1
            # substrate (if mentioned)
            if mp["has_substrate"]:
                sub_id = ent_sub_tested
                fid = new_fact_id()
                page_facts.append({
                    "record_type": "FactRecord", "schema_version": "kg_pack_fact_v1",
                    "node_type": "FACT", "node_id": fid, "fact_id": fid,
                    "patent_id": doc_id, "doc_id": doc_id,
                    "evidence_id": evd_id, "context_id": ctx_slug, "hyperedge_id": hedge["hyperedge_id"],
                    "slot": "substrate", "source_field": "substrate.tested",
                    "name": "tested_substrate", "metric": None, "role": None, "role_detail": None,
                    "value": None, "unit": None,
                    "raw_text": text[:300],
                    "amount_basis": None, "scale": None, "step_type": None,
                    "linked_canonical_entity_id": sub_id, "kg_source": kg_source,
                })
                direct_units.append({"slot": "substrate", "source_field": "substrate.tested",
                                      "name": "tested_substrate", "raw_text": text[:300], "unit_id": f"u{next_u:03d}"})
                next_u += 1
            # test_standard mentions
            for m in ISO_RE.finditer(text):
                std = m.group(0).strip()
                if len(std) > 50: continue
                sid = get_or_create_entity(std, "test_standard")
                fid = new_fact_id()
                page_facts.append({
                    "record_type": "FactRecord", "schema_version": "kg_pack_fact_v1",
                    "node_type": "FACT", "node_id": fid, "fact_id": fid,
                    "patent_id": doc_id, "doc_id": doc_id,
                    "evidence_id": evd_id, "context_id": ctx_slug, "hyperedge_id": hedge["hyperedge_id"],
                    "slot": "test_standard", "source_field": "test_method.standard_id",
                    "name": std, "metric": None, "role": None, "role_detail": None,
                    "value": std, "unit": None, "raw_text": std,
                    "amount_basis": None, "scale": None, "step_type": None,
                    "linked_canonical_entity_id": sid, "kg_source": kg_source,
                })
                direct_units.append({"slot": "test_standard", "source_field": "test_method.standard_id",
                                      "name": std, "raw_text": std, "unit_id": f"u{next_u:03d}"})
                next_u += 1
                if std not in hedge["test_method"]["standard_id"]:
                    hedge["test_method"]["standard_id"].append(std)
            # test_method name (heuristic)
            for tm_kw, tm_canon in [("Salt Spray Test", "Salt Spray Test (SST) / rust creep measurement"),
                                     ("SST", "Salt Spray Test (SST) / rust creep measurement"),
                                     ("Cracking test", "Cracking test / thermal cycling resistance test"),
                                     ("thermal cycling", "Cracking test / thermal cycling resistance test")]:
                if tm_kw.lower() in text.lower():
                    tmid = get_or_create_entity(tm_canon, "test_method", alias=tm_kw)
                    if hedge["test_method"]["name"] is None:
                        hedge["test_method"]["name"] = tm_canon
                    fid = new_fact_id()
                    page_facts.append({
                        "record_type": "FactRecord", "schema_version": "kg_pack_fact_v1",
                        "node_type": "FACT", "node_id": fid, "fact_id": fid,
                        "patent_id": doc_id, "doc_id": doc_id,
                        "evidence_id": evd_id, "context_id": ctx_slug, "hyperedge_id": hedge["hyperedge_id"],
                        "slot": "test_method", "source_field": "test_method.name",
                        "name": tm_canon, "metric": None, "role": None, "role_detail": None,
                        "value": tm_canon, "unit": None, "raw_text": tm_kw,
                        "amount_basis": None, "scale": None, "step_type": None,
                        "linked_canonical_entity_id": tmid, "kg_source": kg_source,
                    })
                    direct_units.append({"slot": "test_method", "source_field": "test_method.name",
                                          "name": tm_canon, "raw_text": tm_kw, "unit_id": f"u{next_u:03d}"})
                    next_u += 1
                    break
            # temperature / time / thickness
            for tm in TEMP_RE.findall(text):
                pcid = get_or_create_entity("temperature", "process_condition", alias=tm)
                fid = new_fact_id()
                page_facts.append({
                    "record_type": "FactRecord", "schema_version": "kg_pack_fact_v1",
                    "node_type": "FACT", "node_id": fid, "fact_id": fid,
                    "patent_id": doc_id, "doc_id": doc_id,
                    "evidence_id": evd_id, "context_id": ctx_slug, "hyperedge_id": hedge["hyperedge_id"],
                    "slot": "process_condition", "source_field": "process.conditions.temperature",
                    "name": "temperature", "metric": None, "role": None, "role_detail": None,
                    "value": tm, "unit": None, "raw_text": tm,
                    "amount_basis": None, "scale": None, "step_type": "testing",
                    "linked_canonical_entity_id": pcid, "kg_source": kg_source,
                })
                direct_units.append({"slot": "process_condition", "source_field": "process.conditions.temperature",
                                      "name": "temperature", "step_type": "testing", "raw_text": tm,
                                      "unit_id": f"u{next_u:03d}"})
                next_u += 1
            for tm in TIME_RE.findall(text):
                pcid = get_or_create_entity("time", "process_condition", alias=tm)
                fid = new_fact_id()
                page_facts.append({
                    "record_type": "FactRecord", "schema_version": "kg_pack_fact_v1",
                    "node_type": "FACT", "node_id": fid, "fact_id": fid,
                    "patent_id": doc_id, "doc_id": doc_id,
                    "evidence_id": evd_id, "context_id": ctx_slug, "hyperedge_id": hedge["hyperedge_id"],
                    "slot": "process_condition", "source_field": "process.conditions.time",
                    "name": "time", "metric": None, "role": None, "role_detail": None,
                    "value": tm, "unit": None, "raw_text": tm,
                    "amount_basis": None, "scale": None, "step_type": "testing",
                    "linked_canonical_entity_id": pcid, "kg_source": kg_source,
                })
                direct_units.append({"slot": "process_condition", "source_field": "process.conditions.time",
                                      "name": "time", "step_type": "testing", "raw_text": tm,
                                      "unit_id": f"u{next_u:03d}"})
                next_u += 1

            if not page_facts:
                # Still keep this paragraph as evidence_unit (with at least sample/example context) for completeness
                pass
            facts.extend(page_facts)
            # Find paragraph bbox
            asset_bbox_image = para.get("bbox_image")
            asset_bbox_pdf = para.get("bbox_pdf")
            evidence_units.append({
                "record_type": "EvidenceRecord",
                "schema_version": "kg_pack_l1_evidence_unit_v1",
                "node_type": "EVD", "node_id": evd_id, "evidence_id": evd_id,
                "patent_id": doc_id, "doc_id": doc_id,
                "layer": "L1_evidence_unit",
                "source": "mineru_layout_plus_llm_decomposition",
                "source_pdf": kg_input["source_pdf_name"],
                "page": page_num, "printed_page": page_num - 1,
                "section": section_guess, "evidence_type": "method_paragraph",
                "source_block_type": "method_or_process_paragraph",
                "table": None, "row": None, "column": None,
                "caption": section_guess, "bbox": None,
                "asset_path": f"pages/page_{page_num:03d}.png",
                "asset_bbox_image": asset_bbox_image, "asset_bbox_pdf": asset_bbox_pdf,
                "text_excerpt": f"{section_guess}: {text[:600]}",
                "quote_style": "structured_paragraph_summary",
                "direct_extracted_units": direct_units,
                "context_bindings": {
                    "linked_l2_context_ids": [ctx_slug],
                    "primary_l2_context_ids": [ctx_slug],
                    "inherited_by_l2_context_ids": [],
                    "linked_bundle_types": ["test_context"],
                    "linked_sample_ids": [hedge["sample_id"]],
                },
                "coverage_scope": "examples_section_tables_and_related_method_paragraphs",
                "confidence": 0.92,
                "qa_flags": ["mineru_layout_based", "examples_section_only"],
                "unresolved_fields": ["bbox"], "kg_source": kg_source,
                "trace_role": "L1_retrieval_context",
                "source_scope": f"examples_pages_{scope_first}_{scope_last}",
            })
            hedge["evidence"].append({"evidence_id": evd_id, "page": page_num,
                                       "section": section_guess,
                                       "table": None, "row": None, "column": None,
                                       "evidence_type": "method_paragraph", "printed_page": page_num - 1})
            hedge["evidence_ids"].append(evd_id)
            for c in example_contexts:
                if c["context_id"] == ctx_slug:
                    c["evidence_ids"].append(evd_id)
                    break

    # ==== prune empty hyperedges (created but never got facts) ====
    keep_ids = {h["hyperedge_id"] for h in hyperedges if h["evidence_ids"]}
    dropped = [h for h in hyperedges if h["hyperedge_id"] not in keep_ids]
    hyperedges = [h for h in hyperedges if h["hyperedge_id"] in keep_ids]
    dropped_ctx_ids = set()
    for d in dropped:
        # find and remove the matching example_context
        for ec in list(example_contexts):
            if d["hyperedge_id"] in ec.get("linked_hyperedge_ids", []):
                # remove that hedge from context; if context now empty, drop it
                ec["linked_hyperedge_ids"] = [x for x in ec["linked_hyperedge_ids"] if x != d["hyperedge_id"]]
                if not ec["linked_hyperedge_ids"] and not ec.get("evidence_ids"):
                    dropped_ctx_ids.add(ec["node_id"])
        sample_hedges.pop(next((k for k, v in sample_hedges.items() if v is d), None), None)
    example_contexts = [ec for ec in example_contexts if ec["node_id"] not in dropped_ctx_ids]

    # ==== baseline binding ====
    # For each result hyperedge whose sample is Model paint X, find a Reference paint hyperedge
    # in the SAME chain and bind baseline.
    for (sid, ci), h in sample_hedges.items():
        if h["bundle_type"] != "performance_result":
            continue
        if not sid.startswith("Model paint"):
            continue
        # Find a reference paint in same chain
        for (sid2, ci2), h2 in sample_hedges.items():
            if ci2 != ci or not sid2.startswith("Reference paint") or h2["bundle_type"] != "performance_result":
                continue
            # Take first reference result as baseline
            if h2["result"]:
                first = h2["result"][0]
                h["baseline"]["baseline_sample_id"] = sid2
                h["baseline"]["baseline_context_id"] = h2["context_id"]
                h["baseline"]["baseline_result"] = f"{first['value']} {first['unit'] or ''}".strip()
                h["baseline"]["comparison_text"] = f"baseline = {sid2} in same {chains[ci]['table_label']}"
                add_edge("HAS_BASELINE_HYPEREDGE", h["hyperedge_id"], h2["hyperedge_id"])
                break

    # ==== edges (typed) ====
    pat_id_node = pat_id
    add_edge("HAS_PROFILE", pat_id_node, profile_id)
    for e in evidence_units:
        add_edge("HAS_EVIDENCE", pat_id_node, e["node_id"])
    for ec in example_contexts:
        add_edge("HAS_EXAMPLE_CONTEXT", pat_id_node, ec["node_id"])
        for hid in ec["linked_hyperedge_ids"]:
            add_edge("CONTEXT_HAS_HYPEREDGE", ec["node_id"], hid, bundle_type=ec["bundle_type"])
    for h in hyperedges:
        add_edge("HAS_HYPEREDGE", pat_id_node, h["node_id"])
        for eid in h["evidence_ids"]:
            add_edge("SUPPORTED_BY_EVIDENCE", h["node_id"], eid)
            add_edge("EVIDENCE_SUPPORTS_HYPEREDGE", eid, h["node_id"])
        # Application / substrate edges
        ent_app_t = get_or_create_entity(h["application"]["tested"], "application")
        ent_app_c = get_or_create_entity(h["application"]["claimed"], "application")
        ent_sub_t = get_or_create_entity(h["substrate"]["tested"], "substrate")
        ent_sub_c = get_or_create_entity(h["substrate"]["claimed"], "substrate")
        add_edge("HAS_APPLICATION", h["node_id"], ent_app_t, relation_scope="tested")
        add_edge("HAS_APPLICATION", h["node_id"], ent_app_c, relation_scope="claimed")
        add_edge("HAS_SUBSTRATE", h["node_id"], ent_sub_t, relation_scope="tested")
        add_edge("HAS_SUBSTRATE", h["node_id"], ent_sub_c, relation_scope="claimed")
        # Property
        if h["property"]["name"]:
            ent_p = get_or_create_entity(h["property"]["name"], "property", category=h["property"]["category"])
            add_edge("HAS_PROPERTY", h["node_id"], ent_p)
        # Test method + standards
        if h["test_method"]["name"]:
            tmid = get_or_create_entity(h["test_method"]["name"], "test_method")
            add_edge("HAS_TEST_METHOD", h["node_id"], tmid)
        for std in h["test_method"]["standard_id"]:
            sid = get_or_create_entity(std, "test_standard")
            add_edge("HAS_TEST_STANDARD", h["node_id"], sid)
        # Resin + components
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
    # Fact edges
    for f in facts:
        add_edge("HAS_FACT", pat_id_node, f["fact_id"])
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
        "scope_note": f"This pack extracts the Examples section and directly related paragraphs/tables, PDF pages {scope_first}-{scope_last}.",
    }
    profile_rec = {
        "record_type": "PatentProfileRecord", "schema_version": "kg_pack_patent_profile_v1",
        "node_type": "PATENT_PROFILE", "node_id": profile_id,
        "profile_id": profile_id, "patent_id": doc_id, "doc_id": doc_id,
        "technology_area": "anti-corrosive zinc primer coating compositions",
        "application": "anti-corrosive primer coatings for protecting iron and steel structures",
        "coating_system": "zinc-rich primer; epoxy-based, silicate-based, polysiloxane-based, and polyurethane-based examples",
        "core_innovation": "Use of zinc particles with conductive pigments and microspheres for improved corrosion / cracking performance.",
        "systems_covered": ["epoxy-based paints", "silicate-based paints", "polysiloxane-based paints", "polyurethane-based paints"],
        "examples_scope": {"pages": f"PDF pages {scope_first}-{scope_last}",
                            "tables": sorted({c['table_label'] for c in chains}),
                            "paragraphs": ["Preparation of test panels", "Cracking test", "Salt Spray Test", "Example processes"]},
        "summary": "Examples-scope KG pack from MinerU OCR + LLM decomposition.",
        "kg_source": kg_source,
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
        "canonical_relations": write_jsonl("canonical_relations.jsonl", []),
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
        "record_type": "ManifestRecord", "schema_version": "kg_pack_manifest_v1",
        "patent_id": doc_id, "doc_id": doc_id, "title": patents_rec["title"],
        "source_pdf": patents_rec["source_pdf"],
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kg_source": kg_source,
        "scope": f"Examples section and directly related paragraphs/tables, PDF pages {scope_first}-{scope_last}",
        "files": {k: f"{k}.jsonl" for k in counts}, "counts": counts,
        "coverage": {
            "source_blocks_covered": [c["caption"][:80] for c in chains],
            "by_bundle_type": by_bundle, "by_evidence_type": by_ev_type,
            "by_fact_slot": slot_counter,
            "trace_rate": {
                "facts_with_evidence_id": sum(1 for f in facts if f["evidence_id"]) / max(1, len(facts)),
                "hyperedges_with_evidence_ids": sum(1 for h in hyperedges if h["evidence_ids"]) / max(1, len(hyperedges)),
                "evidence_units_with_asset_path": sum(1 for e in evidence_units if e["asset_path"]) / max(1, len(evidence_units)),
            },
        },
        "qa": {"review_status": "generated_for_user_review", "missing_obligation_count": 0,
                "known_limitations": [
                    "Cell-level bbox is row-bbox approximation from MinerU table bbox.",
                    "OCR source is MinerU; some chemical names have spacing/encoding noise (e.g. 'Bisphenol Aglyc' → 'Bisphenol A glyc').",
                    "Baseline binding uses first reference paint per chain; review needed for multi-baseline tables.",
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
            if h["bundle_type"] == "test_context":
                continue  # CSV omits pure test_context bundles per prompt §7
            csv_rows_written += 1
            polarity = "positive" if h["sample_id"].lower().startswith("model paint") else \
                       ("negative" if h["sample_id"].lower().startswith("reference paint") else "context")
            sj = lambda items: ";".join(items)
            fmt = lambda r: f"{r['name']}:{r.get('amount','')} {r.get('unit','') or ''}".strip()
            res = h.get("resin", []); add = h.get("additives", [])
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
                sj(fmt(r) for r in add if r.get("role_detail") and "corrosion inhibitor" in (r.get("role_detail") or "").lower()),
                sj(fmt(r) for r in add if r["role"] == "dispersant"),
                sj(fmt(r) for r in add if r["role"] == "rheology_additive"),
                sj(fmt(r) for r in add if r["role"] == "surface_additive"),
                sj(fmt(r) for r in add if r["role"] == "other" and not (r.get("role_detail") and "solvent" in (r.get("role_detail") or "").lower())),
                sj(fmt(r) for r in add if r.get("role_detail") and "solvent" in (r.get("role_detail") or "").lower()),
                "", "", "", "",   # process placeholders
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

    # ==== §6 gates ====
    NODE_INDEX: dict[str, dict] = {}
    for r in [patents_rec, profile_rec] + evidence_units + example_contexts + facts + \
              list(canonical.values()) + hyperedges:
        NODE_INDEX[r["node_id"]] = r
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
        "HAS_PROPERTY": ("HYPEREDGE", "CANONICAL_ENTITY", lambda e: e.get("entity_type") == "property"),
        "HAS_SUBSTRATE": ("HYPEREDGE", "CANONICAL_ENTITY", lambda e: e.get("entity_type") == "substrate"),
        "HAS_APPLICATION": ("HYPEREDGE", "CANONICAL_ENTITY", lambda e: e.get("entity_type") == "application"),
        "HAS_TEST_METHOD": ("HYPEREDGE", "CANONICAL_ENTITY", lambda e: e.get("entity_type") == "test_method"),
        "HAS_TEST_STANDARD": ("HYPEREDGE", "CANONICAL_ENTITY", lambda e: e.get("entity_type") == "test_standard"),
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
        print("[gates] first 5 violations:")
        for v in violations[:5]:
            print(f"   - {v}")
    print(f"[chains] {len(chains)} table chains: {[(c['table_label'], c['kind'], len(c['tables'])) for c in chains]}")
    return 0 if not violations else 1


if __name__ == "__main__":
    prep_dir = Path(sys.argv[1]).resolve()
    out_dir = Path(sys.argv[2]).resolve()
    sys.exit(main(prep_dir, out_dir))
