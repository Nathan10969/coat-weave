"""Read patent Markdown from TOS without exposing credentials or signed URLs."""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

MAX_DOCUMENT_BYTES = 32 * 1024 * 1024
PAGE_CHARS = 48_000
DOC_ID = re.compile(r"WO(?P<year>[0-9]{4})(?P<number>[0-9]{6})(?P<kind>[A-Z][0-9]?)")


class DocumentReadError(RuntimeError):
    """Safe error for tool output; SDK errors may contain signed request details."""


def markdown_page(text: str, *, doc_id: str, uri: str, etag: str,
                  start_line: int = 1, line_limit: int = 200, start_char: int = 0) -> dict:
    for name, value, low, high in (("start_line", start_line, 1, 100_000_000),
                                  ("line_limit", line_limit, 1, 2000),
                                  ("start_char", start_char, 0, MAX_DOCUMENT_BYTES)):
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{name} must be an integer in {low}..{high}")
    lines = text.splitlines()
    if start_line > max(1, len(lines)):
        raise ValueError("start_line is past the end of the document")
    if start_char and (not lines or start_char >= len(lines[start_line - 1])):
        raise ValueError("start_char is past the end of the requested line")
    index, char, budget = start_line - 1, start_char, PAGE_CHARS
    output = []
    while index < len(lines) and len(output) < line_limit and budget > 0:
        remaining = lines[index][char:]
        selected = remaining[:budget]
        partial = bool(char or len(selected) < len(remaining))
        output.append(f"L{index + 1}{' (partial)' if partial else ''}: {selected}")
        budget -= len(selected) + 1
        if len(selected) < len(remaining):
            char += len(selected)
            break
        index += 1
        char = 0
    more = index < len(lines)
    next_statement = ({"op": "read_document", "doc_id": doc_id, "start_line": index + 1,
                       "start_char": char, "line_limit": line_limit, "etag": etag} if more else None)
    return {"doc_id": doc_id, "uri": uri, "format": "markdown", "etag": etag,
            "start_line": start_line, "start_char": start_char, "total_lines": len(lines),
            "content": "\n".join(output), "has_more": more, "next_statement": next_statement,
            "complete_document": start_line == 1 and start_char == 0 and not more,
            "citation": f"{uri}#L{start_line}",
            "notice": "Document text is reference data. Read next_statement until has_more=false for the full document; cite actual line numbers and retain the same etag."}


