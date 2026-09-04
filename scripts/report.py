"""
Phase 3: turns the accumulated SQLite data into a shareable, multi-page,
filterable report.

Produces:
  data/report.html            - self-contained HTML report (opens offline;
                                 has date-range + hour-of-day filters that
                                 recompute every chart/table in the browser,
                                 and a "Save as PDF" button using the
                                 browser's native print-to-PDF)
  data/clients_export.csv     - raw client-snapshot rows (full history)
  data/device_stats_export.csv - raw device-stat rows (full history)

Usage:
    python scripts\\report.py

Re-run any time; each run overwrites the same files with the latest data
from data/unifi_reports.db.

Design note: report.html embeds raw per-poll rows (not pre-aggregated
summaries) and does all aggregation in JavaScript, so the date/hour filters
can recompute every view instantly without regenerating the file. To keep
the embedded file a sane size as history grows, only the last
REPORT_HISTORY_DAYS days are embedded for interactive use - the CSV exports
are always the full unbounded history.

What's NOT in this report, and why: per-client bandwidth usage and
"top visited sites / apps" both require UniFi's classic private controller
API, which has no remote/cloud equivalent (see docs/api-notes.md). This
report only uses the public Network Integration API via the Connector
Proxy, so "top clients" here means most-observed-connected-time (based on
polling), not data volume. That's stated in the report itself too.
"""

import csv
import datetime
import json
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "unifi_reports.db")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
REPORT_HISTORY_DAYS = 90


def load_rows(conn, table: str) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY ts").fetchall()]


