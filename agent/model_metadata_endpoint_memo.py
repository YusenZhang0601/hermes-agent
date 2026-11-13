"""Remote ``/models`` memo for ``agent.model_metadata``: catalog matching, failed probes, and the
window an endpoint last published.

A custom endpoint's probe can fail for reasons that say nothing about its catalog: the caller had no
key (401), the engine behind a gateway was down (502), the network timed out. Read as "this endpoint
publishes nothing", such a failure sent the resolver to the hardcoded catalog, whose substring match
can name a hosted model with a far larger window than the engine serves. A failed probe is therefore
memoized as an ``EndpointProbeFailure``, and the disk memo (``endpoint_model_metadata.json``) keeps
an endpoint's last successful answer long after it stops counting as fresh, so the resolver can
answer with what the endpoint itself last published.
"""
from __future__ import annotations

import contextvars
import logging
import time
from typing import Any, Optional

logger = logging.getLogger("agent.model_metadata")

# A remembered answer is fresh for ``_ENDPOINT_MODEL_CACHE_TTL``; it stays on disk this long so that a
# later failed probe can still use it.
LAST_PUBLISHED_RETENTION_SECONDS = 30 * 86400.0
# Answers showing the endpoint has no usable ``/models`` route. Any other failure (refused, throttled,
# server error, transport) leaves its catalog unknown.
_NO_MODELS_ROUTE_STATUSES = frozenset({404, 405, 410, 501})
# This call stack's latest ``/models`` result, ``(memo key, result)``. The shared in-process memo can change
# between the probe and the resolver's next step (another thread probing the same endpoint).
_LOOKUP: contextvars.ContextVar[Optional[tuple]] = contextvars.ContextVar("endpoint_models_lookup", default=None)


class EndpointProbeFailure(dict):
    """A failed ``/models`` probe. Every caller reads it as ``{}``; the context resolver also asks
    whether the failure leaves the catalog unknown or shows the endpoint has no usable route."""

    def __init__(self):
        super().__init__()
        self.statuses: list[Optional[int]] = []  # one per failed candidate URL; None = transport error

    def note(self, error: Any) -> None:
        """Record one candidate's failure: an HTTP status, or the exception the request raised."""
        status = error if isinstance(error, int) else getattr(getattr(error, "response", None), "status_code", None)
        self.statuses.append(status if isinstance(status, int) else None)

    @property
    def inconclusive(self) -> bool:
        """False only when every candidate answered with a status showing it has no usable ``/models``
        route (404, 405, 410, 501): a refusal, throttle, server error or transport failure may hide one."""
        return not self.statuses or any(status not in _NO_MODELS_ROUTE_STATUSES for status in self.statuses)


def endpoint_context_length(models: dict[str, dict[str, Any]], model: str) -> Optional[int]:
    """``model``'s window in an endpoint's ``/models`` catalog: its exact id, the only entry, else a
    substring match."""
    matched = models.get(model)
    if not matched and len(models) == 1:
        matched = next(iter(models.values()))
    elif not matched and model:  # substring match; "" would match EVERY key and poison the window
        matched = next((entry for key, entry in models.items() if model in key or key in model), None)
    context_length = matched.get("context_length") if matched else None
    return context_length if isinstance(context_length, int) else None


def note_lookup(base_url: str, api_key: object, models: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Pass ``models`` through, remembering it as this call stack's latest lookup of the endpoint."""
    from agent import model_metadata as mm
    _LOOKUP.set((mm._endpoint_memo_key(mm._normalize_base_url(base_url), api_key), models))
    return models


def endpoint_probe_failure(base_url: str, api_key: object) -> Optional[EndpointProbeFailure]:
    """The failure this call stack's latest ``/models`` lookup of the endpoint with this key produced, else
    None (it answered, or none ran). Each lookup is read once."""
    from agent import model_metadata as mm
    lookup = _LOOKUP.get()
    _LOOKUP.set(None)
    if lookup is None or lookup[0] != mm._endpoint_memo_key(mm._normalize_base_url(base_url), api_key):
        return None
    return lookup[1] if isinstance(lookup[1], EndpointProbeFailure) else None


def distrusts_catalog_hit(failure: Optional[EndpointProbeFailure], hit: tuple[str, int], model: str) -> bool:
    """After a refused or failed probe, a catalog SUBSTRING match larger than the default (``qwen3.8-flash``,
    a hosted 1M model, for a self-hosted ``qwen3.8-flash-next``) is a guess about a model the endpoint never
    described, so the default stands. An exact catalog id names a known model, as behind a proxied gateway."""
    from agent.model_metadata import DEFAULT_FALLBACK_CONTEXT
    return (failure is not None and failure.inconclusive and hit[0] != model.lower()
            and hit[1] > DEFAULT_FALLBACK_CONTEXT)


def last_published_context_length(model: str, base_url: str) -> Optional[int]:
    """``model``'s window in the freshest successful ``/models`` answer listing it that the disk memo still
    holds for this endpoint (within the retention), whichever credential slot it was stored under: the bare
    URL, its ``/v1`` alternate (the probe tries both) or a per-credential ``"<url> [<key>]"`` slot.
    Consulted only after a live probe failed, so it can only replace a catalog guess."""
    from agent import model_metadata as mm
    normalized = mm._normalize_base_url(base_url)
    alternate = normalized[:-3].rstrip("/") if normalized.endswith("/v1") else normalized + "/v1"
    published = []
    for slot, entry in mm._load_json_dict(mm._get_endpoint_metadata_cache_path()).items():
        if slot.split(" [", 1)[0] not in (normalized, alternate):
            continue
        try:
            age = time.time() - float(entry["at"])
            # Its exact id or alias only: an old catalog must not lend a window to a model it never listed.
            context_length = entry["models"][model]["context_length"]
        except (KeyError, TypeError, ValueError):  # not listed, or a malformed memo entry
            continue
        if isinstance(context_length, int) and context_length > 0 and age < LAST_PUBLISHED_RETENTION_SECONDS:
            published.append((age, context_length))
    if not published:
        return None
    age, context_length = min(published)
    logger.info("Model metadata probe for %s failed; using the %s-token window it published for %r %d min ago",
                normalized, f"{context_length:,}", model, age // 60)
    return context_length
