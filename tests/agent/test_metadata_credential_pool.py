"""Metadata credential selection must not claim or maintain an authentication pool."""

import json
import time

from agent.metadata_credentials.pool import load_probe_pool

URL = "http://127.0.0.1:43210/v1"


def _manual(row_id, url=URL, **overrides):
    return {
        "id": row_id, "label": row_id, "source": "manual", "auth_type": "api_key",
        "base_url": url, "access_token": f"dummy-{row_id}", "priority": 0, **overrides,
    }


def test_snapshot_skips_unsafe_rows_and_leaves_expired_rows_on_disk(tmp_path, monkeypatch):
    from hermes_cli import auth

    path = tmp_path / "auth.json"
    monkeypatch.setattr(auth, "_auth_file_path", lambda: path)
    monkeypatch.setattr(auth, "_global_auth_file_path", lambda: None)
    payload = json.dumps({"credential_pool": {"lab": [
        _manual("unbound", url=""),
        _manual("other-port", url="http://127.0.0.1:43211/v1"),
        _manual("old-config", source="config:lab"),
        _manual("oauth", auth_type="oauth"),
        _manual("dead", last_status="dead", last_status_at=time.time() - 172800),
        _manual("bound"),
    ]}})
    path.write_text(payload, encoding="utf-8")

    pool = load_probe_pool("lab", {"base_url": URL}, URL)
    assert pool.choose().access_token == "dummy-bound"
    assert path.read_text(encoding="utf-8") == payload
    assert not load_probe_pool("lab", {"base_url": URL}, "http://127.0.0.1:43211/v1")


def test_corrupt_auth_file_is_not_repaired_by_metadata_read(tmp_path, monkeypatch):
    from hermes_cli import auth

    path = tmp_path / "auth.json"
    inherited = tmp_path / "root" / "auth.json"
    inherited.parent.mkdir()
    monkeypatch.setattr(auth, "_auth_file_path", lambda: path)
    monkeypatch.setattr(auth, "_global_auth_file_path", lambda: inherited)
    for target in (path, inherited):
        target.write_text("{broken", encoding="utf-8")

    assert load_probe_pool("lab", {"base_url": URL}, URL) is None
    for target in (path, inherited):
        assert target.read_text(encoding="utf-8") == "{broken"
        assert not target.with_suffix(".json.corrupt").exists()
