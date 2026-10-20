from __future__ import annotations

import json
import sys
import threading
import types
from contextlib import contextmanager, nullcontext
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import app as api_app
from api import orchestrator
from api.config import CoatingApiSettings
from api.schemas import ChatRequest


class Repository:
    def __init__(self):
        self.turns = []
        self.traces = []
        self.lock_entries = []

    @contextmanager
    def conversation_lock(self, conversation_id):
        self.lock_entries.append(conversation_id)
        yield

    def record_turn(self, **turn):
        self.turns.append(turn)

    def sync_conversation_snapshot(self, **kwargs):
        pass

    def record_trace(self, **trace):
        self.traces.append(trace)


class Runtime:
    def __init__(self, events=(), release=None, error=None):
        self.events = events
        self.release = release
        self.error = error
        self.entered = threading.Event()
        self.saved = []
        self.calls = 0
        self.active = 0
        self.max_active = 0

    def patched_workspace(self, *args):
        return nullcontext()

    def prepare_user_turn(self, question):
        return {}

    def _work(self):
        self.calls += 1
        self.active += 1
        self.max_active = max(self.active, self.max_active)
        self.entered.set()
        try:
            if self.release is not None:
                self.release.wait(0.5)
            if self.error:
                raise self.error
        finally:
            self.active -= 1

    def stream_model(self, *args, **kwargs):
        self._work()
        yield from self.events

    def call_model(self, *args, **kwargs):
        self._work()
        return {"answer": "complete answer", "provider": "test", "model": "test-model"}

    def save_assistant_turn(self, question, answer, provider, model):
        self.saved.append(answer)
        return {}


@pytest.fixture
def make_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_STREAM_HEARTBEAT_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(orchestrator, "_stream_diagnostics", lambda *a, **k: None)
    monkeypatch.setattr(api_app, "_stream_diagnostics", lambda *a, **k: None)

    def make(runtime):
        return orchestrator.CoatingConversationEngine(
            CoatingApiSettings(data_root=tmp_path, disable_auth=True),
            repository=Repository(),
            runtime=runtime,
        )

    return make


def test_live_progress_is_forwarded_but_never_saved(make_engine):
    progress = {"type": "progress", "stage": "evidence", "batch_count": 4, "completed": 2,
                "content": "NOT ANSWER"}
    heartbeat = {"type": "heartbeat"}
    runtime = Runtime([progress, heartbeat, {"type": "delta", "content": "answer"}])
    engine = make_engine(runtime)
    events = list(engine.stream_openai_chat_completion(ChatRequest(question="q")))
    assert progress in events
    assert heartbeat in events
    assert runtime.saved == ["answer"]
    assert [t["content"] for t in engine.repository.turns] == ["q", "answer"]
    assert events[-1]["type"] == "done"
    assert len(engine.repository.traces) == 1


@pytest.mark.parametrize("mode", ["live", "buffered", "native"])
def test_blocked_work_keeps_alive_without_partial_answers(make_engine, mode):
    release = threading.Event()
    runtime = Runtime([{"type": "delta", "content": "complete answer"}], release=release)
    engine = make_engine(runtime)
    request = ChatRequest(question="q")
    if mode == "live":
        source = engine.stream_openai_chat_completion(request)
    elif mode == "buffered":
        source = api_app._buffered_openai_events(engine, request, 4)
    else:
        source = engine.stream_chat(request)
    try:
        first = next(source)
        saved_at_first = list(runtime.saved)
    finally:
        release.set()
        rest = list(source)
    assert first.get("type") == "heartbeat"
    assert saved_at_first == []
    assert runtime.calls == 1
    assert runtime.saved == ["complete answer"]
    assert rest[-1].get("type", rest[-1].get("event")) == "done"


def test_platform_progress_uses_comments_not_openai_content(make_engine, monkeypatch):
    monkeypatch.setenv("COATING_PLATFORM_STREAM_MODE", "live")
    runtime = Runtime([
        {"type": "progress", "stage": "evidence\ndata: injected", "batch_count": 3, "completed": 1},
        {"type": "heartbeat"},
        {"type": "delta", "content": "answer"},
    ])
    engine = make_engine(runtime)
    with TestClient(api_app.create_app(settings=engine.settings, engine=engine)) as client:
        response = client.post("/api/ask/stream/chat/completions", json={
            "messages": [{"role": "user", "content": "q"}],
        })
    comments = [line for line in response.text.splitlines() if line.startswith(":")]
    assert any(line.startswith(": progress ") for line in comments)
    assert ": heartbeat" in comments
    progress = json.loads(next(line.removeprefix(": progress ") for line in comments
                               if line.startswith(": progress ")))
    assert progress["batch_count"] == 3 and progress["completed"] == 1
    data = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    assert data[-1] == "[DONE]"
    chunks = [json.loads(line) for line in data[:-1]]
    assert "".join(c["choices"][0]["delta"].get("content", "") for c in chunks) == "answer"
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert runtime.saved == ["answer"]


