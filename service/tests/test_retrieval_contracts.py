from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "kg_tools"))
sys.path.insert(0, str(ROOT))

import answering
import demo_text
import kg_expand_http_service as kg_service
import routing
import tool_runtime


def hybrid_call(query: str, *, assignees: list[str] | None = None) -> dict:
    return {
        "tool": "kg.hybrid_search",
        "query": query,
        "top_k": 1,
        "candidate_k": 1,
        "expand_top_k": 1,
        "filters": {"assignees": assignees or []},
    }


def test_expand_selector_honors_thirty_item_limit() -> None:
    result = {
        "items": [
            {
                "rank": index,
                "object_id": f"COLL::AKZO::DOC{index:03d}::HE{index:03d}",
                "doc_id": f"DOC{index:03d}",
                "metadata": {},
            }
            for index in range(1, 51)
        ]
    }

    selected = tool_runtime.select_expand_object_ids(result, 30, "Akzo formulation")

    assert len(selected) == 30


def test_expand_selector_fills_limit_when_results_share_few_documents() -> None:
    result = {
        "items": [
            {
                "rank": index,
                "object_id": f"COLL::AKZO::DOC1::HE{index:03d}",
                "doc_id": "DOC1",
                "metadata": {},
            }
            for index in range(1, 41)
        ]
    }

    selected = tool_runtime.select_expand_object_ids(result, 30, "Akzo formulation")

    assert len(selected) == 30


def test_pagination_returns_second_page_without_repeating_first_page() -> None:
    items = [{"object_id": f"OBJ_{index:03d}"} for index in range(100)]

    first_page, first_meta = kg_service.paginate_search_items(items, offset=0, top_k=50)
    second_page, second_meta = kg_service.paginate_search_items(items, offset=50, top_k=50)

    assert [item["object_id"] for item in first_page] == [f"OBJ_{index:03d}" for index in range(50)]
    assert [item["object_id"] for item in second_page] == [f"OBJ_{index:03d}" for index in range(50, 100)]
    assert first_meta == {"offset": 0, "page_size": 50, "next_offset": 50, "has_more": True}
    assert second_meta == {"offset": 50, "page_size": 50, "next_offset": None, "has_more": False}


def test_more_route_reuses_query_company_scope_and_next_offset() -> None:
    previous = {
        "tool": "kg.hybrid_search",
        "result": {
            "query": "Akzo Nobel coating formulation",
            "applied_filters": {
                "assignees": ["Akzo Nobel"],
                "doc_ids": ["US_AKZO_1", "US_AKZO_2"],
            },
            "pagination": {"offset": 0, "page_size": 50, "next_offset": 50, "has_more": True},
        },
    }

    route = routing.pagination_route_from_observation("再给我更多", previous)
    call = route["calls"][0]

    assert call["query"] == "Akzo Nobel coating formulation"
    assert call["offset"] == 50
    assert call["filters"]["assignees"] == ["Akzo Nobel"]
    assert call["filters"]["doc_ids"] == ["US_AKZO_1", "US_AKZO_2"]
    assert call["top_k"] == 50
    assert call["expand_top_k"] == 30


def test_assignee_filter_resolves_to_hard_doc_scope() -> None:
    store = {
        "patents_by_doc": {
            "US_AKZO_1": {"doc_id": "US_AKZO_1", "applicants": ["Akzo Nobel Coatings International B.V."]},
            "US_PPG_1": {"doc_id": "US_PPG_1", "applicants": ["PPG Industries Ohio, Inc."]},
        }
    }

    resolved = kg_service.resolve_assignee_doc_ids(store, ["Akzo Nobel"])

    assert resolved == ["US_AKZO_1"]


def test_assignee_scope_intersects_explicit_doc_scope() -> None:
    store = {
        "patents_by_doc": {
            "US_AKZO_1": {"doc_id": "US_AKZO_1", "assignee": "Akzo Nobel"},
            "US_AKZO_2": {"doc_id": "US_AKZO_2", "assignee": "Akzo Nobel"},
            "US_PPG_1": {"doc_id": "US_PPG_1", "assignee": "PPG Industries"},
        }
    }

    filters, warnings = kg_service.apply_hard_search_scopes(
        store,
        {"doc_ids": ["US_AKZO_2", "US_PPG_1"], "assignees": ["Akzo Nobel"]},
    )

    assert filters["doc_ids"] == ["US_AKZO_2"]
    assert warnings == []


def test_material_roles_are_controlled_and_invalid_system_terms_are_dropped() -> None:
    raw = {"material_roles": ["pigments", "fillers", "epoxy", "zinc-rich primer", "additive"]}

    core_filters = demo_text.normalize_kg_search_filters(raw)
    service_filters = kg_service.normalize_search_filters(raw)

    assert core_filters["material_roles"] == ["pigment", "filler", "additive"]
    assert service_filters["material_roles"] == ["pigment", "filler", "additive"]


