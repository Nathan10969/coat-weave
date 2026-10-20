import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
import answering


def aggregate(intent='distinct_count', target='doc_id', count=65, items=None, **fields):
    result = {
        'status': 'ok', 'count_unit': 'patent', 'cohort_available': True,
        'query_interpretation': {'intent': intent, 'target': target, 'group_by': [],
                                 'filters': {'application_domains': ['marine']}},
        'summary': {'distinct_count': count, 'total_count': count},
        'items': items or [], **fields,
    }
    return {'tool': 'kg.sql_aggregate', 'status': result['status'], 'result': result}


def packet(*rows):
    return {'tool_observations': list(rows), 'recent_tool_observations': [
        {'tool': 'kg.hybrid_search', 'status': 'ok', 'result': {'items': []}}]}


def render(*rows):
    return answering.structured_aggregate_answer(packet(*rows))


def test_marine_coverage_is_membership_not_relevance_or_expansion():
    row = aggregate(classification_complete=False, classification_coverage={
        'scope': 'db_cohort_before_profile_axes', 'source_doc_count': 1002,
        'publication_count': 907, 'unmapped_profile_count': 855,
        'requested_axes': ['coating_functions'], 'complete_for_requested_axes': False,
        'axes': {'coating_functions': {'mapped': 0, 'partial': 86, 'unclassified': 911,
                                     'missing_profile': 5}},
    }, items=[{'value': 'DOC', 'count': 1, 'examples': [
        {'doc_id': 'WO2014131695A1', 'page': 999, 'quote': 'UNVERIFIED_FORMULA',
         'classification': {'claim': 'UNVERIFIED_TOPIC'}}]}])
    text = render(row)
    assert '65 篇专利' in text
    assert '1002 个 source-doc' in text and '907 篇去重公开文献' in text
    assert '855 个 source-doc' in text and '911' in text and '86' in text
    assert '是否相关未知，可能漏检' in text
    assert '不能据此估计漏检数量或专利密度' in text
    assert all(x not in text for x in ('1002 篇', '855 篇', '实际具有', '已展开',
                                     'UNVERIFIED_FORMULA', 'UNVERIFIED_TOPIC', '999'))


def test_absent_coverage_counts_are_not_invented():
    text = render(aggregate(classification_coverage={
        'complete_for_requested_axes': False, 'requested_axes': ['coating_functions']}))
    assert 'source_doc_count' not in text and '篇去重公开文献' not in text
    assert '0 个 source-doc' not in text


def test_all_groups_and_complete_unknown_year_document_list():
    ids = ['EP1257607B1', 'US6703070B1', 'WO2015002958A1', 'WO2015002961A1', 'WO2019157211A1']
    items = [{'value': str(year), 'group_values': {'publication_year': str(year)},
              'count': 1, 'doc_count': 1} for year in range(1950, 2010)]
    items.append({'value': 'unknown_year', 'group_values': {'publication_year': 'unknown_year'},
                  'count': 5, 'doc_count': 5, 'examples': [{'doc_id': doc, 'page': 987654} for doc in ids]})
    row = aggregate('group_count', count=65, items=items)
    row['result']['query_interpretation']['group_by'] = ['publication_year']
    row['result']['summary'].update(group_count=61, returned=61)
    text = render(row)
    assert all(str(year) in text for year in range(1950, 2010))
    assert all(doc in text for doc in ids)
    assert '文档清单：5/5（完整）' in text
    assert '987654' not in text and 'publication_year' in text


def test_partial_and_duplicate_identity_list_does_not_claim_complete():
    row = aggregate('group_count', items=[{
        'value': 'unknown_year', 'count': 5, 'doc_count': 5,
        'examples': [{'doc_id': '001_WO2015002958A1'}, {'doc_id': 'WO2015002958A1'}],
    }])
    text = render(row)
    assert '文档清单：1/5（不完整）' in text


def test_multiple_aggregates_filters_and_composite_memberships():
    first = aggregate('group_count', count=3, items=[{
        'value': 'marine | primer', 'group_values': {'application_domains': 'marine', 'coating_layers': 'primer'},
        'count': 2, 'doc_count': 2}])
    first['result'].update(requested_group_by=['application_domains', 'coating_layers'],
                           effective_group_by=['application_domains', 'coating_layers'],
                           requested_filters={'assignees': ['ACME'], 'qa_policy': 'include_all'},
                           effective_filters={'assignees': ['ACME'], 'qa_policy': 'include_all'})
    first['result']['summary'].update(total_distinct_docs=3, bucket_membership_count=4, group_count=2, returned=1)
    second = aggregate('list_distinct', count=1, items=[{'value': 'EP1257607B1', 'count': 1}])
    text = render(first, second)
    assert 'ACME' in text and 'include_all' not in text
    assert 'coating_layers' in text and 'primer' in text and 'EP1257607B1' in text
    assert '分组成员关系：4' in text
    assert '分组可重叠，不能将分组数相加当作去重专利数' in text
    assert '已返回 1/2 个分组，分组清单不完整' in text
    assert '| application_domains | coating_layers |' in text