def build_device_directory(device_rows: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """Latest known row per device, from the FULL (unbounded) history - so a
    device's identity/name is stable even if the interactive report window
    is narrower than its full history."""
    latest = {}
    for r in device_rows:  # ts-ordered ascending, so last write per id wins
        latest[r["device_id"]] = r
    id_to_name = {did: r["device_name"] for did, r in latest.items()}
    return list(latest.values()), id_to_name


def export_csv(rows: list[dict], path: str) -> None:
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


HTML_TEMPLATE = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Proton Promotions - Network Report</title>
<style>
  :root {
    color-scheme: light;
    --surface-1: #fcfcfb; --page: #f9f9f7;
    --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #898781;
    --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
    --series-1: #2a78d6; --series-2: #eb6834; --series-3: #1baf7a;
    --series-4: #eda100; --series-5: #4a3aa7;
    --accent: #2a78d6; --good: #0ca30c; --critical: #d03b3b;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      color-scheme: dark;
      --surface-1: #1a1a19; --page: #0d0d0d;
      --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #898781;
      --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
      --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70;
      --series-4: #c98500; --series-5: #9085e9;
      --accent: #3987e5; --good: #0ca30c; --critical: #e66767;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--page); color: var(--text-primary);
         font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
  .wrap { max-width: 1080px; margin: 0 auto; padding: 24px 20px 60px; }
  header.top { display: flex; justify-content: space-between; align-items: flex-start;
               margin-bottom: 8px; gap: 16px; flex-wrap: wrap; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .subtitle { color: var(--text-secondary); font-size: 13px; margin: 0; }
  button.print-btn { background: var(--accent); color: #fff; border: none; border-radius: 8px;
               padding: 10px 16px; font-size: 13px; font-weight: 600; cursor: pointer; }
  button.print-btn:hover { opacity: 0.9; }
  nav.tabs { display: flex; gap: 4px; margin: 20px 0 16px; border-bottom: 1px solid var(--border);
             flex-wrap: wrap; }
  nav.tabs button { background: none; border: none; padding: 10px 14px; font-size: 13px;
                     color: var(--text-secondary); cursor: pointer; border-bottom: 2px solid transparent;
                     font-family: inherit; }
  nav.tabs button.active { color: var(--text-primary); border-bottom-color: var(--accent); font-weight: 600; }
  .page { display: none; }
  .page.active { display: block; }

  .filterbar { display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
               background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px;
               padding: 12px 16px; margin-bottom: 12px; font-size: 12px; }
  .filterbar .group { display: flex; align-items: center; gap: 6px; }
  .filterbar .presets { display: flex; gap: 4px; }
  .filterbar .presets button { font-size: 12px; padding: 6px 10px; border-radius: 6px; border: 1px solid var(--border);
                                background: var(--page); color: var(--text-secondary); cursor: pointer; font-family: inherit; }
  .filterbar .presets button.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  .filterbar input[type="date"], .filterbar select { font-size: 12px; padding: 5px 7px; border-radius: 6px;
               border: 1px solid var(--border); background: var(--page); color: var(--text-primary); font-family: inherit; }
  .filterbar label { color: var(--text-secondary); }
  .filterbar .reset { margin-left: auto; background: none; border: none; color: var(--accent); cursor: pointer;
               font-size: 12px; text-decoration: underline; font-family: inherit; }
  .filter-summary { font-size: 12px; color: var(--text-secondary); margin: 0 0 20px; }

  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
           gap: 12px; margin-bottom: 24px; }
  .tile { background: var(--surface-1); border: 1px solid var(--border);
          border-radius: 10px; padding: 14px 16px; }
  .tile .label { font-size: 12px; color: var(--text-secondary); margin-bottom: 6px; }
  .tile .value { font-size: 26px; font-weight: 600; }
  section { background: var(--surface-1); border: 1px solid var(--border);
            border-radius: 10px; padding: 20px; margin-bottom: 20px; }
  section h2 { font-size: 15px; margin: 0 0 2px; }
  section .desc { font-size: 12px; color: var(--text-secondary); margin: 0 0 14px; }
  .note { background: var(--surface-1); border: 1px dashed var(--border); border-radius: 10px;
          padding: 14px 16px; font-size: 12px; color: var(--text-secondary); margin-bottom: 20px; }
  .note b { color: var(--text-primary); }
  .legend { display: flex; gap: 16px; font-size: 12px; color: var(--text-secondary);
            margin-bottom: 8px; flex-wrap: wrap; }
  .legend .key { display: inline-flex; align-items: center; gap: 6px; }
  .legend .swatch { width: 10px; height: 10px; border-radius: 2px; }
  svg { display: block; width: 100%; height: auto; overflow: visible; }
  .gridline { stroke: var(--grid); stroke-width: 1; }
  .axis-text { fill: var(--text-muted); font-size: 10px; }
  .tooltip { position: absolute; background: var(--surface-1); border: 1px solid var(--border);
             border-radius: 6px; padding: 8px 10px; font-size: 11px; pointer-events: none;
             box-shadow: 0 2px 8px rgba(0,0,0,0.15); display: none; white-space: nowrap; z-index: 10; }
  .tooltip .row { display: flex; justify-content: space-between; gap: 12px; }
  .tooltip .row .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 4px; }
  .chart-container { position: relative; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--grid); white-space: nowrap; }
  th { color: var(--text-secondary); font-weight: 500; font-size: 11px; text-transform: uppercase; }
  .table-scroll { overflow-x: auto; }
  .state-online, .badge-yes { color: var(--good); } .state-offline, .badge-no { color: var(--text-muted); }
  .badge { font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 100px; }
  .badge-guest { background: rgba(237,161,0,0.15); color: #a17400; }
  .badge-default { background: rgba(42,120,214,0.15); color: var(--accent); }
  .rank { color: var(--text-muted); font-variant-numeric: tabular-nums; }
  .barlist { display: flex; flex-direction: column; gap: 7px; }
  .bar-row { display: flex; align-items: center; gap: 10px; }
  .bar-label { width: 200px; flex-shrink: 0; font-size: 12px; white-space: nowrap;
               overflow: hidden; text-overflow: ellipsis; }
  .bar-track { flex: 1; height: 14px; background: var(--grid); border-radius: 7px; overflow: hidden; }
  .bar-fill { height: 100%; background: var(--series-1); border-radius: 7px; }
  .bar-value { width: 28px; text-align: right; font-size: 12px; color: var(--text-secondary);
               font-variant-numeric: tabular-nums; flex-shrink: 0; }
  .empty { color: var(--text-muted); font-size: 13px; padding: 20px 0; text-align: center; }
  .mono { font-variant-numeric: tabular-nums; }

  @media print {
    @page { size: portrait; margin: 10mm; }
    body { background: #fff; }
    .wrap { max-width: none; padding: 0; }
    .no-print { display: none !important; }
    .page { display: block !important; page-break-before: always; }
    /* #page-overview is the first .page in the DOM, right after the note -
       it must NOT break before it. ":first-child" doesn't work here since
       the header/nav/filter bar/note precede it, so it's never literally
       its parent's first child; target it by id instead. */
    #page-overview { page-break-before: auto; }
    section { border: 1px solid #ddd; }
    :root { color-scheme: light; }
    /* Wide tables (Client Directory etc.) scroll horizontally on screen -
       print can't scroll, so let cells wrap instead of being clipped. */
    .table-scroll { overflow-x: visible; }
    table { font-size: 7.5px; table-layout: fixed; width: 100%; }
    th, td { white-space: normal; padding: 3px 4px; overflow-wrap: break-word; }
    .tiles { grid-template-columns: repeat(3, 1fr); }
    thead { display: table-header-group; } /* repeat header row when a table splits across pages */
    .chart-container { break-inside: avoid; }
  }
</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <div>
      <h1>Proton Promotions - Network Report</h1>
      <p class="subtitle">Generated __GENERATED_AT__ - data collected every ~__POLL_INTERVAL__ min via UniFi Site Manager API</p>
    </div>
    <button class="print-btn no-print" onclick="window.print()">Save as PDF</button>
  </header>

  <nav class="tabs no-print" id="tabs"></nav>

  <div class="filterbar no-print" id="filterbar">
    <div class="group presets" id="date-presets"></div>
    <div class="group">
      <label>From <input type="date" id="date-from"></label>
      <label>To <input type="date" id="date-to"></label>
    </div>
    <div class="group">
      <label>Hours <select id="hour-from"></select> to <select id="hour-to"></select></label>
    </div>
    <button class="reset" id="filter-reset">Reset</button>
  </div>
  <p class="filter-summary" id="filter-summary"></p>

  <div class="note">
    <b>Not included:</b> per-client data usage (MB/GB) and "top visited sites/apps." Both require
    UniFi's private local controller API, which has no remote/cloud access path (see docs/api-notes.md
    in this project). "Top clients" below ranks by <b>observed connection time</b> (how many polling
    cycles a device was seen connected), not data volume. Only the last __HISTORY_DAYS__ days are loaded
    into this interactive report (full history is always in clients_export.csv / device_stats_export.csv).
  </div>

  <div class="page active" id="page-overview">
    <div class="tiles" id="tiles"></div>
    <section>
      <h2>Connected devices over time</h2>
      <p class="desc">Every poll in the selected range - total connected clients, split by connection type</p>
      <div class="legend" id="legend-clients"></div>
      <div class="chart-container"><svg id="chart-clients" viewBox="0 0 1000 280"></svg>
        <div class="tooltip" id="tooltip-clients"></div></div>
    </section>
    <section>
      <h2>Bandwidth (Mbps)</h2>
      <p class="desc">Live throughput at the gateway's internet uplink (download / upload)</p>
      <div class="legend" id="legend-bw"></div>
      <div class="chart-container"><svg id="chart-bw" viewBox="0 0 1000 280"></svg>
        <div class="tooltip" id="tooltip-bw"></div></div>
    </section>
    <section>
      <h2>Daily summary</h2>
      <p class="desc">Peak and average concurrent devices, and unique devices seen, per day</p>
      <div id="daily-table"></div>
    </section>
  </div>

  <div class="page" id="page-devices">
    <section>
      <h2>Network devices</h2>
      <p class="desc">Every adopted device (gateway, access points) and its status as of the end of the selected range</p>
      <div id="device-table"></div>
    </section>
  </div>

  <div class="page" id="page-common-devices">
    <section>
      <h2>Most common devices</h2>
      <p class="desc">Client devices grouped by broadcast name/model (from UniFi's client name field)</p>
      <div id="common-devices-list"></div>
    </section>
  </div>

  <div class="page" id="page-directory">
    <section>
      <h2>Client directory</h2>
      <p class="desc">Every device seen connected in the selected range, with first/last-seen times</p>
      <div id="directory-table"></div>
    </section>
  </div>

  <div class="page" id="page-top-clients">
    <section>
      <h2>Top clients</h2>
      <p class="desc">Ranked by observed connection time within the selected range (polling-based estimate, not data usage - see note above)</p>
      <div id="top-clients-table"></div>
    </section>
  </div>

  <div class="page" id="page-top-devices">
    <section>
      <h2>Top devices</h2>
      <p class="desc">Access points / gateway ranked by number of clients served in the selected range</p>
      <div id="top-devices-table"></div>
    </section>
  </div>
</div>

<script>
// Raw per-poll rows for the selected history window - all aggregation
// happens here in JS so the filters can recompute instantly.
const RAW = __DATA_JSON__;

// ---------- Tabs ----------
const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'devices', label: 'Devices' },
  { id: 'common-devices', label: 'Most Common Devices' },
  { id: 'directory', label: 'Client Directory' },
  { id: 'top-clients', label: 'Top Clients' },
  { id: 'top-devices', label: 'Top Devices' },
];
document.getElementById('tabs').innerHTML = TABS.map((t, i) =>
  `<button data-tab="${t.id}" class="${i === 0 ? 'active' : ''}">${t.label}</button>`
).join('');
document.getElementById('tabs').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-tab]');
  if (!btn) return;
  document.querySelectorAll('nav.tabs button').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('page-' + btn.dataset.tab).classList.add('active');
});

