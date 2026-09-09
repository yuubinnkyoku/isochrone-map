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
    // 旧「IDW補間対象外駅」表示の見た目を、現在は
    // 「最適経路でこの物理地点の鉄道を使わない地点」に再利用する。
    _excludedStationMode: 'hollow',

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

    _getExcludedMarkerStroke: function () {
      return getComputedStyle(document.documentElement).getPropertyValue('--excluded-marker-highlight').trim() || '#ff2d55';
    },

    _isAlternateRoutePoint: function (data) {
      var audit = data && data.physicalPointAudit;
      var reason = audit && audit.reason;
      return reason === 'boards-different-component' ||
        reason === 'boards-different-station' ||
        reason === 'no-rail-boarded';
    },

    _alternateRouteStatusHtml: function (data) {
      var audit = data && data.physicalPointAudit;
      if (!audit) return '';
      var reason = audit.reason;
      var message = '';
      if (reason === 'boards-different-component') {
        message = '○ 最適経路では同じ乗換駅の別地点から乗車';
      } else if (reason === 'boards-different-station') {
        message = '○ 最適経路では別の駅へ移動して乗車';
      } else if (reason === 'no-rail-boarded') {
        message = '○ 最適経路ではこの地点から鉄道に乗車しない';
      }
      if (!message) return '';

      var firstRail = audit.firstRail || {};
      var boarding = '';
      if (firstRail.boardStation) {
        boarding = '<div class="tt-detail">最初の鉄道乗車: ' + firstRail.boardStation +
          (firstRail.service ? '（' + firstRail.service + '）' : '') + '</div>';
      } else if (reason === 'no-rail-boarded') {
        boarding = '<div class="tt-detail">この最適経路では鉄道を使用しません</div>';
      }
      return '<div class="tt-excluded">' + message + '</div>' + boarding;
    },

    _applyMarkerStyle: function (item, baseRadius, markerStroke, excludedStroke) {
      var isAlternate = this._isAlternateRoutePoint(item.data);
      var mode = this._excludedStationMode;
      var style = {
        radius: baseRadius,
        fillColor: item.timeColor,
        color: markerStroke,
        weight: item.isMajor ? 2 : 1.5,
        opacity: 1,
        fillOpacity: 0.9
      };

      if (isAlternate && mode === 'hollow') {
        style.color = item.timeColor;
        style.fillOpacity = 0;
      } else if (isAlternate && mode === 'highlight') {
        style.radius = baseRadius + 2;
        style.color = excludedStroke;
        style.weight = 3;
        style.fillOpacity = 1;
      }

      item.marker.setRadius(style.radius);
      delete style.radius;
      item.marker.setStyle(style);
    },

    _createStationMarkers: function (stations) {
      var self = this;
      var strokeColor = this._getMarkerStroke();

      stations.forEach(function (s) {
        var c = minutesToColor(s.minutes);
        var timeColor = colorToCSS(c);
        var ts = s.departureDisplay || minutesToTimeStr(s.minutes);
        var travelHtml = s.duration !== undefined ? '<div class="tt-travel">所要 ' + s.duration + '分</div>' : '';
        var isMajor = !!s.major;

        var marker = L.circleMarker([s.lat, s.lng], {
          radius: 6,
          fillColor: timeColor,
          color: strokeColor,
          weight: isMajor ? 2 : 1.5,
          opacity: 1,
          fillOpacity: 0.9
        }).addTo(self._map);

        var routeHtml = '';
        if (s.route) {
          var routeParts = String(s.route).split(' → ');
          var routeLines = [];
          var routeLine = routeParts[0] || '';
          for (var routeIndex = 1; routeIndex < routeParts.length; routeIndex++) {
            var candidateLine = routeLine + ' → ' + routeParts[routeIndex];
            if (candidateLine.length <= 24) {
              routeLine = candidateLine;
            } else {
              routeLines.push(routeLine);
              routeLine = '→ ' + routeParts[routeIndex];
            }
          }
          if (routeLine) routeLines.push(routeLine);
          routeHtml = '<div class="tt-line tt-route">' + routeLines.map(function (line) {
            return '<span class="tt-route-line">' + line + '</span>';
          }).join('') + '</div>';
        }
        var routeUsageHtml = self._alternateRouteStatusHtml(s);
        var noteHtml = s.note ? '<div class="tt-detail">' + s.note + '</div>' : '';
        var searchDateHtml = s.searchDate ? '<div class="tt-detail">検索日: ' + s.searchDate + '</div>' : '';
        var passengerHtml = Number.isFinite(s.passengers)
          ? '<div class="tt-detail">1日乗降客数: ' + s.passengers.toLocaleString('ja-JP') + '人（' + s.passengerYear + '）</div>'
          : '';
        var logicalHtml = s.logicalStation && s.logicalStation !== s.station
          ? '<div class="tt-detail">乗換駅グループ: ' + s.logicalStation + '</div>'
          : '';
        var details = (routeHtml || noteHtml || searchDateHtml || passengerHtml || logicalHtml)
          ? '<div class="tt-divider"></div>' + routeHtml + logicalHtml + noteHtml + searchDateHtml + passengerHtml
          : '';

        marker.bindTooltip(
          '<div class="station-tooltip">' +
            '<div class="tt-name">' + s.station + '</div>' +
            '<div class="tt-time" style="color:' + timeColor + '">' + ts + '</div>' +
            routeUsageHtml +
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
            iconSize: [160, 40],
            iconAnchor: [-8, 15]
          }),
          interactive: false
        });

        self._markers.push({ marker: marker, label: label, data: s, isMajor: isMajor, timeColor: timeColor });
      });
    },

    _labelBox: function (item) {
      var config = CONFIG.stationLabels || {};
      var boxConfig = config.collisionBox || {};
      var width = boxConfig.width || 160;
      var height = boxConfig.height || 40;
      var gap = boxConfig.gap || 0;
      var point = this._map.latLngToContainerPoint(item.marker.getLatLng());
      return {
        left: point.x + 8 - gap,
        right: point.x + 8 + width + gap,
        top: point.y - 15 - gap,
        bottom: point.y - 15 + height + gap
      };
    },

    _boxesOverlap: function (a, b) {
      return !(a.right < b.left || a.left > b.right || a.bottom < b.top || a.top > b.bottom);
    },

    _setLabelVisible: function (item, visible, showDetail) {
      if (visible && !this._map.hasLayer(item.label)) item.label.addTo(this._map);
      if (!visible && this._map.hasLayer(item.label)) this._map.removeLayer(item.label);
      if (visible && item.label._icon) {
        var detail = item.label._icon.querySelector('.sl-detail');
        if (detail) detail.style.display = showDetail ? '' : 'none';
      }
    },

    _updateDisplay: function () {
      // Settings are interactive before station data finishes loading. Treat
      // pre-init updates as no-ops; init() reapplies the current settings later.
      if (!this._map) return;

      var z = this._map.getZoom();
      var self = this;
      var labelConfig = CONFIG.stationLabels || {};
      var allLabelsMinZoom = labelConfig.allLabelsMinZoom || 15;
      var majorOnlyMaxZoom = labelConfig.majorOnlyMaxZoom || 11;
      var collisionMaxZoom = labelConfig.collisionMaxZoom || 14;

      var markerStroke = this._getMarkerStroke();
      var excludedStroke = this._getExcludedMarkerStroke();
      this._markers.forEach(function (item) {
        var hidden = self._isAlternateRoutePoint(item.data) && self._excludedStationMode === 'hidden';
        self._setLabelVisible(item, false, false);
        if (hidden) {
          if (self._map.hasLayer(item.marker)) self._map.removeLayer(item.marker);
          return;
        }
        if (!self._map.hasLayer(item.marker)) item.marker.addTo(self._map);
        var r = item.isMajor ? (z <= 10 ? 5 : z === 11 ? 6 : z <= 13 ? 7 : 8) : (z <= 10 ? 3 : z === 11 ? 4 : z <= 13 ? 5 : 6);
        self._applyMarkerStyle(item, r, markerStroke, excludedStroke);
      });

      if (!this._labelsEnabled) return;

      var candidates = this._markers.filter(function (item) {
        if (self._isAlternateRoutePoint(item.data) && self._excludedStationMode === 'hidden') return false;
        // At high zoom every physical point gets a label. At lower zooms only
        // one representative point per logical station is considered. Between
        // those levels passenger counts decide collision priority, not whether
        // an otherwise isolated station is allowed to appear at all.
        if (z >= allLabelsMinZoom) return true;
        if (item.data.labelPrimary === false) return false;
        if (z <= majorOnlyMaxZoom) return item.isMajor;
        return true;
      });

      candidates.sort(function (a, b) {
        if (a.isMajor !== b.isMajor) return a.isMajor ? -1 : 1;
        var ar = Number.isFinite(a.data.passengerRank) ? a.data.passengerRank : Infinity;
        var br = Number.isFinite(b.data.passengerRank) ? b.data.passengerRank : Infinity;
        if (ar !== br) return ar - br;
        return a.data.station.localeCompare(b.data.station, 'ja');
      });

      var occupied = [];
      var useCollision = z <= collisionMaxZoom && z > majorOnlyMaxZoom;
      candidates.forEach(function (item) {
        var box = self._labelBox(item);
        var collides = useCollision && occupied.some(function (other) {
          return self._boxesOverlap(box, other);
        });
        if (collides) return;
        self._setLabelVisible(item, true, z >= allLabelsMinZoom);
        occupied.push(box);
      });
    },

    setLabelsEnabled: function (enabled) {
      this._labelsEnabled = enabled;
      this._updateDisplay();
    },

    setExcludedStationMode: function (mode) {
      if (['highlight', 'hollow', 'hidden'].indexOf(mode) === -1) mode = 'hollow';
      this._excludedStationMode = mode;
      this._updateDisplay();
    },

    findStationMarker: function (name) {
      var i;
      for (i = 0; i < this._markers.length; i++) {
        if (this._markers[i].data.station === name) return this._markers[i].marker;
      }
      for (i = 0; i < this._markers.length; i++) {
        if (this._markers[i].data.logicalStation === name && this._markers[i].data.labelPrimary !== false) {
          return this._markers[i].marker;
        }
      }
      for (i = 0; i < this._markers.length; i++) {
        if (this._markers[i].data.logicalStation === name) return this._markers[i].marker;
      }
      return null;
    },

    refresh: function (stations, meta) {
      if (!this._map) return;
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
      this._updateDisplay();

      if (this._destRings.length && this._map) {
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
