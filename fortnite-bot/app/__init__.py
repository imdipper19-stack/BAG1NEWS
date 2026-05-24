"""Bootstrap: install the outbound HTTP proxy before any HTTP client
imports anything.

Reads OUTBOUND_PROXY_URL straight from os.environ to avoid importing
``app.config`` (which triggers pydantic model validation that we want
to keep clean from side-effects). When the var is set, we mirror it
into HTTP_PROXY / HTTPS_PROXY so httpx, aiohttp, requests and others
pick it up automatically — but **only** if those vars aren't already
set, so a host-level proxy override takes priority.
"""

import os

_proxy = os.environ.get("OUTBOUND_PROXY_URL", "").strip()
if _proxy:
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.setdefault(var, _proxy)
    # Local services should NEVER go through the proxy; they live on
    # the docker-compose network.
    os.environ.setdefault(
        "NO_PROXY",
        "localhost,127.0.0.1,postgres,redis,api.telegram.org",
    )
    os.environ.setdefault(
        "no_proxy",
        "localhost,127.0.0.1,postgres,redis,api.telegram.org",
    )
