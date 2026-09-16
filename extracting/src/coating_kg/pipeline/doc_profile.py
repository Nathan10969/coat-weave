"""Stage 0.6 document-level coating profile.

The profile is deliberately doc-level and conservative. It gives trend and
context-inheritance code a single place to read the patent's likely coating
family, application, binder, cure mechanism, and substrate scope without
pretending that these are row-level facts.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential

try:
    from openai import OpenAI  # type: ignore[import-not-found]
    _OPENAI_AVAILABLE = True
except ImportError:
    OpenAI = None  # type: ignore[assignment]
    _OPENAI_AVAILABLE = False

from ..config import SETTINGS

logger = logging.getLogger(__name__)


_PROFILE_PROMPT = """You are classifying a coating patent at document level.

Return STRICT JSON only. Do not invent a coating use when the patent is really
polymer recycling, foam, adhesive, surfactant, agrochemical, or generic
chemistry.

Schema:
{
  "coating_relevance": "coating | non_coating | adjacent | uncertain",
  "coating_relevance_reason": "short reason",
  "coating_family": {"label": "...", "canonical_id": "... or null", "confidence": 0.0, "evidence": "..."},
  "application_family": {"label": "...", "canonical_id": "... or null", "confidence": 0.0, "evidence": "..."},
  "binder_family": {"label": "...", "canonical_id": "... or null", "confidence": 0.0, "evidence": "..."},
  "cure_mechanism": {"label": "...", "canonical_id": "... or null", "confidence": 0.0, "evidence": "..."},
  "substrate_scope": {"label": "...", "canonical_id": "... or null", "confidence": 0.0, "evidence": "..."},
  "evidence_spans": [
    {"field": "application_family", "source": "title|abstract|description", "text": "..."}
  ]
}

Metadata:
{metadata_json}

