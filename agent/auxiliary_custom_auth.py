"""Resolve credentials for an explicit OpenAI-compatible auxiliary endpoint."""

from typing import Any

from utils import base_url_host_matches, base_url_origin


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