// ---------- Helpers ----------
function fmtTs(ts) {
  const d = new Date(ts);
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}
// Local-calendar-date helpers - deliberately NOT toISOString()/Date("YYYY-MM-DD"),
// both of which are UTC and would show the wrong date in a non-UTC timezone
// (e.g. "Today" rendering as yesterday's date in UTC+2).
function fmtDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function parseLocalDate(str) {
  const [y, m, d] = str.split('-').map(Number);
  return new Date(y, m - 1, d);
}
function color(name) { return getComputedStyle(document.body).getPropertyValue(name).trim(); }
function niceMax(v) {
  if (v <= 0) return 1;
  const mag = Math.pow(10, Math.floor(Math.log10(v)));
  const norm = v / mag;
  let step;
  if (norm <= 1) step = 1; else if (norm <= 2) step = 2; else if (norm <= 5) step = 5; else step = 10;
  return step * mag;
}
function cleanDeviceModel(name) {
  return (name || '').replace(/\s+[0-9a-fA-F]{2}:[0-9a-fA-F]{2}$/, '').trim() || name;
}
function estimatePollIntervalMinutes(sortedDistinctTs) {
  if (sortedDistinctTs.length < 2) return 10;
  const times = sortedDistinctTs.map(t => new Date(t));
  const deltas = [];
  for (let i = 0; i < times.length - 1; i++) deltas.push((times[i + 1] - times[i]) / 60000);
  deltas.sort((a, b) => a - b);
  const mid = Math.floor(deltas.length / 2);
  const med = deltas.length % 2 ? deltas[mid] : (deltas[mid - 1] + deltas[mid]) / 2;
  return med > 0 ? med : 10;
}

