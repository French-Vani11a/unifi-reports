# unifi-reports

Extracts per-client bandwidth/usage data from the "Proton Promotions" UniFi
site (UCG Ultra, gateway behind a Starlink connection) and turns it into a
report/dashboard. Built because the UniFi UI has no export button, and the
management machine has no local network access to the console — only remote
access via unifi.ui.

See [docs/api-notes.md](docs/api-notes.md) for the full research/reasoning
behind the approach below, and [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for
moving this to a different always-on machine.

## Approach

UniFi's per-client data (who's connected, bytes used, signal, etc.) only
lives on the console's local Network API — the public cloud Site Manager API
doesn't expose it. Reaching it remotely without a VPN or an on-site device is
possible via UniFi's **Connector Proxy**: a cloud-to-local relay at
`api.ui.com` (introduced in console firmware 5.0.3+) that forwards requests
to the console's local Network Integration API.

```
Site Manager API key (cloud, unifi.ui.com account)
        │
        ▼
https://api.ui.com/v1/connector/consoles/{consoleId}/proxy/network/integration/v1/...
        │  (UniFi's cloud relays this to the console over its existing
        │   remote-access connection — no VPN, no port forwarding, no
        │   on-site hardware)
        ▼
UCG Ultra's local Network API
```

Important caveat: this API appears to expose **live/current counters** per
client, not the historical daily-usage reports the UI's "Top Clients" widget
shows. To build a usage-over-time report we poll on a schedule and
accumulate our own history — it can't be backfilled from before we start
polling.

## Status

- [x] Confirmed the Connector Proxy works remotely with an owner-level
      Site Manager API key (no on-site device needed)
- [x] **Phase 1: discovery.** Confirmed the public Network Integration API
      has no per-client bandwidth/usage data at all (checked against its
      own OpenAPI spec, not just a couple of guessed URLs) — it only has
      per-client identity/connection info, plus per-device (AP/gateway)
      throughput and health. See docs/api-notes.md for full detail.
- [x] **Decision:** ship reporting on what's actually available —
      connected-client counts and composition (wireless/wired,
      guest/default) over time, plus gateway/AP bandwidth and health —
      rather than blocking on per-client usage, which would require an
      on-site device. That option is still open later if needed.
- [x] **Phase 2: scheduled collector — running.** `scripts/collector.py`
      polls every 10 minutes via Windows Task Scheduler
      (task name `UnifiReportsCollector`), writing to
      `data/unifi_reports.db` (SQLite). See "Operations" below.
- [ ] Phase 3: report generation (CSV/Excel, graphs) from the accumulated
      data — next up.

## Setup

1. Create a **Site Manager API key** at unifi.ui.com (account-level, not the
   console's local Integrations panel). See docs/api-notes.md for why these
   are different and which one you need.
2. Set it as an environment variable — never commit it or paste it into
   chat/code:
   ```powershell
   $env:UNIFI_API_KEY = "your-key-here"
   ```
3. Run the discovery script:
   ```powershell
   python scripts\discover.py
   ```
4. Share the output (or any errors) so the collector can be finalized
   against the real response shape.

No third-party packages required — everything so far uses the Python
standard library only, to keep a future scheduled task simple (no venv to
maintain).

## Operations

Windows Task Scheduler task **`UnifiReportsCollector`** runs every 10
minutes, chaining two steps: `scripts\collector.py` (polls UniFi, appends
to the database) then `scripts\report.py` (regenerates `data\report.html`
from the updated database). Output from both is redirected to
`data\collector_log.txt` (overwritten each run — it's a "last run status"
file, not a full history; the real history is in the SQLite database and
in `report.html` itself).

**`report.html` is a static snapshot, not a live page.** It doesn't fetch
anything on its own — every viewing is whatever the last scheduled run
baked into it. Since that now happens automatically every 10 minutes, just
refresh the browser tab to see current data (a hard refresh — Ctrl+Shift+R
— if the browser cached the old version).

Useful commands (PowerShell):
```powershell
# Check status / next run time
Get-ScheduledTask -TaskName "UnifiReportsCollector" | Get-ScheduledTaskInfo

# Run it immediately, outside the schedule
Start-ScheduledTask -TaskName "UnifiReportsCollector"

# See the last run's output (errors show up here)
Get-Content C:\unifi-reports\data\collector_log.txt

# Pause / resume
Disable-ScheduledTask -TaskName "UnifiReportsCollector"
Enable-ScheduledTask -TaskName "UnifiReportsCollector"

# Remove entirely (or use scripts\uninstall_task.ps1)
Unregister-ScheduledTask -TaskName "UnifiReportsCollector" -Confirm:$false
```

The task itself is set up via `scripts\install_task.ps1` (safe to re-run -
replaces the existing task rather than erroring), with a matching
`scripts\uninstall_task.ps1`. These are also what
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) uses to set this up on a new
machine.

**Gotcha hit and fixed during setup:** Task Scheduler's default settings
include `DisallowStartIfOnBatteries`, which silently leaves the task stuck
in a "Queued" state (never actually running) whenever this machine isn't
on AC power. Explicitly disabled via `AllowStartIfOnBatteries` /
`DontStopIfGoingOnBatteries` — if the task ever needs to be recreated,
carry that setting over or it'll intermittently stop collecting data
without any visible error.

**Also fixed during setup:** the task originally launched via `cmd.exe`,
which pops a visible console window on screen for a couple of seconds every
run. Fixed by launching through `powershell.exe -WindowStyle Hidden`
instead — no elevation needed. (A tempting alternative, an S4U principal
for a fully non-interactive session, needs admin rights to register; tried
it, got `Access Denied` on a standard session, and it briefly left the task
unregistered. Not worth it for what's a cosmetic annoyance.)

**Also worth knowing:** the Connector Proxy (`api.ui.com` → console relay)
times out occasionally — observed directly during setup, not
hypothetical. `unifi_client.api_get` retries transient network failures
automatically; a single missed poll just means a 10-minute gap in the
data, not a crash.

## Security notes

- The API key grants read access to your UniFi account's consoles. Treat it
  like a password: env var or local `.env` (gitignored), never in source
  control, never pasted into chat/logs.
- `data/` (the local database/export files this project produces) is
  gitignored — it will contain real network/device data once populated.
