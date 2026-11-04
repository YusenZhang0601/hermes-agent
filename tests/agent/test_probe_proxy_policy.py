"""Metadata probes must not be handed to an inherited HTTP proxy for a LAN/Tailscale box.

Reproduced in the wild: HTTP_PROXY=http://127.0.0.1:7897 plus
NO_PROXY=localhost,127.0.0.1,::1,.local,10.0.0.0/8,100.64.0.0/10,172.16.0.0/12,192.168.0.0/16
(Clash-style CIDR list, which httpx cannot parse) made every probe to an engine at
100.109.104.79:8080 ReadTimeout at the full 2.0s budget — 12s for the
detect_local_server_type waterfall — so GET /api/model/info blew past the Desktop
Model Settings request timeout and context-length resolution fell back to the
hardcoded catalog.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx
import pytest

from agent import model_metadata, model_metadata_http


@pytest.fixture
def servers():
    active = []

    def start(handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        active.append((server, thread))
        return f"http://127.0.0.1:{server.server_port}"

    yield start
    for server, thread in reversed(active):
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    "base_url,expected",
    [
        ("http://100.109.104.79:8080/v1", False),  # Tailscale CGNAT (RFC 6598)
        ("http://100.64.0.1:8080/v1", False),  # bottom of the CGNAT block
        ("http://192.168.18.28:8080/v1", False),  # LAN
        ("http://10.0.5.9:11434", False),  # RFC-1918
        ("http://127.0.0.1:8080/v1", False),  # loopback
        ("http://localhost:8080/v1", False),
        ("http://ollama-box.local:11434", False),  # mDNS
        ("https://api.openai.com/v1", True),  # hosted: keep the environment proxy
        ("https://openrouter.ai/api/v1", True),
        ("", True),  # unknown target: leave the caller's default behaviour alone
    ],
)
def test_probe_trust_env_policy(base_url, expected):
    assert model_metadata._probe_trust_env(base_url) is expected


def test_local_probe_succeeds_while_http_proxy_is_set_to_a_dead_port(servers, monkeypatch):
    """The regression: an unreachable inherited proxy must not stall a local probe."""
    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    dead_port = probe.getsockname()[1]
    probe.close()  # nothing listens here any more

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/v1/models":
                body = b'{"data":[{"id":"qwen-local","meta":{"n_ctx":262144}}]}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format, *args):  # signature required by BaseHTTPRequestHandler
            pass

    base_url = servers(Handler)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, f"http://127.0.0.1:{dead_port}")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,::1,.local,10.0.0.0/8,100.64.0.0/10,172.16.0.0/12,192.168.0.0/16")
    monkeypatch.setattr(model_metadata, "_endpoint_probe_path_cache", {})
    monkeypatch.setattr(model_metadata, "_LOCAL_CTX_PROBE_CACHE", {})
    monkeypatch.setattr(model_metadata, "_local_probe_disk_get", lambda *a, **k: None)
    monkeypatch.setattr(model_metadata, "_local_probe_disk_put", lambda *a, **k: None)

    assert model_metadata.detect_local_server_type(base_url) is None
    assert model_metadata._query_local_context_length("qwen-local", base_url) == 262144


def test_hosted_probe_still_trusts_the_environment(monkeypatch):
    seen = []

    class RecordingClient:
        def __init__(self, **kwargs):
            seen.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, *a, **k):
            raise httpx.ConnectTimeout("stop after the kwargs assertion")

    monkeypatch.setattr(httpx, "Client", RecordingClient)
    monkeypatch.setattr(model_metadata, "_endpoint_probe_path_cache", {})
    monkeypatch.setattr(model_metadata, "_endpoint_blackholed", lambda url: False)
    monkeypatch.setattr(model_metadata, "_local_probe_disk_get", lambda *a, **k: None)

    # A non-registry domain is not skipped as a hosted provider, so the waterfall
    # actually builds a client and the kwarg is observable.
    model_metadata.detect_local_server_type("https://engine.example.invalid/v1")
    assert seen and seen[-1]["trust_env"] is True

    seen.clear()
    model_metadata.detect_local_server_type("http://100.109.104.79:8080/v1")
    assert seen and seen[-1]["trust_env"] is False

def test_metadata_http_layer_applies_the_same_policy(servers, monkeypatch):
    """``model_metadata_http.get`` is how llama.cpp ``/props`` is read; same rule, same bug.

    ``NO_PROXY`` is left empty here to isolate the mechanism: on the reporting machine the list is
    full of CIDR ranges httpx cannot match, which is the equivalent of no usable bypass entry.
    """
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/v1/props"):
                body = b'{"default_generation_settings":{"n_ctx":262144}}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format, *args):  # signature required by BaseHTTPRequestHandler
            pass

    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    dead_port = probe.getsockname()[1]
    probe.close()

    base_url = servers(Handler)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, f"http://127.0.0.1:{dead_port}")
    monkeypatch.setenv("NO_PROXY", "")

    assert model_metadata_http.get(f"{base_url}/v1/props", timeout=5).status_code == 200
    # A hosted destination must still be routed through the configured proxy: the fix is about
    # local engines, not about switching proxy support off.
    with pytest.raises(httpx.TransportError):
        model_metadata_http.get("http://probe.invalid/models", timeout=2)
