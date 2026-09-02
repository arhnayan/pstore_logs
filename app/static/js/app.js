/* PowerStore Monitor Dashboard — live I/O edition */

const MAX_POINTS = 180;
const state = {
  alerts: [], events: [], hardware: [], ports: [], metrics: [],
  audit: [], collections: [], overview: null, status: {},
  live: null, capacity: null, storage: null, nas: null,
  cluster: null, protection: null, portMetrics: null, resources: null,
  charts: {}, volumeNames: {}, hostNames: {}, portNames: {},
  pinnedVolumeId: null, collectionPollTimer: null,
  reportLocations: [], reportStatus: null, reportPollTimer: null,
};

function $(sel) { return document.querySelector(sel); }
function $$(sel) { return document.querySelectorAll(sel); }

function toast(msg) {
  const el = $('#toast');
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 3000);
}

function fmtErrorDetail(detail) {
  if (!detail) return '';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(item => item.msg || JSON.stringify(item)).join('; ');
  return JSON.stringify(detail);
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(fmtErrorDetail(err.detail) || res.statusText);
  }
  if (res.headers.get('content-type')?.includes('application/json')) return res.json();
  return res;
}

function fmtTime(ts) {
  if (!ts) return '—';
  return new Date(ts).toLocaleString();
}

function fmtBytes(n) {
  if (n == null || isNaN(n)) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let v = Number(n), i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(i > 1 ? 1 : 0)} ${units[i]}`;
}

function fmtElapsed(startTs) {
  if (!startTs) return '—';
  const ms = Date.now() - new Date(startTs).getTime();
  if (ms < 0) return '—';
  const mins = Math.floor(ms / 60000);
  const hrs = Math.floor(mins / 60);
  if (hrs > 0) return `${hrs}h ${mins % 60}m`;
  return `${mins}m`;
}

function applianceSummary(row) {
  const apps = row.appliances || [];
  if (!apps.length) return '—';
  return apps.map(a => {
    const sn = a.appliance_serial_number?.slice(-4) || '?';
    const size = a.compressed_size ? `, ${fmtBytes(a.compressed_size)}` : '';
    return `${sn}: ${a.status || '—'}${size}`;
  }).join('; ');
}

function cpuUtil(payload) {
  if (!payload) return null;
  const v = payload.io_workload_cpu_utilization ?? payload.avg_io_workload_cpu_utilization;
  return v != null ? Number(v) : null;
}

function fmtMbps(bytesPerSec) {
  if (bytesPerSec == null) return '—';
  return (Number(bytesPerSec) / (1024 * 1024)).toFixed(1);
}

function fmtIops(n) {
  if (n == null || isNaN(n)) return '—';
  const v = Number(n);
  if (v > 0 && v < 1) return v.toFixed(1);
  return v.toFixed(0);
}

function chartScaleMax(values, floor = 1, pad = 1.3) {
  const nums = values.filter(v => v != null && !isNaN(v)).map(Number);
  const peak = nums.length ? Math.max(...nums) : 0;
  return Math.max(floor, peak * pad);
}

function resetClusterCharts() {
  for (const id of ['cluster-iops-chart', 'cluster-bw-chart', 'cluster-lat-chart', 'cluster-cpu-chart']) {
    const chart = state.charts[id];
    if (!chart) continue;
    chart.data.labels = [];
    chart.data.datasets.forEach(ds => { ds.data = []; });
  }
}

function perfField(payload, instantKey, rollupKey) {
  const v = payload?.[instantKey] ?? payload?.[rollupKey];
  return v != null ? Number(v) : null;
}

function normalizePerf(payload) {
  if (!payload) return null;
  return {
    read_iops: perfField(payload, 'read_iops', 'avg_read_iops'),
    write_iops: perfField(payload, 'write_iops', 'avg_write_iops'),
    total_iops: perfField(payload, 'total_iops', 'avg_total_iops'),
    read_bandwidth: perfField(payload, 'read_bandwidth', 'avg_read_bandwidth'),
    write_bandwidth: perfField(payload, 'write_bandwidth', 'avg_write_bandwidth'),
    avg_latency: payload.avg_latency,
    avg_read_latency: payload.avg_read_latency,
    avg_write_latency: payload.avg_write_latency,
    io_workload_cpu_utilization: cpuUtil(payload),
  };
}

function sevBadge(sev) {
  return `<span class="sev sev-${sev}">${sev || '—'}</span>`;
}

function escAttr(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;');
}

function tableHtml(headers, rows, opts = {}) {
  if (!rows.length) return '<div class="empty">No data</div>';
  const { tableClass = '', colClasses = [] } = opts;
  const head = headers.map((h, i) => {
    const cls = colClasses[i] ? ` class="${colClasses[i]}"` : '';
    return `<th${cls}>${h.label}</th>`;
  }).join('');
  const body = rows.map(row =>
    `<tr>${headers.map(h => `<td>${h.render ? h.render(row) : (row[h.key] ?? '—')}</td>`).join('')}</tr>`
  ).join('');
  const cls = tableClass ? ` class="${tableClass}"` : '';
  return `<table${cls}><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

const chartDefaults = {
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  plugins: { legend: { labels: { color: '#8b949e' } } },
  scales: {
    x: { ticks: { color: '#8b949e', maxRotation: 0, autoSkip: true, maxTicksLimit: 8 }, grid: { color: '#30363d' } },
    y: { ticks: { color: '#8b949e' }, grid: { color: '#30363d' } },
  },
};

function getOrCreateChart(id, config) {
  const key = id.replace('#', '');
  if (state.charts[key]) return state.charts[key];
  const ctx = $(id);
  if (!ctx) return null;
  state.charts[key] = new Chart(ctx, config);
  return state.charts[key];
}

function pushPoint(chart, label, values) {
  if (!chart) return;
  chart.data.labels.push(label);
  chart.data.datasets.forEach((ds, i) => {
    ds.data.push(values[i] ?? null);
  });
  while (chart.data.labels.length > MAX_POINTS) {
    chart.data.labels.shift();
    chart.data.datasets.forEach(ds => ds.data.shift());
  }
  chart.update('none');
}

function initLineChart(id, datasets, yLabel = '') {
  return getOrCreateChart(id, {
    type: 'line',
    data: { labels: [], datasets: datasets.map(d => ({ ...d, data: [] })) },
    options: {
      ...chartDefaults,
      scales: {
        ...chartDefaults.scales,
        y: { ...chartDefaults.scales.y, title: yLabel ? { display: true, text: yLabel, color: '#8b949e' } : undefined },
      },
    },
  });
}

function ensureClusterCharts() {
  initLineChart('#cluster-iops-chart', [
    { label: 'Read', borderColor: '#54a0ff', backgroundColor: '#54a0ff22', tension: 0.2, fill: false },
    { label: 'Write', borderColor: '#ff9f43', backgroundColor: '#ff9f4322', tension: 0.2, fill: false },
    { label: 'Total', borderColor: '#7c5cff', backgroundColor: '#7c5cff22', tension: 0.2, fill: false },
  ], 'IOPS');
  initLineChart('#cluster-bw-chart', [
    { label: 'Read MB/s', borderColor: '#2ed573', backgroundColor: '#2ed57322', tension: 0.2, fill: false },
    { label: 'Write MB/s', borderColor: '#ff4757', backgroundColor: '#ff475722', tension: 0.2, fill: false },
  ], 'MB/s');
  initLineChart('#cluster-lat-chart', [
    { label: 'Avg', borderColor: '#7c5cff', tension: 0.2, fill: false },
    { label: 'Read', borderColor: '#54a0ff', tension: 0.2, fill: false },
    { label: 'Write', borderColor: '#ff9f43', tension: 0.2, fill: false },
  ], 'µs');
  initLineChart('#overview-iops-spark', [
    { label: 'Total IOPS', borderColor: '#7c5cff', backgroundColor: '#7c5cff33', tension: 0.3, fill: true },
  ]);
  initLineChart('#overview-lat-spark', [
    { label: 'Latency', borderColor: '#54a0ff', backgroundColor: '#54a0ff33', tension: 0.3, fill: true },
  ]);
  initLineChart('#cluster-cpu-chart', [
    { label: 'Cluster CPU %', borderColor: '#ff6b81', backgroundColor: '#ff6b8122', tension: 0.2, fill: true },
  ], 'CPU %');
}

