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



def test_aux_usage_pricing_probes_the_gateway_with_its_configured_key(tmp_path, monkeypatch):
    """Aux usage accounting prices the response without the aux client's key. A keyed gateway answered
    that keyless ``/models`` probe 401, so pricing must use the key config declares for the endpoint."""
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    from agent.usage_pricing import CanonicalUsage, estimate_usage_cost

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            status, body = 404, b""
            if self.path.rstrip("/") in ("/v1/models", "/models"):
                seen.append(self.headers.get("Authorization"))
                status, body = 401, b'{"error":{"message":"authentication failed"}}'
                if self.headers.get("Authorization") == "Bearer dummy-selfhost":
                    status, body = 200, json.dumps({"data": [{"id": "test-model", "meta": {"n_ctx": 262144}}]}).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/v1"
    (tmp_path / "config.yaml").write_text(
        f"model:\n  provider: main\n  default: test-model\n  base_url: {endpoint}\n"
        f"providers:\n  main:\n    name: selfhost\n    base_url: {endpoint}\n    key_env: TEST_SELFHOST_KEY\n",
        encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TEST_SELFHOST_KEY", "dummy-selfhost")
    try:
        estimate_usage_cost("test-model", CanonicalUsage(input_tokens=10, output_tokens=5),
                            provider="main", base_url=endpoint)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert seen and set(seen) == {"Bearer dummy-selfhost"}
