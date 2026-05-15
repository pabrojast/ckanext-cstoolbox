/* ckanext-cstoolbox — collection dashboard
 *
 * Lightweight map-only dashboard for a CST Collection: loads the combined
 * GeoJSON and plots one marker per feature, colour-coded by source survey.
 */
(function () {
  'use strict';

  var cfg = window.CST_COLL_CONFIG || {};
  if (!cfg.geojsonUrl) return;

  var PALETTE = [
    '#0072BC', '#dc3545', '#198754', '#ff8a00', '#6f42c1',
    '#0dcaf0', '#e91e63', '#795548', '#607d8b', '#1baa80',
  ];

  function $(id) { return document.getElementById(id); }

  function init() {
    var node = $('cstCollMap');
    if (!node || !window.L) return;
    var map = L.map(node, { worldCopyJump: true }).setView([0, 20], 2);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 18,
      attribution: '© OpenStreetMap',
    }).addTo(map);

    var markers = (L.markerClusterGroup
      ? L.markerClusterGroup({
          iconCreateFunction: function (cluster) {
            return L.divIcon({
              html: '<div class="cst-marker-cluster">' + cluster.getChildCount() + '</div>',
              className: '',
              iconSize: L.point(28, 28),
            });
          },
        })
      : L.layerGroup()
    ).addTo(map);

    fetch(cfg.geojsonUrl, { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (gj) {
        var features = (gj && gj.features) || [];
        var surveyColor = {};
        var colorIdx = 0;
        var bounds = [];

        features.forEach(function (f) {
          if (!f.geometry || !f.geometry.coordinates) return;
          var lon = f.geometry.coordinates[0];
          var lat = f.geometry.coordinates[1];
          if (!isFinite(lat) || !isFinite(lon)) return;

          var survey = (f.properties && (f.properties._survey || f.properties._survey_title)) || 'unknown';
          if (!(survey in surveyColor)) {
            surveyColor[survey] = PALETTE[colorIdx % PALETTE.length];
            colorIdx += 1;
          }
          var color = surveyColor[survey];

          var marker = L.circleMarker([lat, lon], {
            radius: 5,
            color: color,
            weight: 1.5,
            fillColor: color,
            fillOpacity: 0.7,
          });

          marker.bindPopup(buildPopup(f.properties || {}));
          markers.addLayer(marker);
          bounds.push([lat, lon]);
        });

        var countNode = $('cstCollMapCount');
        if (countNode) countNode.textContent = features.length + ' observations';

        if (bounds.length > 0) {
          map.fitBounds(bounds, { padding: [40, 40], maxZoom: 9 });
        }
      })
      .catch(function (e) {
        var countNode = $('cstCollMapCount');
        if (countNode) countNode.textContent = 'Error loading GeoJSON';
        console.error('cstoolbox: collection GeoJSON load failed', e);
      });
  }

  function buildPopup(props) {
    var html = '<div class="cst-leaflet-popup">';
    if (props._survey_title) html += '<div class="site">' + esc(props._survey_title) + '</div>';
    Object.keys(props).slice(0, 8).forEach(function (k) {
      if (k.charAt(0) === '_') return;
      var v = props[k];
      if (v === null || v === undefined) return;
      html += '<div class="field"><strong>' + esc(k) + ':</strong> <span>' + esc(String(v)) + '</span></div>';
    });
    html += '</div>';
    return html;
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (m) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[m];
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
