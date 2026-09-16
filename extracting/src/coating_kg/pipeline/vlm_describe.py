"""Qwen VLM 客户端，用于 figure / table 描述 (W2.2)。

实现 V1.2.2 §修订 3 的严格 JSON prompts。走 DashScope OpenAI-compatible
endpoint (``openai`` SDK)，model 名是任意字符串，可以通过 ``QWEN_VL_MODEL`` /
``QWEN_TABLE_OCR_MODEL`` / ``QWEN_TEXT_MODEL`` 环境变量切换。

图片以 ``data:image/png;base64,...`` URL 装在 ``image_url`` content part 里发送
（跟 GPT-4o 一样），用 ``response_format={"type": "json_object"}`` 要 JSON 回。

Tenacity 跑 retry 策略。方法签名跟之前 DashScope-SDK 实现完全一致。
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

try:
    from openai import OpenAI  # type: ignore[import-not-found]
    _OPENAI_AVAILABLE = True
except ImportError:
    OpenAI = None  # type: ignore[assignment,misc]
    _OPENAI_AVAILABLE = False

from ..config import SETTINGS

logger = logging.getLogger(__name__)


# prompts 目录路径（项目根的相对位置）
_PROMPT_DIR = SETTINGS.project_root / "prompts"


class VLMError(RuntimeError):
    """VLM 调用 retry 后仍失败抛这个。"""


def _guess_image_mime(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    if suffix in {"jpg", "jpeg"}:
        return "image/jpeg"
    if suffix == "webp":
        return "image/webp"
    if suffix == "gif":
        return "image/gif"
    return "image/png"


class QwenVLClient:
    """Qwen VL endpoint 的轻量包装（OpenAI-compatible API）。

    ``model`` 是字符串，调用方可传任一个：
        - ``QWEN_VL_MODEL``: figure description.
        - ``QWEN_TABLE_OCR_MODEL``: table image OCR / structure recovery.
        - ``QWEN_TEXT_MODEL``: text-only table fallback.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        text_model: str | None = None,
        table_ocr_model: str | None = None,
        prompt_dir: Path | None = None,
    ) -> None:
        self.api_key = api_key or SETTINGS.openai.api_key
        self.base_url = base_url or SETTINGS.openai.base_url
        self.model = model or SETTINGS.models.qwen_vl
        self.text_model = text_model or SETTINGS.models.qwen_text
        self.table_ocr_model = table_ocr_model or SETTINGS.models.qwen_table_ocr
        self._prompt_dir = prompt_dir or _PROMPT_DIR

        self._client: Any = None
        if _OPENAI_AVAILABLE and self.api_key:
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)

        # 懒加载 prompt 模板
        self._prompt_figure: str | None = None
        self._prompt_table: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def describe_figure(
        self,
        image_path: str | Path,
        candidate_canonical_ids: list[str],
    ) -> dict[str, Any]:
        """对 figure 返回解析后的 VLM 描述 JSON。"""
        prompt = self._render_prompt(self._figure_prompt(), candidate_canonical_ids)
        return self._call_vlm(prompt=prompt, image_path=Path(image_path))

    def describe_table(
        self,
        table_html: str,
        caption_footnote: str,
        candidate_canonical_ids: list[str],
    ) -> dict[str, Any]:
        """对 table 返回解析后的 VLM 描述 JSON。"""
        prompt = self._render_prompt(
            self._table_prompt(),
            candidate_canonical_ids,
            extra={"table_html": table_html, "caption_footnote": caption_footnote},
        )
        return self._call_vlm(prompt=prompt, image_path=None)

    def describe_table_image(
        self,
        image_path: str | Path,
        table_html: str,
        caption_footnote: str,
        candidate_canonical_ids: list[str],
    ) -> dict[str, Any]:
        """Recover a table from its image, treating HTML as a noisy draft."""
        prompt = self._render_prompt(
            self._table_prompt(),
            candidate_canonical_ids,
            extra={
                "table_html": table_html,
                "caption_footnote": (
                    caption_footnote
                    + "\n\nThe attached image is the source of truth. "
                    + "The HTML above is an OCR draft; if image and HTML conflict, "
                    + "trust the visible table image."
                ),
            },
        )
        return self._call_vlm(
            prompt=prompt,
            image_path=Path(image_path),
            model_override=self.table_ocr_model,
        )

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    def _figure_prompt(self) -> str:
        if self._prompt_figure is None:
            self._prompt_figure = (self._prompt_dir / "vlm_figure.txt").read_text(encoding="utf-8")
        return self._prompt_figure

    def _table_prompt(self) -> str:
        if self._prompt_table is None:
            self._prompt_table = (self._prompt_dir / "vlm_table.txt").read_text(encoding="utf-8")
        return self._prompt_table

    @staticmethod
    def _render_prompt(
        template: str,
        candidate_canonical_ids: list[str],
        extra: dict[str, str] | None = None,
    ) -> str:
        kwargs: dict[str, str] = {
            "candidate_canonical_ids": "\n".join(f"  - {c}" for c in candidate_canonical_ids),
        }
        if extra:
            kwargs.update(extra)
        # 用 ``str.format_map`` 让缺失的占位符不崩
        return template.format_map(_SafeFmt(kwargs))

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(4),
        retry=retry_if_exception_type((VLMError, IOError)),
        reraise=True,
    )
    def _call_vlm(
        self,
        prompt: str,
        image_path: Path | None,
        *,
        model_override: str | None = None,
    ) -> dict[str, Any]:
        if not _OPENAI_AVAILABLE:
            raise VLMError(
                "openai SDK not installed. `pip install openai>=1.0`."
            )
        if not self.api_key or self._client is None:
            raise VLMError("OPENAI_API_KEY is not set in env.")

        # Figure → 多模态 user turn；table → 纯文本
        if image_path is not None:
            b64 = encode_image_b64(image_path)
            mime = _guess_image_mime(image_path)
            user_content: list[dict[str, Any]] = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                },
                {"type": "text", "text": prompt},
            ]
            model = model_override or self.model
        else:
            user_content = [{"type": "text", "text": prompt}]
            # Text-only fallback path for tables without an image crop.
            model = model_override or self.text_model

        try:
            response = self._client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": user_content}],
                response_format={"type": "json_object"},
            )
        except Exception as exc:  # noqa: BLE001
            raise VLMError(f"openai chat.completions failed: {exc}") from exc

        return self._parse_response(response)

    @staticmethod
    def _parse_response(response: Any) -> dict[str, Any]:
        """从 OpenAI 形态 response 抽 JSON payload。"""
        try:
            choice = response.choices[0]
            content = choice.message.content
            # 部分 Qwen 版本会把 JSON 包成 list of parts
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "") if isinstance(part, dict) else getattr(part, "text", "")
                    for part in content
                )
            if content is None:
                raise VLMError("response.choices[0].message.content is None")
            return json.loads(content)
        except (KeyError, IndexError, TypeError, AttributeError, json.JSONDecodeError) as exc:
            raise VLMError(f"could not parse VLM response: {exc!r}") from exc


class _SafeFmt(dict):
    """``format_map`` 帮手 — 未知 key 不替换。"""

    def __missing__(self, key: str) -> str:  # type: ignore[override]
        return "{" + key + "}"


# ----------------------------------------------------------------------
# 给想要原始 bytes 的调用方用（如 MinerU 切图）
# ----------------------------------------------------------------------
def encode_image_b64(image_path: str | Path) -> str:
    """返回图片文件的 base64 编码内容。"""
    return base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
