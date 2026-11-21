"""Select already-bound, persisted API keys without acquiring a runtime pool."""

from typing import Any

from agent.credential_pool import AUTH_TYPE_API_KEY, CredentialPool, PooledCredential, _is_manual_source
from hermes_cli.auth import read_credential_pool
from utils import same_base_url_origin


class _ProbePool(CredentialPool):
    """A disposable snapshot: no refresh, resync, or credential-store writes."""

    def _resync_stale_entry(self, entry: PooledCredential) -> PooledCredential:
        return entry

    def _persist(self, **kwargs: Any) -> None:
        # Availability checks can prune dead entries even for choose().
        # That cleanup applies only to our snapshot, never the store.
        pass


def load_probe_pool(pool_key: str, owner: dict | None, base_url: str) -> CredentialPool | None:
    """Read a pool's already-bound manual keys at this origin.

    An optional metadata probe cannot bind legacy rows to an address, seed keys,
    or refresh OAuth. Current declared keys are resolved separately from config.
    """
    owner_url = (owner or {}).get("base_url")
    if not base_url or (owner_url and not same_base_url_origin(owner_url, base_url)):
        return None
    entries = []
    for row in read_credential_pool(pool_key, read_only=True):
        if not isinstance(row, dict) or not _is_manual_source(row.get("source") or ""):
            continue
        if row.get("auth_type") != AUTH_TYPE_API_KEY:
            continue
        url = row.get("base_url")
        if isinstance(url, str) and same_base_url_origin(url, base_url):
            entries.append(PooledCredential.from_dict(pool_key, row))
    return _ProbePool(pool_key, entries) if entries else None
