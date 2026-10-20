import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
import routing


def test_lookup_only_model_completion_does_not_force_hybrid(monkeypatch):
    monkeypatch.setattr(routing, 'route_tools_with_qwen', lambda *a, **kw: {
        'calls': [], 'needs_tools': False, 'router': 'llm-function-calling'})
    monkeypatch.setattr(routing, '_record_route_decision', lambda *a: None)
    monkeypatch.setattr(routing, 'fallback_tool_routing', lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError('lookup completion must not become a forced search')))
    result = routing.replan_tools_after_feedback('Explain the vocabulary dimensions only', {
        'calls': [{'tool': 'kg.lookup_vocabulary', 'query': 'primer'}]}, [
        {'tool': 'kg.lookup_vocabulary', 'result': {'status': 'ok', 'items': [
            {'dimension': 'coating_layers', 'value': 'primer', 'definition': 'Primer layer'}]}}])
    assert result['calls'] == []
