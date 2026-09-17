"""Golden regression for the query-routing → aggregate-count pipeline.

Cases are frozen from the 2026-06-12 incident traces, where the same question
"你有多少篇光纤专利" produced 0 / 31 / 554 across four asks. This script asserts
three things per case:
  1. the routed call lands on the right tool / intent / filter dimensions;
  2. executing the call against the live KG service returns the golden count;
  3. an empty result under non-empty filters carries the filter-miss warning
     (never a silent 0).

Run on the serving host from the service root:
    python3 scripts/run_router_regression.py

Requires the KG tools service (port 8021) to be up. Router cases use the LLM
router when an API key is configured and fall back to keyword routing
otherwise — both paths must satisfy the same assertions.

GOLDEN COUNTS are pinned to the kg_286_aggregate data pack (2026-06-02, 554
patents). Update them deliberately when the data pack is rebuilt.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv(SERVICE_ROOT / ".env")
sys.path.insert(0, str(SERVICE_ROOT / "core"))

import routing  # noqa: E402
from tool_clients import kg_sql_aggregate  # noqa: E402
from tool_runtime import retry_empty_aggregate  # noqa: E402

# Family-axis count: distinct patents whose application_family (post-taxonomy
# remap, 2026-06-12) contains "optical_fiber". Replaces the old substrate-route
# golden of 31 for router cases — the two axes legitimately differ.
GOLDEN_FIBER_FAMILY_PATENTS = 37
# Substrate-axis count: distinct patents matched by the raw substrate strings
# ("optical fiber" etc.) via the fuzzy substrates matcher. Still used by the
# empty-fallback case, whose recovery path retries demoted values as substrates.
GOLDEN_FIBER_SUBSTRATE_PATENTS = 31
GOLDEN_ALL_PATENTS = 554

FAILURES: list[str] = []
PASSES: list[str] = []


def check(case: str, condition: bool, detail: str) -> None:
    if condition:
        PASSES.append(case)
        print(f"  PASS  {case}: {detail}")
    else:
        FAILURES.append(case)
        print(f"  FAIL  {case}: {detail}")


def guarded(case: str, fn) -> None:
    """Run one case; an exception (LLM endpoint down, KG service 5xx) fails
    that case instead of killing the whole regression."""
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        FAILURES.append(case)
        print(f"  FAIL  {case}: raised {type(exc).__name__}: {exc}")


def route(question: str) -> dict:
    decision = routing.route_tools_with_qwen(question)
    if not decision.get("calls"):
        decision = routing.fallback_tool_routing(question)
    return decision


def first_aggregate_call(decision: dict) -> dict | None:
    for call in decision.get("calls") or []:
        if call.get("tool") == "kg.sql_aggregate":
            return call
    return None


def execute(call: dict) -> dict:
    return kg_sql_aggregate(
        intent=call["intent"],
        target=call["target"],
        filters=call.get("filters"),
        group_by=call.get("group_by") or [],
        limit=call.get("limit") or 50,
        include_examples=bool(call.get("include_examples", False)),
    )


def substrates_lower(call: dict) -> set[str]:
    return {str(v).lower() for v in (call.get("filters") or {}).get("substrates") or []}


def run_fiber_routing_case(case: str, question: str) -> None:
    print(f"\n[{case}] {question}")
    decision = route(question)
    call = first_aggregate_call(decision)
    check(f"{case}/tool", call is not None, f"router={decision.get('router')} picked kg.sql_aggregate")
    if call is None:
        return
    check(f"{case}/intent", call.get("intent") == "distinct_count", f"intent={call.get('intent')}")
    check(f"{case}/target", call.get("target") == "doc_id", f"target={call.get('target')}")
    families = [str(v).strip().lower() for v in (call.get("filters") or {}).get("application_family") or []]
    check(
        f"{case}/family",
        "optical_fiber" in families,
        f"application_family={families}",
    )
    leftovers = [
        value
        for value in families
        if value not in routing.load_kg_filter_vocab().get("application_family", set())
        and value not in routing.APPLICATION_FAMILY_EXACT_MATCH_EXEMPT
    ]
    check(f"{case}/vocab_guard", not leftovers, f"non-vocabulary application_family leftovers={leftovers}")
    result = execute(call)
    count = (result.get("summary") or {}).get("distinct_count")
    check(
        f"{case}/count",
        count == GOLDEN_FIBER_FAMILY_PATENTS,
        f"distinct_count={count} (golden {GOLDEN_FIBER_FAMILY_PATENTS}), status={result.get('status')}",
    )


def case_fiber_fallback() -> None:
    # Deterministic fallback path must hit the golden count without any LLM.
    print("\n[fiber_fallback] build_aggregate_call (no-LLM path)")
    fallback_call = routing.build_aggregate_call("你有多少篇光纤专利")
    families = [str(v).strip().lower() for v in (fallback_call.get("filters") or {}).get("application_family") or []]
    check(
        "fiber_fallback/family",
        "optical_fiber" in families,
        f"application_family={families}",
    )
    fallback_result = execute(fallback_call)
    fallback_count = (fallback_result.get("summary") or {}).get("distinct_count")
    check(
        "fiber_fallback/count",
        fallback_count == GOLDEN_FIBER_FAMILY_PATENTS,
        f"distinct_count={fallback_count}",
    )


def case_all_patents() -> None:
    # Whole-KG count: empty filters are CORRECT here; the count must be the library total.
    print("\n[all_patents] 你有多少篇专利")
    decision = route("你有多少篇专利")
    call = first_aggregate_call(decision)
    check("all_patents/tool", call is not None, f"router={decision.get('router')}")
    if call is not None:
        result = execute(call)
        count = (result.get("summary") or {}).get("distinct_count")
        check("all_patents/count", count == GOLDEN_ALL_PATENTS, f"distinct_count={count} (golden {GOLDEN_ALL_PATENTS})")


def case_marine() -> None:
    # Marine corrosion keeps working (the one family the backend matches by alias).
    print("\n[marine] 有多少篇船舶防腐专利")
    decision = route("有多少篇船舶防腐专利")
    call = first_aggregate_call(decision)
    check("marine/tool", call is not None, f"router={decision.get('router')}")
    if call is not None:
        result = execute(call)
        count = (result.get("summary") or {}).get("distinct_count")
        check("marine/count_positive", isinstance(count, int) and count > 0, f"distinct_count={count}")


def case_automotive() -> None:
    # The hypernym undercount this taxonomy fixes: "automotive" used to
    # exact-match only 6 patents (missing automotive_oem's 81); the family
    # key now covers the whole automotive group.
    print("\n[automotive] 汽车涂料有多少篇专利")
    decision = route("汽车涂料有多少篇专利")
    call = first_aggregate_call(decision)
    check("automotive/tool", call is not None, f"router={decision.get('router')}")
    if call is not None:
        families = [str(v).strip().lower() for v in (call.get("filters") or {}).get("application_family") or []]
        check("automotive/family", "automotive" in families, f"application_family={families}")
        result = execute(call)
        count = (result.get("summary") or {}).get("distinct_count")
        check("automotive/count", count == 107, f"distinct_count={count} (golden 107, was 6 pre-taxonomy)")


def case_material_role_alias() -> None:
    # Guard for the singular-key fix: "material_role" (singular) used to be
    # silently dropped by the aggregate filter normalizer, turning a filtered
    # count into the whole-KG count. The alias must now resolve to
    # material_roles and produce the same count as the plural key.
    print("\n[material_role_alias] filters={'material_role': ['resin']} (singular key)")
    singular = kg_sql_aggregate(
        intent="distinct_count", target="doc_id", filters={"material_role": ["resin"]},
        group_by=[], limit=1, include_examples=False,
    )
    interp_roles = ((singular.get("query_interpretation") or {}).get("filters") or {}).get("material_roles")
    check(
        "material_role_alias/interpreted",
        bool(interp_roles),
        f"query_interpretation.filters.material_roles={interp_roles}",
    )
    singular_count = (singular.get("summary") or {}).get("distinct_count")
    check(
        "material_role_alias/filter_applied",
        isinstance(singular_count, int) and 1 <= singular_count <= GOLDEN_ALL_PATENTS - 1,
        f"distinct_count={singular_count} (must be 1..{GOLDEN_ALL_PATENTS - 1}: neither 0 nor whole-KG {GOLDEN_ALL_PATENTS})",
    )
    plural = kg_sql_aggregate(
        intent="distinct_count", target="doc_id", filters={"material_roles": ["resin"]},
        group_by=[], limit=1, include_examples=False,
    )
    plural_count = (plural.get("summary") or {}).get("distinct_count")
    check(
        "material_role_alias/plural_equivalent",
        singular_count == plural_count,
        f"singular distinct_count={singular_count}, plural distinct_count={plural_count}",
    )


def case_assignee_alias() -> None:
    # Same fix, assignee dimension: the "applicants" alias must resolve to the
    # assignees filter instead of being dropped. JOTUN is a high-frequency
    # applicant in the data pack, so both spellings must agree on a positive count.
    print("\n[assignee_alias] filters={'applicants': ['JOTUN']} vs {'assignees': ['JOTUN']}")
    alias_result = kg_sql_aggregate(
        intent="distinct_count", target="doc_id", filters={"applicants": ["JOTUN"]},
        group_by=[], limit=1, include_examples=False,
    )
    canonical_result = kg_sql_aggregate(
        intent="distinct_count", target="doc_id", filters={"assignees": ["JOTUN"]},
        group_by=[], limit=1, include_examples=False,
    )
    alias_count = (alias_result.get("summary") or {}).get("distinct_count")
    canonical_count = (canonical_result.get("summary") or {}).get("distinct_count")
    check(
        "assignee_alias/equivalent_positive",
        isinstance(alias_count, int) and alias_count > 0 and alias_count == canonical_count,
        f"applicants distinct_count={alias_count}, assignees distinct_count={canonical_count}",
    )


def case_empty_fallback() -> None:
    # The empty-fallback safety net: a bad application_family route must
    # recover via substrates and never return a bare 0. The 4-value set below
    # is the incident trace's exact filter, empirically == 31 patents.
    print("\n[empty_fallback] application_family=['optical fiber', ...] (simulated bad route)")
    bad_values = ["optical fiber", "optical fibre", "fiber optic", "fibre optic"]
    bad_result = kg_sql_aggregate(
        intent="distinct_count", target="doc_id", filters={"application_family": bad_values},
        group_by=[], limit=50, include_examples=False,
    )
    if bad_result.get("status") == "empty":
        from demo_text import normalize_kg_aggregate_filters
        recovered = retry_empty_aggregate(
            kg_sql_aggregate, bad_result,
            intent="distinct_count", target="doc_id",
            filters=normalize_kg_aggregate_filters({"application_family": bad_values}),
            group_by=[], limit=50, include_examples=False,
        )
        count = (recovered.get("summary") or {}).get("distinct_count")
        check("empty_fallback/recovered", recovered.get("status") != "empty", f"status={recovered.get('status')}")
        check("empty_fallback/count", count == GOLDEN_FIBER_SUBSTRATE_PATENTS, f"distinct_count={count}")
        check(
            "empty_fallback/warning",
            any("retried_as_substrates" in w for w in recovered.get("warnings") or []),
            f"warnings={recovered.get('warnings')}",
        )
    else:
        # Vocabulary guard upstream may already prevent this from ever being
        # empty (e.g. backend matching改了); that is also acceptable — note it.
        check("empty_fallback/skipped", True, f"bad filters no longer empty (status={bad_result.get('status')}) — backend matching improved?")


def case_true_zero() -> None:
    # Vocabulary-valid empty intersection must stay a true zero, NOT be
    # rewritten into a union count (review finding: marine ∩ fiber == 0).
    print("\n[true_zero] application_family=['marine'] + substrates=fiber (true empty intersection)")
    filters = {
        "application_family": ["marine"],
        "substrates": ["optical fiber", "optical fibre"],
    }
    result = kg_sql_aggregate(
        intent="distinct_count", target="doc_id", filters=filters,
        group_by=[], limit=50, include_examples=False,
    )
    if result.get("status") != "empty":
        check("true_zero/skipped", True, f"intersection not empty (status={result.get('status')}) — data changed, re-pin this case")
        return
    from demo_text import normalize_kg_aggregate_filters
    recovered = retry_empty_aggregate(
        kg_sql_aggregate, result,
        intent="distinct_count", target="doc_id",
        filters=normalize_kg_aggregate_filters(filters),
        group_by=[], limit=50, include_examples=False,
    )
    check("true_zero/not_rewritten", recovered.get("status") == "empty", f"status={recovered.get('status')} (must stay empty)")
    check(
        "true_zero/warning",
        any("vocabulary_valid" in str(w) for w in recovered.get("warnings") or []),
        f"warnings={recovered.get('warnings')}",
    )


def case_determinism() -> None:
    # Routing determinism: three asks of the SAME question must execute to
    # the same count (the 2026-06-12 incident was 0/31/554 here).
    print("\n[determinism] 你有多少篇光纤专利 ×3")
    counts: list[int | None] = []
    for _ in range(3):
        decision = route("你有多少篇光纤专利")
        call = first_aggregate_call(decision)
        if call is None:
            counts.append(None)
            continue
        counts.append((execute(call).get("summary") or {}).get("distinct_count"))
    check("determinism/stable", len(set(counts)) == 1 and counts[0] == GOLDEN_FIBER_FAMILY_PATENTS, f"counts={counts}")


def case_hint_predicate() -> None:
    # Pure-function check for the conv-hint suppression predicate: bare count
    # questions skip the hint, context-dependent follow-ups keep it.
    print("\n[hint_predicate] aggregate_question_self_contained")
    expectations = [
        ("你有多少篇光纤专利", True),
        # "你有多少篇专利" is deliberately NOT self-contained: without an
        # explicit whole-KG phrasing it may inherit scope from the prior turn,
        # so the hint stays (empty filters → whole-KG count remains correct).
        ("全库有多少篇专利", True),
        ("你有多少篇专利", False),
        ("那按公司分呢", False),
        ("其中多少是船舶的", False),
    ]
    for question, expected in expectations:
        actual = routing.aggregate_question_self_contained(question)
        check(f"hint_predicate/{question}", actual is expected, f"self_contained={actual} (expected {expected})")


def main() -> int:
    print(f"service root: {SERVICE_ROOT}")
    vocab_present = routing.KG_FILTER_VOCAB_PATH.exists()
    print(f"vocab file:   {routing.KG_FILTER_VOCAB_PATH} ({'present' if vocab_present else 'MISSING'})")
    check(
        "preflight/vocab_present",
        vocab_present,
        "kg_filter_vocab.json present" if vocab_present else "run scripts/export_kg_vocab.py first — the vocabulary guard is inactive without it",
    )

    guarded("fiber_zh_1", lambda: run_fiber_routing_case("fiber_zh_1", "你有多少篇光纤专利"))
    guarded("fiber_zh_2", lambda: run_fiber_routing_case("fiber_zh_2", "你有多少光纤专利"))
    guarded("fiber_fallback", case_fiber_fallback)
    guarded("all_patents", case_all_patents)
    guarded("marine", case_marine)
    guarded("automotive", case_automotive)
    guarded("material_role_alias", case_material_role_alias)
    guarded("assignee_alias", case_assignee_alias)
    guarded("empty_fallback", case_empty_fallback)
    guarded("true_zero", case_true_zero)
    guarded("determinism", case_determinism)
    guarded("hint_predicate", case_hint_predicate)

    print(f"\n{'=' * 50}\n{len(PASSES)} passed, {len(FAILURES)} failed")
    if FAILURES:
        print("failed: " + ", ".join(FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
