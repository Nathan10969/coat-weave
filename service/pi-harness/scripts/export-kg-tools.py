"""Export the existing KG schemas for Pi; no routing or retrieval implementation."""
from __future__ import annotations

import json
from pathlib import Path
import sys

SERVICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVICE / "core"))

from kg_contract import load_tool_contract, openai_tool_parameters, retrieval_policy
from query_classification import load_registry


DESCRIPTIONS = {
    "kg.lookup_vocabulary": "Look up material names, aliases, canonical IDs and classification definitions in the existing KG vocabulary. Returns vocabulary candidates, not patent evidence.",
    "kg.hybrid_search": "Search the existing KG using the user's original question and explicitly requested filters. Returns ranked candidates, exact/adjacent evidence boundaries and filter receipts. Expand selected object IDs before answering sample-level facts; do not expand candidate-only listings.",
    "kg.expand_hyperedge_multihop": "Expand selected source-qualified object IDs from KG retrieval into full sample-associated facts, evidence and provenance. Partial failures remain explicit. "
        f"Expand at most {retrieval_policy()['expand_top_k']} object IDs per call.",
    "kg.sql_aggregate": "Run the existing KG aggregation for counts, distinct values, groups and distributions. Use target=doc_id for patent counts and retain all user filters. Do not infer corpus totals from search top-k.",
    "kg.doc_field_scan": "Inspect structured materials, process, tests, results and evidence for explicit KG patent document IDs.",
}


def catalog() -> dict:
    contract = load_tool_contract()
    return {
        "contract_version": contract["version"],
        "classification_registry_version": load_registry()["version"],
        "retrieval_policy": retrieval_policy(),
        "tools": [
            {"name": name.replace(".", "_"), "endpoint": name,
             "description": DESCRIPTIONS[name], "parameters": openai_tool_parameters(name)}
            for name in contract["tools"]
        ],
    }


if __name__ == "__main__":
    output = SERVICE / "pi-harness" / "src" / "kg-tools-schema.json"
    output.write_text(json.dumps(catalog(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Exported five Pi tool schemas from the existing KG contract and registry.")