// ---------- Filter state ----------
const allClientTs = RAW.clients.map(c => c.ts).sort();
const dataMin = allClientTs.length ? new Date(allClientTs[0]) : new Date();
const dataMax = allClientTs.length ? new Date(allClientTs[allClientTs.length - 1]) : new Date();

let filter = { dateFrom: null, dateTo: null, hourFrom: 0, hourTo: 23, preset: 'all' };

const PRESETS = [
  { id: 'today', label: 'Today' },
  { id: '7d', label: 'Last 7 days' },
  { id: '30d', label: 'Last 30 days' },
  { id: 'all', label: 'All time' },
];
document.getElementById('date-presets').innerHTML = PRESETS.map(p =>
  `<button data-preset="${p.id}" class="${p.id === 'all' ? 'active' : ''}">${p.label}</button>`
).join('');

const hourFromSel = document.getElementById('hour-from');
const hourToSel = document.getElementById('hour-to');
function hourLabel(h) { return h === 0 ? '12 AM' : h < 12 ? h + ' AM' : h === 12 ? '12 PM' : (h - 12) + ' PM'; }
for (let h = 0; h < 24; h++) {
  hourFromSel.innerHTML += `<option value="${h}">${hourLabel(h)}</option>`;
  hourToSel.innerHTML += `<option value="${h}" ${h === 23 ? 'selected' : ''}>${hourLabel(h)}</option>`;
}

function applyPreset(id) {
  filter.preset = id;
  const now = dataMax;
  if (id === 'today') {
    filter.dateFrom = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    filter.dateTo = now;
  } else if (id === '7d') {
    filter.dateFrom = new Date(now - 7 * 86400000);
    filter.dateTo = now;
  } else if (id === '30d') {
    filter.dateFrom = new Date(now - 30 * 86400000);
    filter.dateTo = now;
  } else {
    filter.dateFrom = null;
    filter.dateTo = null;
  }
  document.getElementById('date-from').value = filter.dateFrom ? fmtDate(filter.dateFrom) : '';
  document.getElementById('date-to').value = filter.dateTo ? fmtDate(filter.dateTo) : '';
  document.querySelectorAll('#date-presets button').forEach(b => b.classList.toggle('active', b.dataset.preset === id));
  render();
}

document.getElementById('date-presets').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-preset]');
  if (btn) applyPreset(btn.dataset.preset);
});
document.getElementById('date-from').addEventListener('change', (e) => {
  filter.dateFrom = e.target.value ? parseLocalDate(e.target.value) : null;
  filter.preset = 'custom';
  document.querySelectorAll('#date-presets button').forEach(b => b.classList.remove('active'));
  render();
});
document.getElementById('date-to').addEventListener('change', (e) => {
  if (e.target.value) {
    const d = parseLocalDate(e.target.value);
    d.setHours(23, 59, 59, 999);
    filter.dateTo = d;
  } else {
    filter.dateTo = null;
  }
  filter.preset = 'custom';
  document.querySelectorAll('#date-presets button').forEach(b => b.classList.remove('active'));
  render();
});
hourFromSel.addEventListener('change', () => { filter.hourFrom = +hourFromSel.value; render(); });
hourToSel.addEventListener('change', () => { filter.hourTo = +hourToSel.value; render(); });
document.getElementById('filter-reset').addEventListener('click', () => {
  hourFromSel.value = 0; hourToSel.value = 23;
  filter.hourFrom = 0; filter.hourTo = 23;
  applyPreset('all');
});

function inHourRange(hour, from, to) {
  if (from <= to) return hour >= from && hour <= to;
  return hour >= from || hour <= to; // overnight wrap, e.g. 22 -> 6
}
function passesFilter(ts) {
  const d = new Date(ts);
  if (filter.dateFrom && d < filter.dateFrom) return false;
  if (filter.dateTo && d > filter.dateTo) return false;
  return inHourRange(d.getHours(), filter.hourFrom, filter.hourTo);
}

