import json
from app.config import Settings
from app.redact import REDACTED, configured_secrets, redact, strip_reasoning
from app.tracing import Tracing, get_tracing, init_tracing
from tests.test_workbench import new_run, start, wait_status

pytest_plugins = ("tests.test_workbench",)


def test_redact_authorization_tavily_cookie_and_secrets():
    payload = {
        "headers": {"Authorization": "Bearer sk-live-secret", "Cookie": "session=abc", "Content-Type": "application/json"},
        "body": {"api_key": "tvly-secret", "query": "定价", "Authorization": "should hide"},
        "note": "user said use sk-live-secret and tvly-secret",
    }
    cleaned = redact(payload, ["sk-live-secret", "tvly-secret"])
    blob = json.dumps(cleaned, ensure_ascii=False)
    assert cleaned["headers"]["Authorization"] == REDACTED
    assert cleaned["headers"]["Cookie"] == REDACTED
    assert cleaned["body"]["api_key"] == REDACTED
    assert cleaned["body"]["Authorization"] == REDACTED
    assert "sk-live-secret" not in blob
    assert "tvly-secret" not in blob
    assert cleaned["headers"]["Content-Type"] == "application/json"
    assert "定价" in cleaned["body"]["query"]


def test_configured_secrets_and_reasoning_stripped():
    settings = Settings(_env_file=None, llm_api_key="llm-secret-key", tavily_api_key="tvly-aaaa", langfuse_secret_key="lf-secret", langfuse_redact="extra-token")
    secrets = configured_secrets(settings)
    assert "llm-secret-key" in secrets and "extra-token" in secrets
    message = {"content": "ok", "reasoning_content": "do not store", "reasoning": "hidden"}
    assert strip_reasoning(message) == {"content": "ok"}


def test_generation_redacts_before_leaving_process():
    recorded = []

    class FakeTrace:
        def generation(self, **kwargs):
            recorded.append(kwargs)

        def span(self, **kwargs):
            return self

        def update(self, **kwargs):
            recorded.append(("update", kwargs))

        def end(self, **kwargs):
            return None

    class FakeClient:
        def trace(self, **kwargs):
            recorded.append(("trace", kwargs))
            return FakeTrace()

        def flush(self):
            recorded.append("flush")

    tracing = Tracing(enabled=True, client=FakeClient(), secrets=["super-secret"], host="http://localhost:3001")
    token = tracing.start("run_demo", name="research-run", metadata={"Authorization": "Bearer super-secret"})
    tracing.generation("llm.complete", model="demo", input={"Authorization": "Bearer super-secret", "text": "hello super-secret"}, output={"reasoning_content": "nope", "content": "visible"})
    tracing.reset(token)
    blob = json.dumps(recorded, ensure_ascii=False)
    assert "super-secret" not in blob
    assert "nope" not in blob
    generation = next(item for item in recorded if isinstance(item, dict) and item.get("name") == "llm.complete")
    assert generation["input"]["Authorization"] == REDACTED
    assert generation["output"] == {"content": "visible"}


async def test_disabled_tracing_completes_demo_run(runtime):
    engine, store = runtime
    assert get_tracing().enabled is False
    run = await new_run(engine, store, ["产品 A"])
    await start(engine, store, run)
    result = await wait_status(store, run["id"], {"completed", "failed"})
    assert result["status"] == "completed", result["error"]
    assert get_tracing().trace_url(run["id"]) is None


async def test_tracing_exception_does_not_fail_run(runtime, monkeypatch):
    class Boom:
        def trace(self, **kwargs):
            raise RuntimeError("langfuse unavailable")

        def score(self, **kwargs):
            raise RuntimeError("score failed")

        def flush(self):
            raise RuntimeError("flush failed")

    broken = Tracing(enabled=True, client=Boom(), secrets=[], host="http://localhost:3001")
    monkeypatch.setattr("app.engine.get_tracing", lambda: broken)
    monkeypatch.setattr("app.tracing.get_tracing", lambda: broken)
    engine, store = runtime
    run = await new_run(engine, store, ["产品 A"])
    await start(engine, store, run)
    result = await wait_status(store, run["id"], {"completed", "failed"})
    assert result["status"] == "completed", result["error"]


def test_init_tracing_off_without_keys(tmp_path):
    settings = Settings(_env_file=None, data_dir=str(tmp_path))
    tracing = init_tracing(settings)
    assert tracing.enabled is False
    assert tracing.trace_url("run_x") is None