@pytest.mark.parametrize('target,unit', [('formulation', '个配方'), ('resin_system', '个不同 resin_system 值')])
def test_distinct_unit_is_not_bucket_patent_unit(target, unit):
    row = aggregate(target=target, count=2)
    row['result']['summary'].update(distinct_count_unit=target, total_count=10)
    text = render(row)
    assert f'2 {unit}' in text and '2 篇专利' not in text


@pytest.mark.parametrize('incomplete', [False, True])
def test_empty_distinguishes_coverage(incomplete):
    row = aggregate(count=0, status='empty', classification_complete=not incomplete)
    text = render(row)
    assert '0 篇专利' in text
    if incomplete:
        assert '是否相关未知，可能漏检' in text
        assert '当前可查询数据在该口径下没有匹配记录' not in text
    else:
        assert '当前可查询数据在该口径下没有匹配记录' in text


@pytest.mark.parametrize('tool', ['kg.hybrid_search', 'kg.expand_hyperedge_multihop', 'kg.doc_field_scan', 'other'])
def test_mixed_tools_do_not_intercept(tool):
    assert render(aggregate(), {'tool': tool, 'status': 'ok', 'result': {}}) is None


@pytest.mark.parametrize('fields', [
    {'status': 'unsupported'}, {'status': 'error'}, {'cohort_available': False},
    {'unsupported_constraints': ['some_constraint']}, {'summary': {}},
    {'query_interpretation': {'intent': 'numeric_distribution', 'target': 'amount'}},
    {'items': [{'value': 'x', 'count': 'not-a-count'}]},
])
def test_invalid_aggregate_does_not_intercept(fields):
    assert render(aggregate(**fields)) is None


def test_lookup_only_and_unresolved_lookup_are_not_intercepted():
    lookup = {'tool': 'kg.lookup_vocabulary', 'status': 'ok', 'result': {'status': 'ok', 'query': 'ABC'}}
    assert render(lookup) is None
    assert render(lookup, aggregate()) is not None
    lookup['status'] = lookup['result']['status'] = 'unsupported'
    assert render(lookup, aggregate()) is None


@pytest.mark.parametrize('query_present', [True, False])
def test_successful_lookup_correction_resolves_only_its_own_receipt(query_present):
    failed = {'tool': 'kg.lookup_vocabulary', 'status': 'unsupported', 'result': {
        'status': 'unsupported', 'query': 'ABC', 'unsupported_constraints': ['dimension:material']}}
    if not query_present:
        del failed['result']['query']
    fixed = {'tool': 'kg.lookup_vocabulary', 'status': 'ok', 'result': {
        'status': 'ok', 'query': 'ABC', 'parameter_correction': {'unsupported_receipt': copy.deepcopy(failed['result'])}}}
    assert render(failed, fixed, aggregate()) is not None
    if query_present:
        fixed['result']['query'] = 'OTHER'
    else:
        fixed['result']['parameter_correction']['unsupported_receipt']['unsupported_constraints'] = ['different']
    assert render(failed, fixed, aggregate()) is None


def test_no_current_aggregate_does_not_reuse_history():
    assert answering.structured_aggregate_answer({'recent_tool_observations': [aggregate()]}) is None


def test_stat_only_is_readable_and_does_not_dump_candidate_items_or_debug_fields():
    row = aggregate(items=[{'value': 'UNREQUESTED_CANDIDATE', 'count': 1, 'examples': [
        {'doc_id': 'WO2015002958A1', 'page': 123}]}], warnings=['DEBUG_WARNING_CODE'],
        next_cursor={'debug': 'PRIVATE_CURSOR'}, effective_filters={'qa_policy': 'include_all', 'materials': ['ABC']})
    text = render(row)
    assert 'ABC' in text
    assert all(value not in text for value in ('UNREQUESTED_CANDIDATE', 'WO2015002958A1', 'qa_policy',
        'include_all', 'DEBUG_WARNING_CODE', 'PRIVATE_CURSOR', 'next_cursor', 'total_count', '{', '"'))


def test_table_preserves_numeric_sample_and_trend_constraints_without_new_claims():
    row = aggregate('group_count', items=[{
        'value': '2024 | comparative', 'group_values': {'publication_year': '2024', 'example_kind': 'comparative'},
        'count': 3}], requested_filters={'property': ['salt spray'], 'minimum': {'value': 1000, 'unit': 'h'},
                                        'example_kind': ['comparative']})
    text = render(row)
    assert '1000' in text and 'h' in text and 'comparative' in text and 'salt spray' in text
    assert '| 2024 | comparative | 3 |' in text


def test_mismatched_turn_tag_is_not_current_even_when_in_current_section():
    row = aggregate()
    row['turn_id'] = 'old'
    data = packet(row)
    data['recent_turns'] = [{'role': 'user', 'turn_id': 'current', 'content': 'q'}]
    assert answering.structured_aggregate_answer(data) is None
    row['turn_id'] = 'current'
    assert answering.structured_aggregate_answer(data) is not None


