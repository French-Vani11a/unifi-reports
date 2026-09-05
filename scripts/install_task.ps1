<#
Registers (or re-registers) the Windows Task Scheduler task that runs the
UniFi collector + report generator on a schedule. Safe to re-run - it
replaces any existing task of the same name rather than erroring.

Usage (from an elevated-or-not PowerShell, doesn't need admin):
    cd C:\unifi-reports
    .\scripts\install_task.ps1
    .\scripts\install_task.ps1 -IntervalMinutes 15 -TaskName "UnifiReportsCollector"

See docs/DEPLOYMENT.md for the full first-time setup on a new machine.
#>

param(
    [string]$TaskName = "UnifiReportsCollector",
    [int]$IntervalMinutes = 10
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

Write-Output "Project root: $ProjectRoot"

# --- Locate Python ---
# Skip the Windows "App execution alias" stub at .../WindowsApps/python.exe -
# it exists on PATH even when Python isn't actually installed, and just
# prints an "install from the Microsoft Store" message instead of running
# anything. Get-Command finds it before a real install further down PATH,
# so it has to be filtered out explicitly rather than trusted.
$candidates = @(Get-Command python -ErrorAction SilentlyContinue -All) + @(Get-Command python3 -ErrorAction SilentlyContinue -All)
$python = ($candidates | Where-Object { $_.Source -notmatch 'WindowsApps' } | Select-Object -First 1).Source
if (-not $python) {
    Write-Error "Python not found (only the Microsoft Store stub is on PATH, which doesn't count). Install a real Python first: python.org, or 'winget install Python.Python.3.13', then re-run this script."
    exit 1
}
Write-Output "Using Python: $python"

# --- Check for an API key before wiring up a task that'll just fail without one ---
$envFile = Join-Path $ProjectRoot ".env"
if (-not (Test-Path $envFile) -and -not $env:UNIFI_API_KEY) {
    Write-Warning "No .env file found at $envFile and UNIFI_API_KEY isn't set. The task will run but every poll will fail until you create it."
    Write-Warning 'Create it with:  "UNIFI_API_KEY=your-owner-key-here" | Out-File -FilePath .env -Encoding utf8 -NoNewline'
}

# --- Ensure the data directory exists ---
New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "data") | Out-Null

# --- (Re)register the task ---
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Output "Existing task '$TaskName' found - replacing it."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# powershell.exe -WindowStyle Hidden suppresses the console window without
# needing an elevated/S4U principal (which requires admin rights to
# register - tried that first, it failed with Access Denied on a
# non-elevated session and left the task unregistered; this doesn't have
# that problem).
$innerCmd = "& `"$python`" scripts\collector.py *> data\collector_log.txt; & `"$python`" scripts\report.py *>> data\collector_log.txt"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -Command `"$innerCmd`"" `
    -WorkingDirectory "$ProjectRoot"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
# AllowStartIfOnBatteries / DontStopIfGoingOnBatteries: without these the
# task silently sits "Queued" forever on any machine not on AC power - hit
# this during initial setup, see docs/DEPLOYMENT.md.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -Hidden

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Polls UniFi Site Manager API for Proton Promotions client/device stats and regenerates report.html, every $IntervalMinutes min" `
    -Force | Out-Null

Write-Output "Task '$TaskName' registered, running every $IntervalMinutes minutes."
Write-Output "Triggering an immediate run to verify..."
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 12
Write-Output "--- Last run output (data\collector_log.txt) ---"
Get-Content (Join-Path $ProjectRoot "data\collector_log.txt") -ErrorAction SilentlyContinue
Write-Output "--------------------------------------------------"
Write-Output "If you see 'wrote N client rows' above, it's working. Open data\report.html to view the report."
