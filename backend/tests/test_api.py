def chat(client, session, message, headers=None, **extra):
    return client.post(
        "/chat", json={"session_id": session, "message": message, **extra}, headers=headers or {}
    )


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "keys_configured": 2, "cache_enabled": True}


def test_health_requires_two_keys(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app

    monkeypatch.setenv("LLM_API_KEYS", "only-key")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/app.db")
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "true")

    with TestClient(create_app()) as c:
        r = c.get("/health")

    assert r.status_code == 503
    assert r.json() == {"detail": "at least 2 api keys required"}


def test_chat_live_then_cache_in_new_session(client):
    a = chat(client, "s-1", "What is the capital of France?").json()
    assert a["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert a["metadata"]["key_id"] == "key_1"
    assert a["metadata"]["fallback_attempts"] == 0
    b = chat(client, "s-2", "What is the capital of France?").json()
    assert b["metadata"]["resolution_source"] == "LLM_CACHE"
    assert b["reply"] == a["reply"]
    assert b["metadata"]["key_id"] is None
    assert b["metadata"]["fallback_attempts"] == 0
    assert b["metadata"]["input_tokens"] == a["metadata"]["input_tokens"]
    assert b["metadata"]["output_tokens"] == a["metadata"]["output_tokens"]


def test_different_message_never_cached(client, fake):
    chat(client, "s-1", "hello")
    r = chat(client, "s-2", "goodbye").json()
    assert r["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert fake.live_calls == 2


def test_tokens_are_per_request_not_cumulative(client):
    first = chat(client, "s-1", "one two three").json()["metadata"]["input_tokens"]
    chat(client, "s-1", "four five six")
    other = chat(client, "s-2", "one two three").json()["metadata"]["input_tokens"]
    assert first == other


def test_multi_turn_history_is_used_and_sessions_are_separate(client):
    chat(client, "s-1", "my name is Ada")
    r = chat(client, "s-1", "what is my name?").json()
    assert "turn 4" in r["reply"]  # system + user + assistant + new user
    fresh = chat(client, "s-2", "what is my name?").json()
    assert "turn 2" in fresh["reply"]


def test_history_endpoint(client):
    chat(client, "s-1", "hi")
    data = client.get("/sessions/s-1/messages").json()
    assert data["session_id"] == "s-1"
    roles = [m["role"] for m in data["messages"]]
    assert roles == ["user", "assistant"]
    assert "metadata" not in data["messages"][0]
    meta = data["messages"][1]["metadata"]
    assert {"resolution_source", "input_tokens", "output_tokens"} <= set(meta)
    assert not any(m["role"] == "system" for m in data["messages"])


def test_unknown_session_404(client):
    r = client.get("/sessions/nope/messages")
    assert r.status_code == 404
    assert r.json() == {"detail": "session not found"}


def test_system_prompt_applies_to_session(client, fake):
    chat(client, "s-1", "hi", system_prompt="Answer like a pirate")
    chat(client, "s-1", "again")  # prompt is remembered for the session
    assert fake.live_calls == 2


# ---------- validation ----------
def test_validation_errors_422_and_not_stored(client):
    for body in [
        {},
        {"session_id": "s-1"},
        {"session_id": "s-1", "message": ""},
        {"session_id": "s-1", "message": "   "},
        {"session_id": "", "message": "hi"},
        {"session_id": "s-1", "message": "x" * 4001},
        {"session_id": "x" * 129, "message": "hi"},
    ]:
        r = client.post("/chat", json=body)
        assert r.status_code == 422, body
        assert "detail" in r.json()
    assert client.get("/sessions/s-1/messages").status_code == 404


def test_malformed_json_422(client):
    r = client.post("/chat", content="{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    assert "detail" in r.json()


def test_message_is_trimmed_to_4000(client):
    assert chat(client, "s-1", "  " + "a" * 4000 + "  ").status_code == 200


# ---------- failover ----------
def test_real_failure_falls_back_to_next_key(client, fake):
    fake.failing_keys = {"secret-a"}
    meta = chat(client, "s-1", "hi").json()["metadata"]
    assert meta["key_id"] == "key_2"
    assert meta["fallback_attempts"] == 1


def test_model_fallback_reaches_next_model(client, fake, monkeypatch):
    monkeypatch.setenv("LLM_MODELS", "model-one,model-two")
    from fastapi.testclient import TestClient
    from app.main import create_app

    with TestClient(create_app()) as c:
        fake.failing_models = {"model-one"}
        meta = chat(c, "s-model", "hi").json()["metadata"]

    assert meta["model"] == "model-two"
    assert meta["key_id"] == "key_1"
    assert meta["fallback_attempts"] == 2


def test_simulated_failure_key_1(client):
    for status in (429, 500, 502, 503):
        r = chat(client, f"s-{status}", "hi", headers={"X-Simulate-Failure": f"key_1={status}"})
        assert r.status_code == 200
        meta = r.json()["metadata"]
        assert meta["key_id"] == "key_2"
        assert meta["fallback_attempts"] == 1
        assert meta["resolution_source"] == "LIVE_INFERENCE"


def test_simulated_failure_skips_cache(client):
    chat(client, "s-1", "cached question")
    r = chat(client, "s-2", "cached question", headers={"X-Simulate-Failure": "key_1=429"})
    assert r.json()["metadata"]["key_id"] == "key_2"


def test_all_keys_fail_503_and_nothing_stored(client):
    for header in ("*=429", "key_1=429,key_2=500"):
        r = chat(client, "s-x", "hi", headers={"X-Simulate-Failure": header})
        assert r.status_code == 503
        assert r.json() == {"detail": "all providers unavailable"}
    assert client.get("/sessions/s-x/messages").status_code == 404


def test_simulation_ignored_when_disabled(client, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app

    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "false")
    with TestClient(create_app()) as c:
        r = chat(c, "s-1", "hi", headers={"X-Simulate-Failure": "*=429"})
        assert r.status_code == 200
        assert r.json()["metadata"]["key_id"] == "key_1"


def test_api_keys_never_leak(client):
    r = chat(client, "s-1", "hi")
    assert "secret-a" not in r.text and "secret-b" not in r.text


def test_cors_allows_frontend_origin(client):
    r = client.options(
        "/chat",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-simulate-failure",
        },
    )
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_parse_simulation():
    from app.llm import parse_simulation

    assert parse_simulation("key_1=429, key_2=500") == {"key_1": 429, "key_2": 500}
    assert parse_simulation("*=503") == {"*": 503}
    assert parse_simulation("key_1=418,junk,key_2=abc") == {}
    assert parse_simulation(None) == {}
