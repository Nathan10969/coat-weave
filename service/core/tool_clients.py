from __future__ import annotations

import html
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any

from demo_config import (
    KG_DOC_FIELD_SCAN_TIMEOUT_SECONDS,
    KG_DOC_FIELD_SCAN_URL,
    KG_EXPAND_TIMEOUT_SECONDS,
    KG_EXPAND_TOKEN,
    KG_EXPAND_URL,
    KG_HYBRID_DEFAULT_CANDIDATE_K,
    KG_HYBRID_DEFAULT_TOP_K,
    KG_HYBRID_SEARCH_TIMEOUT_SECONDS,
    KG_HYBRID_SEARCH_URL,
    KG_SQL_AGGREGATE_TIMEOUT_SECONDS,
    KG_SQL_AGGREGATE_URL,
)
from demo_text import clamp_int, normalize_kg_aggregate_filters, normalize_kg_search_filters, normalize_string_list


def wants_github_recent_repo_search(question: str) -> bool:
    lower = question.lower()
    github_signal = "github" in lower or "repo" in lower or "repository" in lower or "仓库" in question
    search_signal = any(term in lower for term in ["star", "stars", "trending", "open source"]) or any(
        term in question for term in ["开源", "高星", "高star", "搜索", "联网", "最近"]
    )
    return github_signal and search_signal

def infer_recent_days(question: str) -> int:
    lower = question.lower()
    if any(term in question for term in ["今天", "今日"]) or "today" in lower:
        return 1
    if any(term in question for term in ["一周", "7天", "七天"]) or "week" in lower:
        return 7
    if any(term in question for term in ["一个月", "30天", "近月"]) or "month" in lower:
        return 30
    if any(term in question for term in ["三个月", "90天", "季度"]) or "quarter" in lower:
        return 90
    if any(term in question for term in ["半年", "6个月"]):
        return 180
    if any(term in question for term in ["一年", "12个月", "今年"]) or "year" in lower:
        return 365
    return int(os.environ.get("GITHUB_RECENT_REPO_DAYS", "90"))

class DuckDuckGoLiteParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._in_link = False
        self._current_href = ""
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attrs_dict = {k: v or "" for k, v in attrs}
        href = attrs_dict.get("href", "")
        if "uddg=" in href or href.startswith("http"):
            self._in_link = True
            self._current_href = href
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._in_link:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._in_link:
            return
        title = " ".join("".join(self._current_text).split())
        url = self._normalize_url(self._current_href)
        if title and url and not any(row.get("url") == url for row in self.results):
            self.results.append({"title": html.unescape(title), "url": url, "snippet": ""})
        self._in_link = False
        self._current_href = ""
        self._current_text = []

    @staticmethod
    def _normalize_url(href: str) -> str:
        href = html.unescape(href)
        parsed = urllib.parse.urlparse(href)
        query = urllib.parse.parse_qs(parsed.query)
        if "uddg" in query and query["uddg"]:
            return query["uddg"][0]
        if href.startswith("//"):
            return "https:" + href
        if href.startswith("http"):
            return href
        return ""

def brave_web_search(question: str, *, limit: int = 8) -> dict[str, Any]:
    api_key = os.environ.get("BRAVE_API_KEY")
    if not api_key:
        raise RuntimeError("BRAVE_API_KEY is not configured")
    params = urllib.parse.urlencode({"q": question, "count": str(limit), "freshness": "pd"})
    req = urllib.request.Request(
        f"https://api.search.brave.com/res/v1/web/search?{params}",
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
            "User-Agent": "dialogue-memory-demo-web-search",
        },
        method="GET",
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    items = []
    for item in (payload.get("web") or {}).get("results", [])[:limit]:
        items.append(
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "snippet": item.get("description"),
                "age": item.get("age"),
                "source": item.get("profile", {}).get("name"),
            }
        )
    return {
        "tool": "web.search",
        "provider": "brave",
        "query": question,
        "elapsed_ms": round((time.time() - started) * 1000),
        "items": items,
    }

