"""Live schema facts; never infer document counts from chunk layout."""
from __future__ import annotations

from typing import Any


TYPE_NAMES = {
    1: "BOOL", 2: "INT8", 3: "INT16", 4: "INT32", 5: "INT64",
    10: "FLOAT", 11: "DOUBLE", 20: "STRING", 21: "VARCHAR", 22: "ARRAY", 23: "JSON",
    100: "BINARY_VECTOR", 101: "FLOAT_VECTOR", 102: "FLOAT16_VECTOR",
    103: "BFLOAT16_VECTOR", 104: "SPARSE_FLOAT_VECTOR", 105: "INT8_VECTOR",
}
SCALAR_TYPES = {"BOOL", "INT8", "INT16", "INT32", "INT64", "FLOAT", "DOUBLE",
                "STRING", "VARCHAR", "ARRAY", "JSON"}
DOCUMENT_METADATA_FIELDS = (
    "doc_id", "publication_number", "publication_year", "kind_code", "application_number",
    "filing_date_int", "publication_date_int", "priority_date_int", "language", "ipc_codes",
    "applicant_names", "applicant_countries", "inventor_names", "application_family", "title", "abstract",
)
CHUNK_FIELDS = ("chunk_uid", "reference", "source_row", "section_index", "chunk_index",
                "section_title", "section_path", "content", "chunk_metadata", "embedding", "content_sparse")
FIELD_MEANINGS = {
    "doc_id": "Document identity at publication level, including kind code; not deduplicated by invention or patent family.",
    "application_family": "Application category; this field does not represent a patent family.",
    "applicant_names": "Applicant/assignee names recorded for the document; distinct from inventors.",
    "inventor_names": "Inventor names recorded for the document; distinct from applicants.",
    "chunk_index": "Chunk index within a section; zero alone does not identify one row per document.",
    "content": "Text of this chunk, used for technical recall and reranking.",
    "search_text": "Repeated document summary/index text; not the full chunk body.",
}


def type_name(value: Any) -> str:
    if isinstance(value, int):
        return TYPE_NAMES.get(value, str(value))
    return str(value).upper().removeprefix("DATATYPE.")


def collection_profile(*, schema: dict, database: str, collection: str,
                       doc_id_field: str, content_field: str, fulltext: dict) -> dict:
    fields = {field["name"]: field for field in schema.get("fields", [])}
    scalar_fields = []
    for name, field in fields.items():
        dtype = type_name(field.get("type"))
        if dtype in SCALAR_TYPES:
            item = {"name": name, "type": dtype, "nullable": bool(field.get("nullable", False))}
            if dtype == "ARRAY":
                item["element_type"] = type_name(field.get("element_type"))
            scalar_fields.append(item)

    rule = {"status": "unvalidated_for_current_collection", "automatically_applied": False,
            "notice": "count counts matching rows, not documents. Use bounded count_distinct and check its completeness flag."}
    notes = ["One row represents one chunk; multiple chunks can share a document identity.",
             "Use native Milvus expressions, not SQL or Elasticsearch DSL.",
             "ARRAY fields need ARRAY_CONTAINS operators; grouping requires scalar fields.",
             "Search snippets locate candidates; inspect full text before citing technical facts."]
    return {
        "database_engine": "Milvus", "database": database, "collection": collection,
        "collection_id": schema.get("collection_id"), "record_level": "chunk",
        "document_id_field": doc_id_field if doc_id_field in fields else None,
        "content_field": content_field if content_field in fields else None,
        "fulltext_path_field": None,
        "fulltext_locator": fulltext,
        "document_count_filter": None,
        "document_count_rule": rule,
        "document_metadata_fields": [name for name in DOCUMENT_METADATA_FIELDS if name in fields],
        "chunk_fields": [name for name in CHUNK_FIELDS if name in fields],
        "field_meanings": {name: meaning for name, meaning in FIELD_MEANINGS.items() if name in fields},
        "array_fields": [field["name"] for field in scalar_fields if field["type"] == "ARRAY"],
        "filterable_fields": scalar_fields,
        "notes": notes,
    }
