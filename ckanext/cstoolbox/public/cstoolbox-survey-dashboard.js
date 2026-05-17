/* ckanext-cstoolbox — survey dashboard
 *
 * Builds a Leaflet map + Chart.js charts + filterable data table on top of
 * the live `/cstoolbox-survey/<name>/data` JSON endpoint.
 *
 * Reads `window.CST_DASH_CONFIG = { dataUrl, geojsonUrl, csvUrl, survey, i18n }`.
 */
(function () {
  'use strict';

  var cfg = window.CST_DASH_CONFIG || {};
  if (!cfg.dataUrl) return;

  // ── Palette (kept in sync with stationsdischarge so plugins look related) ──
  var PALETTE = [
    '#0072BC', '#dc3545', '#198754', '#ff8a00', '#6f42c1',
    '#0dcaf0', '#e91e63', '#795548', '#607d8b', '#1baa80',
  ];

  // ── State ────────────────────────────────────────────
  var state = {
    rows: [],
    columns: cfg.survey && cfg.survey.columns || [],
    survey: cfg.survey || {},
    activePreset: cfg.survey && cfg.survey.default_time_range || '3m',
    start: '',
    end: '',
    selectedSites: new Set(),
    selectedStates: new Set(),
    tableSearch: '',
    charts: {},
    map: null,
    markers: null,
  };

  // ── DOM helpers ──────────────────────────────────────
  function $(id) { return document.getElementById(id); }

  // ── Initialisation ───────────────────────────────────
  function init() {
    wirePresets();
    wireRangePickers();
    wireApply();
    wireDownloads();
    wireTableSearch();
    initMap();
    setActivePreset(state.activePreset);
    fetchData();
  }

  function wirePresets() {
    var bar = $('cstPresets');
    if (!bar) return;
    bar.addEventListener('click', function (e) {
      var btn = e.target.closest('.preset-btn');
      if (!btn) return;
      setActivePreset(btn.dataset.range);
      state.start = '';
      state.end = '';
      var s = $('cstStart'); var en = $('cstEnd');
      if (s) s.value = '';
      if (en) en.value = '';
      fetchData();
    });
  }

  function setActivePreset(preset) {
    state.activePreset = preset;
    var bar = $('cstPresets');
    if (!bar) return;
    bar.querySelectorAll('.preset-btn').forEach(function (b) {
      b.classList.toggle('active', b.dataset.range === preset);
    });
  }

  function wireRangePickers() {
    var s = $('cstStart'); var e = $('cstEnd');
    if (s) s.addEventListener('change', function () { state.start = s.value; });
    if (e) e.addEventListener('change', function () { state.end = e.value; });
  }

  function wireApply() {
    var btn = $('cstApply');
    if (!btn) return;
    btn.addEventListener('click', function () {
      if (state.start || state.end) state.activePreset = '';
      var bar = $('cstPresets');
      if (bar && state.activePreset === '') {
        bar.querySelectorAll('.preset-btn').forEach(function (b) {
          b.classList.remove('active');
        });
      }
      fetchData();
    });
  }

  function wireDownloads() {
    var gbtn = $('cstDownloadGeojson');
    var cbtn = $('cstDownloadCsv');
    function refresh() {
      var params = buildParams();
      var qs = params.toString();
      if (gbtn) gbtn.href = cfg.geojsonUrl + (qs ? '?' + qs : '');
      if (cbtn) cbtn.href = cfg.csvUrl + (qs ? '?' + qs : '');
    }
    refresh();
    // Re-bind so links stay current after fetches finish.
    state.refreshDownloads = refresh;
  }

  function wireTableSearch() {
    var box = $('cstTableSearch');
    if (!box) return;
    box.addEventListener('input', function () {
      state.tableSearch = box.value.toLowerCase();
      renderTable();
    });
  }

  // ── Map ──────────────────────────────────────────────
  function initMap() {
    var node = $('cstMap');
    if (!node || !window.L) return;
    state.map = L.map(node, {
      worldCopyJump: true,
    }).setView([0, 20], 2);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 18,
      attribution: '© OpenStreetMap',
    }).addTo(state.map);
    if (L.markerClusterGroup) {
      state.markers = L.markerClusterGroup({
        iconCreateFunction: function (cluster) {
          return L.divIcon({
            html: '<div class="cst-marker-cluster">' + cluster.getChildCount() + '</div>',
            className: '',
            iconSize: L.point(28, 28),
          });
        },
      });
      state.map.addLayer(state.markers);
    } else {
      state.markers = L.layerGroup().addTo(state.map);
    }
  }

  // ── Network ──────────────────────────────────────────
  function buildParams() {
    var params = new URLSearchParams();
    if (state.start) params.set('start', state.start);
    if (state.end) params.set('end', state.end);
    if (state.activePreset) params.set('time_range', state.activePreset);
    return params;
  }

  function fetchData() {
    var params = buildParams();
    var url = cfg.dataUrl + (params.toString() ? '?' + params.toString() : '');
    setLoading(true);
    fetch(url, { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        setLoading(false);
        if (data.error) { showError(data.error); return; }
        state.rows = data.rows || [];
        // Reset detected secondary field so it re-evaluates on each refresh.
        state.secondaryField = undefined;
        if (data.columns && data.columns.length) state.columns = data.columns;
        if (data.survey) state.survey = Object.assign({}, state.survey, data.survey);
        if (state.refreshDownloads) state.refreshDownloads();
        renderAll();
      })
      .catch(function (e) { setLoading(false); showError(e); });
  }

  function setLoading(on) {
    var overlay = $('cstChartsEmpty');
    if (!overlay) return;
    if (on) {
      overlay.classList.remove('hidden');
      overlay.innerHTML = '<i class="fa fa-spinner fa-spin"></i> ' + (cfg.i18n.loading || 'Loading…');
    }
  }

  function showError(err) {
    var overlay = $('cstChartsEmpty');
    if (!overlay) return;
    overlay.classList.remove('hidden');
    var msg = typeof err === 'string' ? err : JSON.stringify(err);
    overlay.innerHTML = '<i class="fa fa-exclamation-triangle"></i> ' + msg;
  }

  // ── Rendering ────────────────────────────────────────
  function renderAll() {
    renderStats();
    renderFacets();
    renderMap();
    renderCharts();
    renderTable();
  }

  // Auto-detect a useful "secondary" categorical column to facet by
  // (e.g. member_state, country, school). Returns null if nothing fits.
  // Cached on state so it stays stable across filter changes.
  var SECONDARY_PREFERRED = [
    'member_state', 'country', 'region', 'organisation', 'organization',
    'school', 'team', 'sampling_group', 'student_group',
  ];
  function detectSecondaryField() {
    if (state.secondaryField !== undefined) return state.secondaryField;
    if (!state.rows.length) return null;
    var siteField = state.survey.site_field;
    var dateField = state.survey.date_field;
    var latField = state.survey.lat_field;
    var lonField = state.survey.lon_field;
    var skip = new Set([
      siteField, dateField, latField, lonField,
      'id', 'timestamp', 'time', 'sample_time', '_uri', '_core_uri',
      'comment', 'weather', 'instrument',
    ]);
    function uniqueCount(key) {
      var seen = new Set();
      for (var i = 0; i < state.rows.length; i++) {
        var v = state.rows[i][key];
        if (v !== null && v !== undefined && v !== '') seen.add(v);
      }
      return seen.size;
    }
    function isStringy(key) {
      for (var i = 0; i < state.rows.length; i++) {
        var v = state.rows[i][key];
        if (v === null || v === undefined) continue;
        return typeof v === 'string';
      }
      return false;
    }
    // 1. Try preferred names first.
    for (var i = 0; i < SECONDARY_PREFERRED.length; i++) {
      var k = SECONDARY_PREFERRED[i];
      if (state.rows[0].hasOwnProperty(k) && !skip.has(k)) {
        var c = uniqueCount(k);
        if (c >= 1 && c <= 50) { state.secondaryField = k; return k; }
      }
    }
    // 2. Otherwise pick the lowest-cardinality string column with 2..30 values.
    var bestK = null, bestC = Infinity;
    Object.keys(state.rows[0]).forEach(function (k) {
      if (skip.has(k)) return;
      if (!isStringy(k)) return;
      var c = uniqueCount(k);
      if (c >= 2 && c <= 30 && c < bestC) { bestK = k; bestC = c; }
    });
    state.secondaryField = bestK;
    return bestK;
  }

  function humanizeFieldName(name) {
    if (!name) return '';
    return name.replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  function trimDate(value) {
    if (!value) return '';
    var s = String(value);
    // Cut off any time component (T or space) so we don't blow out the stat card.
    var sep = s.indexOf('T') !== -1 ? 'T' : ' ';
    var idx = s.indexOf(sep);
    return idx !== -1 ? s.slice(0, idx) : s;
  }

  function filteredRows() {
    var siteField = state.survey.site_field || 'site_name';
    var secondary = detectSecondaryField();
    var rows = state.rows;
    if (state.selectedSites.size > 0) {
      rows = rows.filter(function (r) { return state.selectedSites.has(r[siteField]); });
    }
    if (secondary && state.selectedStates.size > 0) {
      rows = rows.filter(function (r) { return state.selectedStates.has(r[secondary]); });
    }
    return rows;
  }

  function renderStats() {
    var rows = filteredRows();
    var siteField = state.survey.site_field || 'site_name';
    var secondary = detectSecondaryField();
    var sites = new Set();
    var states = new Set();
    var lastDate = '';
    rows.forEach(function (r) {
      if (r[siteField]) sites.add(r[siteField]);
      if (secondary && r[secondary]) states.add(r[secondary]);
      var d = r[state.survey.date_field || 'obs_date'];
      if (d && String(d) > lastDate) lastDate = String(d);
    });
    var o = $('statObs'); if (o) o.textContent = rows.length;
    var s = $('statSites'); if (s) s.textContent = sites.size;
    var ms = $('statStates'); if (ms) ms.textContent = secondary ? states.size : '—';
    var msl = document.querySelector('.cst-stat-card .label[data-stat="secondary"]');
    if (msl) msl.textContent = secondary ? humanizeFieldName(secondary) : '—';
    var l = $('statLast'); if (l) l.textContent = lastDate ? trimDate(lastDate) : '—';
  }

  function renderFacets() {
    var siteField = state.survey.site_field || 'site_name';
    var secondary = detectSecondaryField();
    var sites = {};
    var states = {};
    state.rows.forEach(function (r) {
      if (r[siteField]) sites[r[siteField]] = (sites[r[siteField]] || 0) + 1;
      if (secondary && r[secondary]) states[r[secondary]] = (states[r[secondary]] || 0) + 1;
    });
    renderChips($('cstSiteChips'), sites, state.selectedSites);
    // Update the secondary chip section header + container
    var secLabel = document.querySelector('[data-facet="secondary"] strong');
    if (secLabel) secLabel.textContent = secondary ? humanizeFieldName(secondary) : '';
    var secContainer = document.querySelector('[data-facet="secondary"]');
    if (secContainer) secContainer.style.display = secondary ? '' : 'none';
    renderChips($('cstStateChips'), states, state.selectedStates);
  }

  function renderChips(container, dict, selectedSet) {
    if (!container) return;
    container.innerHTML = '';
    var entries = Object.entries(dict).sort(function (a, b) { return b[1] - a[1]; });
    if (entries.length === 0) {
      container.innerHTML = '<small style="color:#999;">—</small>';
      return;
    }
    entries.forEach(function (entry) {
      var name = entry[0]; var count = entry[1];
      var chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'cst-chip' + (selectedSet.has(name) ? ' active' : '');
      chip.textContent = name + ' (' + count + ')';
      chip.addEventListener('click', function () {
        if (selectedSet.has(name)) selectedSet.delete(name); else selectedSet.add(name);
        renderAll();
      });
      container.appendChild(chip);
    });
  }

  function renderMap() {
    if (!state.markers) return;
    state.markers.clearLayers();
    var rows = filteredRows();
    var siteField = state.survey.site_field || 'site_name';
    var latField = state.survey.lat_field || 'latitude';
    var lonField = state.survey.lon_field || 'longitude';

    // Aggregate by site so the map shows one marker per site with latest sample.
    var bySite = {};
    rows.forEach(function (r) {
      var site = r[siteField] || '(unspecified)';
      if (!bySite[site]) bySite[site] = [];
      bySite[site].push(r);
    });

    var bounds = [];
    Object.keys(bySite).forEach(function (site) {
      var siteRows = bySite[site];
      // Find a row with valid coords; prefer the latest.
      siteRows.sort(function (a, b) {
        var av = a[state.survey.date_field] || '';
        var bv = b[state.survey.date_field] || '';
        return String(av) < String(bv) ? 1 : -1;
      });
      var latest = null;
      for (var i = 0; i < siteRows.length; i++) {
        var r = siteRows[i];
        var lat = parseFloat(r[latField]);
        var lon = parseFloat(r[lonField]);
        if (isFinite(lat) && isFinite(lon)) { latest = { row: r, lat: lat, lon: lon }; break; }
      }
      if (!latest) return;
      bounds.push([latest.lat, latest.lon]);
      var marker = L.circleMarker([latest.lat, latest.lon], {
        radius: 7,
        color: '#005A9C',
        weight: 2,
        fillColor: '#0072BC',
        fillOpacity: 0.7,
      });
      marker.bindPopup(buildPopup(site, siteRows));
      state.markers.addLayer(marker);
    });

    var countNode = $('cstMapCount');
    if (countNode) countNode.textContent = Object.keys(bySite).length + ' sites';

    if (bounds.length > 0) {
      state.map.fitBounds(bounds, { padding: [40, 40], maxZoom: 9 });
    }
  }

  function buildPopup(site, rows) {
    var latest = rows[0];
    var html = '<div class="cst-leaflet-popup">';
    html += '<div class="site">' + escapeHtml(site) + '</div>';
    html += '<div class="field"><strong>Samples:</strong> <span>' + rows.length + '</span></div>';
    if (latest[state.survey.date_field]) {
      html += '<div class="field"><strong>Latest:</strong> <span>' + escapeHtml(String(latest[state.survey.date_field])) + '</span></div>';
    }
    var cols = (state.columns || []).slice(0, 5);
    cols.forEach(function (c) {
      var v = latest[c.column_name];
      if (v === null || v === undefined) return;
      var label = c.label || c.column_name;
      if (c.unit) label += ' (' + c.unit + ')';
      html += '<div class="field"><strong>' + escapeHtml(label) + ':</strong> <span>' + escapeHtml(String(v)) + '</span></div>';
    });
    html += '</div>';
    return html;
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, function (m) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[m];
    });
  }

  // ── Charts ───────────────────────────────────────────
  function detectChartColumns() {
    if (state.columns && state.columns.length) return state.columns;
    // Auto-detect numeric columns from the first row.
    if (!state.rows.length) return [];
    var first = state.rows[0];
    var skip = new Set([
      state.survey.lat_field, state.survey.lon_field,
      state.survey.date_field, state.survey.site_field,
      'sample_date', 'sample_time', 'timestamp', 'obs_date',
    ]);
    var cols = [];
    Object.keys(first).forEach(function (k) {
      if (skip.has(k)) return;
      var v = first[k];
      if (typeof v === 'number' || (typeof v === 'string' && v !== '' && !isNaN(Number(v)))) {
        cols.push({ column_name: k, label: humanize(k), unit: '', chart_type: 'line' });
      }
    });
    return cols;
  }

  function humanize(name) {
    return name.replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  function renderCharts() {
    var grid = $('cstChartsGrid');
    if (!grid) return;

    var rows = filteredRows();
    var columns = detectChartColumns();
    var siteField = state.survey.site_field || 'site_name';
    var dateField = state.survey.date_field || 'obs_date';

    // Cleanup
    Object.keys(state.charts).forEach(function (k) {
      try { state.charts[k].destroy(); } catch (_e) {}
    });
    state.charts = {};
    grid.innerHTML = '';

    if (rows.length === 0) {
      var empty = document.createElement('div');
      empty.className = 'cst-overlay';
      empty.innerHTML = '<i class="fa fa-info-circle"></i> ' + cfg.i18n.no_data;
      grid.appendChild(empty);
      var c = $('cstChartsCount'); if (c) c.textContent = '—';
      return;
    }

    // Build site → date-sorted rows index
    var siteSamples = {};
    rows.forEach(function (r) {
      var site = r[siteField] || '(unspecified)';
      if (!siteSamples[site]) siteSamples[site] = [];
      siteSamples[site].push(r);
    });
    Object.keys(siteSamples).forEach(function (site) {
      siteSamples[site].sort(function (a, b) {
        return String(a[dateField] || '') < String(b[dateField] || '') ? -1 : 1;
      });
    });

    var sites = Object.keys(siteSamples);
    var renderedCharts = 0;

    columns.forEach(function (col, colIdx) {
      var datasets = [];
      var anyData = false;
      sites.forEach(function (site, siteIdx) {
        var points = [];
        siteSamples[site].forEach(function (r) {
          var v = r[col.column_name];
          if (v === null || v === undefined || v === '') return;
          var num = parseFloat(v);
          if (!isFinite(num)) return;
          var dateRaw = r[dateField];
          if (!dateRaw) return;
          var iso = String(dateRaw).replace(' ', 'T');
          points.push({ x: iso, y: num });
        });
        if (!points.length) return;
        anyData = true;
        var color = PALETTE[(siteIdx) % PALETTE.length];
        datasets.push({
          label: site,
          data: points,
          borderColor: color,
          backgroundColor: col.chart_type === 'area' ? hexToRgba(color, 0.18) : color,
          fill: col.chart_type === 'area',
          tension: 0.25,
          pointRadius: points.length > 60 ? 0 : 2.5,
          pointHoverRadius: 4,
          borderWidth: 2,
        });
      });

      if (!anyData) return;

      var card = document.createElement('div');
      card.className = 'cst-chart-card';
      var titleHtml = '<div class="title">' + escapeHtml(col.label || humanize(col.column_name));
      if (col.unit) titleHtml += ' <small>(' + escapeHtml(col.unit) + ')</small>';
      titleHtml += '</div>';
      card.innerHTML = titleHtml + '<canvas></canvas>';
      grid.appendChild(card);

      var canvas = card.querySelector('canvas');
      var chartType = col.chart_type === 'bar' ? 'bar' : 'line';
      state.charts[col.column_name] = new Chart(canvas, {
        type: chartType,
        data: { datasets: datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: 'nearest', intersect: false },
          plugins: {
            legend: {
              display: datasets.length > 1,
              position: 'bottom',
              labels: { boxWidth: 10, padding: 8, font: { size: 10 } },
            },
            tooltip: { enabled: true },
          },
          scales: {
            x: { type: 'time', time: { unit: 'day' }, ticks: { maxTicksLimit: 6, font: { size: 10 } } },
            y: { ticks: { font: { size: 10 } } },
          },
        },
      });
      renderedCharts += 1;
    });

    if (renderedCharts === 0) {
      var none = document.createElement('div');
      none.className = 'cst-overlay';
      none.innerHTML = '<i class="fa fa-info-circle"></i> ' + cfg.i18n.no_data;
      grid.appendChild(none);
    }
    var c = $('cstChartsCount'); if (c) c.textContent = renderedCharts + ' charts';
  }

  function hexToRgba(hex, alpha) {
    var n = parseInt(hex.replace('#', ''), 16);
    var r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
    return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
  }

  // ── Table ────────────────────────────────────────────
  function renderTable() {
    var tbl = $('cstTable');
    if (!tbl) return;
    var rows = filteredRows();
    if (state.tableSearch) {
      var s = state.tableSearch;
      rows = rows.filter(function (r) {
        return Object.values(r).some(function (v) {
          return v !== null && v !== undefined && String(v).toLowerCase().indexOf(s) !== -1;
        });
      });
    }

    var head = tbl.querySelector('thead');
    var body = tbl.querySelector('tbody');
    head.innerHTML = '';
    body.innerHTML = '';

    if (rows.length === 0) {
      head.innerHTML = '<tr><th>—</th></tr>';
      body.innerHTML = '<tr><td style="text-align:center; color:#888; padding:1rem;">' + cfg.i18n.no_data + '</td></tr>';
      var cnt0 = $('cstTableCount');
      if (cnt0) cnt0.textContent = '';
      return;
    }

    // Choose columns to show in the table — prefer identity/date + configured charts.
    var keys = Object.keys(rows[0]);
    var priority = [
      state.survey.site_field, 'member_state', 'sampling_group',
      state.survey.date_field, 'sample_time',
    ];
    var configured = (state.columns || []).map(function (c) { return c.column_name; });
    var picked = [];
    var seen = new Set();
    priority.forEach(function (k) { if (k && keys.indexOf(k) !== -1 && !seen.has(k)) { picked.push(k); seen.add(k); } });
    configured.forEach(function (k) { if (k && keys.indexOf(k) !== -1 && !seen.has(k)) { picked.push(k); seen.add(k); } });
    // Pad with anything else (skipping geometry-only and meta).
    keys.forEach(function (k) {
      if (seen.has(k)) return;
      if (k === state.survey.lat_field || k === state.survey.lon_field) return;
      if (k.indexOf('_') === 0) return;
      picked.push(k); seen.add(k);
    });
    picked = picked.slice(0, 14);

    var headHtml = '<tr>';
    picked.forEach(function (k) { headHtml += '<th>' + escapeHtml(humanize(k)) + '</th>'; });
    headHtml += '</tr>';
    head.innerHTML = headHtml;

    var MAX_ROWS = 500;
    var truncated = rows.length > MAX_ROWS;
    var displayRows = truncated ? rows.slice(0, MAX_ROWS) : rows;

    var bodyHtml = '';
    displayRows.forEach(function (r) {
      bodyHtml += '<tr>';
      picked.forEach(function (k) {
        var v = r[k];
        var cellClass = (typeof v === 'number' || (!isNaN(parseFloat(v)) && isFinite(v))) ? ' class="num"' : '';
        bodyHtml += '<td' + cellClass + '>' + (v === null || v === undefined ? '' : escapeHtml(String(v))) + '</td>';
      });
      bodyHtml += '</tr>';
    });
    body.innerHTML = bodyHtml;

    var cnt = $('cstTableCount');
    if (cnt) {
      cnt.textContent = (cfg.i18n.showing || 'Showing') + ' ' + displayRows.length +
                       ' ' + (cfg.i18n.of || 'of') + ' ' + rows.length + ' ' + (cfg.i18n.rows || 'rows');
    }
  }

  // ── Boot ─────────────────────────────────────────────
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
