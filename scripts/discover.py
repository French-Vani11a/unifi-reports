"""
Phase 1: discovery script.

Purpose: confirm we can reach UniFi's cloud API with a Site Manager API key,
find the consoleId for "Proton Promotions" (the UCG Ultra), and inspect the
raw JSON shape of the hosts/sites/clients endpoints before we build the real
collector against them. Field names in UniFi's newer Network Integration API
aren't fully nailed down from public docs alone, so this script prints raw
responses rather than assuming a schema.

Usage (PowerShell):
    $env:UNIFI_API_KEY = "your-site-manager-api-key"
    python scripts\\discover.py

No third-party dependencies (stdlib only), so it runs with a plain python
install and doesn't need pip/venv setup.
"""

import json
import os
import sys
import urllib.error
import urllib.request

SITE_MANAGER_BASE = "https://api.ui.com/v1"
# UCG Ultra at "Proton Promotions", confirmed from its device panel in the UniFi UI.
CONSOLE_MAC_HINT = os.environ.get("UNIFI_CONSOLE_MAC_HINT", "9C05D665D2CF")


def api_get(url: str, api_key: str) -> tuple[int, dict | list | str]:
    req = urllib.request.Request(url, headers={
        "X-API-KEY": api_key,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        status = e.code
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body


def pretty(label: str, status: int, data) -> None:
    print(f"\n=== {label} (HTTP {status}) ===")
    if isinstance(data, (dict, list)):
        print(json.dumps(data, indent=2))
    else:
        print(str(data))


def main() -> None:
    api_key = os.environ.get("UNIFI_API_KEY")
    if not api_key:
        print("ERROR: set UNIFI_API_KEY environment variable first.")
        print('  PowerShell:  $env:UNIFI_API_KEY = "your-key-here"')
        sys.exit(1)

    # Step 1: list hosts (consoles) tied to this UI.com account.
    status, hosts = api_get(f"{SITE_MANAGER_BASE}/hosts", api_key)
    print(f"\n=== GET /v1/hosts (HTTP {status}) ===")

    if status != 200:
        pretty("GET /v1/hosts (error body)", status, hosts)
        print("\nCould not list hosts. Check the API key is a Site Manager "
              "(cloud, unifi.ui.com account-level) key, not the console-level "
              "Network API key.")
        sys.exit(1)

    # Response shape is unconfirmed (could be a list, or {"data": [...]}).
    host_list = hosts.get("data", hosts) if isinstance(hosts, dict) else hosts
    if not isinstance(host_list, list):
        print("\nUnexpected shape for host list, inspect the raw output above.")
        sys.exit(1)

    print(f"\nFound {len(host_list)} host(s).")
    match = None
    if len(host_list) == 1:
        match = host_list[0]
    else:
        for h in host_list:
            print(f"  - id={h.get('id')} type={h.get('type')} owner={h.get('owner')}")
            if str(h.get("id", "")).upper().startswith(CONSOLE_MAC_HINT.upper()):
                match = h

    if not match:
        print(f"\nNo host id started with MAC hint '{CONSOLE_MAC_HINT}'. "
              "Inspect the list above and note the id manually.")
        return

    console_id = match.get("id") or match.get("consoleId") or match.get("hostId")
    print(f"\nMatched host, guessed consoleId = {console_id}")
    if not console_id:
        print("Could not guess the id field name — check the raw JSON above "
              "and identify the correct field manually.")
        return

    # Step 2: through the connector proxy, list sites on that console.
    proxy_base = f"{SITE_MANAGER_BASE}/connector/consoles/{console_id}/proxy/network/integration/v1"
    status, sites = api_get(f"{proxy_base}/sites", api_key)
    pretty("GET .../proxy/network/integration/v1/sites", status, sites)

    if status != 200:
        print("\nConnector proxy call failed. Possible causes: firmware "
              "< 5.0.3, wrong path (try 'integrations' plural), or this key "
              "type isn't authorized for the proxy.")
        return

    site_list = sites.get("data", sites) if isinstance(sites, dict) else sites
    if not isinstance(site_list, list) or not site_list:
        print("\nNo sites returned, inspect raw output above.")
        return

    site_id = site_list[0].get("id") or site_list[0].get("siteId")
    print(f"\nUsing first siteId = {site_id}")

    # Step 3: list clients on that site — this is the data we actually want.
    status, clients = api_get(f"{proxy_base}/sites/{site_id}/clients?limit=100", api_key)
    pretty("GET .../sites/{siteId}/clients", status, clients)

    client_list = clients.get("data", clients) if isinstance(clients, dict) else clients
    if isinstance(client_list, list) and client_list:
        first_client_id = client_list[0].get("id")
        status, detail = api_get(f"{proxy_base}/sites/{site_id}/clients/{first_client_id}", api_key)
        pretty("GET .../sites/{siteId}/clients/{clientId} (single client detail)", status, detail)

    # Step 3b: WAN interface stats (site-wide bandwidth, not per-client).
    status, wans = api_get(f"{proxy_base}/sites/{site_id}/wans", api_key)
    pretty("GET .../sites/{siteId}/wans", status, wans)

    # Step 3c: adopted devices (APs/switches/gateway) and one device's stats.
    status, devices = api_get(f"{proxy_base}/sites/{site_id}/devices", api_key)
    pretty("GET .../sites/{siteId}/devices", status, devices)
    device_list = devices.get("data", devices) if isinstance(devices, dict) else devices
    if isinstance(device_list, list) and device_list:
        first_device_id = device_list[0].get("id")
        status, dstats = api_get(
            f"{proxy_base}/sites/{site_id}/devices/{first_device_id}/statistics/latest", api_key)
        pretty("GET .../devices/{deviceId}/statistics/latest", status, dstats)

    # Step 4: fetch the full OpenAPI spec for this API through the proxy, to
    # see every endpoint it actually supports rather than guessing more URLs.
    network_proxy_root = f"{SITE_MANAGER_BASE}/connector/consoles/{console_id}/proxy/network"
    status, spec = api_get(f"{network_proxy_root}/api-docs/integration.json", api_key)
    if status == 200 and isinstance(spec, dict) and "paths" in spec:
        print(f"\n=== OpenAPI spec paths (HTTP {status}) ===")
        for path, methods in spec["paths"].items():
            summaries = [m.get("summary", "") for m in methods.values() if isinstance(m, dict)]
            print(f"  {path}  {' / '.join(s for s in summaries if s)}")
    else:
        pretty("GET .../api-docs/integration.json", status, spec)

    print("\nDone. Copy the full output above (or redirect this script's "
          "output to a file) so we can pin down field names for the real "
          "collector.")


if __name__ == "__main__":
    main()