function updateConnection(status) {
  const dot = $('#conn-dot');
  const text = $('#conn-text');
  const conn = status.connection || 'pending';
  dot.className = 'status-dot ' + (conn === 'connected' ? 'connected' : conn === 'no_credentials' ? 'pending' : 'error');
  const labels = { connected: 'Connected', no_credentials: 'No credentials — configure in Settings', auth_failed: 'Authentication failed', error: 'Connection error' };
  text.textContent = labels[conn] || conn;
  const last = status.last_perf_fast_poll || status.last_alerts_poll;
  $('#last-poll').textContent = last ? `Last poll: ${fmtTime(last)}` : '';
}

function appendClusterPerf(payload, ts) {
  if (!payload) return;
  const p = normalizePerf(payload);
  const label = new Date(ts || payload.timestamp || Date.now()).toLocaleTimeString();
  pushPoint(state.charts['cluster-iops-chart'], label, [p.read_iops, p.write_iops, p.total_iops]);
  pushPoint(state.charts['cluster-bw-chart'], label, [
    fmtMbps(p.read_bandwidth), fmtMbps(p.write_bandwidth),
  ]);
  pushPoint(state.charts['cluster-lat-chart'], label, [p.avg_latency, p.avg_read_latency, p.avg_write_latency]);
  pushPoint(state.charts['overview-iops-spark'], label, [p.total_iops]);
  pushPoint(state.charts['overview-lat-spark'], label, [p.avg_latency]);
}

function appendClusterCpu(cpuPct, ts) {
  if (cpuPct == null || isNaN(cpuPct)) return;
  const label = new Date(ts || Date.now()).toLocaleTimeString();
  pushPoint(state.charts['cluster-cpu-chart'], label, [Number(cpuPct)]);
}

function updateNodeCharts(nodes) {
  if (!nodes?.length) return;
  const iopsKey = 'node-iops-chart';
  if (!state.charts[iopsKey]) {
    state.charts[iopsKey] = new Chart($('#node-iops-chart'), {
      type: 'bar',
      data: { labels: [], datasets: [{ label: 'Total IOPS (5m avg)', backgroundColor: '#7c5cff', data: [] }] },
      options: {
        ...chartDefaults,
        animation: { duration: 200 },
        scales: { ...chartDefaults.scales, y: { ...chartDefaults.scales.y, beginAtZero: true } },
      },
    });
  }
  const iopsValues = nodes.map(n =>
    n.recent_avg?.total_iops ?? normalizePerf(n.payload)?.total_iops ?? 0
  );
  const iopsChart = state.charts[iopsKey];
  iopsChart.data.labels = nodes.map((n, i) => n.entity_id || `Node ${i + 1}`);
  iopsChart.data.datasets[0].data = iopsValues;
  iopsChart.options.scales.y.suggestedMax = chartScaleMax(iopsValues, 0.5);
  iopsChart.update('none');

  const cpuKey = 'node-cpu-chart';
  if (!state.charts[cpuKey]) {
    state.charts[cpuKey] = new Chart($('#node-cpu-chart'), {
      type: 'bar',
      data: { labels: [], datasets: [{ label: 'I/O CPU % (5m avg)', backgroundColor: '#ff6b81', data: [] }] },
      options: {
        ...chartDefaults,
        animation: { duration: 200 },
        scales: { ...chartDefaults.scales, y: { ...chartDefaults.scales.y, beginAtZero: true } },
      },
    });
  }
  const cpuValues = nodes.map(n =>
    n.recent_avg?.io_workload_cpu_utilization ?? cpuUtil(n.payload) ?? 0
  );
  const cpuChart = state.charts[cpuKey];
  cpuChart.data.labels = nodes.map((n, i) => n.entity_id || `Node ${i + 1}`);
  cpuChart.data.datasets[0].data = cpuValues;
  cpuChart.options.scales.y.suggestedMax = chartScaleMax(cpuValues, 0.01);
  cpuChart.update('none');
}

function updateNodeChart(nodes) {
  updateNodeCharts(nodes);
}

function renderClusterInfo() {
  const cluster = state.cluster?.cluster || state.overview?.cluster;
  const appliances = state.cluster?.appliances || state.overview?.appliances || [];
  if (!cluster && !appliances.length) {
    $('#cluster-info').innerHTML = '<div class="empty">Waiting for cluster metadata…</div>';
    return;
  }
  const enc = cluster?.is_encryption_enabled ? 'Enabled' : 'Disabled';
  $('#cluster-info').innerHTML = tableHtml([
    { key: 'k', label: 'Property', render: r => r.k },
    { key: 'v', label: 'Value', render: r => r.v },
  ], [
    { k: 'Cluster Name', v: cluster?.name || '—' },
    { k: 'Global ID', v: cluster?.global_id || '—' },
    { k: 'Management IP', v: cluster?.management_address || '—' },
    { k: 'State', v: cluster?.state || '—' },
    { k: 'Appliances', v: cluster?.appliance_count ?? appliances.length },
    { k: 'Encryption', v: enc },
    { k: 'System Time', v: fmtTime(cluster?.system_time) },
  ].filter(r => r.v !== '—' || r.k === 'Cluster Name'));
  if (appliances.length) {
    $('#cluster-info').innerHTML += tableHtml([
      { key: 'name', label: 'Appliance' },
      { key: 'model', label: 'Model' },
      { key: 'service_tag', label: 'Service Tag' },
      { key: 'release_version', label: 'Firmware' },
      { key: 'node_count', label: 'Nodes' },
    ], appliances);
  }
}

function renderOverview() {
  renderClusterInfo();
  const stats = state.overview?.stats || {};
  const counts = stats.severity_counts || {};
  $('#overview-cards').innerHTML = `
    <div class="card"><div class="card-label">Critical</div><div class="card-value critical">${counts.Critical || 0}</div></div>
    <div class="card"><div class="card-label">Major</div><div class="card-value major">${counts.Major || 0}</div></div>
    <div class="card"><div class="card-label">Minor</div><div class="card-value minor">${counts.Minor || 0}</div></div>
    <div class="card"><div class="card-label">Info</div><div class="card-value info">${counts.Info || 0}</div></div>
    <div class="card"><div class="card-label">Unhealthy HW</div><div class="card-value">${stats.unhealthy_hardware || 0}</div></div>
    <div class="card"><div class="card-label">Ports Down</div><div class="card-value">${stats.down_ports || 0}</div></div>
  `;

  const perf = normalizePerf(stats.cluster_perf?.payload || state.live?.cluster?.payload);
  const space = stats.cluster_space?.payload || state.capacity?.cluster?.payload;
  const cpuPct = state.live?.cluster_cpu_utilization ?? perf?.io_workload_cpu_utilization;
  $('#overview-live').innerHTML = `
    <div class="card"><div class="card-label">Cluster IOPS</div><div class="card-value live">${perf?.total_iops?.toFixed?.(0) ?? '—'}</div><div class="sub-label">R: ${perf?.read_iops?.toFixed?.(0) ?? '—'} / W: ${perf?.write_iops?.toFixed?.(0) ?? '—'}</div></div>
    <div class="card"><div class="card-label">I/O CPU</div><div class="card-value live">${cpuPct != null ? cpuPct.toFixed(1) + '%' : '—'}</div><div class="sub-label">I/O workload cores</div></div>
    <div class="card"><div class="card-label">Avg Latency</div><div class="card-value live">${perf?.avg_latency?.toFixed?.(0) ?? '—'} µs</div></div>
    <div class="card"><div class="card-label">Physical Used</div><div class="card-value live">${space ? fmtBytes(space.physical_used) : '—'}</div><div class="sub-label">${space ? fmtBytes(space.physical_total) + ' total' : ''}</div></div>
    <div class="card"><div class="card-label">Efficiency</div><div class="card-value live">${space?.efficiency_ratio ? space.efficiency_ratio.toFixed(1) + ':1' : '—'}</div></div>
  `;

  const rawPerf = stats.cluster_perf?.payload || state.live?.cluster?.payload;
  if (rawPerf) appendClusterPerf(rawPerf, stats.cluster_perf?.collected_at);

  const recent = stats.recent_alerts || [];
  $('#overview-recent').innerHTML = tableHtml([
    { key: 'severity', label: 'Severity', render: r => sevBadge(r.severity) },
    { key: 'state_l10n', label: 'State' },
    { key: 'description', label: 'Description', render: r => `<span class="desc-cell" title="${r.description || ''}">${r.description || '—'}</span>` },
    { key: 'resource_name', label: 'Resource' },
    { key: 'generated_timestamp', label: 'Time', render: r => fmtTime(r.generated_timestamp) },
  ], recent);
}

