"""Minimal Pydantic models for the local pipeline runner."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FigureTableUnit(BaseModel):
    model_config = ConfigDict(extra="allow")

    unit_id: str
    unit_type: Literal["figure", "table"]
    doc_id: str
    page: int | None = None
    region_id: str | None = None
    image_path: str | None = None
    caption_footnote_text: str | None = None
    extracted_table_html: str | None = None
    vlm_description: str | None = None
    tagged_entities: list[str] = Field(default_factory=list)
    figure_subtype: str | None = None
    bbox: list[float] | None = None


class FactHyperedge(BaseModel):
    model_config = ConfigDict(extra="allow")

    fact_id: str
    doc_id: str | None = None
    application: str | None = None
    property: str | None = None
    result_value_text: str | None = None
    polarity_hint: str | None = "unknown"
    evidence_pointer: dict[str, Any] = Field(default_factory=dict)

