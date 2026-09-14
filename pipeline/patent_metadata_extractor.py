"""Stage 0.5：从 MinerU 首页输出抽专利 metadata（applicant / dates / title / IPC）。

V1.2.5 新增:
  - is_coating_patent (4 信号算法): IPC + title + abstract + anti-keyword
  - section_split_mode（由上游 pipeline 在 section 检测后填）

PCT 首页字段标识高度规范（(10) / (22) / (54) / (71) 等），但 MinerU OCR 会有
噪声（全角括号、缺空格、字符替换）。我们把首页文本喂给 Qwen-Plus + 严格 JSON
schema，比纯 regex 抗 OCR 噪声更稳。

输出: data/patents/<doc_id>__patent_meta.json
成本: ~5-10 sec/篇, ~¥0.005/篇。348 篇 ≈ ¥2。

用法:
    from coating_kg.pipeline.patent_metadata_extractor import (
        extract_patent_metadata, is_coating_patent, save_patent_metadata,
    )
    meta = extract_patent_metadata(content_list_path, doc_id)
    save_patent_metadata(repo_root, doc_id, meta)
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


_PROMPT = """You are extracting structured metadata from the first page of a PCT
international patent publication. The first page follows a fixed layout
with bracketed field codes like (10), (21), (22), (43), (51), (54), (71),
(72), etc. OCR may have introduced minor noise (full-width parens, missing
spaces, character substitutions like 'Muinster' for 'Münster').

Output STRICT JSON only — no markdown fence, no commentary. Schema:

{
  "publication_number": "<e.g. 'WO 2026/077939 A1'>",
  "publication_date":   "<ISO YYYY-MM-DD, e.g. '2026-04-16'>",
  "filing_date":        "<ISO YYYY-MM-DD or null>",
  "priority_date":      "<ISO YYYY-MM-DD of the EARLIEST listed (30) item, or null>",
  "application_number": "<e.g. 'PCT/EP2025/078741' or null>",
  "applicant":          "<name only, e.g. 'BASF COATINGS GMBH' (no address)>",
  "applicant_country":  "<2-letter ISO country code from the bracket like '[DE/DE]', e.g. 'DE'>",
  "inventor":           ["<inventor 1 surname, given names>", "<inventor 2>", ...],
  "title":              "<title from (54), preserve original casing>",
  "ipc_codes":          ["<e.g. 'C08G 18/72'>", "<e.g. 'C09D 175/04'>"],
  "abstract":           "<first 200 chars of (57) abstract, or null>"
}

Rules:
- All dates: convert "16 April 2026" or "16.04.2026" or "(16.04.2026)" to ISO "2026-04-16".
- (30) Priority Data may list multiple items "<num> <date> <country>"; pick the
  EARLIEST date, ignore the others.
- If a field is genuinely missing, emit null (or empty list for ipc/inventor).
- "applicant_country": from the bracket after the company name, format like
  "[DE/DE]" — first 2 letters. If absent, null.
- "inventor": parse from "(72)Inventors:" line. Each inventor follows the
  pattern "SURNAME,Givennames; <address>." — keep only "Surname, Given names",
  drop the address. Multiple inventors separated by ".", ";" or "and".
- Do NOT include the agent (74) as an inventor.
- DO NOT invent values not in the source.