@pytest.mark.parametrize("path", ["/api/ask/stream/chat/completions", "/api/v1/coating/chat/stream"])
def test_buffered_sse_heartbeat_is_comment(make_engine, monkeypatch, path):
    monkeypatch.setenv("COATING_PLATFORM_STREAM_MODE", "buffered")
    runtime = Runtime(release=threading.Event())
    engine = make_engine(runtime)
    payload = {"question": "q"} if path.endswith("chat/stream") else {
        "messages": [{"role": "user", "content": "q"}],
    }
    with TestClient(api_app.create_app(settings=engine.settings, engine=engine)) as client:
        response = client.post(path, json=payload)
    assert response.status_code == 200
    assert response.text.startswith(": heartbeat\n\n")
    assert response.headers["x-accel-buffering"] == "no"
    assert runtime.saved == ["complete answer"]
    assert runtime.calls == 1


def test_progress_pressure_does_not_evict_answer(make_engine, monkeypatch):
    monkeypatch.setenv("COATING_STREAM_EVENT_QUEUE_MAXSIZE", "4")
    paused = threading.Event()
    release = threading.Event()

    def upstream():
        yield {"type": "delta", "content": "first"}
        assert release.wait(2)
        yield {"type": "delta", "content": "second"}
        for completed in range(100):
            yield {"type": "progress", "stage": "evidence", "completed": completed}
        paused.set()

    runtime = Runtime(upstream())
    engine = make_engine(runtime)
    source = engine.stream_openai_chat_completion(ChatRequest(question="q"))
    assert next(source)["content"] == "first"
    release.set()
    assert paused.wait(2)
    events = list(source)
    assert any(event.get("content") == "second" for event in events)
    assert events[-1]["type"] == "done"
    assert runtime.saved == ["firstsecond"]


@pytest.mark.parametrize("second_mode", ["live", "buffered"])
def test_same_conversation_is_serial_even_without_repository_lock(make_engine, second_mode):
    release = threading.Event()
    runtime = Runtime([{"type": "delta", "content": "answer"}], release=release)
    engine = make_engine(runtime)
    request = ChatRequest(question="q", conversation_id="same")
    first = engine.stream_openai_chat_completion(request)
    second = (engine.stream_openai_chat_completion(request) if second_mode == "live"
              else api_app._buffered_openai_events(engine, request, 4))
    try:
        next(first)
        assert runtime.entered.wait(1)
        assert runtime.active == 1
        next(second)
        assert runtime.calls == 1
    finally:
        release.set()
        list(first)
        list(second)
    assert runtime.calls == 2
    assert runtime.max_active == 1
    assert [t["role"] for t in engine.repository.turns] == ["user", "assistant", "user", "assistant"]
    assert engine.repository.lock_entries == ["same", "same"]


def test_buffered_exception_reaches_existing_terminal_handler(make_engine, monkeypatch):
    monkeypatch.setenv("COATING_PLATFORM_STREAM_MODE", "buffered")
    runtime = Runtime(release=threading.Event(), error=ValueError("failed"))
    engine = make_engine(runtime)
    with TestClient(api_app.create_app(settings=engine.settings, engine=engine)) as client:
        response = client.post("/api/ask/stream/chat/completions", json={
            "messages": [{"role": "user", "content": "q"}],
        })
    assert ": heartbeat\n\n" in response.text
    assert response.text.endswith("data: [DONE]\n\n")
    assert runtime.saved == []
    assert engine.repository.traces[0]["error"] == "failed"


@pytest.mark.parametrize("mode", ["chat", "live", "buffered", "native"])
def test_evidence_receipt_retained_outside_answer(make_engine, mode):
    receipt = {"batch_count": 2, "completed": 2, "failed_objects": []}

    class ReceiptRuntime(Runtime):
        def call_model(self, *args, **kwargs):
            return {**super().call_model(*args, **kwargs), "evidence_delivery": receipt}

    runtime = ReceiptRuntime([
        {"type": "delta", "content": "complete answer"},
        {"type": "done", "evidence_delivery": receipt},
    ])
    engine = make_engine(runtime)
    request = ChatRequest(question="q", options={"include_debug": True})
    if mode == "chat":
        result = engine.chat(request)
        assert result.evidence_delivery == receipt
        assert result.debug["evidence_delivery"] == receipt
    else:
        if mode == "live":
            source = engine.stream_openai_chat_completion(request)
        elif mode == "buffered":
            source = api_app._buffered_openai_events(engine, request, 4)
        else:
            source = engine.stream_chat(request)
        done = list(source)[-1]
        assert done.get("data", done)["evidence_delivery"] == receipt
    assert runtime.saved == ["complete answer"]


