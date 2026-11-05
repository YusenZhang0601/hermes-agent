"""Custom summary endpoints use their own credential, including the main alias (#91308)."""

import httpx
import pytest


@pytest.mark.parametrize(
    ("endpoint", "explicit_key", "expected_key"),
    [
        ("https://selfhost.example.invalid/v1", None, "dummy-selfhost"),
        ("https://other.example.invalid/v1", None, "no-key-required"),
        ("https://api.openai.com/v1", None, "dummy-openai"),
        ("https://selfhost.example.invalid/v1", "dummy-explicit", "dummy-explicit"),
    ],
)
def test_summary_route_authorization(tmp_path, monkeypatch, endpoint, explicit_key, expected_key):
    from agent import auxiliary_client as aux
    from agent.secret_scope import reset_secret_scope, set_secret_scope
    from openai import OpenAI

    home = tmp_path / "profile"
    home.mkdir()
    (home / "config.yaml").write_text(
        "model:\n  provider: main\n  default: test-model\n", encoding="utf-8"
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    observed = []

    def respond(request):
        observed.append(request.headers["authorization"])
        return httpx.Response(200, json={
            "id": "test-summary", "object": "chat.completion", "created": 0,
            "model": "test-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "summary"},
                         "finish_reason": "stop"}],
        })

    def create_client(**kwargs):
        return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(respond)))

    monkeypatch.setattr(aux, "_create_openai_client", create_client)
    runtime = {"provider": "custom", "model": "test-model",
               "base_url": "https://selfhost.example.invalid/v1", "api_key": "dummy-selfhost"}
    token = set_secret_scope({"OPENAI_API_KEY": "dummy-openai"})
    try:
        with aux.scoped_runtime_main(runtime):
            client, _ = aux.resolve_provider_client(
                "main", "test-model", explicit_base_url=endpoint,
                explicit_api_key=explicit_key, main_runtime=runtime,
            )
            try:
                result = client.chat.completions.create(
                    model="test-model", messages=[{"role": "user", "content": "summarize"}],
                )
            finally:
                client.close()
    finally:
        reset_secret_scope(token)
    assert result.choices[0].message.content == "summary"
    assert observed == [f"Bearer {expected_key}"]

