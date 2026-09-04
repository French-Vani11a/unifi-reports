# Deploying to a new (always-on) machine

This project currently runs from `C:\unifi-reports` on a Windows machine, polling
UniFi every 10 minutes via Task Scheduler. This guide moves that setup onto a
different Windows machine that's always powered on - so collection doesn't
depend on someone's laptop staying awake.

## Prerequisites

- A Windows machine that's on and connected to the internet essentially all the
  time (outbound HTTPS access to `api.ui.com` - no inbound ports, no VPN, no
  need to be on the same network as the UniFi console).
- Python 3.10+. Check with:
  ```powershell
  python --version
  ```
  If missing, install from [python.org](https://www.python.org/downloads/) or:
  ```powershell
  winget install Python.Python.3.13
  ```

## 1. Copy the project

Copy the whole `C:\unifi-reports` folder to the new machine (zip it, use a
network share, a USB drive, whatever's convenient) - **except**:

- `data\` - leave this behind. It's this machine's local database/report
  history; the new machine will build its own from here on.
- `.env` - leave this behind too. Don't move the API key file itself;
  create a fresh one on the new machine in step 2. Keeps the key from
  sitting in a zip file, an email attachment, a USB drive, etc.

So: copy `docs\`, `scripts\`, `README.md`, `.gitignore` - that's everything
needed. On the new machine, create an empty `data\` folder (the scripts
create it automatically on first run anyway, so this is optional).

## 2. Create the `.env` file

In the project root on the **new** machine, create a file named `.env`
containing:
```
UNIFI_API_KEY=your-owner-key-here
```

This must be a **Site Manager API key belonging to the console's owner**
account (not just an admin) - see `docs/api-notes.md` for why. Reuse the
same key that's already working on the current machine (an API key isn't
tied to a specific computer), or have the owner generate a fresh one at
unifi.ui.com -> API Keys -> Create API Key.

PowerShell one-liner if you're typing the key directly at the new machine's
terminal (avoids it ever being saved in shell history as a separate
command):
```powershell
"UNIFI_API_KEY=paste-the-key-here" | Out-File -FilePath .env -Encoding utf8 -NoNewline
```

## 3. Register the scheduled task

From the project root on the new machine:
```powershell
.\scripts\install_task.ps1
```

This finds Python automatically, registers the `UnifiReportsCollector`
Task Scheduler task (polls every 10 minutes, chaining
`collector.py` -> `report.py`), and immediately triggers one run to verify
it works - you should see `wrote N client rows` in the output.

To use a different interval:
```powershell
.\scripts\install_task.ps1 -IntervalMinutes 15
```

The script is safe to re-run any time (e.g. to change the interval, or
after moving the project again) - it replaces the existing task rather
than erroring on a duplicate.

## 4. Verify

```powershell
# Check the task is actually enabled and not stuck
Get-ScheduledTask -TaskName "UnifiReportsCollector" | Get-ScheduledTaskInfo

# See the most recent run's output (errors show up here)
Get-Content data\collector_log.txt

# Open the report
start data\report.html
```

Give it a few polling cycles (an hour or so) before judging the charts -
a single data point looks empty; the report gets more useful as history
accumulates.

## Managing the task

```powershell
# Run immediately, outside the schedule
Start-ScheduledTask -TaskName "UnifiReportsCollector"

# Pause / resume
Disable-ScheduledTask -TaskName "UnifiReportsCollector"
Enable-ScheduledTask -TaskName "UnifiReportsCollector"

# Remove entirely (keeps data\ and .env untouched)
.\scripts\uninstall_task.ps1
```

## Known gotchas (already handled by install_task.ps1, noted for awareness)

- **Battery restriction:** Task Scheduler's default settings refuse to run
  a task while on battery power, leaving it silently stuck "Queued"
  forever with no error. `install_task.ps1` explicitly disables this
  (`AllowStartIfOnBatteries` / `DontStopIfGoingOnBatteries`). Only matters
  if the target machine is a laptop rather than a desktop/server - but the
  setting is harmless either way.
- **Console window flash:** the task runs via `powershell.exe -WindowStyle
  Hidden` specifically to avoid a visible console popping up every run. Don't
  swap this back to a plain `cmd.exe` action without a reason - that's what
  caused it originally.
- **Connector Proxy timeouts:** the UniFi cloud relay
  (`api.ui.com` -> console) times out occasionally - this is normal,
  observed in practice, not specific to any one machine. `collector.py`
  retries automatically; a missed poll just means one 10-minute gap in the
  data.

## If something's fundamentally different on the new setup

If the new machine needs to poll a *different* UniFi console/site, see the
`UNIFI_CONSOLE_ID` / `UNIFI_SITE_ID` overrides documented in
`scripts/unifi_client.py`, and re-run `scripts/discover.py` to find the
right IDs (see `docs/api-notes.md` for the discovery process).
