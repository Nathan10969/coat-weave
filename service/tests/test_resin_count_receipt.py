import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'core'))
sys.path.insert(0, str(ROOT / 'kg_tools'))
import kg_expand_http_service as kg
import answering


@pytest.mark.parametrize('limit', [1, 50])
def test_resin_group_total_is_patents_not_resin_bucket_count(limit):
    rows = [{'doc_id': doc, 'hyperedge_id': he, 'resin_system': resin}
            for doc, he, resin in [('WO1A1', 'H1', 'epoxy'), ('WO1A1', 'H2', 'epoxy'),
                                   ('WO2A1', 'H3', 'epoxy'), ('WO3A1', 'H4', None)]]
    result = kg.run_resin_system_aggregate(matched=rows, all_hyperedges=rows,
        patents={}, evidence_map={}, target='resin_system', group_by=['resin_system'],
        filters={}, limit=limit, include_examples=False, warnings=[])
    assert result['summary']['total_count'] == 3
    assert result['summary']['matched_hyperedges'] == 4
    assert result['count_unit'] == 'patent'
    answer = answering.structured_tool_fallback_answer({'tool_observations': [
        {'tool': 'kg.sql_aggregate', 'status': 'ok', 'result': result}]})
    assert ' 3 ' in answer