function renderTopIo() {
  const vols = state.live?.top_volumes || [];
  const hosts = state.live?.top_hosts || [];
  $('#top-volumes-table').innerHTML = tableHtml([
    { key: 'entity_id', label: 'Volume', render: r => state.volumeNames[r.entity_id] || r.entity_id.slice(0, 8) + '…' },
    { key: 'sort_value', label: 'IOPS', render: r => r.sort_value.toFixed(0) },
    { key: 'payload', label: 'Latency', render: r => `${(r.payload?.avg_latency ?? 0).toFixed(0)} µs` },
    { key: 'payload', label: 'BW', render: r => {
      const p = normalizePerf(r.payload);
      return fmtMbps((p?.read_bandwidth || 0) + (p?.write_bandwidth || 0)) + ' MB/s';
    }},
    { key: 'entity_id', label: '', render: r => `<button class="small secondary" onclick="pinVolume('${r.entity_id}')">Pin</button>` },
  ], vols);
  $('#top-hosts-table').innerHTML = tableHtml([
    { key: 'entity_id', label: 'Host', render: r => state.hostNames[r.entity_id] || r.entity_id.slice(0, 8) + '…' },
    { key: 'sort_value', label: 'IOPS', render: r => r.sort_value.toFixed(0) },
    { key: 'payload', label: 'Latency', render: r => `${(r.payload?.avg_latency ?? 0).toFixed(0)} µs` },
    { key: 'payload', label: 'BW', render: r => {
      const p = normalizePerf(r.payload);
      return fmtMbps((p?.read_bandwidth || 0) + (p?.write_bandwidth || 0)) + ' MB/s';
    }},
  ], hosts);
}

async function pinVolume(id) {
  try {
    await api(`/api/metrics/pin/${id}`, { method: 'POST' });
    state.pinnedVolumeId = id;
    toast('Volume pinned for fast metrics');
    $('#pinned-volume-panel').style.display = 'block';
    $('#pinned-volume-title').textContent = `Pinned: ${state.volumeNames[id] || id.slice(0, 8)}`;
  } catch (e) { toast('Pin failed: ' + e.message); }
}
window.pinVolume = pinVolume;

function renderWearChart() {
  const wear = state.metrics.filter(m => m.metric_type === 'wear');
  const wearByDrive = {};
  wear.forEach(m => {
    if (!wearByDrive[m.entity_id] || m.collected_at > wearByDrive[m.entity_id].collected_at)
      wearByDrive[m.entity_id] = m;
  });
  const items = Object.values(wearByDrive);
  const ctx = $('#wear-chart');
  if (!ctx) return;
  if (state.charts.wear) state.charts.wear.destroy();
  state.charts.wear = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: items.map(w => w.entity_id.slice(0, 8) + '…'),
      datasets: [{ label: '% Endurance Remaining', data: items.map(w => w.payload?.percent_endurance_remaining ?? 0),
        backgroundColor: items.map(w => (w.payload?.percent_endurance_remaining ?? 100) < 20 ? '#ff4757' : '#2ed573') }],
    },
    options: { ...chartDefaults, scales: { ...chartDefaults.scales, y: { ...chartDefaults.scales.y, min: 0, max: 100 } } },
  });
}

function renderCapacity() {
  if (state.capacity?.error) {
    $('#capacity-cards').innerHTML = `<div class="empty">Capacity unavailable: ${escAttr(state.capacity.error)}</div>`;
    return;
  }
  const cluster = state.capacity?.cluster?.payload;
  if (!cluster) {
    $('#capacity-cards').innerHTML = '<div class="empty">Waiting for space metrics…</div>';
    return;
  }
  const physPct = cluster.physical_total ? (cluster.physical_used / cluster.physical_total * 100) : 0;
  $('#capacity-cards').innerHTML = `
    <div class="card"><div class="card-label">Physical Used</div><div class="card-value live">${fmtBytes(cluster.physical_used)}</div><div class="sub-label">${physPct.toFixed(1)}% of ${fmtBytes(cluster.physical_total)}</div></div>
    <div class="card"><div class="card-label">Logical Provisioned</div><div class="card-value live">${fmtBytes(cluster.logical_provisioned)}</div></div>
    <div class="card"><div class="card-label">Logical Used</div><div class="card-value live">${fmtBytes(cluster.logical_used)}</div></div>
    <div class="card"><div class="card-label">Efficiency</div><div class="card-value live">${cluster.efficiency_ratio?.toFixed(1) ?? '—'}:1</div></div>
    <div class="card"><div class="card-label">Data Reduction</div><div class="card-value live">${cluster.data_reduction?.toFixed(1) ?? '—'}:1</div></div>
  `;

  const gauge = getOrCreateChart('#physical-gauge', {
    type: 'doughnut',
    data: { labels: ['Used', 'Free'], datasets: [{ data: [cluster.physical_used, Math.max(0, cluster.physical_total - cluster.physical_used)], backgroundColor: ['#7c5cff', '#30363d'] }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { labels: { color: '#8b949e' } } } },
  });
  if (gauge) { gauge.data.datasets[0].data = [cluster.physical_used, Math.max(0, cluster.physical_total - cluster.physical_used)]; gauge.update('none'); }

  const logical = getOrCreateChart('#logical-chart', {
    type: 'bar',
    data: { labels: ['Provisioned', 'Used'], datasets: [{ label: 'Bytes', backgroundColor: ['#54a0ff', '#ff9f43'], data: [cluster.logical_provisioned, cluster.logical_used] }] },
    options: { ...chartDefaults, indexAxis: 'y' },
  });
  if (logical) { logical.data.datasets[0].data = [cluster.logical_provisioned, cluster.logical_used]; logical.update('none'); }

  $('#capacity-breakdown').innerHTML = tableHtml([
    { key: 'k', label: 'Category', render: r => r.k },
    { key: 'v', label: 'Logical Used', render: r => fmtBytes(r.v) },
  ], [
    { k: 'Block Volumes', v: cluster.logical_used_volume },
    { k: 'File Systems', v: cluster.logical_used_file_system },
    { k: 'Virtual Volumes', v: cluster.logical_used_vvol },
  ].filter(r => r.v != null));
}

function renderStorage() {
  const s = state.storage;
  if (!s) return;
  const ov = s.overview || {};
  $('#storage-cards').innerHTML = `
    <div class="card"><div class="card-label">Primary Volumes</div><div class="card-value">${ov.volume_count ?? s.volumes?.length ?? 0}</div></div>
    <div class="card"><div class="card-label">Hosts</div><div class="card-value">${ov.host_count ?? s.hosts?.length ?? 0}</div></div>
    <div class="card"><div class="card-label">Mappings</div><div class="card-value">${ov.mapping_count ?? s.mappings?.length ?? 0}</div></div>
    <div class="card"><div class="card-label">Nodes</div><div class="card-value">${s.nodes?.length ?? 0}</div></div>
  `;
  (s.volumes || []).forEach(v => { state.volumeNames[v.id] = v.name; });
  (s.hosts || []).forEach(h => { state.hostNames[h.id] = h.name; });

  $('#volumes-table').innerHTML = tableHtml([
    { key: 'name', label: 'Name' },
    { key: 'size', label: 'Size', render: r => fmtBytes(r.size) },
    { key: 'space', label: 'Used', render: r => r.space ? fmtBytes(r.space.logical_used ?? r.space.physical_used) : '—' },
    { key: 'state', label: 'State' },
    { key: 'mapped_hosts', label: 'Mapped Hosts' },
    { key: 'id', label: '', render: r => `<button class="small secondary" onclick="pinVolume('${r.id}')">Pin I/O</button>` },
  ], s.volumes || []);

  $('#hosts-table').innerHTML = tableHtml([
    { key: 'name', label: 'Name' },
    { key: 'os_type', label: 'OS' },
    { key: 'host_connectivity', label: 'Connectivity' },
    { key: 'mapped_volumes', label: 'Volumes' },
  ], s.hosts || []);
}

function renderNas() {
  const n = state.nas;
  if (!n) return;
  $('#nas-servers-table').innerHTML = tableHtml([
    { key: 'name', label: 'Name' },
    { key: 'operational_status', label: 'Status' },
    { key: 'current_node_id', label: 'Node', render: r => r.current_node_id?.slice(0, 8) + '…' || '—' },
  ], n.nas_servers || []);
  $('#file-systems-table').innerHTML = tableHtml([
    { key: 'name', label: 'Name' },
    { key: 'filesystem_type', label: 'Type' },
    { key: 'size_used', label: 'Used', render: r => fmtBytes(r.size_used) },
    { key: 'size_total', label: 'Total', render: r => fmtBytes(r.size_total) },
    { key: 'size_used', label: 'Usage', render: r => r.size_total ? `${(r.size_used / r.size_total * 100).toFixed(1)}%` : '—' },
  ], n.file_systems || []);
}

