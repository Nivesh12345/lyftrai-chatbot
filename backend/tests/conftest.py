import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import llm
from app.main import create_app


class FakeLiteLLM:
    """Stands in for litellm.completion: emulates a live call + a response cache."""

    def __init__(self):
        self.cache = {}
        self.live_calls = 0
        self.failing_keys = set()  # keys that "return 429" for real (not simulated)
        self.failing_models = set()
        self.keys_used = []

    def completion(self, model, messages, api_key, caching=True, **_):
        self.keys_used.append(api_key)
        cache_key = json.dumps([model, messages], sort_keys=True)
        if caching and cache_key in self.cache:
            reply, usage = self.cache[cache_key]
            return self._resp(reply, usage, cache_hit=True)
        if model in self.failing_models:
            raise RuntimeError("model unavailable")
        if api_key in self.failing_keys:
            raise RuntimeError("429 quota exceeded")
        self.live_calls += 1
        reply = f"echo: {messages[-1]['content']} (turn {len(messages)})"
        usage = (sum(len(m["content"].split()) for m in messages) + 3, len(reply.split()))
        if caching:
            self.cache[cache_key] = (reply, usage)
        return self._resp(reply, usage, cache_hit=False)

    @staticmethod
    def _resp(reply, usage, cache_hit):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
            usage=SimpleNamespace(prompt_tokens=usage[0], completion_tokens=usage[1]),
            _hidden_params={"cache_hit": cache_hit},
        )


@pytest.fixture
def fake(monkeypatch):
    f = FakeLiteLLM()
    monkeypatch.setattr(llm.litellm, "completion", f.completion)
    return f


@pytest.fixture
def client(tmp_path, monkeypatch, fake):
    monkeypatch.setenv("LLM_API_KEYS", "secret-a,secret-b")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/app.db")
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "true")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:3000")
    with TestClient(create_app()) as c:
        yield c
