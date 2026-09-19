"""One registry defines model schema and runtime validation, without keyword routing."""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
import kg_contract
import query_classification


def test_registry_axes_are_present_in_both_tools_and_grouping():
    definitions = query_classification.axis_definitions()
    contract = kg_contract.load_tool_contract()
    for tool in ('kg.hybrid_search', 'kg.sql_aggregate'):
        properties = kg_contract.openai_filter_properties(tool)
        for axis, definition in definitions.items():
            assert properties[axis]['items']['enum'] == definition['values']
            assert properties[axis]['description'] == definition['definition']
            assert axis in contract['aggregate']['group_by']
            assert axis in contract['aggregate']['document_level_group_by']


def test_known_alias_wrong_dimension_and_unknown_all_have_receipts():
    raw = {'coating_functions': ['anti-corrosion', 'primer', 'unknown-purpose'],
           'application_domains': ['marine']}
    before = copy.deepcopy(raw)
    result = kg_contract.normalize_filter_request('kg.hybrid_search', raw)
    assert result['effective_filters']['coating_functions'] == ['anticorrosion']
    assert len(result['unsupported_constraints']) == 2
    assert any(c['dimension'] == 'coating_layers' and c['value'] == 'primer'
               for c in result['constraint_candidates'])
    assert raw == before
    twice = kg_contract.normalize_filter_request('kg.hybrid_search', result['effective_filters'])
    assert twice['effective_filters'] == result['effective_filters']


def test_legacy_market_is_not_silently_reinterpreted_as_function():
    result = kg_contract.normalize_filter_request('kg.sql_aggregate', {
        'application_family': ['protective_anticorrosive']})
    assert result['effective_filters']['application_family'] == ['protective_anticorrosive']
    assert not result['effective_filters']['coating_functions']
    assert 'legacy_semantics_preserved:application_family' in result['route_adjustments']


def test_unknown_property_does_not_turn_into_unfiltered_success():
    result = kg_contract.normalize_filter_request('kg.sql_aggregate', {
        'property_families': ['marine_corrosion', 'never-a-registered-property']})
    assert result['effective_filters']['property_families'] == ['marine_corrosion']
    assert result['unsupported_constraints'] == ['property_families:never-a-registered-property']


def test_vocabulary_tool_has_shared_discovery_parameters():
    schema = kg_contract.openai_tool_parameters('kg.lookup_vocabulary')
    assert schema['required'] == ['query']
    assert set(schema['properties']) == {'query', 'dimension', 'limit'}
    assert schema['properties']['limit']['maximum'] == 100