function filterAlerts() {
  const sev = $('#alert-severity-filter').value;
  const st = $('#alert-state-filter').value;
  return state.alerts.filter(a => (!sev || a.severity === sev) && (!st || a.state === st));
}

function renderAlerts() {
  $('#alerts-table').innerHTML = tableHtml([
    { key: 'severity', label: 'Severity', render: r => sevBadge(r.severity) },
    { key: 'state_l10n', label: 'State' },
    { key: 'description', label: 'Description', render: r => `<span class="desc-cell" title="${r.description || ''}">${r.description || '—'}</span>` },
    { key: 'resource_name', label: 'Resource' },
    { key: 'generated_timestamp', label: 'Updated', render: r => fmtTime(r.generated_timestamp) },
    { key: 'id', label: '', render: r => r.is_acknowledged ? '<span class="badge">Acked</span>' : `<button class="small secondary" onclick="ackAlert('${r.id}')">Ack</button>` },
  ], filterAlerts());
}

async function ackAlert(id) {
  try { await api(`/api/alerts/${id}/acknowledge`, { method: 'POST' }); toast('Alert acknowledged'); await loadAlerts(); }
  catch (e) { toast('Ack failed: ' + e.message); }
}
window.ackAlert = ackAlert;

function renderEvents() {
  const sev = $('#event-severity-filter').value;
  $('#events-table').innerHTML = tableHtml([
    { key: 'severity', label: 'Severity', render: r => sevBadge(r.severity) },
    { key: 'description', label: 'Description', render: r => {
      const text = r.description || '—';
      return `<span class="desc-cell desc-cell-wrap" title="${escAttr(r.description)}">${text}</span>`;
    }},
    { key: 'resource_name', label: 'Resource', render: r => {
      const text = r.resource_name || '—';
      return `<span class="resource-cell" title="${escAttr(r.resource_name)}">${text}</span>`;
    }},
    { key: 'generated_timestamp', label: 'Time', render: r => fmtTime(r.generated_timestamp) },
  ], state.events.filter(e => !sev || e.severity === sev), {
    tableClass: 'table-events',
    colClasses: ['col-sev', 'col-desc', 'col-resource', 'col-time'],
  });
}

function hardwareDetails(row) {
  const extra = row.extra || {};
  if (row.hw_type === 'Node') {
    const parts = [];
    if (extra.cpu_model) parts.push(extra.cpu_model);
    if (extra.cpu_cores) parts.push(`${extra.cpu_cores} cores`);
    if (extra.cpu_sockets) parts.push(`${extra.cpu_sockets} sockets`);
    if (extra.physical_memory_size_gb) parts.push(`${extra.physical_memory_size_gb} GB RAM`);
    return parts.join(' · ') || '—';
  }
  if (row.hw_type === 'Drive') {
    const parts = [];
    if (extra.size) parts.push(fmtBytes(extra.size));
    if (extra.firmware_version) parts.push(`FW ${extra.firmware_version}`);
    if (extra.drive_type) parts.push(extra.drive_type);
    if (extra.encryption_status) parts.push(extra.encryption_status);
    if (extra.fips_status) parts.push(extra.fips_status);
    return parts.join(' · ') || '—';
  }
  if (extra.firmware_version) return `FW ${extra.firmware_version}`;
  if (extra.model_name) return extra.model_name;
  return '—';
}

function renderNodeSpecs() {
  const nodes = state.hardware.filter(h => h.hw_type === 'Node');
  const panel = $('#node-specs-panel');
  if (!nodes.length) { panel.style.display = 'none'; return; }
  panel.style.display = 'block';
  $('#node-specs').innerHTML = tableHtml([
    { key: 'name', label: 'Node' },
    { key: 'lifecycle_state', label: 'State', render: r => r.lifecycle_state === 'Healthy' ? `<span class="badge up">${r.lifecycle_state}</span>` : `<span class="badge unhealthy">${r.lifecycle_state}</span>` },
    { key: 'extra', label: 'CPU', render: r => r.extra?.cpu_model || '—' },
    { key: 'extra', label: 'Cores', render: r => r.extra?.cpu_cores ?? '—' },
    { key: 'extra', label: 'Sockets', render: r => r.extra?.cpu_sockets ?? '—' },
    { key: 'extra', label: 'RAM (GB)', render: r => r.extra?.physical_memory_size_gb ?? '—' },
    { key: 'serial_number', label: 'Serial' },
  ], nodes);
}

function populateHardwareTypeFilter() {
  const sel = $('#hardware-type-filter');
  if (!sel) return;
  const types = [...new Set(state.hardware.map(h => h.hw_type).filter(Boolean))].sort();
  const current = sel.value;
  sel.innerHTML = '<option value="">All types</option>' +
    types.map(t => `<option value="${t}">${t.replace(/_/g, ' ')}</option>`).join('');
  if (types.includes(current)) sel.value = current;
}

function renderHardware() {
  populateHardwareTypeFilter();
  let rows = state.hardware;
  const typeFilter = $('#hardware-type-filter')?.value;
  if (typeFilter) rows = rows.filter(h => h.hw_type === typeFilter);
  if ($('#hardware-unhealthy-only')?.checked) rows = rows.filter(h => h.lifecycle_state && h.lifecycle_state !== 'Healthy');
  renderNodeSpecs();
  $('#hardware-table').innerHTML = tableHtml([
    { key: 'hw_type', label: 'Type', render: r => (r.hw_type || '').replace(/_/g, ' ') },
    { key: 'name', label: 'Name' },
    { key: 'lifecycle_state', label: 'State', render: r => r.lifecycle_state === 'Healthy' ? `<span class="badge up">${r.lifecycle_state}</span>` : `<span class="badge unhealthy">${r.lifecycle_state}</span>` },
    { key: 'details', label: 'Details', render: r => `<span class="desc-cell">${hardwareDetails(r)}</span>` },
    { key: 'serial_number', label: 'Serial' },
    { key: 'slot', label: 'Slot' },
  ], rows);
}

function renderPortPerf() {
  const pm = state.portMetrics;
  if (!pm) {
    $('#port-perf-table').innerHTML = '<div class="empty">Waiting for port performance metrics…</div>';
    return;
  }
  const rows = [];
  (pm.fc_ports || []).forEach(p => {
    rows.push({ port_type: 'FC', entity_id: p.entity_id, payload: p.payload, sort: normalizePerf(p.payload)?.total_iops ?? 0 });
  });
  (pm.eth_ports || []).forEach(p => {
    rows.push({ port_type: 'Eth', entity_id: p.entity_id, payload: p.payload, sort: normalizePerf(p.payload)?.total_iops ?? 0 });
  });
  rows.sort((a, b) => b.sort - a.sort);
  const top = rows.slice(0, 20);
  $('#port-perf-table').innerHTML = tableHtml([
    { key: 'port_type', label: 'Type' },
    { key: 'entity_id', label: 'Port', render: r => state.portNames[r.entity_id] || r.entity_id.slice(0, 12) + '…' },
    { key: 'sort', label: 'IOPS', render: r => r.sort.toFixed(0) },
    { key: 'payload', label: 'Latency', render: r => `${(r.payload?.avg_latency ?? 0).toFixed(0)} µs` },
    { key: 'payload', label: 'BW', render: r => {
      const perf = normalizePerf(r.payload);
      return fmtMbps((perf?.read_bandwidth || 0) + (perf?.write_bandwidth || 0)) + ' MB/s';
    }},
  ], top);
}

function renderPorts() {
  renderPortPerf();
  const type = $('#port-type-filter').value;
  $('#ports-table').innerHTML = tableHtml([
    { key: 'port_type', label: 'Type', render: r => r.port_type.toUpperCase() },
    { key: 'name', label: 'Name' },
    { key: 'is_link_up', label: 'Link', render: r => r.is_link_up ? '<span class="badge up">Up</span>' : '<span class="badge down">Down</span>' },
    { key: 'is_in_use', label: 'In Use', render: r => r.is_in_use ? 'Yes' : 'No' },
  ], state.ports.filter(p => !type || p.port_type === type));
}

