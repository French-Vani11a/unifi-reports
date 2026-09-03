"""
Phase 2: the actual collector. Polls UniFi's Connector Proxy for connected
clients and device stats, and appends a timestamped snapshot to a local
SQLite database. Meant to be run on a schedule (e.g. every 5-15 min via
Windows Task Scheduler) — each run is a single poll, not a long-running
loop, so the schedule controls the interval.

Per-client bandwidth usage isn't available through this API (see
docs/api-notes.md) — this collects what IS available: who's connected and
when, plus live throughput/health for the gateway and AP. Good enough for
"how many devices, when" and bandwidth-over-time reporting.

Usage:
    python scripts\\collector.py

Needs UNIFI_API_KEY set (env var, or a .env file in the project root —
see unifi_client.get_api_key). Uses the owner's key, since only the
console owner's Site Manager API key can call the Connector Proxy.
"""

import datetime
import os
import sqlite3
import sys

from unifi_client import api_get, get_api_key, proxy_base, SITE_ID

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "unifi_reports.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS client_snapshots (
    ts TEXT NOT NULL,
    client_id TEXT NOT NULL,
    name TEXT,
    mac_address TEXT,
    ip_address TEXT,
    client_type TEXT,
    access_type TEXT,
    connected_at TEXT,
    uplink_device_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_client_snapshots_ts ON client_snapshots(ts);

CREATE TABLE IF NOT EXISTS device_stats (
    ts TEXT NOT NULL,
    device_id TEXT NOT NULL,
    device_name TEXT,
    model TEXT,
    mac_address TEXT,
    ip_address TEXT,
    firmware_version TEXT,
    state TEXT,
    uptime_sec INTEGER,
    cpu_pct REAL,
    mem_pct REAL,
    tx_rate_bps INTEGER,
    rx_rate_bps INTEGER
);
CREATE INDEX IF NOT EXISTS idx_device_stats_ts ON device_stats(ts);
"""


def fetch_all_clients(base: str, api_key: str) -> list[dict]:
    clients = []
    offset = 0
    while True:
        status, resp = api_get(f"{base}/sites/{SITE_ID}/clients?limit=200&offset={offset}", api_key)
        if status != 200:
            print(f"WARN: clients fetch failed (HTTP {status}): {resp}", file=sys.stderr)
            return clients
        page = resp.get("data", [])
        clients.extend(page)
        total = resp.get("totalCount", len(clients))
        offset += len(page)
        if offset >= total or not page:
            break
    return clients


def fetch_devices(base: str, api_key: str) -> list[dict]:
    status, resp = api_get(f"{base}/sites/{SITE_ID}/devices", api_key)
    if status != 200:
        print(f"WARN: devices fetch failed (HTTP {status}): {resp}", file=sys.stderr)
        return []
    return resp.get("data", [])


def fetch_device_stats(base: str, api_key: str, device_id: str) -> dict | None:
    status, resp = api_get(f"{base}/sites/{SITE_ID}/devices/{device_id}/statistics/latest", api_key)
    if status != 200:
        print(f"WARN: stats fetch failed for {device_id} (HTTP {status}): {resp}", file=sys.stderr)
        return None
    return resp


def main() -> None:
    api_key = get_api_key()
    base = proxy_base()
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)

    clients = fetch_all_clients(base, api_key)
    conn.executemany(
        "INSERT INTO client_snapshots "
        "(ts, client_id, name, mac_address, ip_address, client_type, access_type, connected_at, uplink_device_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                ts,
                c.get("id"),
                c.get("name"),
                c.get("macAddress"),
                c.get("ipAddress"),
                c.get("type"),
                (c.get("access") or {}).get("type"),
                c.get("connectedAt"),
                c.get("uplinkDeviceId"),
            )
            for c in clients
        ],
    )

    devices = fetch_devices(base, api_key)
    device_rows = []
    for d in devices:
        stats = fetch_device_stats(base, api_key, d.get("id"))
        uplink = (stats or {}).get("uplink", {})
        device_rows.append((
            ts,
            d.get("id"),
            d.get("name"),
            d.get("model"),
            d.get("macAddress"),
            d.get("ipAddress"),
            d.get("firmwareVersion"),
            d.get("state"),
            (stats or {}).get("uptimeSec"),
            (stats or {}).get("cpuUtilizationPct"),
            (stats or {}).get("memoryUtilizationPct"),
            uplink.get("txRateBps"),
            uplink.get("rxRateBps"),
        ))
    conn.executemany(
        "INSERT INTO device_stats "
        "(ts, device_id, device_name, model, mac_address, ip_address, firmware_version, "
        "state, uptime_sec, cpu_pct, mem_pct, tx_rate_bps, rx_rate_bps) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        device_rows,
    )

    conn.commit()
    conn.close()
    print(f"{ts}  wrote {len(clients)} client rows, {len(device_rows)} device-stat rows -> {DB_PATH}")


if __name__ == "__main__":
    main()
