"""Stage 4.6 structure resolution sidecar.

This stage turns VLM chemical_candidates from register_layer1 visuals into a
reviewable structure sidecar. It keeps uncertain polymer/resin structures out
of ordinary Layer 2 table facts while still making them searchable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from ..config import SETTINGS

logger = logging.getLogger(__name__)


@dataclass
class StructureFact:
    structure_fact_id: str
    doc_id: str
    unit_id: str
    figure_id: str
    page: int | None
    label: str
    candidate_type: str
    formula: str | None
    smiles: str | None
    functional_groups: list[str]
    reaction_summary: str | None
    evidence: str
    confidence: float
    needs_validation: bool
    source_stage: str = "stage4.6"
    resolver: dict[str, Any] | None = None


def resolve_structures_for_doc(
    repo: Path,
    doc_id: str,
    *,
    enable_pubchem: bool = False,
) -> list[StructureFact]:
    """Resolve pending Layer 1 chemical candidates for one document."""
    pending_path = repo / "data" / "layer1" / doc_id / "pending_figures.jsonl"
    records = _load_pending_records(pending_path)
    facts: list[StructureFact] = []
    cache = PubChemCache(repo / "data" / "cache" / "pubchem.sqlite")

    for rec in records:
        if _is_noise_record(rec):
            continue
        candidates = rec.get("chemical_candidates") or []
        if not isinstance(candidates, list):
            continue
        for idx, candidate in enumerate(candidates, 1):
            if not isinstance(candidate, dict):
                continue
            fact = _fact_from_candidate(rec, candidate, idx)
            if enable_pubchem:
                _apply_pubchem_lookup(cache, fact)
            facts.append(fact)

    out_path = save_structure_facts(repo, doc_id, facts)
    logger.info("stage 4.6: wrote %d structure facts to %s", len(facts), out_path)
    return facts


def save_structure_facts(repo: Path, doc_id: str, facts: list[StructureFact]) -> Path:
    out_dir = repo / "data" / "extract_facts" / doc_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "structure_facts.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for fact in facts:
            f.write(json.dumps(asdict(fact), ensure_ascii=False) + "\n")
    return out_path


class PubChemCache:
    """Tiny SQLite cache for PubChem PUG-REST name lookups."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _init(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pubchem_name_cache (
                    query TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def lookup_name(self, name: str) -> dict[str, Any] | None:
        key = name.strip().casefold()
        if not key:
            return None
        cached = self._get(key)
        if cached is not None:
            return cached if cached.get("status") == "ok" else None

        url = (
            "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
            f"{quote(name)}/property/IsomericSMILES,MolecularFormula/JSON"
        )
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 404:
                self._put(key, {"status": "miss", "query": name})
                return None
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("PubChem lookup failed for %r: %s", name, exc)
            return None

        props = (
            data.get("PropertyTable", {}).get("Properties", [])
            if isinstance(data, dict) else []
        )
        if not props:
            self._put(key, {"status": "miss", "query": name})
            return None
        payload = {
            "status": "ok",
            "query": name,
            "isomeric_smiles": props[0].get("IsomericSMILES"),
            "molecular_formula": props[0].get("MolecularFormula"),
        }
        self._put(key, payload)
        return payload

    def _get(self, key: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                "SELECT payload FROM pubchem_name_cache WHERE query = ?",
                (key,),
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None

    def _put(self, key: str, payload: dict[str, Any]) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO pubchem_name_cache(query, payload, status)
                VALUES (?, ?, ?)
                ON CONFLICT(query) DO UPDATE SET
                    payload = excluded.payload,
                    status = excluded.status,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, json.dumps(payload, ensure_ascii=False), payload.get("status", "unknown")),
            )


def _load_pending_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                records.append(rec)
    return records