function renderProtection() {
  const p = state.protection;
  if (!p) {
    $('#protection-cards').innerHTML = '<div class="empty">Loading protection data…</div>';
    return;
  }
  if (p.error) {
    $('#protection-cards').innerHTML = `<div class="empty">Protection unavailable: ${escAttr(p.error)}</div>`;
    return;
  }
  const sessions = p.replication_sessions || [];
  const remotes = p.remote_systems || [];
  const policies = p.policies || [];
  const rules = p.snapshot_rules || [];
  $('#protection-cards').innerHTML = `
    <div class="card"><div class="card-label">Replication Sessions</div><div class="card-value">${sessions.length}</div></div>
    <div class="card"><div class="card-label">Remote Systems</div><div class="card-value">${remotes.length}</div></div>
    <div class="card"><div class="card-label">Protection Policies</div><div class="card-value">${policies.length}</div></div>
    <div class="card"><div class="card-label">Snapshot Rules</div><div class="card-value">${rules.length}</div></div>
  `;
  $('#replication-table').innerHTML = tableHtml([
    { key: 'state', label: 'State' },
    { key: 'role', label: 'Role' },
    { key: 'resource_type', label: 'Resource' },
    { key: 'session_type', label: 'Type' },
    { key: 'progress_percentage', label: 'Progress', render: r => r.progress_percentage != null ? r.progress_percentage + '%' : '—' },
    { key: 'last_sync_timestamp', label: 'Last Sync', render: r => fmtTime(r.last_sync_timestamp) },
  ], sessions);
  $('#remote-systems-table').innerHTML = tableHtml([
    { key: 'name', label: 'Name' },
    { key: 'system_type', label: 'Type' },
    { key: 'management_address', label: 'Address' },
    { key: 'state', label: 'State' },
    { key: 'data_connection_state', label: 'Data Link' },
    { key: 'version', label: 'Version' },
  ], remotes);
  $('#policies-table').innerHTML = tableHtml([
    { key: 'name', label: 'Name' },
    { key: 'policy_type', label: 'Type' },
    { key: 'description', label: 'Description', render: r => `<span class="desc-cell">${r.description || '—'}</span>` },
    { key: 'is_replica', label: 'Replica', render: r => r.is_replica ? 'Yes' : 'No' },
  ], policies);
  $('#snapshot-rules-table').innerHTML = tableHtml([
    { key: 'name', label: 'Name' },
    { key: 'interval', label: 'Interval' },
    { key: 'time_of_day', label: 'Time' },
    { key: 'days_of_week', label: 'Days', render: r => {
      try { const d = JSON.parse(r.days_of_week || '[]'); return d.length ? d.join(', ') : '—'; }
      catch { return r.days_of_week || '—'; }
    }},
  ], rules);
  const copy = p.copy_metrics || {};
  const copyRows = [];
  if (copy.cluster?.payload) copyRows.push({ scope: 'Cluster', payload: copy.cluster.payload });
  (copy.appliances || []).forEach(a => copyRows.push({ scope: 'Appliance ' + a.entity_id.slice(0, 6), payload: a.payload }));
  $('#copy-metrics').innerHTML = copyRows.length ? tableHtml([
    { key: 'scope', label: 'Scope' },
    { key: 'payload', label: 'Read BW', render: r => fmtMbps(r.payload?.read_bandwidth ?? r.payload?.avg_read_bandwidth) + ' MB/s' },
    { key: 'payload', label: 'Write BW', render: r => fmtMbps(r.payload?.write_bandwidth ?? r.payload?.avg_write_bandwidth) + ' MB/s' },
    { key: 'payload', label: 'Total IOPS', render: r => (r.payload?.total_iops ?? r.payload?.avg_total_iops ?? 0).toFixed(0) },
  ], copyRows) : '<div class="empty">No copy metrics collected yet</div>';
}

function renderAudit() {
  const access = state.auditAccess || 'unknown';
  $('#audit-access-banner').innerHTML = access === 'denied'
    ? '<div class="panel" style="padding:1rem;margin-bottom:1rem;color:var(--major);">Audit access denied — Administrator role required.</div>' : '';
  $('#audit-table').innerHTML = tableHtml([
    { key: 'timestamp', label: 'Time', render: r => fmtTime(r.timestamp) },
    { key: 'username', label: 'User' }, { key: 'event_type', label: 'Type' },
    { key: 'message', label: 'Message', render: r => `<span class="desc-cell">${r.message || '—'}</span>` },
  ], state.audit);
}

function renderCollectionBanner() {
  const el = $('#collection-status-banner');
  if (!el) return;
  const running = state.collections.find(c => c.status === 'RUNNING');
  if (!running) {
    el.innerHTML = '';
    el.className = 'collection-banner';
    return;
  }
  const elapsed = fmtElapsed(running.start_timestamp);
  const size = fmtBytes(running.compressed_size);
  const apps = applianceSummary(running);
  const mins = (Date.now() - new Date(running.start_timestamp).getTime()) / 60000;
  const stuck = mins > 30;
  el.className = 'collection-banner active';
  el.innerHTML = [
    `<strong>Collection in progress</strong> — elapsed ${elapsed}, bundle size ${size}.`,
    `Appliance: ${apps}. Auto-refreshing every 10s.`,
    stuck
      ? '<span class="warn">Running over 30 minutes with little progress — may be stuck on the array. Check PowerStore Manager or contact Dell support.</span>'
      : 'Typical duration is about 10 minutes.',
  ].join(' ');
}

function renderCollections() {
  const hasRunning = state.collections.some(c => c.status === 'RUNNING');
  if (hasRunning && !state.collectionPollTimer) {
    state.collectionPollTimer = setInterval(() => {
      if ($('#page-support')?.classList.contains('active')) loadCollections();
    }, 10000);
  } else if (!hasRunning && state.collectionPollTimer) {
    clearInterval(state.collectionPollTimer);
    state.collectionPollTimer = null;
  }

  renderCollectionBanner();

  $('#collections-table').innerHTML = tableHtml([
    { key: 'id', label: 'ID', render: r => r.id?.slice(0, 8) + '…' },
    { key: 'status', label: 'Status', render: r => {
      const s = r.status || '—';
      const cls = s === 'SUCCESS' ? 'up' : s === 'RUNNING' ? 'unhealthy' : s === 'FAILED' ? 'down' : '';
      const extra = s === 'RUNNING' ? `<div class="sub-label">${fmtElapsed(r.start_timestamp)} elapsed</div>` : '';
      return (cls ? `<span class="badge ${cls}">${s}</span>` : s) + extra;
    }},
    { key: 'description', label: 'Description', render: r => r.description || '—' },
    { key: 'compressed_size', label: 'Size', render: r => fmtBytes(r.compressed_size) },
    { key: 'start_timestamp', label: 'Started', render: r => fmtTime(r.start_timestamp) },
    { key: 'end_timestamp', label: 'Finished', render: r => fmtTime(r.end_timestamp) },
    { key: 'appliances', label: 'Appliance', render: r => `<span class="desc-cell" title="${applianceSummary(r)}">${applianceSummary(r)}</span>` },
    { key: 'id', label: '', render: r => {
      if (r.status === 'SUCCESS' || r.status === 'PARTIAL') {
        const label = r.compressed_size ? `Download (${fmtBytes(r.compressed_size)})` : 'Download';
        return `<button class="small" onclick="downloadCollection('${r.id}', this)">${label}</button>`;
      }
      if (r.status === 'RUNNING') {
        return `<button class="small secondary" onclick="refreshCollection('${r.id}')">Refresh</button>`;
      }
      return `<button class="small secondary" onclick="refreshCollection('${r.id}')">Details</button>`;
    }},
  ], state.collections);
}

async function downloadCollection(id, btn) {
  if (btn) { btn.disabled = true; btn.textContent = 'Downloading…'; }
  toast('Downloading bundle — large files can take several minutes');
  try {
    const res = await fetch(`/api/datacollection/${id}/download`, { method: 'POST' });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `pstore_${id.slice(0, 8)}.zip`;
    a.click();
    URL.revokeObjectURL(a.href);
    toast(`Download complete (${fmtBytes(blob.size)})`);
  } catch (e) {
    toast('Download failed: ' + e.message);
  } finally {
    if (btn) {
      btn.disabled = false;
      const row = state.collections.find(c => c.id === id);
      btn.textContent = row?.compressed_size ? `Download (${fmtBytes(row.compressed_size)})` : 'Download';
    }
  }
}
window.downloadCollection = downloadCollection;

async function refreshCollection(id) {
  try {
    toast('Refreshing status…');
    const item = await api(`/api/datacollection/${id}`);
    const idx = state.collections.findIndex(c => c.id === id);
    if (idx >= 0) state.collections[idx] = item; else state.collections.unshift(item);
    renderCollections();
    const msg = item.status === 'RUNNING'
      ? `Still running — ${fmtElapsed(item.start_timestamp)}, size ${fmtBytes(item.compressed_size)}`
      : `Status: ${item.status}${item.end_timestamp ? ', finished ' + fmtTime(item.end_timestamp) : ''}`;
    toast(msg);
  } catch (e) { toast('Refresh failed: ' + e.message); }
}
window.refreshCollection = refreshCollection;

