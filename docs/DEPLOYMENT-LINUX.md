# Deploying to a Linux server

The collector/report scripts (`collector.py`, `report.py`, `unifi_client.py`)
are plain standard-library Python - no Windows-specific code anywhere - so
they run on Linux unchanged. The only Windows-specific piece is the
*scheduling*: Task Scheduler doesn't exist on Linux, so this uses `cron`
instead. Everything else (the API, the database, the report) is identical
to the Windows setup described in `README.md` and `docs/DEPLOYMENT.md`.

## Prerequisites

- Python 3.10+:
  ```bash
  python3 --version
  ```
  If missing: `sudo apt install python3` (Debian/Ubuntu) or the equivalent
  for your distro. No other packages needed - everything used
  (`urllib`, `sqlite3`, `json`, `csv`) is in the standard library.
- `cron` running (installed and enabled by default on nearly every Linux
  distro; check with `systemctl status cron` or `crontab -l`).
- Outbound HTTPS access to `api.ui.com` - no inbound ports, no VPN.

## 1. Copy the project

Copy `docs/`, `scripts/`, `README.md`, `.gitignore` to the server (scp,
rsync, git, whatever's convenient) - same as the Windows deployment,
**skip** `data/` and `.env`:

```bash
rsync -av --exclude data --exclude .env ./ user@server:/opt/unifi-reports/
```

If the shell scripts lose their executable bit in transit (common with zip
files), fix it on the server:
```bash
chmod +x scripts/*.sh
```

## 2. Create the `.env` file

On the server, in the project root:
```bash
echo "UNIFI_API_KEY=your-owner-key-here" > .env
chmod 600 .env
```

Same requirement as the Windows setup: this must be a Site Manager API key
belonging to the console's **owner** account (see `docs/api-notes.md`).
Reuse the existing key (it's not tied to any one machine) or have the
owner generate a new one at unifi.ui.com -> API Keys -> Create API Key.
`chmod 600` restricts it to your own user - worth doing explicitly on a
shared server in a way that wasn't really a concern on a personal Windows
machine.

## 3. Install the cron job

```bash
cd /opt/unifi-reports
./scripts/install_cron.sh
```

This finds `python3`, checks for the API key, creates `data/`, installs a
cron entry (every 10 minutes, chaining `collector.py` -> `report.py`), and
immediately triggers one run to verify - look for `wrote N client rows` in
the output.

Different interval:
```bash
./scripts/install_cron.sh 15    # every 15 minutes
```

Safe to re-run - it replaces its own crontab entry (matched by a marker
comment) rather than duplicating it, and leaves any of your other cron
jobs untouched.

## 4. Verify

```bash
crontab -l                        # confirm the entry is there
cat data/collector_log.txt        # last run's output, errors show up here
```

Open `data/report.html` in a browser. Since this is a headless server,
either:
- copy the file to your own machine (`scp user@server:/opt/unifi-reports/data/report.html .`), or
- serve the `data/` directory over HTTP temporarily for convenience, e.g.
  `python3 -m http.server --directory data 8080` and browse to
  `http://server:8080/report.html` (only do this on a trusted network -
  it has no auth).

Give it a few polling cycles before judging the charts - a single data
point looks empty.

## Managing the cron job

```bash
crontab -l                    # view current cron jobs (this one included)
crontab -e                    # edit manually if needed
./scripts/uninstall_cron.sh   # remove it (data/.env untouched)
```

To run a poll immediately outside the schedule:
```bash
python3 scripts/collector.py && python3 scripts/report.py
```

## Gotchas that applied on Windows but don't here

- **Battery restriction / console window flash:** both were Windows Task
  Scheduler quirks (`install_task.ps1` handles them). `cron` has neither
  problem - it doesn't care about power state and never opens a window.
- **PowerShell encoding gotchas** (`Out-File` BOM, etc.) from the Windows
  setup notes are specific to PowerShell and don't apply here.

## Gotchas that still apply

- **Connector Proxy timeouts:** the UniFi cloud relay (`api.ui.com` ->
  console) times out occasionally regardless of platform.
  `unifi_client.api_get` retries automatically; a missed poll just means
  one gap in the data.
- **`report.html` is a static snapshot, not live** - it only reflects
  whatever the last cron run generated. Refresh the browser tab (or
  re-copy the file, if viewing it off-server) to see current data.
