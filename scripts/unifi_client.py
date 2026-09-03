"""
Shared HTTP helper for talking to UniFi's Site Manager API / Connector Proxy.
Used by collector.py (and originally discover.py, kept separate there since
it was a one-off diagnostic script). Stdlib only, no third-party deps.
"""

import json
import os
import urllib.error
import urllib.request

SITE_MANAGER_BASE = "https://api.ui.com/v1"

# Confirmed 2026-09-03 via scripts/discover.py against the real console.
# Override via env vars if this ever needs to point at a different
# console/site without editing code.
CONSOLE_ID = os.environ.get(
    "UNIFI_CONSOLE_ID",
    "9C05D665D2CF0000000007F949DD00000000086574550000000065F94DF0:511086987",
)
SITE_ID = os.environ.get("UNIFI_SITE_ID", "88f7af54-98f8-306a-a1c7-c9349722b1f6")


def get_api_key() -> str:
    """API key from env var, falling back to a local .env file (KEY=VALUE
    lines) in the project root — env vars are convenient for interactive
    testing, but a scheduled task needs a durable source."""
    key = os.environ.get("UNIFI_API_KEY")
    if key:
        return key

    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line.startswith("UNIFI_API_KEY="):
                    return line.split("=", 1)[1].strip()

    raise RuntimeError(
        "No API key found. Set $env:UNIFI_API_KEY or create a .env file in "
        "the project root containing: UNIFI_API_KEY=your-key-here"
    )


def api_get(url: str, api_key: str, retries: int = 2) -> tuple[int, dict | list | str]:
    """Returns (status, body). status is 0 for network-level failures
    (timeout, connection reset, DNS, etc. — the Connector Proxy relay is
    observed to time out occasionally) rather than an HTTP status, so
    callers can treat it the same as any other non-200 and move on."""
    req = urllib.request.Request(url, headers={
        "X-API-KEY": api_key,
        "Accept": "application/json",
    })
    last_error = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode("utf-8")
                status = resp.status
            break
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            status = e.code
            break
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_error = e
            status, body = 0, None
    else:
        return 0, f"network error after {retries + 1} attempts: {last_error}"

    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body


def proxy_base(console_id: str = CONSOLE_ID) -> str:
    return f"{SITE_MANAGER_BASE}/connector/consoles/{console_id}/proxy/network/integration/v1"
