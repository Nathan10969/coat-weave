from __future__ import annotations

from typing import Any

from answering import provider_config
from demo_config import DURABLE, GRAPH_EDGES, GRAPH_NODES, OBSERVATIONS
from demo_storage import append_jsonl, now_iso, read_jsonl, write_jsonl
from demo_text import stable_id, tokenize


_MODEL_NAME = provider_config()["model"]

GRAPH_CANONICAL_RULES = [
    {
        "name": "memory graph recall",
        "type": "concept",
        "aliases": ["图谱召回", "图谱记忆召回", "graph recall", "memory graph"],
        "relations": [
            ("depends_on", "entity/relation extraction", "pipeline_step"),
            ("feeds", "Dialogue Memory Packet", "context_injection"),
        ],
    },
    {
        "name": _MODEL_NAME,
        "type": "library",
        "aliases": ["OpenAI-compatible model", "DashScope-compatible model", _MODEL_NAME],
        "relations": [
            ("supports", "1M context", "model_capability"),
            ("supports", "64K output", "model_capability"),
            ("avoids", "thinking", "latency_setting"),
        ],
    },
]

GRAPH_NODE_TYPES = {
    _MODEL_NAME: "library",
    "memory graph recall": "concept",
    "entity/relation extraction": "concept",
    "Dialogue Memory Packet": "concept",
    "1M context": "concept",
    "64K output": "concept",
    "thinking": "concept",
}


def graph_node(name: str, node_type: str | None, source_id: str, source_kind: str) -> dict[str, Any]:
    return {
        "id": stable_id("gn", name),
        "type": node_type or GRAPH_NODE_TYPES.get(name, "concept"),
        "name": name,
        "aliases": [],
        "source_ids": [source_id],
        "source_kinds": [source_kind],
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

def graph_edge(source: str, relation: str, target: str, source_id: str, source_kind: str, reason: str) -> dict[str, Any]:
    return {
        "id": stable_id("ge", source, relation, target),
        "source": stable_id("gn", source),
        "target": stable_id("gn", target),
        "source_name": source,
        "target_name": target,
        "relation": relation,
        "weight": 0.9,
        "reason": reason,
        "source_ids": [source_id],
        "source_kinds": [source_kind],
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

def text_matches_rule(text: str, rule: dict[str, Any]) -> bool:
    lower = text.lower()
    return any(alias.lower() in lower for alias in [rule["name"], *rule.get("aliases", [])])

def extract_graph_records(source: dict[str, Any], source_kind: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    text = f"{source.get('title', '')} {source.get('content', '')} {source.get('summary', '')}"
    source_id = str(source.get("id", stable_id("src", text[:80])))
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    for rule in GRAPH_CANONICAL_RULES:
        if text_matches_rule(text, rule):
            nodes.append(graph_node(rule["name"], rule["type"], source_id, source_kind))
            for relation, target, reason in rule["relations"]:
                nodes.append(graph_node(target, GRAPH_NODE_TYPES.get(target), source_id, source_kind))
                edges.append(graph_edge(rule["name"], relation, target, source_id, source_kind, reason))

    return nodes, edges

def upsert_graph_records(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    existing_nodes = {row["id"]: row for row in read_jsonl(GRAPH_NODES)}
    existing_edges = {row["id"]: row for row in read_jsonl(GRAPH_EDGES)}
    for node in nodes:
        old = existing_nodes.get(node["id"])
        if old:
            node = {
                **old,
                "aliases": sorted(set(old.get("aliases", []) + node.get("aliases", []))),
                "source_ids": sorted(set(old.get("source_ids", []) + node.get("source_ids", []))),
                "source_kinds": sorted(set(old.get("source_kinds", []) + node.get("source_kinds", []))),
                "updated_at": now_iso(),
            }
        existing_nodes[node["id"]] = node
    for edge in edges:
        old = existing_edges.get(edge["id"])
        if old:
            edge = {
                **old,
                "source_ids": sorted(set(old.get("source_ids", []) + edge.get("source_ids", []))),
                "source_kinds": sorted(set(old.get("source_kinds", []) + edge.get("source_kinds", []))),
                "updated_at": now_iso(),
            }
        existing_edges[edge["id"]] = edge
    write_jsonl(GRAPH_NODES, list(existing_nodes.values()))
    write_jsonl(GRAPH_EDGES, list(existing_edges.values()))

def rebuild_graph_index() -> None:
    all_nodes: list[dict[str, Any]] = []
    all_edges: list[dict[str, Any]] = []
    for row in read_jsonl(DURABLE):
        nodes, edges = extract_graph_records(row, "memory")
        all_nodes.extend(nodes)
        all_edges.extend(edges)
    for row in read_jsonl(OBSERVATIONS):
        nodes, edges = extract_graph_records(row, "observation")
        all_nodes.extend(nodes)
        all_edges.extend(edges)
    write_jsonl(GRAPH_NODES, [])
    write_jsonl(GRAPH_EDGES, [])
    upsert_graph_records(all_nodes, all_edges)

def update_graph_from_record(record: dict[str, Any], source_kind: str) -> None:
    nodes, edges = extract_graph_records(record, source_kind)
    if nodes or edges:
        upsert_graph_records(nodes, edges)

def graph_recall(question: str, limit: int = 10) -> dict[str, Any]:
    nodes = read_jsonl(GRAPH_NODES)
    edges = read_jsonl(GRAPH_EDGES)
    q_tokens = tokenize(question)
    scored_nodes: list[tuple[float, dict[str, Any]]] = []
    for node in nodes:
        text = " ".join([node.get("name", ""), node.get("type", ""), *node.get("aliases", [])])
        tokens = tokenize(text)
        overlap = len(q_tokens & tokens)
        exact = 2 if node.get("name", "").lower() in question.lower() else 0
        score = overlap + exact
        if score > 0:
            scored_nodes.append((score, node))
    scored_nodes.sort(key=lambda item: item[0], reverse=True)
    selected_nodes = {node["id"]: node for _, node in scored_nodes[:limit]}
    selected_edges: list[dict[str, Any]] = []
    for edge in edges:
        if edge.get("source") in selected_nodes or edge.get("target") in selected_nodes:
            selected_edges.append(edge)
            for node in nodes:
                if node["id"] in {edge.get("source"), edge.get("target")}:
                    selected_nodes[node["id"]] = node
    paths = [
        {
            "source": edge.get("source_name"),
            "relation": edge.get("relation"),
            "target": edge.get("target_name"),
            "reason": edge.get("reason"),
            "source_ids": edge.get("source_ids", []),
            "source_kinds": edge.get("source_kinds", []),
        }
        for edge in selected_edges[:limit]
    ]
    return {
        "query": question,
        "matched_nodes": list(selected_nodes.values())[: limit * 2],
        "paths": paths,
        "recall_method": "token_overlap_graph_traversal",
    }