function progressBarHtml(pct, cls, label) {
  const v = Math.min(100, Math.max(0, pct ?? 0));
  const text = pct != null ? `${v.toFixed(1)}%` : '—';
  return `<div class="metric-row">
    <div class="metric-row-label"><span>${label}</span><span>${text}</span></div>
    <div class="progress-bar"><div class="progress-fill ${cls}" style="width:${pct != null ? v : 0}%"></div></div>
  </div>`;
}

function ratioFmt(v) {
  if (v == null || isNaN(v)) return '—';
  return `${Number(v).toFixed(1)}:1`;
}

function renderResources() {
  const r = state.resources;
  if (!r) {
    $('#resources-summary').innerHTML = '<div class="empty">Loading resources…</div>';
    return;
  }
  if (r.error) {
    $('#resources-summary').innerHTML = `<div class="empty">Resources unavailable: ${escAttr(r.error)}</div>`;
    return;
  }

  const c = r.cluster || {};
  const drivesLow = (r.drives || []).filter(d => d.endurance_remaining != null && d.endurance_remaining < 20).length;
  const hwFailed = (r.hardware_health || []).reduce((s, h) => s + (h.failed || 0), 0);

  $('#resources-summary').innerHTML = `
    <div class="card"><div class="card-label">I/O CPU (cluster)</div><div class="card-value live">${c.cpu_utilization != null ? c.cpu_utilization.toFixed(1) + '%' : '—'}</div><div class="sub-label">I/O workload cores</div></div>
    <div class="card"><div class="card-label">Physical Used</div><div class="card-value live">${c.physical_pct != null ? c.physical_pct.toFixed(1) + '%' : '—'}</div><div class="sub-label">${fmtBytes(c.physical_used)} / ${fmtBytes(c.physical_total)}</div></div>
    <div class="card"><div class="card-label">Cluster IOPS</div><div class="card-value live">${fmtIops(c.total_iops)}</div><div class="sub-label">${c.avg_latency != null ? c.avg_latency.toFixed(0) + ' µs avg · 5m' : '5m avg · idle'}</div></div>
    <div class="card"><div class="card-label">HW Failed</div><div class="card-value ${hwFailed ? 'major' : ''}">${hwFailed}</div><div class="sub-label">Across fans, PSUs, drives, batteries, nodes</div></div>
    <div class="card"><div class="card-label">Low Endurance Drives</div><div class="card-value ${drivesLow ? 'major' : ''}">${drivesLow}</div><div class="sub-label">&lt; 20% remaining</div></div>
    <div class="card"><div class="card-label">Copy Backlog</div><div class="card-value live">${r.copy?.cluster ? fmtBytes(r.copy.cluster.data_remaining) : 'None'}</div><div class="sub-label">${r.copy?.cluster?.transfer_rate ? fmtMbps(r.copy.cluster.transfer_rate) + ' MB/s' : 'No active replication'}</div></div>
  `;

  $('#resources-efficiency').innerHTML = [
    { lbl: 'Efficiency', val: ratioFmt(c.efficiency_ratio) },
    { lbl: 'Data Reduction', val: ratioFmt(c.data_reduction) },
    { lbl: 'Snapshot Savings', val: ratioFmt(c.snapshot_savings) },
    { lbl: 'Thin Savings', val: ratioFmt(c.thin_savings) },
    { lbl: 'Logical Used', val: fmtBytes(c.logical_used) },
    { lbl: 'Provisioned', val: fmtBytes(c.logical_provisioned) },
  ].map(x => `<div class="efficiency-item"><div class="val">${x.val}</div><div class="lbl">${x.lbl}</div></div>`).join('');

  const apps = r.appliances || [];
  $('#resources-appliances').innerHTML = apps.length
    ? apps.map(a => `
      <div class="appliance-card">
        <h4>${a.name || a.id}</h4>
        <div class="meta">${a.model || ''}${a.service_tag ? ' · S/N ' + a.service_tag : ''}</div>
        ${progressBarHtml(a.cpu_utilization, 'cpu', 'I/O Workload CPU')}
        ${progressBarHtml(a.physical_pct, 'space', 'Physical Space')}
        <div class="metric-row-label" style="margin-top:0.5rem">
          <span>IOPS: ${fmtIops(a.total_iops)}</span>
          <span>Latency: ${a.avg_latency != null ? a.avg_latency.toFixed(0) + ' µs' : '—'}</span>
        </div>
      </div>`).join('')
    : '<div class="empty">No appliance data yet</div>';

  const healthRows = r.hardware_health || [];
  if (!healthRows.length) {
    $('#resources-health').innerHTML = '<div class="empty">No hardware inventory</div>';
  } else {
    $('#resources-health').innerHTML = `
      <div class="health-legend">
        <span class="leg-ok">OK</span><span class="leg-degraded">Degraded</span>
        <span class="leg-failed">Failed</span><span class="leg-unknown">Unknown</span>
      </div>
      <div class="health-matrix">${healthRows.map(h => {
        const total = h.total || 1;
        const segs = ['ok', 'degraded', 'failed', 'unknown'].map(k => {
          const n = h[k] || 0;
          if (!n) return '';
          const pct = (n / total * 100).toFixed(0);
          return `<div class="health-seg ${k}" style="width:${pct}%" title="${k}: ${n}">${n > 0 && pct >= 12 ? n : ''}</div>`;
        }).join('');
        return `<div class="health-row"><div class="health-row-label">${h.label} (${h.total})</div><div class="health-bars">${segs || '<div class="health-seg unknown" style="width:100%">0</div>'}</div></div>`;
      }).join('')}</div>`;
  }

  const copy = r.copy || {};
  const copyParts = [];
  if (copy.cluster) {
    copyParts.push(`<p><strong>Cluster</strong> — remaining: ${fmtBytes(copy.cluster.data_remaining)}, transferred: ${fmtBytes(copy.cluster.data_transferred)}, rate: ${copy.cluster.transfer_rate ? fmtMbps(copy.cluster.transfer_rate) + ' MB/s' : '—'}${copy.cluster.session_type ? ' (' + copy.cluster.session_type + ')' : ''}</p>`);
  }
  if (copy.appliances?.length) {
    copyParts.push(tableHtml([
      { key: 'name', label: 'Appliance', render: row => row.name || row.appliance_id?.slice(0, 8) },
      { key: 'data_remaining', label: 'Remaining', render: row => fmtBytes(row.data_remaining) },
      { key: 'data_transferred', label: 'Transferred', render: row => fmtBytes(row.data_transferred) },
      { key: 'transfer_rate', label: 'Rate', render: row => row.transfer_rate ? fmtMbps(row.transfer_rate) + ' MB/s' : '—' },
    ], copy.appliances));
  }
  $('#resources-copy').innerHTML = copyParts.length ? copyParts.join('') : '<div class="empty">No replication copy activity</div>';

  $('#resources-nodes').innerHTML = tableHtml([
    { key: 'appliance_name', label: 'Appliance' },
    { key: 'slot', label: 'Slot', render: row => row.slot ?? '—' },
    { key: 'cpu_utilization', label: 'I/O CPU', render: row => row.cpu_utilization != null ? row.cpu_utilization.toFixed(1) + '%' : '—' },
    { key: 'total_iops', label: 'IOPS', render: row => fmtIops(row.total_iops) },
    { key: 'current_logins', label: 'Logins', render: row => row.current_logins ?? '—' },
    { key: 'physical_memory_gb', label: 'RAM (GB)', render: row => row.physical_memory_gb ?? '—' },
    { key: 'cpu_model', label: 'CPU', render: row => `<span class="desc-cell" title="${escAttr(row.cpu_model)}">${row.cpu_model || '—'}</span>` },
    { key: 'lifecycle_state', label: 'State', render: row => row.lifecycle_state === 'Healthy' ? '<span class="badge up">Healthy</span>' : `<span class="badge unhealthy">${row.lifecycle_state || '—'}</span>` },
  ], r.nodes || []);

  const drives = r.drives || [];
  const wearCtx = $('#resources-wear-chart');
  if (wearCtx && drives.length) {
    const topDrives = drives.slice(0, 25);
    if (state.charts['resources-wear-chart']) state.charts['resources-wear-chart'].destroy();
    state.charts['resources-wear-chart'] = new Chart(wearCtx, {
      type: 'bar',
      data: {
        labels: topDrives.map(d => (d.name || d.id).slice(0, 12)),
        datasets: [{
          label: '% Endurance Left',
          data: topDrives.map(d => d.endurance_remaining ?? 0),
          backgroundColor: topDrives.map(d => (d.endurance_remaining ?? 100) < 20 ? '#ff4757' : '#2ed573'),
        }],
      },
      options: { ...chartDefaults, indexAxis: 'y', scales: { ...chartDefaults.scales, x: { ...chartDefaults.scales.x, min: 0, max: 100 } } },
    });
  }

  const lowDrives = drives.filter(d => d.endurance_remaining != null && d.endurance_remaining < 30);
  $('#resources-drives-table').innerHTML = lowDrives.length
    ? tableHtml([
        { key: 'name', label: 'Drive' },
        { key: 'endurance_remaining', label: 'Endurance %', render: row => `<span class="${row.endurance_remaining < 20 ? 'sev sev-Major' : ''}">${row.endurance_remaining?.toFixed?.(1) ?? '—'}%</span>` },
        { key: 'size', label: 'Size', render: row => fmtBytes(row.size) },
        { key: 'lifecycle_state', label: 'State' },
      ], lowDrives)
    : '<div class="empty" style="padding:1rem">All drives above 30% endurance</div>';

  $('#resources-eth-table').innerHTML = tableHtml([
    { key: 'name', label: 'Port' },
    { key: 'is_link_up', label: 'Link', render: row => row.is_link_up ? '<span class="badge up">Up</span>' : '<span class="badge down">Down</span>' },
    { key: 'bytes_rx', label: 'RX', render: row => fmtMbps(row.bytes_rx) + ' MB/s' },
    { key: 'bytes_tx', label: 'TX', render: row => fmtMbps(row.bytes_tx) + ' MB/s' },
    { key: 'pkt_rx', label: 'Pkts/s', render: row => row.pkt_rx?.toFixed?.(0) ?? '—' },
    { key: 'crc_errors', label: 'CRC err/s', render: row => {
      const v = row.crc_errors;
      return v != null && v > 0 ? `<span class="sev sev-Major">${v.toFixed(2)}</span>` : (v?.toFixed?.(2) ?? '0');
    }},
    { key: 'tx_errors', label: 'TX err/s', render: row => row.tx_errors?.toFixed?.(2) ?? '0' },
  ], r.eth_ports || []);
}