def test_material_role_is_a_hard_raw_hyperedge_filter() -> None:
    raw = {
        "doc_id": "DOC1",
        "hyperedge_id": "HE1",
        "pigments": [{"material": "zinc dust"}],
        "additives": [{"material": "defoamer"}],
    }

    assert kg_service.raw_matches_search_hard_filters(raw, {"material_roles": ["pigment"]})
    assert not kg_service.raw_matches_search_hard_filters(raw, {"material_roles": ["solvent"]})


def test_router_deduplicates_searches_and_inherits_company_constraint() -> None:
    raw = {
        "calls": [
            hybrid_call("epoxy zinc-rich primer", assignees=["Jotun"]),
            hybrid_call("salt spray performance"),
            hybrid_call("epoxy zinc-rich primer", assignees=["Jotun"]),
        ]
    }

    route = routing.sanitize_tool_routing(raw, "Jotun 环氧富锌底漆和盐雾性能", router="test")

    assert len(route["calls"]) == 2
    assert all(call["filters"]["assignees"] == ["Jotun"] for call in route["calls"])


def test_answer_gate_keeps_complete_items_from_partial_expansion() -> None:
    observations = [
        {
            "tool": "kg.hybrid_search",
            "status": "ok",
            "result": {
                "status": "ok",
                "items": [{"object_id": "PPG::HE1", "doc_id": "PPG", "text_preview": "unverified formula"}],
            },
        },
        {
            "tool": "kg.expand_hyperedge_multihop",
            "status": "ok",
            "result": {
                "status": "partial",
                "summary": {"requested": 2, "db_found": 1, "db_missing": 1},
                "items": [
                    {
                        "object_id": "PPG::HE1",
                        "doc_id": "PPG",
                        "hyperedge_id": "HE1",
                        "facts": [{"material": "resin", "value": 10}],
                        "evidence": [{"page": 1, "quote": "verified"}],
                        "unresolved": {"fact_ids": [], "evidence_ids": []},
                    },
                    {
                        "object_id": "PPG::HE2",
                        "doc_id": "PPG",
                        "hyperedge_id": "HE2",
                        "facts": [],
                        "evidence": [],
                        "unresolved": {"fact_ids": ["FACT_MISSING"], "evidence_ids": []},
                    },
                ],
            },
        },
    ]

    gated = answering.gate_tool_observations_for_answer(observations)

    assert "text_preview" not in gated[0]["result"]["items"][0]
    assert [item["hyperedge_id"] for item in gated[1]["result"]["items"]] == ["HE1"]
    assert gated[1]["result"]["evidence_gate"] == {
        "status": "verified",
        "verified_count": 1,
        "partial": True,
        "excluded_count": 1,
        "reason": "incomplete_items_excluded",
    }


def test_answer_gate_blocks_partial_expansion_without_complete_items() -> None:
    observations = [
        {
            "tool": "kg.expand_hyperedge_multihop",
            "status": "ok",
            "result": {
                "status": "partial",
                "summary": {"requested": 1, "db_found": 0, "db_missing": 1},
                "items": [
                    {
                        "object_id": "PPG::HE1",
                        "doc_id": "PPG",
                        "hyperedge_id": "HE1",
                        "facts": [],
                        "evidence": [],
                        "unresolved": {"fact_ids": ["FACT_MISSING"], "evidence_ids": []},
                    }
                ],
            },
        }
    ]

    gated = answering.gate_tool_observations_for_answer(observations)

    assert gated[0]["result"]["items"] == []
    assert gated[0]["result"]["evidence_gate"] == {
        "status": "blocked",
        "reason": "no_complete_expanded_items",
        "verified_count": 0,
    }
def test_answer_gate_keeps_only_complete_expanded_evidence() -> None:
    observations = [
        {
            "tool": "kg.expand_hyperedge_multihop",
            "status": "ok",
            "result": {
                "status": "ok",
                "items": [
                    {
                        "object_id": "AKZO::HE1",
                        "doc_id": "AKZO",
                        "hyperedge_id": "HE1",
                        "facts": [{"material": "resin", "value": 10}],
                        "evidence": [{"page": 17, "quote": "verified"}],
                        "unresolved": {"fact_ids": [], "evidence_ids": []},
                    }
                ],
            },
        }
    ]

    gated = answering.gate_tool_observations_for_answer(observations)

    assert len(gated[0]["result"]["items"]) == 1
    assert gated[0]["result"]["evidence_gate"] == {"status": "verified", "verified_count": 1}
