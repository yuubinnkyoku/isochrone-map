// ============================================================
// markers.js — 駅マーカー・ラベル・ツールチップ・目的地マーカー
// ============================================================
(function () {
  'use strict';

  var MarkerManager = {
    _map: null,
    _meta: null,
    _markers: [],
    _destMarker: null,
    _destRings: [],
    _labelsEnabled: true,

    init: function (map, stations, meta) {
      this._map = map;
      this._meta = meta;
      this._createDestMarker();
      this._createDestinationRings();
      this._createStationMarkers(stations);
      map.on('zoomend', this._updateDisplay.bind(this));
      this._updateDisplay();
    },

    _createDestMarker: function () {
      var dest = CONFIG.destination;
      this._destMarker = L.marker([dest.lat, dest.lng], {
        icon: L.divIcon({
          className: '',
          html: '<div class="dest-marker"></div>',
          iconSize: [16, 16],
          iconAnchor: [8, 8]
        }),
        zIndexOffset: 1000
      }).addTo(this._map);

      this._destMarker.bindTooltip(
        '<div class="station-tooltip">' +
          '<div class="tt-name">🏫 ' + dest.name + '</div>' +
          '<div class="tt-time" style="color:#ff6b6b">' + dest.classStartTime + '</div>' +
          '<div class="tt-line">1限開始</div>' +
          '<div class="tt-detail">検索基準の学校到着: ' + dest.dataTargetTime + '</div>' +
        '</div>',
        { direction: 'top', offset: [0, -12], className: 'station-tooltip-wrapper', opacity: 1 }
      );
    },

    _getDestinationRingStyles: function () {
      var style = getComputedStyle(document.documentElement);
      var config = CONFIG.destinationRings && CONFIG.destinationRings.styleVars ? CONFIG.destinationRings.styleVars : {};
      return {
        fillColor: style.getPropertyValue(config.fill || '--dest-ring-fill').trim() || 'rgba(255,107,107,0.08)',
        strokeByRadius: config.strokeByRadius || {}
      };
    },

    _createDestinationRings: function () {
      var dest = CONFIG.destination;
      var rings = CONFIG.destinationRings;
      if (!rings || !rings.radiiMeters || !rings.radiiMeters.length) return;
      var ringStyles = this._getDestinationRingStyles();
      var style = getComputedStyle(document.documentElement);
      this._destRings = rings.radiiMeters.map(function (radius) {
        var strokeVar = ringStyles.strokeByRadius[radius] || '--dest-ring-15km-stroke';
        return L.circle([dest.lat, dest.lng], {
          radius: radius,
          color: style.getPropertyValue(strokeVar).trim() || 'rgba(255,107,107,0.5)',
          weight: 1.5,
          opacity: 0.9,
          fillColor: ringStyles.fillColor,
          fillOpacity: 0.08,
          interactive: false
        });
      });
    },

    setDestinationRingsVisible: function (visible) {
      var self = this;
      this._destRings.forEach(function (ring) {
        if (visible) {
          if (!self._map.hasLayer(ring)) ring.addTo(self._map);
        } else if (self._map.hasLayer(ring)) {
          self._map.removeLayer(ring);
        }
      });
    },

    _getMarkerStroke: function () {
      return getComputedStyle(document.documentElement).getPropertyValue('--marker-stroke').trim() || '#fff';
    },

    _createStationMarkers: function (stations) {
      var self = this;
      var strokeColor = this._getMarkerStroke();

      stations.forEach(function (s) {
        var c = minutesToColor(s.minutes);
        var ts = minutesToTimeStr(s.minutes);
        var travelHtml = s.duration !== undefined ? '<div class="tt-travel">所要 ' + s.duration + '分</div>' : '';
        var isMajor = !!s.major;

        var marker = L.circleMarker([s.lat, s.lng], {
          radius: 6,
          fillColor: colorToCSS(c),
          color: strokeColor,
          weight: isMajor ? 2 : 1.5,
          opacity: 1,
          fillOpacity: 0.9
        }).addTo(self._map);

        var routeHtml = s.route ? '<div class="tt-line">' + s.route + '</div>' : '';
        var noteHtml = s.note ? '<div class="tt-detail">' + s.note + '</div>' : '';
        var searchDateHtml = s.searchDate ? '<div class="tt-detail">検索日: ' + s.searchDate + '</div>' : '';
        var details = (routeHtml || noteHtml || searchDateHtml) ? '<div class="tt-divider"></div>' + routeHtml + noteHtml + searchDateHtml : '';

        marker.bindTooltip(
          '<div class="station-tooltip">' +
            '<div class="tt-name">' + s.station + '</div>' +
            '<div class="tt-time" style="color:' + colorToCSS(c) + '">' + ts + '</div>' +
            travelHtml +
            '<div class="tt-divider"></div>' +
            '<div class="tt-line">' + s.line + '</div>' +
            details +
          '</div>',
          { direction: 'top', offset: [0, -8], className: 'station-tooltip-wrapper', opacity: 1 }
        );

        var label = L.marker([s.lat, s.lng], {
          icon: L.divIcon({
            className: 'station-label',
            html: '<span class="sl-name">' + s.station + '</span><br>' +
              '<span class="sl-time">' + ts + '</span>' +
              '<span class="sl-detail" style="display:none"><br>' + s.line + '</span>',
            iconSize: [120, 40],
            iconAnchor: [-8, 15]
          }),
          interactive: false
        });

        self._markers.push({ marker: marker, label: label, data: s, isMajor: isMajor });
      });
    },

    _updateDisplay: function () {
      var z = this._map.getZoom();
      var self = this;
      this._markers.forEach(function (item) {
        var r = item.isMajor ? (z <= 10 ? 5 : z === 11 ? 6 : z <= 13 ? 7 : 8) : (z <= 10 ? 3 : z === 11 ? 4 : z <= 13 ? 5 : 6);
        item.marker.setRadius(r);

        var showLabel = self._labelsEnabled && (z >= 12 || (z <= 11 && item.isMajor));
        if (showLabel && !self._map.hasLayer(item.label)) item.label.addTo(self._map);
        if (!showLabel && self._map.hasLayer(item.label)) self._map.removeLayer(item.label);

        if (showLabel && item.label._icon) {
          var detail = item.label._icon.querySelector('.sl-detail');
          if (detail) detail.style.display = z >= 14 ? '' : 'none';
        }
      });
    },

    setLabelsEnabled: function (enabled) {
      this._labelsEnabled = enabled;
      this._updateDisplay();
    },

    findStationMarker: function (name) {
      for (var i = 0; i < this._markers.length; i++) {
        if (this._markers[i].data.station === name) return this._markers[i].marker;
      }
      return null;
    },

    refresh: function (stations, meta) {
      var self = this;
      this._markers.forEach(function (item) {
        if (self._map.hasLayer(item.marker)) self._map.removeLayer(item.marker);
        if (self._map.hasLayer(item.label)) self._map.removeLayer(item.label);
      });
      this._markers = [];
      this._meta = meta;
      this._createStationMarkers(stations);
      this._updateDisplay();
    },

    updateTheme: function () {
      var strokeColor = this._getMarkerStroke();
      this._markers.forEach(function (item) {
        item.marker.setStyle({ color: strokeColor });
      });

      if (this._destRings.length) {
        var ringStyle = this._getDestinationRingStyles();
        var cs = getComputedStyle(document.documentElement);
        this._destRings.forEach(function (ring) {
          var radius = ring.getRadius();
          var strokeVar = ringStyle.strokeByRadius[radius] || '--dest-ring-15km-stroke';
          ring.setStyle({
            color: cs.getPropertyValue(strokeVar).trim() || 'rgba(255,107,107,0.5)',
            fillColor: ringStyle.fillColor
          });
        });
      }
    }
  };

  window.MarkerManager = MarkerManager;
})();