async function loadResources() {
  try {
    state.resources = await api('/api/resources');
  } catch (e) {
    state.resources = { error: e.message };
  }
  renderResources();
}

async function loadOverview() {
  state.overview = await api('/api/overview');
  state.status = state.overview.status || {};
  state.cluster = { cluster: state.overview.cluster, appliances: state.overview.appliances };
  updateConnection(state.status);
  renderOverview();
}
async function loadCluster() { state.cluster = await api('/api/cluster'); renderClusterInfo(); }
async function loadProtection() {
  try {
    state.protection = await api('/api/protection');
  } catch (e) {
    state.protection = { error: e.message, replication_sessions: [], remote_systems: [], policies: [], snapshot_rules: [], copy_metrics: {} };
  }
  renderProtection();
}
async function loadPortMetrics() {
  try {
    state.portMetrics = await api('/api/metrics/ports');
    (state.ports || []).forEach(p => { state.portNames[p.id] = p.name; });
    renderPortPerf();
  } catch { state.portMetrics = null; }
}
async function loadAlerts() { state.alerts = (await api('/api/alerts')).items; renderAlerts(); }
async function loadEvents() { state.events = (await api('/api/events')).items; renderEvents(); }
async function loadHardware() { state.hardware = (await api('/api/hardware')).items; renderHardware(); }
async function loadPorts() {
  state.ports = (await api('/api/ports')).items;
  state.ports.forEach(p => { state.portNames[p.id] = p.name; });
  renderPorts();
}
async function loadMetrics() { state.metrics = (await api('/api/metrics?limit=500')).items; renderWearChart(); }
async function loadLive() {
  state.live = await api('/api/metrics/live');
  if (state.live.cluster?.payload) appendClusterPerf(state.live.cluster.payload, state.live.cluster.collected_at);
  if (state.live.cluster_cpu_utilization != null) {
    appendClusterCpu(state.live.cluster_cpu_utilization, state.live.cluster?.collected_at);
  }
  updateNodeChart(state.live.nodes);
  renderTopIo();
  if ($('#page-overview').classList.contains('active')) renderOverview();
}
async function loadCapacity() {
  try {
    state.capacity = await api('/api/capacity');
  } catch (e) {
    state.capacity = { error: e.message };
  }
  renderCapacity();
}
async function loadStorage() { state.storage = await api('/api/storage'); renderStorage(); if (state.live) renderTopIo(); }
async function loadNas() { state.nas = await api('/api/nas'); renderNas(); }
async function loadAudit() { const data = await api('/api/audit'); state.audit = data.items; state.auditAccess = data.access; renderAudit(); }
async function loadCollections() { try { state.collections = (await api('/api/datacollection')).items; } catch { state.collections = []; } renderCollections(); }
function updateClusterLabel(name, clusterIp) {
  const label = $('#cluster-label');
  if (!label) return;
  if (name && clusterIp) {
    label.textContent = `${name} · ${clusterIp}`;
  } else if (clusterIp) {
    label.textContent = clusterIp;
  } else {
    label.textContent = 'Select location in Settings';
  }
}

async function loadSettings() {
  try {
    const data = await api('/api/settings');
    updateClusterLabel(data.monitor_location, data.cluster_ip);
    const ipEl = $('#settings-cluster-ip');
    if (ipEl) ipEl.textContent = data.cluster_ip ? `Cluster IP: ${data.cluster_ip}` : 'Cluster IP: —';

    const select = $('#settings-location');
    if (select) {
      const locations = data.locations || [];
      if (!locations.length) {
        select.innerHTML = '<option value="">No locations available — check Reports page IPs</option>';
      } else {
        select.innerHTML = locations.map(loc =>
          `<option value="${loc.name}">${loc.name} (${loc.cluster_ip})</option>`
        ).join('');
        if (data.monitor_location) select.value = data.monitor_location;
        else select.value = locations[0].name;
      }
    }

    if (data.username) $('#settings-username').value = data.username;
    state.status = { ...state.status, monitor_location: data.monitor_location, cluster_ip: data.cluster_ip };
    renderReportsCredsWarning(!data.has_credentials);
  } catch (err) {
    const select = $('#settings-location');
    if (select) select.innerHTML = `<option value="">Failed to load locations: ${err.message}</option>`;
    toast('Settings load failed: ' + err.message);
  }
}

async function saveMonitorLocation(name) {
  const result = await api('/api/settings/cluster-location', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  updateClusterLabel(result.name, result.cluster_ip);
  const ipEl = $('#settings-cluster-ip');
  if (ipEl) ipEl.textContent = `Cluster IP: ${result.cluster_ip}`;
  toast(`Monitoring ${result.name} (${result.cluster_ip})`);
  await loadOverview();
}

async function loadReportLocations() {
  const data = await api('/api/reports/locations');
  state.reportLocations = data.locations || [];
  renderReportLocations();
}

async function loadReportStatus() {
  state.reportStatus = await api('/api/reports/status');
  renderReportStatus();
}

function renderReportsCredsWarning(missing) {
  const el = $('#reports-creds-warning');
  if (el) el.style.display = missing ? 'block' : 'none';
}

function renderReportLocations() {
  const el = $('#report-locations-table');
  if (!el) return;
  if (!state.reportLocations.length) {
    el.innerHTML = '<div class="empty">No locations configured.</div>';
    return;
  }
  const rows = [];
  for (const loc of state.reportLocations) {
    const servers = loc.servers || [];
    const ips = loc.server_ips || {};
    servers.forEach((server, idx) => {
      rows.push({
        name: idx === 0 ? loc.name : '',
        server,
        mgmt_ip: ips[server] || '',
        last_status: idx === 0 ? (loc.last_status || '—') : '',
        _locName: loc.name,
      });
    });
  }
  el.innerHTML = tableHtml([
    { key: 'name', label: 'Location' },
    { key: 'server', label: 'Server' },
    { key: 'mgmt_ip', label: 'MGMT IP', render: row =>
      `<input type="text" class="report-server-ip" data-location="${row._locName}" data-server="${row.server}" value="${row.mgmt_ip || ''}" style="width:100%;">` },
    { key: 'last_status', label: 'Last Status' },
  ], rows);
}

function renderReportStatus() {
  const status = state.reportStatus;
  if (!status) return;
  const progress = $('#report-progress');
  const error = $('#report-error');
  const download = $('#report-download');
  if (progress) {
    let text = status.progress || '';
    if (status.servers_with_data) text += ` (${status.servers_with_data} servers with data)`;
    progress.textContent = text;
  }
  if (error) {
    const locErrors = status.location_status
      ? Object.entries(status.location_status)
          .filter(([, data]) => data?.phase === 'location_done' && data?.error)
          .map(([name, data]) => `${name}: ${data.error}`)
      : [];
    error.textContent = status.error || locErrors.join(' | ');
  }
  if (download) {
    if (status.filename && !status.running && !status.error) {
      download.innerHTML = `<a href="/api/reports/download/${status.filename}" download>Download ${status.filename}</a>`;
    } else {
      download.textContent = '';
    }
  }
  const btn = $('#generate-report');
  if (btn) btn.disabled = !!status.running;
  if (status.running && !state.reportPollTimer) {
    state.reportPollTimer = setInterval(async () => {
      try {
        state.reportStatus = await api('/api/reports/status');
        renderReportStatus();
        if (!state.reportStatus.running) {
          clearInterval(state.reportPollTimer);
          state.reportPollTimer = null;
          await loadReportLocations();
        }
      } catch { /* ignore */ }
    }, 2000);
  }
  if (!status.running && state.reportPollTimer) {
    clearInterval(state.reportPollTimer);
    state.reportPollTimer = null;
  }
}

async function saveReportLocations() {
  const byName = Object.fromEntries(
    state.reportLocations.map(loc => [loc.name, { ...loc, server_ips: { ...(loc.server_ips || {}) } }])
  );
  $$('.report-server-ip').forEach(input => {
    const location = input.dataset.location;
    const server = input.dataset.server;
    if (byName[location]) byName[location].server_ips[server] = input.value.trim();
  });
  await api('/api/reports/locations', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ locations: Object.values(byName) }),
  });
  toast('Locations saved');
  await loadReportLocations();
}

