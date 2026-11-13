"""A failed ``/models`` probe must not trade a custom endpoint's own window for a catalog guess.

Reproduced on a keyed self-hosted gateway serving ``qwen3.8-flash-next`` with a 262,144-token window.
Whenever the probe failed (a caller without the key got 401; the engine behind the gateway was down
and the gateway answered 502), the resolver fell through to the hardcoded catalog, whose
``qwen3.8-flash`` entry (a hosted 1M model) matched by substring. Sessions then sized compression for
1M and overflowed the real window. The endpoint's last published window was already on disk, but its
memo was pruned five minutes after any later write.
"""
from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from agent import model_metadata, model_metadata_http

MODEL = "qwen3.8-flash-next"
WINDOW = 262_144
KEY = "dummy-gateway-key"
CATALOG_1M = model_metadata.DEFAULT_CONTEXT_LENGTHS["qwen3.8-flash"]


class _Gateway:
    """A keyed OpenAI-compatible gateway on loopback. ``failure`` is None (serve) or the HTTP status
    every ``/models`` request gets instead."""

    def __init__(self):
        self.failure = None
        gateway = self

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
                if gateway.failure is not None:
                    return self._reply(gateway.failure, b'{"error":{"message":"unavailable"}}')
                if self.headers.get("Authorization") != f"Bearer {KEY}":
                    return self._reply(401, b'{"error":{"message":"authentication failed"}}')
                card = {"id": MODEL, "object": "model", "owned_by": "strata", "meta": {"n_ctx": WINDOW}}
                return self._reply(200, json.dumps({"object": "list", "data": [card]}).encode())

            def do_POST(self):
                self._reply(404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def stop(self):
        if self.thread.is_alive():
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=5)


@pytest.fixture
def gateways(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # The gateway in the report is a public host; loopback stands in for it. Its probes still connect
    # directly: an environment or system proxy would answer for a stopped gateway.
    monkeypatch.setattr(model_metadata, "is_local_endpoint", lambda base_url: False)
    monkeypatch.setattr(model_metadata_http, "_stream_trust_env", lambda url: False)
    monkeypatch.setattr(model_metadata, "_endpoint_model_metadata_cache", {})
    monkeypatch.setattr(model_metadata, "_endpoint_model_metadata_cache_time", {})
    monkeypatch.setattr(model_metadata, "_FALLBACK_WARNED", set())
    started = []

    def start():
        started.append(_Gateway())
        return started[-1]

    yield start
    for gateway in started:
        gateway.stop()


def _window(base_url, api_key=KEY, model=MODEL):
    return model_metadata.get_model_context_length(model, base_url=base_url, api_key=api_key, provider="main")


def _memo_path():
    return model_metadata._get_endpoint_metadata_cache_path()


def _age_endpoint_memo(seconds):
    """Pretend every remembered ``/models`` answer is ``seconds`` old and forget the in-process memo."""
    data = json.loads(_memo_path().read_text(encoding="utf-8"))
    for entry in data.values():
        entry["at"] = time.time() - seconds
    _memo_path().write_text(json.dumps(data), encoding="utf-8")
    model_metadata._endpoint_model_metadata_cache.clear()
    model_metadata._endpoint_model_metadata_cache_time.clear()


def _remember(slot, *, age, model=MODEL):
    """A remembered successful answer for ``slot`` (a memo key as the disk memo stores it)."""
    _memo_path().parent.mkdir(parents=True, exist_ok=True)
    models = {model: {"context_length": WINDOW, "name": model}}
    _memo_path().write_text(json.dumps({slot: {"at": time.time() - age, "models": models}}), encoding="utf-8")


@pytest.mark.parametrize("failure", ["keyless-401", "upstream-502", "connection-refused"])
def test_a_failed_probe_keeps_the_window_the_endpoint_last_published(gateways, failure):
    gateway = gateways()
    assert _window(gateway.url) == WINDOW
    _age_endpoint_memo(600)  # past the five-minute freshness window: the next lookup probes again

    api_key = KEY
    if failure == "keyless-401":
        api_key = ""
    elif failure == "upstream-502":
        gateway.failure = 502
    else:
        gateway.stop()

    assert _window(gateway.url, api_key=api_key) == WINDOW


def test_another_endpoints_probe_does_not_discard_the_last_published_window(gateways):
    first, second = gateways(), gateways()
    assert _window(first.url) == WINDOW
    _age_endpoint_memo(600)
    assert _window(second.url) == WINDOW  # rewrites the shared memo file

    first.failure = 502
    assert _window(first.url) == WINDOW


@pytest.mark.parametrize("slot", ["{url}", "{root}", "{url} [credential-fingerprint]"])
def test_the_last_published_window_is_found_in_any_slot_of_the_endpoint(gateways, slot):
    """The probe tries the URL with and without ``/v1``, and memo slots may be per credential."""
    gateway = gateways()
    gateway.failure = 502
    _remember(slot.format(url=gateway.url, root=gateway.url[: -len("/v1")]), age=3600)

    assert _window(gateway.url) == WINDOW


def test_a_window_published_beyond_the_retention_is_not_used(gateways):
    gateway = gateways()
    gateway.failure = 502
    _remember(gateway.url, age=31 * 86400)

    assert _window(gateway.url) == model_metadata.DEFAULT_FALLBACK_CONTEXT


def test_an_old_catalog_does_not_lend_its_window_to_a_model_it_never_listed(gateways):
    gateway = gateways()
    gateway.failure = 502
    _remember(gateway.url, age=3600, model="some-other-model")

    assert _window(gateway.url) == model_metadata.DEFAULT_FALLBACK_CONTEXT


@pytest.mark.parametrize("failure", ["keyless-401", 400, 407, 429, 502, 503])
def test_a_failed_probe_without_history_does_not_take_a_larger_catalog_window(gateways, failure, caplog):
    gateway = gateways()
    api_key = KEY
    if failure == "keyless-401":
        api_key = ""
    else:
        gateway.failure = failure

    with caplog.at_level("WARNING", logger="agent.model_metadata"):
        window = _window(gateway.url, api_key=api_key)

    assert window == model_metadata.DEFAULT_FALLBACK_CONTEXT
    assert any("falling back" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("status", [404, 405, 501])
def test_an_endpoint_without_a_models_route_still_uses_the_catalog(gateways, status):
    """Proxied gateways with no usable ``/models`` route keep the catalog as their last resort."""
    gateway = gateways()
    gateway.failure = status

    assert _window(gateway.url) == CATALOG_1M


def test_an_exact_catalog_id_survives_a_refused_probe(gateways):
    """A proxied gateway that refuses the probe still serves a model the catalog names exactly."""
    gateway = gateways()

    assert _window(gateway.url, api_key="", model="qwen3.8-flash") == CATALOG_1M