@pytest.mark.parametrize("mode", ["live", "buffered"])
def test_six_hundred_seconds_of_silence_has_no_transport_deadline(make_engine, monkeypatch, mode):
    release = threading.Event()
    simulated_wait = 0
    real_queue = orchestrator.queue.Queue

    class SilentQueue(real_queue):
        def get(self, block=True, timeout=None):
            nonlocal simulated_wait
            if timeout is not None and simulated_wait < 600:
                simulated_wait += timeout
                raise orchestrator.queue.Empty
            release.set()
            return super().get(block=block, timeout=timeout)

    runtime = Runtime([{"type": "delta", "content": "answer"}], release=release)
    engine = make_engine(runtime)
    monkeypatch.setattr(orchestrator, "_STREAM_HEARTBEAT_SECONDS", 15)
    monkeypatch.setattr(orchestrator.queue, "Queue", SilentQueue)
    request = ChatRequest(question="q")
    source = (engine.stream_openai_chat_completion(request) if mode == "live"
              else api_app._buffered_openai_events(engine, request, 4))
    try:
        for _ in range(40):
            assert next(source) == {"type": "heartbeat"}
        assert runtime.saved == []
        events = list(source)
    finally:
        release.set()
        list(source)
    assert simulated_wait == 600
    assert events[-1]["type"] == "done"
    assert runtime.calls == 1


@pytest.mark.parametrize("mode", ["live", "buffered"])
def test_disconnect_does_not_duplicate_or_cancel_persistence(make_engine, mode):
    release = threading.Event()
    runtime = Runtime([{"type": "delta", "content": "answer"}], release=release)
    engine = make_engine(runtime)
    request = ChatRequest(question="q", conversation_id=f"disconnect-{mode}")
    source = (engine.stream_openai_chat_completion(request) if mode == "live"
              else api_app._buffered_openai_events(engine, request, 4))
    try:
        assert next(source)["type"] == "heartbeat"
        source.close()
    finally:
        release.set()
        for thread in threading.enumerate():
            if thread.name.endswith(request.conversation_id):
                thread.join(timeout=2)
                assert not thread.is_alive()
    assert runtime.calls == 1
    assert len(runtime.saved) == 1
    assert [t["role"] for t in engine.repository.turns] == ["user", "assistant"]
    assert len(engine.repository.traces) == 1
    assert request.conversation_id not in orchestrator._conversation_locks


@pytest.mark.parametrize("existing_app", [False, True])
@pytest.mark.parametrize("body_raises", [False, True])
def test_workspace_waiter_restores_app_captured_under_lock(tmp_path, monkeypatch, existing_app, body_raises):
    original = types.ModuleType("original_app") if existing_app else None
    monkeypatch.setitem(sys.modules, "app", original)
    if original is None:
        sys.modules.pop("app")
    waiter_attempted_lock = threading.Event()
    owner = threading.current_thread()
    workspace_lock = threading.RLock()

    class ObservedLock:
        def __enter__(self):
            if threading.current_thread() is not owner:
                waiter_attempted_lock.set()
            workspace_lock.acquire()

        def __exit__(self, *args):
            workspace_lock.release()

    runtime = orchestrator.LegacyRuntime.__new__(orchestrator.LegacyRuntime)
    runtime._lock = ObservedLock()
    module = types.ModuleType("workspace_test")
    module.SESSION_ID = "original"
    runtime.modules = [module]
    runtime._ensure_platform_seed_data = lambda: None
    errors = []

    def wait_for_workspace():
        try:
            with pytest.raises(ValueError, match="body failed") if body_raises else nullcontext():
                with runtime.patched_workspace(tmp_path / "second", "second"):
                    assert module.SESSION_ID == "second"
                    assert sys.modules["app"].SESSION_ID == "second"
                    if body_raises:
                        raise ValueError("body failed")
        except BaseException as exc:
            errors.append(exc)

    waiter = threading.Thread(target=wait_for_workspace, daemon=True)
    try:
        with runtime.patched_workspace(tmp_path / "first", "first"):
            assert module.SESSION_ID == "first"
            waiter.start()
            # The waiter reaches lock acquisition while the first shim is installed.
            assert waiter_attempted_lock.wait(2)
            assert sys.modules["app"].SESSION_ID == "first"
    finally:
        if waiter.ident is not None:
            waiter.join(timeout=2)
    assert not waiter.is_alive()
    assert not errors, errors
    assert module.SESSION_ID == "original"
    if existing_app:
        assert sys.modules["app"] is original
    else:
        assert "app" not in sys.modules
