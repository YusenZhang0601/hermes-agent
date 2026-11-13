"""Resolve credentials for explicit OpenAI-compatible custom endpoints (auxiliary routes, metadata probes)."""

import logging
from typing import Any

from utils import base_url_host_matches, base_url_origin

logger = logging.getLogger(__name__)


def custom_endpoint_api_key(explicit_key: Any, endpoint: str, main_runtime: dict | None) -> Any:
    """An explicit key wins, then the matching main route, then an OpenAI-host env key.

    A profile's unrelated OPENAI_API_KEY must not shadow the self-hosted summary
    route or travel to another endpoint (#91308).
    """
    from agent import auxiliary_client as aux

    key = aux._normalize_api_key(explicit_key)
    runtime = aux._normalize_main_runtime(main_runtime)
    if not key and base_url_origin(endpoint) == base_url_origin(runtime.get("base_url", "")):
        key = aux._normalize_api_key(runtime.get("api_key"))
    key = key or aux._read_main_api_key_if_same_origin(endpoint)
    if not key and (base_url_host_matches(endpoint, "openai.com")
                    or base_url_host_matches(endpoint, "openai.azure.com")):
        key = aux._scoped_key_env("OPENAI_API_KEY")
    return key or "no-key-required"


def configured_endpoint_key(provider: str, base_url: str) -> str:
    """The key config.yaml declares for ``base_url``, as the main conversation sends it: the named custom
    provider's ``api_key`` / ``key_env`` (``providers.<name>`` or ``custom_providers``), else ``model.api_key``
    / ``model.key_env`` when ``model.base_url`` is this endpoint. Only a key declared for this endpoint's
    origin is returned, and it is read without side effects (no ``key_cmd`` runs, no credential pool
    rotates, no OAuth token refreshes), so lookups that are not a conversation turn (metadata and pricing
    probes, the public model-info route) can authenticate to a keyed gateway instead of drawing a 401.
    "" when nothing is declared, or when no profile's secrets are in scope (multi-profile hosting)."""
    from agent.secret_scope import UnscopedSecretError, get_secret_str
    from hermes_cli.auth import has_usable_secret
    from hermes_cli.config import load_config
    from hermes_cli.runtime_provider_custom import _get_named_custom_provider

    def declared(block) -> str:
        key_env = str(block.get("key_env") or block.get("api_key_env") or "").strip()
        keys = (str(block.get("api_key") or "").strip(), get_secret_str(key_env, "").strip() if key_env else "")
        return next((key for key in keys if has_usable_secret(key)), "")

    origin = base_url_origin(base_url)
    if not origin[1]:
        return ""
    try:
        named = _get_named_custom_provider(provider) if provider else None
        if named and base_url_origin(named.get("base_url") or "") == origin:
            return declared(named)
        model_cfg = load_config().get("model")
        if isinstance(model_cfg, dict) and base_url_origin(model_cfg.get("base_url") or "") == origin:
            return declared(model_cfg)
    except UnscopedSecretError:
        logger.debug("No profile secret scope for the %s key lookup; probing without a key", origin[1])
    return ""