FIRST-PAGE TEXT
===============
{first_page_text}
"""


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
        raise RuntimeError("empty content from model")
    return content


def _load_first_page_text(content_list_path: Path) -> str:
    """Load the first text-bearing page from MinerU content_list.

    Some online MinerU outputs start page_idx at 1, or emit a blank cover page
    before the patent front page.  Treating only page_idx=0 as the first page
    makes those documents fall into an unnecessarily expensive fallback path.
    """
    raw = json.loads(content_list_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"unexpected content_list shape: {type(raw)}")
    by_page: dict[int, list[str]] = {}
    for block in raw:
        page_idx = block.get("page_idx")
        if not isinstance(page_idx, int):
            page_idx = 0
        text = (block.get("text") or "").strip()
        if text:
            by_page.setdefault(page_idx, []).append(text)

    if not by_page:
        return ""
    page0 = "\n".join(by_page.get(0, []))
    if len(page0) >= 50:
        return page0
    for page_idx in sorted(by_page):
        page_text = "\n".join(by_page[page_idx])
        if len(page_text) >= 50:
            return page_text
    return "\n".join(by_page[sorted(by_page)[0]])


def _load_document_text(content_list_path: Path, *, max_chars: int = 4000, max_pages: int = 3) -> str:
    """Load a small front-matter fallback, not the whole document."""
    raw = json.loads(content_list_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"unexpected content_list shape: {type(raw)}")
    by_page: dict[int, list[str]] = {}
    for block in raw:
        page_idx = block.get("page_idx")
        if not isinstance(page_idx, int):
            page_idx = 0
        text = (block.get("text") or "").strip()
        if text:
            by_page.setdefault(page_idx, []).append(text)

    lines: list[str] = []
    total = 0
    for page_idx in sorted(by_page)[:max_pages]:
        for text in by_page[page_idx]:
            lines.append(text)
            total += len(text)
            if total >= max_chars:
                return "\n".join(lines)[:max_chars]
    return "\n".join(lines)[:max_chars]


def _light_regex_fallback(first_page_text: str, doc_id: str) -> dict[str, Any]:
    """LLM 调不通时的 regex 兜底。只能抽规则严格的字段。"""
    out: dict[str, Any] = {
        "patent_id": doc_id,
        "publication_number": None,
        "publication_date": None,
        "filing_date": None,
        "priority_date": None,
        "application_number": None,
        "applicant": None,
        "applicant_country": None,
        "inventor": [],
        "title": None,
        "ipc_codes": [],
        "abstract": None,
    }
    # (10) Publication Number
    m = re.search(r"\(\s*10\s*[\)）]\s*International Publication Number\s*([A-Z0-9 /]+)", first_page_text)
    if m:
        out["publication_number"] = m.group(1).strip()
    # 日期：形如 "(43) ... 16 April 2026 (16.04.2026)"
    iso_re = re.compile(r"\((\d{2})\.(\d{2})\.(\d{4})\)")
    for code, key in [(43, "publication_date"), (22, "filing_date")]:
        m2 = re.search(rf"\(\s*{code}\s*[\)）].{{0,300}}", first_page_text)
        if m2:
            iso = iso_re.search(m2.group(0))
            if iso:
                d, mo, y = iso.groups()
                out[key] = f"{y}-{mo}-{d}"
    # (54) Title
    m = re.search(r"\(\s*54\s*[\)）]\s*Title:\s*(.+?)(?=\(\s*\d+\s*[\)）]|\Z)", first_page_text, re.DOTALL)
    if m:
        out["title"] = m.group(1).strip()
    # (51) IPC list
    m = re.search(r"\(\s*51\s*[\)）].*?Classification:\s*\n?(.+?)\n", first_page_text, re.DOTALL)
    if m:
        codes = re.findall(r"[A-Z]\d{2}[A-Z]\s*\d+/\d+", m.group(1))
        out["ipc_codes"] = list(dict.fromkeys(codes))  # 去重保序
    # (71) Applicant
    m = re.search(r"\(\s*71\s*[\)）][^A-Z]{0,5}Applicant:\s*([A-Z &\-\.]+)", first_page_text)
    if m:
        out["applicant"] = m.group(1).strip().rstrip(",.")
    return out


# ---------------------------------------------------------------------------
# V1.2.5: is_coating_patent 4 信号算法
# ---------------------------------------------------------------------------

# 强信号：title 含任一关键词 → 涂料
_COATING_TITLE_KEYWORDS = (
    "coating", "paint", "primer", "clearcoat", "basecoat", "topcoat",
    "lacquer", "varnish", "powder coat", "electrocoat", "anti-corrosion",
    "anticorrosion", "corrosion protection",
    "涂料", "涂层", "清漆", "底漆", "面漆", "电泳漆",
)

# 强信号：IPC 类前缀任一 → 涂料
_PRIMARY_COATING_IPC = ("C09D", "C09K", "B05D", "B05B")

# 弱信号：还需 abstract 协同
_SECONDARY_COATING_IPC = ("C08G", "C08L", "C08K", "C09J", "C25D")

# 反信号：title 或 abstract 含任一 → 非涂料（CO2 / detergent 等）
_ANTI_COATING_KEYWORDS = (
    # CO2 capture / gas treatment family
    "co2 capture", "co2 removal", "carbon capture",
    "co2 loading", "absorption capacity",            # ★ V1.2.5 nit B fix
    "amine purification", "amine recovery",
    "amine solution", "amine storage",               # ★ V1.2.5 nit B fix
    "transportation and storage of amine",           # ★ V1.2.5 nit B fix (WO2026057367A1)
    "gas treatment", "flue gas",                     # ★ V1.2.5 nit B fix
    # surfactant / detergent / personal care
    "alkoxylated diamine", "biodegradable surfactant",
    "detergent composition", "fabric softener",
    # agriculture
    "fertilizer", "pesticide", "herbicide",
    # personal care / pharma / food
    "fragrance", "perfume composition", "personal care",
    "drug delivery", "pharmaceutical",
    "food additive",
)


def is_coating_patent(
    ipc_codes: list[str] | None,
    title: str | None,
    abstract: str | None,
) -> tuple[bool, str]:
    """4 信号分类器 — 涂料专利 vs imposter (CO2 / detergent / etc.)。

    Returns (is_coating, reason)。reason 用于调试 false neg/pos。

    判定顺序（precision over recall）:
      Step 1 — title/abstract 含 anti-keyword → False（拦 CO2/diamine 等）
      Step 2 — primary 涂料 IPC (C09D/C09K/B05D/B05B) → True
      Step 3 — title 含涂料关键词 → True
      Step 4 — secondary IPC (C08G/C08L/C08K/C09J/C25D) + abstract 含涂料词 → True
      Step 5 — metadata 全空 (无 IPC、无 title、无 abstract) → True (defer)
      Step 6 — 无任何信号 → False

    11 篇已知 patent dry-run 验证 (5 modern + 6 old):
      WO2026077939A1 (C09D 175/04, CLEARCOAT) → True (primary IPC)
      WO2026052438A1 (C09D 175/14)            → True (primary IPC)
      WO2026057741A1 (C09D 5/16)              → True (primary IPC)
      WO2026068329A1 (alkoxylated diamines)   → False (anti-keyword)
      WO2026057366A1 (CO2 capture)            → False (anti-keyword)
      WO2026057367A1 (CO2 capture)            → False (anti-keyword)
      WO1989008675A1 (refinish primer)        → True (title keyword "primer")
      老专利 metadata 空                        → True (defer)
    """
    title_lower = (title or "").lower()
    abstract_lower = (abstract or "").lower()
    text = title_lower + " " + abstract_lower

    # Step 1 — 反信号优先 (precision over recall)
    for kw in _ANTI_COATING_KEYWORDS:
        if kw in text:
            return (False, f"anti-keyword: {kw!r}")

    # Step 2 — primary IPC
    for ipc in (ipc_codes or []):
        ipc_clean = (ipc or "").strip()
        for prefix in _PRIMARY_COATING_IPC:
            if ipc_clean.startswith(prefix):
                return (True, f"primary IPC: {ipc_clean}")

    # Step 3 — title 含涂料关键词
    for kw in _COATING_TITLE_KEYWORDS:
        if kw in title_lower:
            return (True, f"title keyword: {kw!r}")

    # Step 4 — secondary IPC 或 abstract 含涂料词（V1.2.5 改 OR：拉高 recall，
    # 接受少量 false positive，比如 polyester compostable / battery cathode 这类
    # 偶尔会过 gate。下游 stage 9 CSV 可对 fact 量级做 sanity 检查再剔。）
    has_secondary = any(
        (ipc or "").strip().startswith(_SECONDARY_COATING_IPC)
        for ipc in (ipc_codes or [])
    )
    abstract_has_coating = any(kw in abstract_lower for kw in _COATING_TITLE_KEYWORDS)
    if has_secondary:
        return (True, f"secondary IPC (OR rule)")
    if abstract_has_coating:
        return (True, "abstract coating keyword (OR rule)")

    # Step 5 — metadata 全空 → defer
    if not ipc_codes and not title and not abstract:
        return (True, "empty metadata (defer to downstream filter)")

    # Step 6 — 无涂料信号
    return (False, "no coating signal")


def extract_patent_metadata(
    content_list_path: Path,
    doc_id: str,
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """端到端 metadata 抽取。

    读首页文本 → qwen-plus 严格 JSON schema → 解析；LLM 调不通时回退到 regex。

    Returns 跟 prompt schema 一致的 dict，外加 ``patent_id``。
    """
    text = _load_first_page_text(content_list_path)
    if len(text) < 50:
        fallback_text = _load_document_text(content_list_path)
        if len(fallback_text) < 50:
            raise ValueError(f"{doc_id} has <50 chars of text in MinerU content_list")
        logger.warning(
            "first page of %s has only %d chars; using document text fallback for metadata",
            doc_id,
            len(text),
        )
        text = fallback_text

    api_key = api_key or SETTINGS.openai.api_key
    base_url = base_url or SETTINGS.openai.base_url
    model = model or SETTINGS.models.qwen_text

    fallback = _light_regex_fallback(text, doc_id)

    if not (_OPENAI_AVAILABLE and api_key):
        logger.warning("No OpenAI client available; returning regex-only metadata for %s", doc_id)
        _annotate_coating_classification(fallback)
        return fallback

    client = OpenAI(api_key=api_key, base_url=base_url)
    prompt = _PROMPT.replace("{first_page_text}", text)

    try:
        raw = _call_qwen(client, model, prompt)
        payload = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM metadata extraction failed for %s: %s; using regex fallback", doc_id, exc)
        _annotate_coating_classification(fallback)
        return fallback

    # 合并：优先 LLM 输出，缺字段时用 regex 兜底
    merged: dict[str, Any] = {"patent_id": doc_id}
    for key, default in fallback.items():
        if key == "patent_id":
            continue
        llm_val = payload.get(key)
        if llm_val in (None, "", []):
            merged[key] = default
        else:
            merged[key] = llm_val

    # V1.2.5: 分类涂料 vs imposter (CO2 / detergent 等)
    _annotate_coating_classification(merged)
    return merged


def _annotate_coating_classification(meta: dict[str, Any]) -> None:
    """对 metadata in-place 跑 is_coating_patent，加两字段：
      is_coating_patent: bool
      is_coating_reason: str — false neg/pos 的调试 hint
    """
    is_coating, reason = is_coating_patent(
        meta.get("ipc_codes"),
        meta.get("title"),
        meta.get("abstract"),
    )
    meta["is_coating_patent"] = is_coating
    meta["is_coating_reason"] = reason


def save_patent_metadata(repo: Path, doc_id: str, meta: dict[str, Any]) -> Path:
    """落 data/patents/<doc_id>__patent_meta.json。"""
    out_dir = repo / "data" / "patents"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{doc_id}__patent_meta.json"
    out_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out_path


def find_content_list(mineru_output_dir: Path, doc_id: str) -> Path | None:
    """MinerU 给文件加 UUID 前缀且每次不同，按 glob 在
    data/mineru_output/<doc_id>/ 下定位。

    本地 MinerU CLI 可能把输出放在 method 子目录下（``auto/`` / ``ocr/``）。
    先查根目录（向后兼容），然后按 batch runner 同序探子目录。
    """
    doc_dir = mineru_output_dir / doc_id
    for subdir in ("", "auto", "ocr", "txt"):
        search_dir = doc_dir / subdir if subdir else doc_dir
        candidates = sorted(search_dir.glob("*_content_list.json"))
        if candidates:
            return candidates[0]
    return None
