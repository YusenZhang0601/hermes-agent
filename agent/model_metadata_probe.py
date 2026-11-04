"""Transport policy for model-metadata probes against local inference engines.

Kept out of ``model_metadata.py`` because that file is already past the code-health
line target (see CONTRIBUTING.md "Code health ratchet"); these are pure transport
helpers with no dependency on the metadata resolver itself.
"""

import re


def _localhost_to_ipv4(url: str) -> str:
    """``localhost`` HOST -> ``127.0.0.1`` (Windows dual-stack resolves ::1 first and pays a ~2s IPv6
    connect timeout on IPv4-only servers). Scheme-anchored so ``?upstream=http://localhost`` is untouched."""
    if not url or not isinstance(url, str):
        return url  # non-string values (test doubles, lazy config) pass through
    return re.sub(r"^(https?://)localhost(?=[:/]|$)", r"\g<1>127.0.0.1", url, count=1)


def _probe_trust_env(base_url: str) -> bool:
    """Whether a metadata probe may inherit ``HTTP_PROXY``/``HTTPS_PROXY`` from the environment.

    A metadata probe asks "is this engine an OpenAI-compatible server, and what context window does
    it publish?" — a question only the engine itself can answer. ``httpx`` evaluates ``NO_PROXY``
    per literal hostname/path prefix, so a CIDR entry such as ``100.64.0.0/10`` (Tailscale CGNAT)
    never matches an address there, while the Clash/Verge-style bypass lists users keep in the shell
    are written exactly that way. A probe aimed at an engine reached over a tailnet or a LAN address
    is therefore handed to the local proxy, which opens no connection to the engine, and every probe
    in the waterfall burns its full timeout (12s for the five-path server-detection chain). The
    desktop Model Settings page then times out, the visible assistant model list stays empty, and the
    context window falls back to the longest substring match in the catalog instead of what the engine
    publishes.

    Loopback, container-internal DNS, ``*.local``, RFC-1918, link-local and Tailscale CGNAT are
    probed directly. Public hosted endpoints keep the environment's proxy. The same policy already
    applies to the web app's own HTTP client (``httpx.AsyncClient(trust_env=not
    is_local_endpoint(url))`` in ``hermes_cli/web_routers/config_env.py``); this brings the
    agent-side probes in line with it.

    ``is_local_endpoint`` is imported at call time, which also keeps
    ``patch("agent.model_metadata.is_local_endpoint", ...)`` effective here.
    """
    from agent.model_metadata import is_local_endpoint

    return not is_local_endpoint(base_url)
