from __future__ import annotations

import os
import secrets
from typing import Annotated
from typing import Any
from functools import lru_cache

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from patent_milvus_tool import build_from_env


app = FastAPI(title="Patent Search", version="0.2.0")


@lru_cache(maxsize=1)
def get_tool():
    # Health/schema endpoints and scalar requests must not load GPU models.
    return build_from_env()


class ToolRequest(BaseModel):
    statement: dict[str, Any]


def require_api_token(request: Request, authorization: Annotated[str | None, Header()] = None):
    """Unauthenticated access is local-only; this is not tenant authorization."""
    expected = os.environ.get("PATENT_SEARCH_API_TOKEN", "")
    if not expected:
        if request.client and request.client.host in ("127.0.0.1", "::1"):
            return
        raise HTTPException(status_code=401, detail="Configure a service token for remote access")
    scheme, separator, supplied = (authorization or "").partition(" ")
    if (not separator or scheme.lower() != "bearer" or not supplied or
            not secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Bearer"})


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/tools/patent_search/schema", dependencies=[Depends(require_api_token)])
def schema():
    from patent_milvus_tool import PatentMilvusTool
    return PatentMilvusTool.definition()


@app.post("/tools/patent_search", dependencies=[Depends(require_api_token)])
def patent_search(req: ToolRequest):
    try:
        tool = get_tool()
    except Exception:
        raise HTTPException(status_code=503, detail="Patent service configuration or connection failed") from None
    return tool.execute(req.statement)