def test_list_distinct_keeps_every_returned_item():
    values = [f'DOC_{index:03d}' for index in range(80)]
    text = render(aggregate('list_distinct', count=80, items=[{'value': value, 'count': 1} for value in values]))
    assert all(value in text for value in values)


def test_lookup_correction_survives_current_observation_projection():
    failed = {'tool': 'kg.lookup_vocabulary', 'status': 'unsupported', 'result': {
        'status': 'unsupported', 'items': [], 'unsupported_constraints': ['dimension:material'],
        'endpoint': 'fixture', 'elapsed_ms': 12, 'lookup_resolution_incomplete': True}}
    fixed = {'tool': 'kg.lookup_vocabulary', 'status': 'ok', 'result': {
        'status': 'ok', 'query': 'ABC', 'items': [],
        'parameter_correction': {'unsupported_receipt': copy.deepcopy(failed['result'])}}}
    data = packet(failed, fixed, aggregate())
    projected = answering.compact_packet_for_answer(data)
    assert answering.structured_aggregate_answer(projected) == answering.structured_aggregate_answer(data)


def test_scope_clarification_still_precedes_aggregate(monkeypatch):
    monkeypatch.setattr(answering, 'provider_config', lambda: {'model': 'fixture'})
    data = packet(aggregate())
    data['last_scope_resolution'] = {'scope_action': 'clarify'}
    result = answering.call_qwen('q', data)
    assert result['provider'] == 'scope-resolver'


def test_stream_structured_path_obeys_track_a_projection_before_helper(monkeypatch):
    current = aggregate()
    current['turn_id'] = 'current'
    data = packet(current)
    data.update(
        recent_turns=[{'role': 'user', 'turn_id': 'current', 'content': 'stats'}],
        session_summary={'seed': 'AUDIT_ONLY'}, stale_or_superseded=[aggregate(count=999)],
        future_audit_section={'seed': 'FUTURE_AUDIT'},
        project_state=[{'status': 'active', 'content': 'ACTIVE'},
                       {'status': 'stale', 'content': 'STALE'}, {'content': 'MISSING_STATUS'}],
        relevant_memories=[{'status': 'superseded', 'content': 'SUPERSEDED'}],
        memory_graph_recall={'matched_nodes': [
            {'name': 'seed', 'source_kinds': ['seed']},
            {'name': 'observed', 'source_kinds': ['observation']}],
            'paths': [{'source_name': 'seed', 'target_name': 'observed', 'source_kinds': ['seed']}]},
    )
    before = copy.deepcopy(data)
    original = answering.structured_aggregate_answer
    seen = []
    def inspect(projected):
        seen.append(projected)
        assert projected == answering.project_model_packet(data)
        assert 'session_summary' not in projected and 'future_audit_section' not in projected
        assert 'stale_or_superseded' not in projected
        assert projected['project_state'] == [{'status': 'active', 'content': 'ACTIVE'}]
        assert projected['relevant_memories'] == []
        assert [node['name'] for node in projected['memory_graph_recall']['matched_nodes']] == ['observed']
        assert projected['memory_graph_recall']['paths'] == []
        return original(projected)
    monkeypatch.setattr(answering, 'structured_aggregate_answer', inspect)
    monkeypatch.setattr(answering, 'provider_config', lambda: {'model': 'fixture'})
    result = answering.call_qwen('stats', data)
    assert result['provider'] == 'structured-aggregate' and len(seen) == 1
    assert data == before


@pytest.mark.parametrize('status', ['active', 'stale', 'superseded', None])
def test_structured_aggregate_requires_tool_success_status_not_memory_status(status):
    row = aggregate()
    row['status'] = status
    assert render(row) is None


@pytest.mark.parametrize('fields', [
    {'classification_coverage': 'invalid'}, {'query_interpretation': 'invalid'},
    {'classification_coverage': {'source_doc_count': 'not-a-count'}},
    {'items': [{'value': 'x', 'count': 1, 'group_values': 'invalid'}]},
])
def test_malformed_receipts_do_not_crash_or_deliver_statistics(fields):
    assert render(aggregate(**fields)) is None


def test_stream_and_buffered_bypass_model_record_outcome_and_preserve_packet(monkeypatch):
    data = packet(aggregate())
    data['evidence_delivery'] = {'existing': 'preserve'}
    before = copy.deepcopy(data)
    monkeypatch.setattr(answering, 'provider_config', lambda: {'model': 'fixture', 'api_key': 'unused'})
    def forbidden(*args, **kwargs):
        pytest.fail('pure aggregate must not call model or evidence planning')
    monkeypatch.setattr(answering, 'build_model_messages', forbidden)
    monkeypatch.setattr(answering.evidence_delivery, 'plan_batches', forbidden)
    result = answering.call_qwen('stats and classification impact', data)
    events = list(answering.stream_qwen('stats and classification impact', data))
    assert result['answer'] == ''.join(e.get('content', '') for e in events)
    assert result['provider'] == 'structured-aggregate'
    assert result['evidence_delivery']['answer_outcome'] == 'structured_aggregate'
    assert result['evidence_delivery']['attempts'] == 0
    assert result['evidence_delivery']['existing'] == 'preserve'
    assert data == before