def duckduckgo_web_search(question: str, *, limit: int = 8) -> dict[str, Any]:
    params = urllib.parse.urlencode({"q": question})
    req = urllib.request.Request(
        f"https://lite.duckduckgo.com/lite/?{params}",
        headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0 dialogue-memory-demo-web-search"},
        method="GET",
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw_html = resp.read().decode("utf-8", errors="replace")
    parser = DuckDuckGoLiteParser()
    parser.feed(raw_html)
    items = parser.results[:limit]
    return {
        "tool": "web.search",
        "provider": "duckduckgo_lite",
        "query": question,
        "elapsed_ms": round((time.time() - started) * 1000),
        "items": items,
    }

def web_search(question: str, *, limit: int = 8) -> dict[str, Any]:
    try:
        return brave_web_search(question, limit=limit)
    except Exception as brave_error:  # noqa: BLE001
        result = duckduckgo_web_search(question, limit=limit)
        result["fallback_reason"] = str(brave_error)
        return result

def kg_expand_hyperedge_multihop(
    object_ids: list[str],
    *,
    max_context_facts: int = 20,
    max_evidence_per_item: int = 5,
) -> dict[str, Any]:
    body = {
        "object_ids": object_ids,
        "max_context_facts": max_context_facts,
        "max_evidence_per_item": max_evidence_per_item,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "dialogue-memory-demo-kg-expand-tool",
    }
    if KG_EXPAND_TOKEN:
        headers["Authorization"] = f"Bearer {KG_EXPAND_TOKEN}"
    req = urllib.request.Request(
        KG_EXPAND_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=KG_EXPAND_TIMEOUT_SECONDS) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    payload.setdefault("tool", "kg.expand_hyperedge_multihop")
    payload["endpoint"] = KG_EXPAND_URL
    payload["elapsed_ms"] = round((time.time() - started) * 1000)
    return payload

def kg_hybrid_search(
    query: str,
    *,
    top_k: int = KG_HYBRID_DEFAULT_TOP_K,
    candidate_k: int = KG_HYBRID_DEFAULT_CANDIDATE_K,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = {
        "query": query,
        "top_k": top_k,
        "candidate_k": candidate_k,
        "filters": normalize_kg_search_filters(filters),
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "dialogue-memory-demo-kg-hybrid-search-tool",
    }
    if KG_EXPAND_TOKEN:
        headers["Authorization"] = f"Bearer {KG_EXPAND_TOKEN}"
    req = urllib.request.Request(
        KG_HYBRID_SEARCH_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=KG_HYBRID_SEARCH_TIMEOUT_SECONDS) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    payload.setdefault("tool", "kg.hybrid_search")
    payload["endpoint"] = KG_HYBRID_SEARCH_URL
    payload["elapsed_ms"] = round((time.time() - started) * 1000)
    return payload

def kg_sql_aggregate(
    *,
    intent: str,
    target: str,
    filters: dict[str, Any] | None = None,
    group_by: list[str] | None = None,
    limit: int = 50,
    include_examples: bool = True,
) -> dict[str, Any]:
    body = {
        "intent": intent,
        "target": target,
        "filters": normalize_kg_aggregate_filters(filters),
        "group_by": normalize_string_list(group_by),
        "limit": clamp_int(limit, 50, 1, 200),
        "include_examples": bool(include_examples),
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "dialogue-memory-demo-kg-sql-aggregate-tool",
    }
    if KG_EXPAND_TOKEN:
        headers["Authorization"] = f"Bearer {KG_EXPAND_TOKEN}"
    req = urllib.request.Request(
        KG_SQL_AGGREGATE_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=KG_SQL_AGGREGATE_TIMEOUT_SECONDS) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    payload.setdefault("tool", "kg.sql_aggregate")
    payload["endpoint"] = KG_SQL_AGGREGATE_URL
    payload["elapsed_ms"] = round((time.time() - started) * 1000)
    return payload

def kg_doc_field_scan(
    *,
    doc_ids: list[str],
    query: str,
    field_groups: list[str] | None = None,
    limit: int = 200,
    include_evidence: bool = True,
) -> dict[str, Any]:
    body = {
        "doc_ids": normalize_string_list(doc_ids),
        "query": str(query or ""),
        "field_groups": normalize_string_list(field_groups)
        or ["test_method", "test_condition", "property", "result", "facts", "materials", "evidence"],
        "limit": clamp_int(limit, 200, 1, 500),
        "include_evidence": bool(include_evidence),
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "dialogue-memory-demo-kg-doc-field-scan-tool",
    }
    if KG_EXPAND_TOKEN:
        headers["Authorization"] = f"Bearer {KG_EXPAND_TOKEN}"
    req = urllib.request.Request(
        KG_DOC_FIELD_SCAN_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=KG_DOC_FIELD_SCAN_TIMEOUT_SECONDS) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    payload.setdefault("tool", "kg.doc_field_scan")
    payload["endpoint"] = KG_DOC_FIELD_SCAN_URL
    payload["elapsed_ms"] = round((time.time() - started) * 1000)
    return payload

def github_recent_high_star_repos(question: str, *, limit: int = 8) -> dict[str, Any]:
    days = infer_recent_days(question)
    created_after = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    min_stars = int(os.environ.get("GITHUB_MIN_STARS", "50"))
    query = f"created:>={created_after} stars:>={min_stars} archived:false"
    params = urllib.parse.urlencode(
        {
            "q": query,
            "sort": "stars",
            "order": "desc",
            "per_page": str(limit),
        }
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "dialogue-memory-demo-mcp-tool",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"https://api.github.com/search/repositories?{params}",
        headers=headers,
        method="GET",
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    repos = []
    for item in payload.get("items", [])[:limit]:
        repos.append(
            {
                "full_name": item.get("full_name"),
                "html_url": item.get("html_url"),
                "description": item.get("description"),
                "stargazers_count": item.get("stargazers_count"),
                "forks_count": item.get("forks_count"),
                "language": item.get("language"),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "topics": item.get("topics", [])[:8],
            }
        )
    return {
        "tool": "github.search_repositories",
        "query": query,
        "recent_days": days,
        "created_after": created_after,
        "min_stars": min_stars,
        "total_count": payload.get("total_count", 0),
        "elapsed_ms": round((time.time() - started) * 1000),
        "items": repos,
    }

CITY_COORDS = {
    "\u4e0a\u6d77": {"name": "\u4e0a\u6d77", "latitude": 31.2304, "longitude": 121.4737, "timezone": "Asia/Shanghai"},
    "\u5317\u4eac": {"name": "\u5317\u4eac", "latitude": 39.9042, "longitude": 116.4074, "timezone": "Asia/Shanghai"},
    "\u5e7f\u5dde": {"name": "\u5e7f\u5dde", "latitude": 23.1291, "longitude": 113.2644, "timezone": "Asia/Shanghai"},
    "\u6df1\u5733": {"name": "\u6df1\u5733", "latitude": 22.5431, "longitude": 114.0579, "timezone": "Asia/Shanghai"},
    "shanghai": {"name": "\u4e0a\u6d77", "latitude": 31.2304, "longitude": 121.4737, "timezone": "Asia/Shanghai"},
    "beijing": {"name": "\u5317\u4eac", "latitude": 39.9042, "longitude": 116.4074, "timezone": "Asia/Shanghai"},
}

WEATHER_CODE_LABELS = {
    0: "\u6674",
    1: "\u5927\u90e8\u6674\u6717",
    2: "\u5c40\u90e8\u591a\u4e91",
    3: "\u9634\u6216\u591a\u4e91",
    45: "\u6709\u96fe",
    48: "\u96fe\u51c7",
    51: "\u5c0f\u6bdb\u6bdb\u96e8",
    53: "\u4e2d\u7b49\u6bdb\u6bdb\u96e8",
    55: "\u8f83\u5f3a\u6bdb\u6bdb\u96e8",
    61: "\u5c0f\u96e8",
    63: "\u4e2d\u96e8",
    65: "\u5927\u96e8",
    71: "\u5c0f\u96ea",
    73: "\u4e2d\u96ea",
    75: "\u5927\u96ea",
    80: "\u5c0f\u9635\u96e8",
    81: "\u4e2d\u7b49\u9635\u96e8",
    82: "\u5f3a\u9635\u96e8",
    95: "\u96f7\u66b4",
    96: "\u96f7\u66b4\u4f34\u5c0f\u51b0\u96f9",
    99: "\u96f7\u66b4\u4f34\u5f3a\u51b0\u96f9",
}

def wants_weather(question: str) -> bool:
    lower = question.lower()
    return "weather" in lower or any(term in question for term in ["\u5929\u6c14", "\u6c14\u6e29", "\u4e0b\u96e8", "\u51b7\u4e0d\u51b7", "\u70ed\u4e0d\u70ed"])

def infer_weather_location(question: str) -> dict[str, Any]:
    lower = question.lower()
    for key, location in CITY_COORDS.items():
        if key in lower or key in question:
            return location
    return CITY_COORDS["\u4e0a\u6d77"]

def current_weather(question: str) -> dict[str, Any]:
    location = infer_weather_location(question)
    params = urllib.parse.urlencode(
        {
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            "current": ",".join(
                [
                    "temperature_2m",
                    "relative_humidity_2m",
                    "apparent_temperature",
                    "precipitation",
                    "rain",
                    "weather_code",
                    "wind_speed_10m",
                    "wind_direction_10m",
                ]
            ),
            "timezone": location["timezone"],
        }
    )
    started = time.time()
    req = urllib.request.Request(
        f"https://api.open-meteo.com/v1/forecast?{params}",
        headers={"Accept": "application/json", "User-Agent": "dialogue-memory-demo-weather-tool"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    current = payload.get("current") or {}
    units = payload.get("current_units") or {}
    code = current.get("weather_code")
    return {
        "tool": "weather.current",
        "source": "Open-Meteo Forecast API",
        "location": location["name"],
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "timezone": location["timezone"],
        "observed_at": current.get("time"),
        "weather_code": code,
        "weather_label": WEATHER_CODE_LABELS.get(code, f"weather_code={code}"),
        "temperature_2m": current.get("temperature_2m"),
        "temperature_unit": units.get("temperature_2m", "\u00b0C"),
        "apparent_temperature": current.get("apparent_temperature"),
        "relative_humidity_2m": current.get("relative_humidity_2m"),
        "humidity_unit": units.get("relative_humidity_2m", "%"),
        "precipitation": current.get("precipitation"),
        "rain": current.get("rain"),
        "precipitation_unit": units.get("precipitation", "mm"),
        "wind_speed_10m": current.get("wind_speed_10m"),
        "wind_speed_unit": units.get("wind_speed_10m", "km/h"),
        "wind_direction_10m": current.get("wind_direction_10m"),
        "elapsed_ms": round((time.time() - started) * 1000),
    }
