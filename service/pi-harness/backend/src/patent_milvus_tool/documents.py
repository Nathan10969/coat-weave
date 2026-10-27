"""Resolve document identities without scanning or loading the corpus."""

from __future__ import annotations

import re
import hashlib
from pathlib import Path
from typing import Any
from .tos_documents import MAX_DOCUMENT_BYTES, markdown_page


class DocumentStore:
    def __init__(self, root: str | Path, template: str = "{doc_id}.md"):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError("PATENT_MD_ROOT must be an existing Markdown directory")
        self.template = template

    def resolve(self, value: str, *, exists: bool = True) -> Path:
        if not isinstance(value, str) or not value:
            raise ValueError("A Markdown path is required")
        requested = Path(value)
        path = (requested if requested.is_absolute() else self.root / requested).resolve()
        if not path.is_relative_to(self.root) or path.suffix.lower() != ".md":
            raise ValueError("Only Markdown files inside PATENT_MD_ROOT are readable")
        if exists and not path.is_file():
            raise FileNotFoundError("Markdown not found in the configured document root")
        return path

    def reference(self, row: dict[str, Any], doc_id: str, source_field: str = "source_path") -> dict:
        candidates: list[tuple[str, str]] = []
        for container in (row, row.get("chunk_metadata"), row.get("metadata")):
            if isinstance(container, dict):
                for key in ("full_text_path", "markdown_path", "md_path", source_field):
                    value = container.get(key)
                    # Parser JSON/PDF/archive paths are provenance, NOT full text.
                    if isinstance(value, str) and value.lower().endswith(".md"):
                        candidates.append((value, "stored_markdown_path"))
        if re.fullmatch(r"[A-Za-z0-9_-]+", doc_id):
            candidates.append((self.template.format(doc_id=doc_id), "doc_id_template"))
        expected = None
        for value, method in candidates:
            try:
                path = self.resolve(value, exists=False)
            except ValueError:
                continue
            expected = expected or path.relative_to(self.root).as_posix()
            if path.is_file():
                return {"path": path.relative_to(self.root).as_posix(), "available": True,
                        "resolution": method, "format": "markdown"}
        return {"path": expected, "available": False, "format": "markdown",
                "reason": "No readable Markdown; check the mount and PATENT_MD_TEMPLATE"}

    def read(self, doc_id: str, *, start_line: int = 1, line_limit: int = 200,
             start_char: int = 0, etag: str | None = None) -> dict:
        if not isinstance(doc_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", doc_id):
            raise ValueError("Invalid document identity")
        path = self.resolve(self.template.format(doc_id=doc_id))
        with path.open("rb") as stream:
            data = stream.read(MAX_DOCUMENT_BYTES + 1)
        if len(data) > MAX_DOCUMENT_BYTES:
            raise ValueError("Markdown exceeds the document read limit")
        version = hashlib.sha256(data).hexdigest()
        if etag is not None and etag != version:
            raise ValueError("Document changed between pages; restart at line 1")
        return {**markdown_page(data.decode("utf-8-sig"), doc_id=doc_id,
            uri=f"patent://{doc_id}", etag=version, start_line=start_line,
            line_limit=line_limit, start_char=start_char), "source": "local", "available": True}
