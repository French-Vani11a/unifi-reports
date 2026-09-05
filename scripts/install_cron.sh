#!/usr/bin/env bash
# Installs (or replaces) the cron job that runs the UniFi collector +
# report generator on a schedule. Safe to re-run - it replaces any
# previous entry (matched by a marker comment) rather than duplicating it.
#
# Usage:
#   cd /path/to/unifi-reports
#   ./scripts/install_cron.sh            # every 10 minutes
#   ./scripts/install_cron.sh 15         # every 15 minutes
#
# See docs/DEPLOYMENT-LINUX.md for full first-time setup.

set -euo pipefail

INTERVAL_MINUTES="${1:-10}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARKER="# unifi-reports-collector"

echo "Project root: $PROJECT_ROOT"

PYTHON="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON" ]; then
    echo "ERROR: no python3/python found on PATH. Install it first (e.g. 'sudo apt install python3') and re-run." >&2
    exit 1
fi
echo "Using Python: $PYTHON"

if [ ! -f "$PROJECT_ROOT/.env" ] && [ -z "${UNIFI_API_KEY:-}" ]; then
    echo "WARNING: no .env file at $PROJECT_ROOT/.env and UNIFI_API_KEY isn't set."
    echo "  The cron job will run but every poll will fail until you create it:"
    echo "    echo 'UNIFI_API_KEY=your-owner-key-here' > '$PROJECT_ROOT/.env' && chmod 600 '$PROJECT_ROOT/.env'"
fi

mkdir -p "$PROJECT_ROOT/data"

CRON_LINE="*/${INTERVAL_MINUTES} * * * * cd \"$PROJECT_ROOT\" && \"$PYTHON\" scripts/collector.py > data/collector_log.txt 2>&1 && \"$PYTHON\" scripts/report.py >> data/collector_log.txt 2>&1 $MARKER"

# Replace any existing entry from a previous run of this script, keep everything else in the user's crontab untouched.
{ crontab -l 2>/dev/null | grep -vF "$MARKER" || true; echo "$CRON_LINE"; } | crontab -

echo "Cron job installed, running every $INTERVAL_MINUTES minutes."
echo "Triggering an immediate run to verify..."
cd "$PROJECT_ROOT"
"$PYTHON" scripts/collector.py > data/collector_log.txt 2>&1 || true
"$PYTHON" scripts/report.py >> data/collector_log.txt 2>&1 || true
echo "--- Last run output (data/collector_log.txt) ---"
cat data/collector_log.txt 2>/dev/null || echo "(no output yet)"
echo "--------------------------------------------------"
echo "If you see 'wrote N client rows' above, it's working. Open data/report.html to view the report."
