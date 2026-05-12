"""Batch driver for stage 1.5 — passage_extractor (Layer 1).

Runs Tier A (alias matching) on every doc_id under data/mineru_output/.
Tier B (LLM NER) and Tier C (BGE-M3 embedding) are TODO for V2.0.5.

Output: data/passages/<doc_id>/passages.json (one file per PDF).

Usage:
    cd G:\\coating_1\\coating_kg
    python scripts/run_passage_extractor.py
    # or limit to first N docs:
    python scripts/run_passage_extractor.py --limit 10
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from coating_kg.config import SETTINGS  # noqa: E402
from coating_kg.pipeline.passage_extractor import (  # noqa: E402
    AliasMatcher,
    load_ontology_aliases,
    run_for_doc,
)


def _build_demo_alias_dict() -> dict[str, list[str]]:
    """Until ontology alias JSON is shipped, use a demo dict for V1.2.5
    smoke testing. Replace with real ontology in V2.0.5.

    Coverage: ~50 high-frequency coating canonicals.
    """
    return {
        # Resins
        "MAT_polyurethane": ["polyurethane", "PU", "polyurethane resin", "PUR", "聚氨酯"],
        "MAT_acrylate": ["acrylate", "acrylic", "(meth)acrylate", "丙烯酸酯"],
        "MAT_polyester": ["polyester", "polyester resin", "聚酯"],
        "MAT_epoxy": ["epoxy", "epoxy resin", "环氧"],
        "MAT_alkyd": ["alkyd", "alkyd resin", "醇酸"],

        # Crosslinkers / curing agents
        "MAT_polyisocyanate": ["polyisocyanate", "isocyanate"],
        "MAT_HDI": ["HDI", "hexamethylene diisocyanate", "HDI trimer"],
        "MAT_IPDI": ["IPDI", "isophorone diisocyanate"],
        "MAT_melamine": ["melamine", "melamine formaldehyde", "三聚氰胺"],

        # Pigments / fillers
        "MAT_titanium_dioxide": ["titanium dioxide", "TiO2", "二氧化钛"],
        "MAT_silica": ["silica", "silicon dioxide", "SiO2", "硅石", "二氧化硅"],
        "MAT_carbon_black": ["carbon black", "炭黑"],

        # Solvents
        "MAT_butyl_acetate": ["butyl acetate", "BAc"],
        "MAT_xylene": ["xylene", "二甲苯"],
        "MAT_water": ["water", "H2O"],

        # Additives
        "MAT_silane_coupling_agent": ["silane", "silane coupling agent", "硅烷偶联剂"],
        "MAT_tin_catalyst": ["dibutyltin dilaurate", "DBTDL", "tin octoate", "stannous octoate"],
        "MAT_BYK": ["BYK", "BYK-"],
        "MAT_Tego": ["Tego", "Tego Wet", "TegoWet"],
        "MAT_EFKA": ["EFKA"],
        "MAT_Hydropalat": ["Hydropalat", "Hydropalat WE"],

        # Properties
        "PROP_adhesion_cross_cut": ["cross-cut adhesion", "cross cut adhesion", "划格附着力"],
        "PROP_pendulum_hardness": ["pendulum hardness", "Konig hardness", "摆杆硬度"],
        "PROP_pencil_hardness": ["pencil hardness", "铅笔硬度"],
        "PROP_gloss": ["gloss", "20° gloss", "60° gloss", "光泽"],
        "PROP_water_contact_angle": ["water contact angle", "WCA", "接触角"],
        "PROP_scratch_resistance": ["scratch resistance", "mar resistance", "耐划伤"],
        "PROP_chemical_resistance": ["chemical resistance", "耐化学性"],
        "PROP_uv_resistance": ["UV resistance", "weathering", "耐候性"],

        # Applications
        "APP_automotive_oem_clearcoat": ["automotive OEM clearcoat", "OEM clearcoat", "automotive clearcoat"],
        "APP_automotive_refinish": ["automotive refinish", "refinish coating"],
        "APP_industrial_protective": ["industrial protective", "protective coating"],
        "APP_architectural": ["architectural coating", "建筑涂料"],
        "APP_marine": ["marine coating", "船舶涂料"],
        "APP_powder_coating": ["powder coating", "粉末涂料"],

        # Substrates
        "SUB_metal": ["metal substrate", "steel", "aluminum", "aluminium"],
        "SUB_plastic": ["plastic substrate", "PP", "polypropylene"],
        "SUB_concrete": ["concrete", "mineral substrate"],

        # Processes
        "PROC_spray_apply": ["spray", "spraying", "airless spray", "喷涂"],
        "PROC_brush_apply": ["brush", "brushing"],
        "PROC_dip_coat": ["dip coat", "dipping"],
        "PROC_thermal_cure": ["thermal cure", "baking", "oven cure", "热固化"],
        "PROC_uv_cure": ["UV cure", "UV curing", "紫外固化"],
        "PROC_ambient_cure": ["ambient cure", "air dry", "room temperature cure"],

        # Test methods
        "TEST_ISO_2409": ["ISO 2409", "ISO2409"],
        "TEST_ASTM_D3359": ["ASTM D3359", "ASTM D 3359"],
        "TEST_DIN_53157": ["DIN 53157", "DIN53157"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Stage 1.5 batch — Layer 1 PassageHyperedge extraction")
    ap.add_argument("--limit", type=int, default=0, help="Process only first N docs (0 = all)")
    ap.add_argument(
        "--ontology",
        type=Path,
        default=None,
        help="Path to ontology aliases JSON. If not given, uses demo dict.",
    )
    ap.add_argument(
        "--enable-tier-b",
        action="store_true",
        help="Enable Tier B (qwen-plus LLM NER for synonym variants). Costs ~¥0.02/triggered passage.",
    )
    args = ap.parse_args()

    mineru_dir = SETTINGS.mineru_output_dir
    if not mineru_dir.exists():
        print(f"ERROR: MinerU dir does not exist: {mineru_dir}", file=sys.stderr)
        return 2

    # Build alias matcher
    if args.ontology and args.ontology.exists():
        alias_dict_flat = load_ontology_aliases(args.ontology)
        print(f"Loaded {len(alias_dict_flat)} aliases from {args.ontology}")
    else:
        demo = _build_demo_alias_dict()
        alias_dict_flat = {}
        for canonical, aliases in demo.items():
            for a in aliases:
                alias_dict_flat[a.lower()] = canonical
        print(f"Using demo alias dict: {len(demo)} canonicals, {len(alias_dict_flat)} total aliases")

    matcher = AliasMatcher(alias_dict_flat)

    # Discover doc_ids
    doc_ids = sorted(p.name for p in mineru_dir.iterdir() if p.is_dir() and not p.name.startswith("_"))
    if args.limit > 0:
        doc_ids = doc_ids[: args.limit]

    print()
    print("=" * 70)
    tiers_str = "A+B (Tier B uses qwen-plus)" if args.enable_tier_b else "A only"
    print(f"Stage 9.5 — passage_extractor ({tiers_str})")
    print("=" * 70)
    print(f"  MinerU dir:  {mineru_dir}")
    print(f"  Output dir:  {REPO / 'data' / 'passages'}")
    print(f"  Doc count:   {len(doc_ids)}")
    print()

    overall_t0 = time.time()
    n_total_passages = 0
    n_total_figures = 0
    n_total_entities = 0
    failures: list[str] = []

    for i, doc_id in enumerate(doc_ids, 1):
        t0 = time.time()
        try:
            n_pass, n_fig, n_ent = run_for_doc(
                repo=REPO,
                mineru_dir=mineru_dir,
                doc_id=doc_id,
                alias_matcher=matcher,
                enable_tier_b=args.enable_tier_b,
            )
            n_total_passages += n_pass
            n_total_figures += n_fig
            n_total_entities += n_ent
            elapsed = time.time() - t0
            print(
                f"  [{i:3d}/{len(doc_ids)}] {doc_id}: "
                f"{n_pass} passages, {n_fig} figures, {n_ent} entity hits ({elapsed:.1f}s)"
            )
        except Exception as exc:
            elapsed = time.time() - t0
            failures.append(doc_id)
            print(f"  [{i:3d}/{len(doc_ids)}] [FAIL] {doc_id}: {type(exc).__name__}: {str(exc)[:100]}", file=sys.stderr)

    elapsed = time.time() - overall_t0
    print()
    print("=" * 70)
    print(f"Done in {elapsed:.0f}s")
    print(f"  total passages: {n_total_passages}")
    print(f"  total figures (Layer 1): {n_total_figures}")
    print(f"  total entity hits: {n_total_entities}")
    print(f"  avg entities/passage: {n_total_entities/max(n_total_passages,1):.1f}")
    print(f"  failures: {len(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
