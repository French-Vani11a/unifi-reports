<#
Removes the scheduled task installed by install_task.ps1. Doesn't touch
the database, the report, or the .env file - just stops future polling.

Usage:
    .\scripts\uninstall_task.ps1
#>

param(
    [string]$TaskName = "UnifiReportsCollector"
)

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Task '$TaskName' removed. Existing data (data\unifi_reports.db, data\report.html) is untouched."
} else {
    Write-Output "No task named '$TaskName' found - nothing to do."
}