def _fact_from_candidate(
    rec: dict[str, Any],
    candidate: dict[str, Any],
    idx: int,
) -> StructureFact:
    label = str(candidate.get("label") or candidate.get("name") or "unknown").strip()
    smiles = candidate.get("smiles")
    formula = candidate.get("formula")
    candidate_type = str(candidate.get("candidate_type") or "unknown")
    needs_validation = bool(candidate.get("needs_validation", True))
    try:
        confidence = float(candidate.get("confidence", 0.5) or 0.5)
    except (TypeError, ValueError):
        confidence = 0.5
    if needs_validation:
        confidence = min(confidence, 0.85)

    unit_id = str(rec.get("unit_id") or "unknown")
    digest = hashlib.sha1(f"{unit_id}:{idx}:{label}".encode("utf-8")).hexdigest()[:10]
    return StructureFact(
        structure_fact_id=f"SF_{unit_id}_{idx:03d}_{digest}",
        doc_id=str(rec.get("doc_id") or ""),
        unit_id=unit_id,
        figure_id=str(rec.get("figure_id") or f"FIG_{unit_id}"),
        page=rec.get("page"),
        label=label,
        candidate_type=candidate_type,
        formula=str(formula) if formula else None,
        smiles=str(smiles) if smiles else None,
        functional_groups=[
            str(x) for x in (candidate.get("functional_groups") or []) if x
        ],
        reaction_summary=candidate.get("reaction_summary"),
        evidence=str(candidate.get("evidence") or rec.get("caption") or rec.get("vlm_description") or ""),
        confidence=max(0.0, min(1.0, confidence)),
        needs_validation=needs_validation,
        resolver={"source": "vlm_chemical_candidate", "pubchem": None},
    )


def _apply_pubchem_lookup(cache: PubChemCache, fact: StructureFact) -> None:
    if fact.candidate_type not in {"molecule", "unknown"}:
        return
    if fact.smiles and fact.formula:
        return
    if not fact.label or _GENERIC_LABEL_RE.search(fact.label):
        return
    hit = cache.lookup_name(fact.label)
    if not hit:
        if fact.resolver is not None:
            fact.resolver["pubchem"] = {"status": "miss"}
        return
    if hit.get("isomeric_smiles") and not fact.smiles:
        fact.smiles = hit["isomeric_smiles"]
    if hit.get("molecular_formula") and not fact.formula:
        fact.formula = hit["molecular_formula"]
    if fact.resolver is not None:
        fact.resolver["pubchem"] = hit
    fact.needs_validation = True
    fact.confidence = min(fact.confidence, 0.88)


def _is_noise_record(rec: dict[str, Any]) -> bool:
    text = " ".join(
        str(rec.get(key) or "")
        for key in ("subtype", "vlm_description", "caption")
    ).casefold()
    if not text:
        return True
    if _NOISE_RE.search(text):
        return True
    # "TOTAL 100" is common in real formulations; never use it alone as noise.
    if "total" in text and _FORMULATION_RE.search(text):
        return False
    return False


_NOISE_RE = re.compile(
    r"\b(search report|citation table|barcode|bibliographic|patent family|"
    r"international search|page footer|footer only)\b",
    re.IGNORECASE,
)
_FORMULATION_RE = re.compile(r"\b(formulation|composition|wt\.?%|parts?|resin|additive|solvent)\b", re.I)
_GENERIC_LABEL_RE = re.compile(r"^(compound|polymer|resin|mixture|product|formula)\s*$", re.I)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Resolve Stage 4.6 structure candidates")
    parser.add_argument("doc_ids", nargs="*", help="Doc IDs; default reads data/layer1/*")
    parser.add_argument("--pubchem", action="store_true", help="Enable PubChem PUG-REST lookups")
    args = parser.parse_args(argv)

    repo = SETTINGS.project_root
    doc_ids = args.doc_ids or sorted(p.name for p in (repo / "data" / "layer1").glob("*") if p.is_dir())
    total = 0
    for doc_id in doc_ids:
        total += len(resolve_structures_for_doc(repo, doc_id, enable_pubchem=args.pubchem))
    print(f"Stage 4.6 structure facts: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