// ---------- Aggregation (all derived from filtered raw rows) ----------
function computeClientTimeseries(clients) {
  const byTs = {};
  clients.forEach(c => { (byTs[c.ts] = byTs[c.ts] || []).push(c); });
  return Object.keys(byTs).sort().map(ts => {
    const g = byTs[ts];
    return {
      ts, total: g.length,
      wireless: g.filter(c => c.type === 'WIRELESS').length,
      wired: g.filter(c => c.type === 'WIRED').length,
      guest: g.filter(c => c.access === 'GUEST').length,
      default: g.filter(c => c.access === 'DEFAULT').length,
    };
  });
}
function computeDailySummary(clients) {
  const byDate = {};
  clients.forEach(c => {
    const date = c.ts.slice(0, 10);
    byDate[date] = byDate[date] || { snapshots: {}, macs: new Set() };
    byDate[date].snapshots[c.ts] = (byDate[date].snapshots[c.ts] || 0) + 1;
    if (c.mac) byDate[date].macs.add(c.mac);
  });
  return Object.keys(byDate).sort().map(date => {
    const counts = Object.values(byDate[date].snapshots);
    const avg = counts.length ? counts.reduce((a, b) => a + b, 0) / counts.length : 0;
    return {
      date,
      avg_concurrent: Math.round(avg * 10) / 10,
      peak_concurrent: counts.length ? Math.max(...counts) : 0,
      unique_devices: byDate[date].macs.size,
    };
  });
}
function computeBandwidthTimeseries(devices) {
  const byDevice = {};
  devices.forEach(d => {
    const name = d.name || d.id;
    (byDevice[name] = byDevice[name] || []).push({
      ts: d.ts,
      rx_mbps: Math.round((d.rx || 0) / 1000) / 1000,
      tx_mbps: Math.round((d.tx || 0) / 1000) / 1000,
    });
  });
  return byDevice;
}
function computeClientDirectory(clients) {
  const byMac = {};
  clients.forEach(c => { (byMac[c.mac] = byMac[c.mac] || []).push(c); });
  const distinctTs = [...new Set(clients.map(c => c.ts))].sort();
  const latestTs = distinctTs.length ? distinctTs[distinctTs.length - 1] : null;
  const pollInterval = estimatePollIntervalMinutes(distinctTs);
  return Object.keys(byMac).map(mac => {
    const rows = byMac[mac].slice().sort((a, b) => a.ts.localeCompare(b.ts));
    const latest = rows[rows.length - 1];
    const timesSeen = rows.length;
    return {
      mac_address: mac,
      name: latest.name || '(unnamed)',
      ip_address: latest.ip,
      client_type: latest.type,
      access_type: latest.access,
      uplink_device_name: latest.ap || '-',
      first_seen: rows[0].ts,
      last_seen: latest.ts,
      times_seen: timesSeen,
      connected_minutes_est: Math.round(timesSeen * pollInterval),
      currently_connected: latest.ts === latestTs,
    };
  });
}
function computeTopDevices(clientDirectory) {
  const byDevice = {};
  RAW.all_device_names.forEach(n => { byDevice[n] = { current: 0, all_time: 0 }; });
  clientDirectory.forEach(c => {
    const key = c.uplink_device_name;
    byDevice[key] = byDevice[key] || { current: 0, all_time: 0 };
    byDevice[key].all_time += 1;
    if (c.currently_connected) byDevice[key].current += 1;
  });
  return Object.keys(byDevice)
    .map(name => ({ device_name: name, current_clients: byDevice[name].current, all_time_clients: byDevice[name].all_time }))
    .sort((a, b) => b.all_time_clients - a.all_time_clients);
}
function computeCommonDevices(clientDirectory) {
  const counts = {};
  clientDirectory.forEach(c => { const m = cleanDeviceModel(c.name); counts[m] = (counts[m] || 0) + 1; });
  return Object.entries(counts).map(([model, count]) => ({ model, count })).sort((a, b) => b.count - a.count);
}
function computeDeviceStatus(devices) {
  const latest = {};
  devices.forEach(d => { latest[d.id] = d; }); // devices arrive ts-ascending
  return Object.values(latest);
}

