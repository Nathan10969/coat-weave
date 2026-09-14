"""把一个 unit 的 VLM 描述跟 Examples 章节里的段落匹配。

PoC-2（偏离 V1.2.2 §红线 1 的撤回 — 作为有意识的 PoC 记录，看结果决定
是否回归 V1.2.x 主线，需要先跟项目主导对齐）。

每 unit 一次 qwen-plus 调用。输入纯文本 — figure 和 table 都先经
``vlm_describe`` 描述后再进这里。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import SETTINGS

logger = logging.getLogger(__name__)


try:
    from openai import OpenAI  # type: ignore[import-not-found]
    _OPENAI_AVAILABLE = True
except ImportError:
    OpenAI = None  # type: ignore[assignment]
    _OPENAI_AVAILABLE = False


_PROMPT_PATH = SETTINGS.project_root / "prompts" / "paragraph_match.txt"


class ParagraphMatcherError(RuntimeError):
    pass


class ParagraphMatcher:
    """单次调用 matcher（OpenAI-compatible DashScope endpoint）。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self.api_key = api_key or SETTINGS.openai.api_key
        self.base_url = base_url or SETTINGS.openai.base_url
        self.model = model or SETTINGS.models.qwen_text
        self._client: Any = None
        if _OPENAI_AVAILABLE and self.api_key:
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        self._prompt_template: str | None = None

    def _load_prompt(self) -> str:
        if self._prompt_template is None:
            self._prompt_template = _PROMPT_PATH.read_text(encoding="utf-8")
        return self._prompt_template

    def match(
        self,
        description: str,
        paragraphs: list[dict[str, Any]],
        *,
        top_k: int = 10,
        region_label: str | None = None,
    ) -> list[dict[str, Any]]:
        """返回最多 ``top_k`` 条匹配段落，每条带文本。

        ``paragraphs`` 项必须有 ``para_id`` (int) 和 ``text`` (str)；
        ``page`` 可选但出现时会保留到返回 dict 里。``region_label``
        (e.g. ``"Table 1"``) 传给 prompt，让模型除了语义描述外也能按
        正文里 "Table 1 shows..." 这种显式引用匹配。
        """
        if not paragraphs or not description.strip():
            return []
        if self._client is None:
            raise ParagraphMatcherError(
                "ParagraphMatcher has no OpenAI client — set OPENAI_API_KEY in .env"
            )

        # 每条段落整段发 — qwen-plus 128k 上下文，单 Examples 章节候选
        # 文本远远不及。换行折成空格让 per-paragraph 视图保持单行。
        para_lines: list[str] = []
        for p in paragraphs:
            txt = (p.get("text") or "").strip().replace("\n", " ")
            page = p.get("page")
            page_tag = f"[p{page}] " if page is not None else ""
            para_lines.append(f"#{p['para_id']} {page_tag}{txt}")
        paragraphs_block = "\n".join(para_lines)

        prompt = self._load_prompt().format(
            description=description.strip(),
            paragraphs_block=paragraphs_block,
            top_k=top_k,
            region_label=region_label or "(unknown — no explicit label)",
        )

        raw = self._call(prompt)
        try:
            payload = json.loads(raw)
            matches_raw = payload.get("matches", [])
        except json.JSONDecodeError as exc:
            raise ParagraphMatcherError(f"matcher returned invalid JSON: {exc}\n{raw[:500]}") from exc

        # 把源段落文本附回去给下游
        by_id = {p["para_id"]: p for p in paragraphs}
        out: list[dict[str, Any]] = []
        for m in matches_raw[:top_k]:
            pid = m.get("para_id")
            if pid not in by_id:
                logger.warning("matcher returned unknown para_id=%s — skipping", pid)
                continue
            src = by_id[pid]
            out.append({
                "para_id": pid,
                "score": float(m.get("score", 0.0)),
                "reason": m.get("reason", ""),
                "page": src.get("page"),
                "text": src.get("text", ""),
            })
        return out

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    def _call(self, prompt: str) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        content = resp.choices[0].message.content
        if not content:
            raise ParagraphMatcherError("empty content from model")
        return content


def match_paragraphs(
    description: str,
    paragraphs: list[dict[str, Any]],
    *,
    top_k: int = 10,
    region_label: str | None = None,
    matcher: ParagraphMatcher | None = None,
) -> list[dict[str, Any]]:
    """方便函数：用默认设置实例化 matcher 跑一次。"""
    return (matcher or ParagraphMatcher()).match(
        description, paragraphs, top_k=top_k, region_label=region_label,
    )
