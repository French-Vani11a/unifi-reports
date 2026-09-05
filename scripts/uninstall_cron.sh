#!/usr/bin/env bash
# Removes the cron job installed by install_cron.sh. Doesn't touch the
# database, the report, or the .env file - just stops future polling.
#
# Usage:
#   ./scripts/uninstall_cron.sh

set -euo pipefail

MARKER="# unifi-reports-collector"

if crontab -l 2>/dev/null | grep -qF "$MARKER"; then
    crontab -l 2>/dev/null | grep -vF "$MARKER" | crontab -
    echo "Cron job removed. Existing data (data/unifi_reports.db, data/report.html) is untouched."
else
    echo "No unifi-reports cron job found - nothing to do."
fi