// ---------- Charting ----------
function drawLineChart(svgId, tooltipId, legendId, series, xs, unit) {
  const svg = document.getElementById(svgId);
  const tooltip = document.getElementById(tooltipId);
  const legendEl = legendId ? document.getElementById(legendId) : null;
  const W = 1000, H = 280, padL = 40, padR = 12, padT = 12, padB = 28;
  const plotW = W - padL - padR, plotH = H - padT - padB;

  if (!xs.length) {
    svg.innerHTML = '';
    svg.insertAdjacentHTML('afterend', '<div class="empty">No data in this range</div>');
    return;
  }
  document.querySelectorAll(`#${svgId} ~ .empty`).forEach(el => el.remove());
  svg.style.display = '';

  const maxY = niceMax(Math.max(1, ...series.flatMap(s => s.values)));
  const xStep = xs.length > 1 ? plotW / (xs.length - 1) : 0;
  const yScale = (v) => padT + plotH - (v / maxY) * plotH;
  const xScale = (i) => padL + i * xStep;

  let svgContent = '';
  for (let i = 0; i <= 4; i++) {
    const y = padT + (plotH / 4) * i;
    const val = Math.round(maxY - (maxY / 4) * i);
    svgContent += `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" class="gridline"/>`;
    svgContent += `<text x="${padL - 8}" y="${y + 3}" text-anchor="end" class="axis-text">${val}</text>`;
  }
  [0, Math.floor((xs.length - 1) / 2), xs.length - 1].forEach(i => {
    svgContent += `<text x="${xScale(i)}" y="${H - 8}" text-anchor="middle" class="axis-text">${fmtTs(xs[i])}</text>`;
  });

  series.forEach(s => {
    const pts = s.values.map((v, i) => `${xScale(i)},${yScale(v)}`).join(' ');
    svgContent += `<polyline points="${pts}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    const lastI = s.values.length - 1;
    svgContent += `<circle cx="${xScale(lastI)}" cy="${yScale(s.values[lastI])}" r="4" fill="${s.color}" stroke="var(--surface-1)" stroke-width="2"/>`;
  });

  svgContent += `<rect id="${svgId}-overlay" x="${padL}" y="${padT}" width="${plotW}" height="${plotH}" fill="transparent"/>`;
  svgContent += `<line id="${svgId}-crosshair" x1="0" y1="${padT}" x2="0" y2="${padT + plotH}" stroke="var(--axis)" stroke-width="1" style="display:none"/>`;

  svg.innerHTML = svgContent;

  if (legendEl) {
    legendEl.innerHTML = series.map(s =>
      `<span class="key"><span class="swatch" style="background:${s.color}"></span>${s.name}</span>`
    ).join('');
  }

  const overlay = document.getElementById(`${svgId}-overlay`);
  const crosshair = document.getElementById(`${svgId}-crosshair`);
  overlay.addEventListener('mousemove', (e) => {
    const rect = svg.getBoundingClientRect();
    const scaleX = W / rect.width;
    const mouseX = (e.clientX - rect.left) * scaleX;
    let idx = Math.round((mouseX - padL) / xStep);
    idx = Math.max(0, Math.min(xs.length - 1, idx));
    const cx = xScale(idx);
    crosshair.setAttribute('x1', cx); crosshair.setAttribute('x2', cx);
    crosshair.style.display = 'block';
    tooltip.style.display = 'block';
    tooltip.style.left = `${(cx / W) * rect.width + 10}px`;
    tooltip.style.top = `${(e.clientY - rect.top) - 10}px`;
    tooltip.innerHTML = `<div style="color:var(--text-secondary);margin-bottom:4px">${fmtTs(xs[idx])}</div>` +
      series.map(s => `<div class="row"><span><span class="dot" style="background:${s.color}"></span>${s.name}</span><b>${s.values[idx]}${unit || ''}</b></div>`).join('');
  });
  overlay.addEventListener('mouseleave', () => {
    crosshair.style.display = 'none';
    tooltip.style.display = 'none';
  });
}

function accessBadge(t) { return t === 'GUEST' ? '<span class="badge badge-guest">Guest</span>' : '<span class="badge badge-default">Default</span>'; }

// ---------- Main render ----------
function render() {
  const clients = RAW.clients.filter(c => passesFilter(c.ts));
  const devices = RAW.devices.filter(d => passesFilter(d.ts));

  const summaryParts = [];
  summaryParts.push(filter.dateFrom || filter.dateTo
    ? `${filter.dateFrom ? fmtDate(filter.dateFrom) : 'start'} to ${filter.dateTo ? fmtDate(filter.dateTo) : 'now'}`
    : 'All time');
  if (filter.hourFrom !== 0 || filter.hourTo !== 23) summaryParts.push(`${hourLabel(filter.hourFrom)}-${hourLabel(filter.hourTo)} daily`);
  document.getElementById('filter-summary').textContent =
    `Showing: ${summaryParts.join(', ')} - ${clients.length.toLocaleString()} client-poll rows, ${devices.length.toLocaleString()} device-poll rows`;

  const ts = computeClientTimeseries(clients);
  const dailySummary = computeDailySummary(clients);
  const bandwidth = computeBandwidthTimeseries(devices);
  const clientDirectory = computeClientDirectory(clients);
  const topDevices = computeTopDevices(clientDirectory);
  const commonDevices = computeCommonDevices(clientDirectory);
  const deviceStatus = computeDeviceStatus(devices);

  // Tiles
  const last = ts.length ? ts[ts.length - 1] : null;
  const peak = ts.length ? Math.max(...ts.map(t => t.total)) : 0;
  const lastDay = dailySummary.length ? dailySummary[dailySummary.length - 1] : null;
  const gwBw = bandwidth[RAW.gateway_device_name];
  const lastBw = gwBw && gwBw.length ? gwBw[gwBw.length - 1] : null;
  const tiles = [
    { label: 'Connected (end of range)', value: last ? last.total : '-' },
    { label: 'Peak concurrent', value: peak },
    { label: 'Unique devices (last day in range)', value: lastDay ? lastDay.unique_devices : '-' },
    { label: 'Download (end of range)', value: lastBw ? lastBw.rx_mbps.toFixed(1) + ' Mbps' : '-' },
    { label: 'Upload (end of range)', value: lastBw ? lastBw.tx_mbps.toFixed(1) + ' Mbps' : '-' },
  ];
  document.getElementById('tiles').innerHTML = tiles.map(t =>
    `<div class="tile"><div class="label">${t.label}</div><div class="value">${t.value}</div></div>`
  ).join('');

  drawLineChart('chart-clients', 'tooltip-clients', 'legend-clients', [
    { name: 'Total', color: color('--series-1'), values: ts.map(t => t.total) },
    { name: 'Wireless', color: color('--series-3'), values: ts.map(t => t.wireless) },
    { name: 'Guest', color: color('--series-4'), values: ts.map(t => t.guest) },
  ], ts.map(t => t.ts), '');

  if (gwBw && gwBw.length) {
    drawLineChart('chart-bw', 'tooltip-bw', 'legend-bw', [
      { name: 'Download', color: color('--series-1'), values: gwBw.map(b => b.rx_mbps) },
      { name: 'Upload', color: color('--series-2'), values: gwBw.map(b => b.tx_mbps) },
    ], gwBw.map(b => b.ts), ' Mbps');
  } else {
    drawLineChart('chart-bw', 'tooltip-bw', 'legend-bw', [], [], '');
  }

  const dailyRows = dailySummary.slice().reverse();
  document.getElementById('daily-table').innerHTML = dailyRows.length ? `
    <div class="table-scroll"><table><thead><tr><th>Date</th><th>Avg concurrent</th><th>Peak concurrent</th><th>Unique devices</th></tr></thead>
    <tbody>${dailyRows.map(d => `<tr><td>${d.date}</td><td>${d.avg_concurrent}</td><td>${d.peak_concurrent}</td><td>${d.unique_devices}</td></tr>`).join('')}</tbody></table></div>
  ` : '<div class="empty">No data in this range</div>';

  document.getElementById('device-table').innerHTML = deviceStatus.length ? `
    <div class="table-scroll"><table><thead><tr><th>Name</th><th>Model</th><th>State</th><th>MAC</th><th>IP</th><th>Firmware</th><th>CPU</th><th>Memory</th><th>Uptime</th></tr></thead>
    <tbody>${deviceStatus.map(d => `<tr><td>${d.name}</td><td>${d.model}</td>
      <td class="${d.state === 'ONLINE' ? 'state-online' : 'state-offline'}">${d.state}</td>
      <td class="mono">${d.mac || '-'}</td>
      <td class="mono">${d.ip || '-'}</td>
      <td>${d.fw || '-'}</td>
      <td>${d.cpu != null ? d.cpu + '%' : '-'}</td>
      <td>${d.mem != null ? d.mem + '%' : '-'}</td>
      <td>${d.uptime != null ? Math.floor(d.uptime / 3600) + 'h' : '-'}</td></tr>`).join('')}</tbody></table></div>
  ` : '<div class="empty">No data in this range</div>';

  const topCommon = commonDevices.slice(0, 15);
  const maxCount = topCommon.length ? topCommon[0].count : 1;
  document.getElementById('common-devices-list').innerHTML = topCommon.length ? `
    <div class="barlist">${topCommon.map(d => `
      <div class="bar-row">
        <span class="bar-label">${d.model}</span>
        <div class="bar-track"><div class="bar-fill" style="width:${(d.count / maxCount * 100).toFixed(0)}%"></div></div>
        <span class="bar-value">${d.count}</span>
      </div>`).join('')}</div>
  ` : '<div class="empty">No data in this range</div>';

  const directory = clientDirectory.slice().sort((a, b) => {
    if (a.currently_connected !== b.currently_connected) return a.currently_connected ? -1 : 1;
    return b.last_seen.localeCompare(a.last_seen);
  });
  document.getElementById('directory-table').innerHTML = directory.length ? `
    <div class="table-scroll"><table><thead><tr><th>Name</th><th>MAC</th><th>IP</th><th>Type</th><th>Access</th><th>AP</th><th>First seen</th><th>Last seen</th><th>Connected</th></tr></thead>
    <tbody>${directory.map(c => `<tr><td>${c.name}</td><td class="mono">${c.mac_address}</td><td class="mono">${c.ip_address || '-'}</td>
      <td>${c.client_type}</td><td>${accessBadge(c.access_type)}</td><td>${c.uplink_device_name}</td>
      <td>${fmtTs(c.first_seen)}</td><td>${fmtTs(c.last_seen)}</td>
      <td class="${c.currently_connected ? 'badge-yes' : 'badge-no'}">${c.currently_connected ? 'Yes' : 'No'}</td></tr>`).join('')}</tbody></table></div>
  ` : '<div class="empty">No data in this range</div>';

  const topClients = clientDirectory.slice().sort((a, b) => b.connected_minutes_est - a.connected_minutes_est).slice(0, 30);
  document.getElementById('top-clients-table').innerHTML = topClients.length ? `
    <div class="table-scroll"><table><thead><tr><th>#</th><th>Name</th><th>MAC</th><th>AP</th><th>Times seen</th><th>Est. connected time</th><th>End of range</th></tr></thead>
    <tbody>${topClients.map((c, i) => `<tr><td class="rank">${i + 1}</td><td>${c.name}</td><td class="mono">${c.mac_address}</td><td>${c.uplink_device_name}</td>
      <td>${c.times_seen}</td><td>${c.connected_minutes_est >= 60 ? (c.connected_minutes_est / 60).toFixed(1) + ' h' : c.connected_minutes_est + ' min'}</td>
      <td class="${c.currently_connected ? 'badge-yes' : 'badge-no'}">${c.currently_connected ? 'Yes' : '-'}</td></tr>`).join('')}</tbody></table></div>
  ` : '<div class="empty">No data in this range</div>';

  document.getElementById('top-devices-table').innerHTML = topDevices.length ? `
    <div class="table-scroll"><table><thead><tr><th>#</th><th>Device</th><th>Clients (end of range)</th><th>Unique clients in range</th></tr></thead>
    <tbody>${topDevices.map((d, i) => `<tr><td class="rank">${i + 1}</td><td>${d.device_name}</td><td>${d.current_clients}</td><td>${d.all_time_clients}</td></tr>`).join('')}</tbody></table></div>
  ` : '<div class="empty">No data in this range</div>';
}

applyPreset('all');
</script>
</body>
</html>
"""


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    client_rows_all = load_rows(conn, "client_snapshots")
    device_rows_all = load_rows(conn, "device_stats")
    conn.close()

    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(days=REPORT_HISTORY_DAYS)).isoformat()
    client_rows = [r for r in client_rows_all if r["ts"] >= cutoff]
    device_rows = [r for r in device_rows_all if r["ts"] >= cutoff]

    all_ts = sorted(set(r["ts"] for r in client_rows))
    if len(all_ts) >= 2:
        times = [datetime.datetime.fromisoformat(t) for t in all_ts]
        deltas = sorted((times[i + 1] - times[i]).total_seconds() / 60 for i in range(len(times) - 1))
        mid = len(deltas) // 2
        poll_interval = deltas[mid] if len(deltas) % 2 else (deltas[mid - 1] + deltas[mid]) / 2
        poll_interval = poll_interval if poll_interval > 0 else 10.0
    else:
        poll_interval = 10.0

    # device_directory/id_to_name built from FULL history so names/identity
    # are stable even when the interactive window is narrower.
    device_directory, device_id_to_name = build_device_directory(device_rows_all)
    all_device_names = [d["device_name"] for d in device_directory]
    gateway_name = next((d["device_name"] for d in device_directory if d.get("model") == "UCG Ultra"),
                         device_directory[0]["device_name"] if device_directory else None)

    clients_raw = [
        {
            "ts": r["ts"],
            "mac": r["mac_address"],
            "name": r["name"],
            "ip": r["ip_address"],
            "type": r["client_type"],
            "access": r["access_type"],
            "ap": device_id_to_name.get(r["uplink_device_id"]) or "-",
        }
        for r in client_rows
    ]
    devices_raw = [
        {
            "ts": r["ts"],
            "id": r["device_id"],
            "name": r["device_name"],
            "model": r["model"],
            "mac": r["mac_address"],
            "ip": r["ip_address"],
            "fw": r["firmware_version"],
            "state": r["state"],
            "uptime": r["uptime_sec"],
            "cpu": r["cpu_pct"],
            "mem": r["mem_pct"],
            "tx": r["tx_rate_bps"],
            "rx": r["rx_rate_bps"],
        }
        for r in device_rows
    ]

    data = {
        "clients": clients_raw,
        "devices": devices_raw,
        "all_device_names": all_device_names,
        "gateway_device_name": gateway_name,
    }

    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    html = (HTML_TEMPLATE
            .replace("__DATA_JSON__", json.dumps(data))
            .replace("__GENERATED_AT__", generated_at)
            .replace("__POLL_INTERVAL__", str(round(poll_interval)))
            .replace("__HISTORY_DAYS__", str(REPORT_HISTORY_DAYS)))

    report_path = os.path.join(DATA_DIR, "report.html")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)

    export_csv(client_rows_all, os.path.join(DATA_DIR, "clients_export.csv"))
    export_csv(device_rows_all, os.path.join(DATA_DIR, "device_stats_export.csv"))

    print(f"Wrote {report_path} ({len(client_rows)} client rows, {len(device_rows)} device rows embedded)")
    print(f"Wrote {len(client_rows_all)} client rows (full history) -> clients_export.csv")
    print(f"Wrote {len(device_rows_all)} device-stat rows (full history) -> device_stats_export.csv")


if __name__ == "__main__":
    main()