Front matter / early description:
{front_text}
"""


def extract_doc_profile(
    layout: dict[str, Any],
    doc_id: str,
    meta: dict[str, Any] | None = None,
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    max_chars: int = 20000,
) -> dict[str, Any]:
    """Extract and return a Stage 0.6 coating profile."""
    meta = meta or {}
    text = _front_text(layout, max_chars=max_chars)
    fallback = _rule_profile(doc_id, meta, text)

    api_key = SETTINGS.openai.api_key if api_key is None else api_key
    base_url = SETTINGS.openai.base_url if base_url is None else base_url
    model = SETTINGS.models.qwen_text if model is None else model
    if not (_OPENAI_AVAILABLE and api_key):
        return fallback

    client = OpenAI(api_key=api_key, base_url=base_url)
    prompt = (
        _PROFILE_PROMPT
        .replace("{metadata_json}", json.dumps(meta, ensure_ascii=False, indent=2))
        .replace("{front_text}", text[:max_chars])
    )
    try:
        payload = json.loads(_call_qwen(client, model, prompt))
    except Exception as exc:  # noqa: BLE001
        logger.warning("doc_profile LLM failed for %s: %s; using rules", doc_id, exc)
        return fallback

    profile = _normalize_profile(doc_id, payload, fallback)
    return profile


def save_doc_profile(repo: Path, doc_id: str, profile: dict[str, Any]) -> Path:
    out_dir = repo / "data" / "patents"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{doc_id}__coating_profile.json"
    out_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def load_doc_profile(repo: Path, doc_id: str) -> dict[str, Any] | None:
    path = repo / "data" / "patents" / f"{doc_id}__coating_profile.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("cannot read doc_profile for %s: %s", doc_id, exc)
        return None
    return data if isinstance(data, dict) else None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20), reraise=True)
def _call_qwen(client: Any, model: str, prompt: str) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.0,
        max_tokens=2048,
    )
    content = resp.choices[0].message.content
    if not content:
        raise RuntimeError("empty content from doc_profile model")
    return content


def _front_text(layout: dict[str, Any], *, max_chars: int) -> str:
    data = layout.get("data") or []
    blocks = data if isinstance(data, list) else data.get("para_blocks", [])
    lines: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") not in {"text", "title"}:
            continue
        text = (block.get("text") or block.get("content") or "").strip()
        if text:
            lines.append(text)
        if sum(len(line) + 2 for line in lines) >= max_chars:
            break
    return "\n\n".join(lines)[:max_chars]


def _rule_profile(doc_id: str, meta: dict[str, Any], text: str) -> dict[str, Any]:
    source = " ".join(
        str(x or "")
        for x in (meta.get("title"), meta.get("abstract"), text[:6000])
    )
    low = source.casefold()
    gate_status = meta.get("coating_gate_status")
    if gate_status == "non_coating":
        relevance = "non_coating"
    elif _has_any(low, ("recycling", "depolymeriz", "caprolactam", "rigid foam", "polyisocyanurate foam")):
        relevance = "adjacent"
    elif _has_any(low, ("coating", "paint", "clearcoat", "basecoat", "topcoat", "primer", "lacquer")):
        relevance = "coating"
    else:
        relevance = "uncertain"

    return {
        "doc_id": doc_id,
        "coating_relevance": relevance,
        "coating_relevance_reason": meta.get("is_coating_reason") or "rule-based front-matter profile",
        "coating_family": _field_from_rules(low, _COATING_FAMILY_RULES),
        "application_family": _field_from_rules(low, _APPLICATION_RULES),
        "binder_family": _field_from_rules(low, _BINDER_RULES),
        "cure_mechanism": _field_from_rules(low, _CURE_RULES),
        "substrate_scope": _field_from_rules(low, _SUBSTRATE_RULES),
        "evidence_spans": _evidence_spans(source),
        "profile_source": "rules",
    }


def _normalize_profile(
    doc_id: str,
    payload: dict[str, Any],
    fallback: dict[str, Any],
) -> dict[str, Any]:
    profile = dict(fallback)
    profile["doc_id"] = doc_id
    for key in (
        "coating_relevance",
        "coating_relevance_reason",
        "coating_family",
        "application_family",
        "binder_family",
        "cure_mechanism",
        "substrate_scope",
        "evidence_spans",
    ):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            profile[key] = value
    for key in ("coating_family", "application_family", "binder_family", "cure_mechanism", "substrate_scope"):
        profile[key] = _normalize_field(profile.get(key), fallback.get(key))
    profile["profile_source"] = "llm"
    return profile


def _normalize_field(value: Any, fallback: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        out = dict(value)
    elif isinstance(value, str) and value.strip():
        out = {"label": value.strip(), "canonical_id": None, "confidence": 0.6, "evidence": ""}
    else:
        out = dict(fallback) if isinstance(fallback, dict) else {}
    out.setdefault("label", "unknown")
    out.setdefault("canonical_id", None)
    out.setdefault("confidence", 0.0)
    out.setdefault("evidence", "")
    try:
        out["confidence"] = max(0.0, min(1.0, float(out.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        out["confidence"] = 0.0
    return out


def _field_from_rules(text: str, rules: list[tuple[tuple[str, ...], str, str]]) -> dict[str, Any]:
    for needles, label, canonical_id in rules:
        if _has_any(text, needles):
            return {
                "label": label,
                "canonical_id": canonical_id,
                "confidence": 0.78,
                "evidence": _first_evidence(text, needles),
            }
    return {"label": "unknown", "canonical_id": None, "confidence": 0.0, "evidence": ""}


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _first_evidence(text: str, needles: tuple[str, ...]) -> str:
    for needle in needles:
        idx = text.find(needle)
        if idx >= 0:
            return text[max(0, idx - 80) : idx + 120].strip()
    return ""


def _evidence_spans(text: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for field, rules in (
        ("application_family", _APPLICATION_RULES),
        ("binder_family", _BINDER_RULES),
        ("substrate_scope", _SUBSTRATE_RULES),
    ):
        for needles, _label, _canonical in rules:
            ev = _first_evidence(text.casefold(), needles)
            if ev:
                out.append({"field": field, "source": "front_matter", "text": ev[:240]})
                break
    return out


_APPLICATION_RULES = [
    (("fouling control", "antifouling", "anti-fouling", "marine coating"), "marine fouling-control coating", "APP_marine_fouling_control"),
    (("wind turbine", "wind blade", "rotor blade", "leading edge protection"), "wind turbine blade coating", "APP_wind_blade_coating"),
    (("automotive", "clearcoat", "basecoat", "oem coating"), "automotive coating", "APP_automotive_oem_clearcoat"),
    (("powder coating",), "powder coating", "APP_powder_coating"),
    (("corrosion", "anti-corrosion", "anticorrosion"), "anti-corrosion coating", "APP_anti_corrosion_coating"),
    (("floor coating",), "floor coating", "APP_floor_coating"),
]

_COATING_FAMILY_RULES = [
    (("clearcoat",), "clearcoat", "APP_automotive_oem_clearcoat"),
    (("basecoat",), "basecoat", "APP_automotive_basecoat"),
    (("primer",), "primer", "APP_primer"),
    (("powder coating",), "powder coating", "APP_powder_coating"),
    (("fouling control", "antifouling", "anti-fouling"), "fouling-control coating", "APP_marine_fouling_control"),
]

_BINDER_RULES = [
    (("polyurethane", "polyisocyanate", "isocyanate"), "polyurethane/isocyanate binder", "MAT_polyurethane"),
    (("acrylic", "acrylate", "methacrylate"), "acrylic binder", "MAT_acrylic_resin"),
    (("epoxy",), "epoxy binder", "MAT_epoxy_resin"),
    (("polyester",), "polyester binder", "MAT_polyester_resin"),
    (("alkyd",), "alkyd binder", "MAT_alkyd_resin"),
    (("silicone", "polysiloxane"), "silicone binder", "MAT_silicone_resin"),
]

_CURE_RULES = [
    (("uv cure", "uv-cur", "actinic radiation"), "UV cure", "PROC_uv_cure"),
    (("thermal cure", "bake", "baked", "stoved"), "thermal cure", "PROC_thermal_cure"),
    (("ambient cure", "room temperature cure"), "ambient cure", "PROC_ambient_cure"),
    (("isocyanate", "crosslinker", "cross-linker"), "isocyanate crosslinking", "PROC_isocyanate_crosslinking"),
]

_SUBSTRATE_RULES = [
    (("steel", "metal", "aluminum", "aluminium", "tinplate"), "metal", "SUB_metal"),
    (("plywood", "wood"), "wood", "SUB_wood"),
    (("glass",), "glass", "SUB_glass"),
    (("plastic", "polypropylene", "polycarbonate", "polyamide"), "plastic", "SUB_plastic"),
    (("concrete", "cement"), "concrete", "SUB_concrete"),
    (("blade", "wind turbine blade", "rotor blade"), "wind turbine blade", "SUB_wind_turbine_blade"),
]