async function generateReport() {
  const btn = $('#generate-report');
  if (btn) btn.disabled = true;
  try {
    await saveReportLocations();
    await api('/api/reports/generate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    toast('Report generation started');
    await loadReportStatus();
  } catch (e) {
    toast('Failed: ' + e.message);
    if (btn) btn.disabled = false;
  }
}

function setupReports() {
  $('#save-locations')?.addEventListener('click', async () => {
    try { await saveReportLocations(); } catch (e) { toast('Save failed: ' + e.message); }
  });
  $('#generate-report')?.addEventListener('click', async () => {
    try { await generateReport(); } catch (e) { toast('Failed: ' + e.message); }
  });
}

async function loadClusterSeries() {
  resetClusterCharts();
  const data = await api('/api/metrics/series?entity=performance_metrics_by_cluster&metric_type=performance&limit=180');
  data.items.forEach(item => appendClusterPerf(item.payload, item.collected_at));

  const nodeData = await api('/api/metrics/series?entity=performance_metrics_by_node&metric_type=performance&limit=360');
  const cpuByTime = new Map();
  nodeData.items.forEach(item => {
    const cpu = cpuUtil(item.payload);
    if (cpu == null) return;
    const bucket = cpuByTime.get(item.collected_at) || [];
    bucket.push(cpu);
    cpuByTime.set(item.collected_at, bucket);
  });
  [...cpuByTime.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .forEach(([ts, cpus]) => {
      const avg = cpus.reduce((sum, v) => sum + v, 0) / cpus.length;
      appendClusterCpu(avg, ts);
    });

  const cpuChart = state.charts['cluster-cpu-chart'];
  if (cpuChart?.data.datasets[0]?.data.length) {
    cpuChart.options.scales.y.suggestedMax = chartScaleMax(cpuChart.data.datasets[0].data, 0.01);
    cpuChart.update('none');
  }
}

async function refreshAll() {
  ensureClusterCharts();
  await Promise.all([
    loadOverview(), loadResources(), loadAlerts(), loadEvents(), loadHardware(), loadPorts(), loadPortMetrics(),
    loadMetrics(), loadLive(), loadCapacity(), loadStorage(), loadNas(), loadAudit(), loadProtection(),
  ]);
  await loadClusterSeries();
}

function setupNav() {
  $$('.nav-link').forEach(link => {
    link.addEventListener('click', e => {
      e.preventDefault();
      const page = link.dataset.page;
      $$('.nav-link').forEach(l => l.classList.remove('active'));
      link.classList.add('active');
      $$('.page').forEach(p => p.classList.remove('active'));
      $(`#page-${page}`).classList.add('active');
      if (page === 'support') loadCollections();
      if (page === 'settings') loadSettings();
      if (page === 'reports') { loadReportLocations(); loadReportStatus(); loadSettings(); }
      if (page === 'protection') loadProtection();
      if (page === 'resources') { loadResources().then(() => renderResources()); }
    });
  });
}

function setupFilters() {
  $('#alert-severity-filter')?.addEventListener('change', renderAlerts);
  $('#alert-state-filter')?.addEventListener('change', renderAlerts);
  $('#event-severity-filter')?.addEventListener('change', renderEvents);
  $('#hardware-unhealthy-only')?.addEventListener('change', renderHardware);
  $('#hardware-type-filter')?.addEventListener('change', renderHardware);
  $('#port-type-filter')?.addEventListener('change', renderPorts);
}

function setupSettings() {
  $('#settings-location')?.addEventListener('change', async e => {
    const name = e.target.value;
    if (!name) return;
    try {
      await saveMonitorLocation(name);
    } catch (err) {
      toast('Location change failed: ' + err.message);
      await loadSettings();
    }
  });

  $('#settings-form')?.addEventListener('submit', async e => {
    e.preventDefault();
    try {
      const result = await api('/api/settings/credentials', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: $('#settings-username').value, password: $('#settings-password').value }) });
      if (result.validated === false) {
        toast(result.warning || 'Credentials saved (login not verified — cluster unreachable)');
      } else {
        toast(result.tested_ip ? `Credentials saved (verified at ${result.tested_ip})` : 'Credentials saved');
      }
      $('#settings-password').value = '';
      await loadSettings();
      await loadOverview();
    } catch (err) { toast('Save failed: ' + err.message); }
  });
  $('#clear-credentials')?.addEventListener('click', async () => { await api('/api/settings/credentials', { method: 'DELETE' }); toast('Credentials cleared'); await loadOverview(); });
}

function setupSupport() {
  $('#trigger-collection')?.addEventListener('click', async () => {
    const btn = $('#trigger-collection');
    btn.disabled = true;
    try {
      const result = await api('/api/datacollection', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: 'PowerStore Monitor log bundle' }),
      });
      toast('Log collection started');
      await loadCollections();
      if (result.result?.id) setTimeout(() => refreshCollection(result.result.id), 3000);
    } catch (e) {
      if (e.message.includes('409') || e.message.toLowerCase().includes('running') || e.message.toLowerCase().includes('conflict')) {
        toast('A log collection is already running — see list below');
        await loadCollections();
      } else {
        toast('Failed: ' + e.message);
      }
    } finally {
      btn.disabled = false;
    }
  });
}

function setupSSE() {
  const es = new EventSource('/api/stream');
  es.onmessage = ev => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'status') {
        state.status = { ...state.status, ...msg.data };
        updateConnection(state.status);
        if (msg.data.cluster_ip || msg.data.monitor_location) {
          updateClusterLabel(
            msg.data.monitor_location || state.status.monitor_location,
            msg.data.cluster_ip || state.status.cluster_ip
          );
        }
      }
      else if (msg.type === 'alerts') { loadAlerts().then(() => loadOverview()); }
      else if (msg.type === 'events') loadEvents();
      else if (msg.type === 'hardware') { loadHardware(); loadPorts(); loadOverview(); loadResources(); }
      else if (msg.type === 'perf') { loadLive(); loadClusterSeries(); loadResources(); }
      else if (msg.type === 'space') { loadCapacity(); loadOverview(); loadStorage(); loadResources(); }
      else if (msg.type === 'inventory') { loadStorage(); loadNas(); }
      else if (msg.type === 'io_rank') { loadLive(); loadStorage(); }
      else if (msg.type === 'metrics') { loadMetrics(); loadResources(); }
      else if (msg.type === 'audit') loadAudit();
      else if (msg.type === 'cluster_info') { loadOverview(); loadResources(); }
      else if (msg.type === 'port_perf') { loadPortMetrics(); loadPorts(); loadResources(); }
      else if (msg.type === 'object_space') { loadCapacity(); loadStorage(); loadResources(); }
      else if (msg.type === 'protection') { loadProtection(); loadResources(); }
      else if (msg.type === 'report') { state.reportStatus = msg.data; renderReportStatus(); loadReportLocations(); }
    } catch { /* ignore */ }
  };
}

async function init() {
  setupNav(); setupFilters(); setupSettings(); setupSupport(); setupReports(); setupSSE();
  ensureClusterCharts();
  await loadSettings();
  await refreshAll();
}

init();