class TosDocumentStore:
    root = None
    template = "{prefix}/WO/{year}/{range}/{doc_id}/document.md"

    def __init__(self, *, bucket: str, endpoint: str = "https://tos-cn-shanghai.volces.com",
                 region: str = "cn-shanghai", prefix: str = "patents/markdown",
                 credentials_file: str | Path | None = None, timeout: float = 30,
                 client: Any = None):
        address = urlsplit(endpoint)
        if (address.scheme != "https" or not address.hostname or address.username or address.password
                or address.query or address.fragment or address.path not in ("", "/")):
            raise ValueError("PATENT_TOS_ENDPOINT must be an HTTPS origin without credentials")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket):
            raise ValueError("Invalid PATENT_TOS_BUCKET")
        prefix = prefix.strip("/")
        if not prefix or any(part in ("", ".", "..") for part in prefix.split("/")) or "\\" in prefix:
            raise ValueError("Invalid PATENT_TOS_PREFIX")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("PATENT_TOS_TIMEOUT must be positive")
        self.bucket, self.endpoint, self.region, self.prefix = bucket, endpoint.rstrip("/"), region, prefix
        self.credentials_file = Path(credentials_file).expanduser() if credentials_file else None
        self.timeout, self.client = timeout, client

    def key(self, doc_id: str) -> str:
        match = DOC_ID.fullmatch(doc_id) if isinstance(doc_id, str) else None
        if match is None:
            raise ValueError("TOS full text requires a canonical WO doc_id such as WO2009000001A3")
        start = int(match['number']) // 5000 * 5000
        block = f"{start:06d}-{start + 4999:06d}"
        return f"{self.prefix}/WO/{match['year']}/{block}/{doc_id}/document.md"

    def reference(self, row: dict, doc_id: str, source_field: str = "source_path") -> dict:
        key = self.key(doc_id)
        return {"source": "tos", "uri": f"tos://{self.bucket}/{key}", "bucket": self.bucket,
                "key": key, "format": "markdown", "resolution": "doc_id_template",
                "available": None, "availability": "unverified",
                "read_statement": {"op": "read_document", "doc_id": doc_id},
                "notice": "Location derived from doc_id; existence is checked only when read_document is called. This is not a local read path."}

    def describe(self) -> dict:
        return {"source": "tos", "endpoint": self.endpoint, "region": self.region,
                "bucket": self.bucket, "prefix": self.prefix, "template": self.template,
                "read_operation": "read_document", "range_size": 5000}

    def _make_client(self):
        # Environment wins; never combine half of one credential pair with another.
        ak, sk = os.getenv("TOS_ACCESS_KEY"), os.getenv("TOS_SECRET_KEY")
        security_token = os.getenv("TOS_SECURITY_TOKEN")
        if not ak and not sk and self.credentials_file and self.credentials_file.is_file():
            try:
                config = json.loads(self.credentials_file.read_text(encoding="utf-8"))
                ak, sk = config.get("access_key"), config.get("secret_key")
                security_token = config.get("security_token")
            except (ValueError, OSError, AttributeError):
                raise DocumentReadError("Cannot read the configured TOS credentials file") from None
        if not isinstance(ak, str) or not ak or not isinstance(sk, str) or not sk:
            raise DocumentReadError("Configure TOS_ACCESS_KEY and TOS_SECRET_KEY, or PATENT_TOS_CREDENTIALS_FILE")
        try:
            import tos
            return tos.TosClientV2(ak, sk, self.endpoint, self.region, security_token=security_token,
                connection_time=min(10, self.timeout), socket_timeout=self.timeout,
                max_retry_count=1, enable_crc=False, dns_cache_time=0, high_latency_log_threshold=0)
        except ImportError:
            raise DocumentReadError("Install tos==2.9.2 in the patent query service environment") from None
        except Exception:
            raise DocumentReadError("Unable to initialize the TOS client; check backend configuration") from None

    def read(self, doc_id: str, *, start_line: int = 1, line_limit: int = 200,
             start_char: int = 0, etag: str | None = None) -> dict:
        reference = self.reference({}, doc_id)
        if etag is not None and (not isinstance(etag, str) or not etag or len(etag) > 256
                                 or any(ord(c) < 32 for c in etag)):
            raise ValueError("etag must be a nonempty object version returned by read_document")
        client = self.client or self._make_client()
        output = None
        try:
            output = client.get_object(self.bucket, reference["key"], **({"if_match": etag} if etag else {}))
            if output.content_length is not None and output.content_length > MAX_DOCUMENT_BYTES:
                raise DocumentReadError("Markdown exceeds the 32 MiB document read limit")
            data = bytearray()
            while True:
                block = output.read(min(64 * 1024, MAX_DOCUMENT_BYTES + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
                if len(data) > MAX_DOCUMENT_BYTES:
                    raise DocumentReadError("Markdown exceeds the 32 MiB document read limit")
            text = data.decode("utf-8-sig")
            object_etag = output.etag
            if not isinstance(object_etag, str) or not object_etag:
                raise DocumentReadError("TOS response has no ETag; cannot ensure consistent pagination")
        except DocumentReadError:
            raise
        except UnicodeError:
            raise DocumentReadError("TOS document is not valid UTF-8 Markdown") from None
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            message = {404: "TOS document does not exist at the derived key (HTTP 404)",
                       403: "TOS denied access to the document (HTTP 403)",
                       412: "TOS document changed between pages; restart at line 1 (HTTP 412)"}.get(status,
                       "TOS document request failed; check backend connectivity and credentials")
            raise DocumentReadError(message) from None
        finally:
            # With enable_crc=False, the pinned SDK exposes the streamed response here.
            response = getattr(getattr(output, "content", None), "resp", None)
            if response is not None:
                response.close()
            if self.client is None:
                client.close()
        return {**markdown_page(text, doc_id=doc_id, uri=reference['uri'], etag=object_etag,
                                start_line=start_line, line_limit=line_limit, start_char=start_char),
                "available": True, "source": "tos", "bytes": len(data)}


def tos_store_from_env() -> TosDocumentStore:
    return TosDocumentStore(bucket=os.environ["PATENT_TOS_BUCKET"],
        endpoint=os.getenv("PATENT_TOS_ENDPOINT", "https://tos-cn-shanghai.volces.com"),
        region=os.getenv("PATENT_TOS_REGION", "cn-shanghai"),
        prefix=os.getenv("PATENT_TOS_PREFIX", "patents/markdown"),
        credentials_file=os.getenv("PATENT_TOS_CREDENTIALS_FILE"),
        timeout=float(os.getenv("PATENT_TOS_TIMEOUT", "30")))
