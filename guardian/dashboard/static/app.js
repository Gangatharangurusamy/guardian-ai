/* ============================================================
   GUARDIAN Dashboard — app.js
   Live WebSocket client + DOM helpers + Chart.js integration
   ============================================================ */

'use strict';

// ── Constants ─────────────────────────────────────────────
const API = '/api/v1';
const WS_URL = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/traces`;
const RECONNECT_MS = 3000;

// ── GuardianWS ─────────────────────────────────────────────
class GuardianWS {
  constructor(onTrace, onSnapshot) {
    this._onTrace = onTrace;
    this._onSnapshot = onSnapshot;
    this._ws = null;
    this._reconnectTimer = null;
    this._closed = false;
    this._connect();
  }

  _connect() {
    try {
      this._ws = new WebSocket(WS_URL);

      this._ws.addEventListener('open', () => {
        updateWsStatus('connected');
        if (this._reconnectTimer) { clearTimeout(this._reconnectTimer); this._reconnectTimer = null; }
      });

      this._ws.addEventListener('message', (evt) => {
        try {
          const msg = JSON.parse(evt.data);
          if (msg.type === 'snapshot' && this._onSnapshot) this._onSnapshot(msg.data);
          else if (msg.type === 'trace' && this._onTrace) this._onTrace(msg.data);
          // ping messages are silently ignored
        } catch (_) {}
      });

      this._ws.addEventListener('close', () => {
        updateWsStatus('disconnected');
        if (!this._closed) this._scheduleReconnect();
      });

      this._ws.addEventListener('error', () => updateWsStatus('error'));
    } catch (_) {
      if (!this._closed) this._scheduleReconnect();
    }
  }

  _scheduleReconnect() {
    updateWsStatus('reconnecting');
    this._reconnectTimer = setTimeout(() => this._connect(), RECONNECT_MS);
  }

  close() {
    this._closed = true;
    if (this._ws) this._ws.close();
    if (this._reconnectTimer) clearTimeout(this._reconnectTimer);
  }
}

// ── WS Status indicator ───────────────────────────────────
function updateWsStatus(state) {
  const dot  = document.getElementById('ws-dot');
  const text = document.getElementById('ws-text');
  if (!dot || !text) return;
  dot.className = 'ws-dot';
  const labels = { connected: 'Live', disconnected: 'Offline', error: 'Error', reconnecting: 'Reconnecting…' };
  text.textContent = labels[state] || state;
  if (state === 'connected') dot.classList.add('connected');
  if (state === 'error')     dot.classList.add('error');
}

// ── Fetch helpers ─────────────────────────────────────────
async function apiFetch(path) {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// ── Badge helpers ─────────────────────────────────────────
function severityBadge(sev) {
  const s = (sev || 'log').toLowerCase();
  return `<span class="badge badge-${s}">${s}</span>`;
}

function statusBadge(status) {
  const s = (status || '').toLowerCase();
  const cls = s === 'success' ? 'ok' : s === 'running' ? 'run' : 'err';
  return `<span class="badge badge-${cls}">${s || '—'}</span>`;
}

function shortId(id) {
  return id ? id.substring(0, 8) + '…' : '—';
}

function fmtDate(iso) {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleTimeString(); } catch (_) { return iso; }
}

// ── updateTraceTable ──────────────────────────────────────
function updateTraceTable(traces, tableId = 'trace-table') {
  const tbody = document.querySelector(`#${tableId} tbody`);
  if (!tbody) return;
  tbody.innerHTML = '';
  (traces || []).forEach(t => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="mono truncate" title="${t.session_id || ''}">${shortId(t.session_id)}</td>
      <td>${t.agent_name || '—'}</td>
      <td>${statusBadge(t.status)}</td>
      <td class="mono">${t.duration_ms ?? '—'} ms</td>
      <td class="mono">${fmtDate(t.started_at)}</td>
    `;
    // Expand/collapse row on click
    tr.addEventListener('click', () => toggleRowDetail(tr, t));
    tbody.appendChild(tr);
  });
}

function toggleRowDetail(tr, data) {
  const next = tr.nextSibling;
  if (next && next.classList && next.classList.contains('detail-row')) {
    next.remove();
    return;
  }
  const detailRow = document.createElement('tr');
  detailRow.className = 'detail-row';
  const td = document.createElement('td');
  td.colSpan = 5;
  td.innerHTML = `<div class="row-detail open"><pre>${JSON.stringify(data, null, 2)}</pre></div>`;
  detailRow.appendChild(td);
  tr.after(detailRow);
}

// ── updateEthicsTable ─────────────────────────────────────
function updateEthicsTable(flags, tableId = 'ethics-table') {
  const tbody = document.querySelector(`#${tableId} tbody`);
  if (!tbody) return;
  tbody.innerHTML = '';
  (flags || []).forEach(f => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="mono truncate" title="${f.session_id || ''}">${shortId(f.session_id)}</td>
      <td>${f.agent_name || '—'}</td>
      <td>${f.violation_type || '—'}</td>
      <td>${severityBadge(f.severity)}</td>
      <td class="mono">${fmtDate(f.detected_at)}</td>
    `;
    tbody.appendChild(tr);
  });
}

// ── updateRecoveryTable ───────────────────────────────────
function updateRecoveryTable(actions, tableId = 'recovery-table') {
  const tbody = document.querySelector(`#${tableId} tbody`);
  if (!tbody) return;
  tbody.innerHTML = '';
  (actions || []).forEach(a => {
    const cls = a.success ? 'ok' : 'err';
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="mono truncate" title="${a.session_id || ''}">${shortId(a.session_id)}</td>
      <td>${a.agent_name || '—'}</td>
      <td>${a.action_taken || '—'}</td>
      <td>${a.failure_type || '—'}</td>
      <td><span class="badge badge-${cls}">${a.success ? 'OK' : 'FAIL'}</span></td>
    `;
    tbody.appendChild(tr);
  });
}

// ── downloadCompliance ────────────────────────────────────
async function downloadCompliance(sessionId) {
  try {
    const data = await apiFetch(`/traces/${sessionId}/compliance`);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url;
    a.download = `compliance_${sessionId.substring(0, 8)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    console.error('Compliance download failed:', err);
  }
}

// ── Chart helpers ─────────────────────────────────────────
const _charts = {};

function getOrCreateChart(canvasId, config) {
  if (_charts[canvasId]) return _charts[canvasId];
  const ctx = document.getElementById(canvasId);
  if (!ctx) return null;
  const chart = new Chart(ctx, config);
  _charts[canvasId] = chart;
  return chart;
}

function initLineChart(canvasId) {
  return getOrCreateChart(canvasId, {
    type: 'line',
    data: {
      labels: [],
      datasets: [{
        label: 'Traces / hour',
        data: [],
        borderColor: '#00BFA5',
        backgroundColor: 'rgba(0,191,165,0.08)',
        borderWidth: 2,
        tension: 0.4,
        fill: true,
        pointRadius: 3,
        pointBackgroundColor: '#00BFA5',
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 400 },
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: '#4A5A7A', maxTicksLimit: 8 }, grid: { color: 'rgba(0,191,165,0.06)' } },
        y: { ticks: { color: '#4A5A7A' }, grid: { color: 'rgba(0,191,165,0.06)' }, beginAtZero: true },
      }
    }
  });
}

function initDoughnutChart(canvasId) {
  return getOrCreateChart(canvasId, {
    type: 'doughnut',
    data: {
      labels: ['LOG', 'WARN', 'BLOCK'],
      datasets: [{
        data: [0, 0, 0],
        backgroundColor: ['rgba(59,130,246,0.8)', 'rgba(245,158,11,0.8)', 'rgba(239,68,68,0.8)'],
        borderColor: ['#3B82F6', '#F59E0B', '#EF4444'],
        borderWidth: 1,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 400 },
      plugins: {
        legend: {
          position: 'right',
          labels: { color: '#8B9DC8', font: { size: 12 }, padding: 12 }
        }
      },
      cutout: '68%',
    }
  });
}

function updateLineChart(chart, traces) {
  if (!chart || !traces) return;
  // Group by hour
  const buckets = {};
  traces.forEach(t => {
    if (!t.started_at) return;
    const h = new Date(t.started_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    buckets[h] = (buckets[h] || 0) + 1;
  });
  const labels = Object.keys(buckets).slice(-12);
  const data   = labels.map(l => buckets[l]);
  chart.data.labels = labels;
  chart.data.datasets[0].data = data;
  chart.update('none');
}

function updateDoughnutChart(chart, flags) {
  if (!chart || !flags) return;
  const counts = { log: 0, warn: 0, block: 0 };
  flags.forEach(f => {
    const s = (f.severity || '').toLowerCase();
    if (s in counts) counts[s]++;
  });
  chart.data.datasets[0].data = [counts.log, counts.warn, counts.block];
  chart.update('none');
}

// ── Stat card helpers ─────────────────────────────────────
function setStat(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value ?? '—';
}

// ── Expose globals for inline use ────────────────────────
window.GuardianWS = GuardianWS;
window.apiFetch = apiFetch;
window.updateTraceTable = updateTraceTable;
window.updateEthicsTable = updateEthicsTable;
window.updateRecoveryTable = updateRecoveryTable;
window.downloadCompliance = downloadCompliance;
window.initLineChart = initLineChart;
window.initDoughnutChart = initDoughnutChart;
window.updateLineChart = updateLineChart;
window.updateDoughnutChart = updateDoughnutChart;
window.setStat = setStat;
window.updateWsStatus = updateWsStatus;
window.severityBadge = severityBadge;
window.statusBadge = statusBadge;
