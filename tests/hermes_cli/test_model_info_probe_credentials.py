"""/api/model/info must probe the context window with the key config declares for the endpoint.

The Desktop Model Settings page and session model controls call this route. It probed
``model.base_url`` without a key, so a keyed gateway answered 401 to every lookup, and the
"auto-detected" window fell back to a catalog substring match (1M for ``qwen3.8-flash-next``,
whose engine serves 262,144). The route is public, so the key is read without side effects.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

MODEL = "qwen3.8-flash-next"
WINDOW = 262_144
KEY = "dummy-main-key"


@pytest.fixture
def gateway():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _reply(self, status, body=b""):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.rstrip("/") not in ("/v1/models", "/models"):
                return self._reply(404)
            seen.append(self.headers.get("Authorization"))
            if self.headers.get("Authorization") != f"Bearer {KEY}":
                return self._reply(401, b'{"error":{"message":"authentication failed"}}')
            card = {"id": MODEL, "object": "model", "owned_by": "strata", "meta": {"n_ctx": WINDOW}}
            return self._reply(200, json.dumps({"object": "list", "data": [card]}).encode())

        def do_POST(self):
            self._reply(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1", seen
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def _selfhost_config(base_url, *, provider_url=None, extra=""):
    return (
        "model:\n"
        "  provider: main\n"
        f"  default: {MODEL}\n"
        f"  base_url: {base_url}\n"
        "providers:\n"
        "  main:\n"
        "    name: selfhost\n"
        f"    base_url: {provider_url or base_url}\n"
        f"    model: {MODEL}\n"
        f"{extra}"
    )


def test_model_info_probes_with_the_named_providers_key_env(gateway, home, monkeypatch):
    base_url, seen = gateway
    (home / "config.yaml").write_text(_selfhost_config(base_url, extra="    key_env: TEST_SELFHOST_KEY\n"))
    monkeypatch.setenv("TEST_SELFHOST_KEY", KEY)
    from hermes_cli.web_routers import models as router

    info = router.get_model_info()

    assert info["model"] == MODEL
    assert info["auto_context_length"] == WINDOW
    assert seen and set(seen) == {f"Bearer {KEY}"}


def test_model_info_probes_with_the_model_sections_key_env(gateway, home, monkeypatch):
    base_url, seen = gateway
    (home / "config.yaml").write_text(
        f"model:\n  provider: custom\n  default: {MODEL}\n  base_url: {base_url}\n  key_env: TEST_SELFHOST_KEY\n")
    monkeypatch.setenv("TEST_SELFHOST_KEY", KEY)
    from hermes_cli.web_routers import models as router

    assert router.get_model_info()["auto_context_length"] == WINDOW
    assert seen and set(seen) == {f"Bearer {KEY}"}


def test_a_key_declared_for_another_origin_is_not_sent(gateway, home, monkeypatch):
    base_url, seen = gateway
    elsewhere = "https://other-gateway.example.invalid/v1"
    (home / "config.yaml").write_text(
        _selfhost_config(base_url, provider_url=elsewhere, extra="    key_env: TEST_SELFHOST_KEY\n"))
    monkeypatch.setenv("TEST_SELFHOST_KEY", KEY)
    from hermes_cli.web_routers import models as router

    router.get_model_info()

    assert seen and set(seen) == {None}


def test_the_public_route_never_runs_a_key_command(gateway, home):
    base_url, _seen = gateway
    marker = home / "key_cmd_ran"
    (home / "config.yaml").write_text(_selfhost_config(base_url, extra=f"    key_cmd: touch {marker}\n"))
    from hermes_cli.web_routers import models as router

    router.get_model_info()

    assert not marker.exists()


def test_model_info_reads_the_requested_profiles_key(gateway, tmp_path, monkeypatch):
    """``?profile=`` resolves the key in that profile's secret scope, also inside the probe worker."""
    from hermes_cli import profiles

    base_url, seen = gateway
    default_home, worker_home = tmp_path / "default", tmp_path / "default" / "profiles" / "worker_beta"
    worker_home.mkdir(parents=True)
    (default_home / "config.yaml").write_text("{}\n")
    (worker_home / "config.yaml").write_text(_selfhost_config(base_url, extra="    key_env: TEST_SELFHOST_KEY\n"))
    (worker_home / ".env").write_text(f"TEST_SELFHOST_KEY={KEY}\n")
    monkeypatch.setenv("HERMES_HOME", str(default_home))
    monkeypatch.setenv("TEST_SELFHOST_KEY", "dummy-launch-profile-key")
    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: default_home)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: default_home / "profiles")
    from hermes_cli.web_routers import models as router

    info = router.get_model_info(profile="worker_beta")

    assert info["auto_context_length"] == WINDOW
    assert seen and set(seen) == {f"Bearer {KEY}"}
