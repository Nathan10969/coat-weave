import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
import answering


def test_projection_keeps_dimensions_and_vocab_definitions():
    row = {'tool': 'kg.sql_aggregate', 'status': 'ok', 'result': {
        'status': 'ok', 'classification_complete': False,
        'classification_coverage': {'complete_for_requested_axes': False},
        'items': [{'value': 'marine | unknown_year', 'count': 2,
                   'group_values': {'application_domains': 'marine', 'publication_year': 'unknown_year'},
                   'dimension': 'coating_functions', 'definition': 'Intended function, not measured result',
                   'aliases': ['anticorrosive'], 'roles': ['resin'], 'canonical_ids': ['MAT_TEST']}],
    }}
    before = copy.deepcopy(row)
    projected = answering._compact_current_tool_observation(row)
    assert projected['result']['items'] == row['result']['items']
    assert projected['result']['classification_complete'] is False
    assert row == before


def test_model_instructions_distinguish_missing_coverage_from_absence():
    prompt = answering.build_model_messages('q', {})[0]['content']
    assert 'classification_complete' in prompt
    assert 'NOT proof of absence' in prompt
    assert 'cohort_available' in prompt


def test_missing_classification_does_not_imply_topic_relevance():
    prompt = answering.build_model_messages('marine antifouling patent counts', {})[0]['content']
    assert 'Missing or unmapped classification proves neither relevance nor irrelevance' in prompt
    assert 'their relevance is unknown and relevant records may have been missed' in prompt
    assert 'do not assert that unmapped records are actually relevant' in prompt
    assert 'or estimate missed relevant patents from the missing/unmapped counts' in prompt


def test_coverage_memberships_are_not_unique_patent_counts():
    prompt = answering.build_model_messages('classification coverage', {})[0]['content']
    assert 'source-doc memberships, not unique patents' in prompt
    assert 'the same publication may occur in multiple sources' in prompt
    assert 'Do not relabel source_doc_count, unmapped_profile_count' in prompt
    assert 'or add them to classified matches' in prompt


def test_projection_preserves_missing_memberships_without_inventing_relevance():
    coverage = {
        'source_doc_count': 2, 'publication_count': 1, 'unmapped_profile_count': 2,
        'requested_axes': ['application_domains', 'coating_functions'],
        'complete_for_requested_axes': False,
        'unresolved_examples': [
            {'source_id': 'source_a', 'doc_id': 'DOC1', 'state': 'missing_profile'},
            {'source_id': 'source_b', 'doc_id': 'DOC1', 'state': 'unmapped'},
        ],
    }
    row = {'tool': 'kg.sql_aggregate', 'status': 'empty', 'result': {
        'status': 'empty', 'items': [], 'classification_complete': False,
        'classification_coverage': coverage, 'summary': {'total_distinct_docs': 0},
    }}
    before = copy.deepcopy(row)
    projected = answering._compact_current_tool_observation(row)
    assert projected == before
    assert projected['result']['classification_coverage']['source_doc_count'] == 2
    assert projected['result']['classification_coverage']['publication_count'] == 1
    assert projected['result']['summary']['total_distinct_docs'] == 0
    assert row == before


def empty_count_packet(**result_fields):
    return {'tool_observations': [{'tool': 'kg.sql_aggregate', 'status': 'empty', 'result': {
        'status': 'empty', 'summary': {'distinct_count': 0},
        'query_interpretation': {'intent': 'distinct_count', 'target': 'doc_id',
                                 'filters': {'coating_functions': ['antifouling'], 'assignees': ['Fixture company']}},
        'warnings': ['aggregate_filters_vocabulary_valid_no_matches'], **result_fields,
    }}]}


def test_exact_empty_keeps_conditions_without_requesting_another_question():
    packet = empty_count_packet(classification_complete=True, cohort_available=True)
    before = copy.deepcopy(packet)
    text = answering.aggregate_count_statements(packet)[0]
    assert '当前可查询数据在该口径下没有匹配记录' in text
    assert '已保持原筛选条件' in text and 'Fixture company' in text and 'antifouling' in text
    assert all(phrase not in text for phrase in ('再问', '重试', '更宽', '换一个'))
    assert packet == before


@pytest.mark.parametrize('fields', [
    {'classification_complete': False},
    {'classification_coverage': {'complete_for_requested_axes': False}},
    {'warnings': ['aggregate_filters_vocabulary_valid_no_matches',
                  'classification_coverage_incomplete_not_evidence_of_absence']},
])
def test_incomplete_coverage_overrides_stale_true_zero_warning(fields):
    packet = empty_count_packet(**fields)
    text = answering.aggregate_count_statements(packet)[0]
    assert '分类覆盖不完整' in text and '是否相关未知，可能漏检' in text
    assert '当前可查询数据在该口径下没有匹配记录' not in text
    assert 'Fixture company' in text and 'antifouling' in text
    assert '再问' not in text


def test_unavailable_cohort_is_not_an_observed_zero():
    text = answering.aggregate_count_statements(empty_count_packet(cohort_available=False))[0]
    assert '统计数据本轮不可用' in text and '不能报告为 0' in text
    assert '统计命中 0' not in text


def test_generic_empty_does_not_request_filter_changes():
    text = answering.aggregate_count_statements(empty_count_packet(warnings=[]))[0]
    assert '不能据此断言' in text and '再问' not in text


@pytest.mark.parametrize('reason', ['first_token_timeout', 'content_idle_timeout', 'total_timeout'])
@pytest.mark.parametrize('had_content', [False, True])
def test_timeout_output_does_not_require_reasking_or_narrowing(reason, had_content):
    text = answering.stream_timeout_customer_message(reason, had_content=had_content)
    assert '未完成' in text
    assert all(phrase not in text for phrase in ('追问', '重试', '更窄', '再问'))


def test_filter_miss_prompt_does_not_request_rephrasing():
    prompt = answering.build_model_messages('q', {})[0]['content']
    assert 'original conditions without asking the user to rephrase or broaden them' in prompt
    assert 'concrete rephrasing to copy for the next turn' not in prompt
