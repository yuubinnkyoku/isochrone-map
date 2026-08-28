// ============================================================
// markers.js — 駅マーカー・ラベル・ツールチップ・目的地マーカー
// ============================================================
(function () {
  'use strict';

  var MarkerManager = {
    _map: null,
    _meta: null,
    _markers: [],    // { marker, label, data }
    _destMarker: null,
    _destRings: [],
    _labelsEnabled: true,
    _gakkuLayer: null,
    _gakkuAnimId: null,

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
          iconAnchor: [8, 8],
        }),
        zIndexOffset: 1000,
      }).addTo(this._map);

      this._destMarker.bindTooltip(
        '<div class="station-tooltip">' +
          '<div class="tt-name">\uD83C\uDFEB ' + dest.name + '</div>' +
          '<div class="tt-time" style="color:#ff6b6b">' + dest.arrivalTime + '</div>' +
          '<div class="tt-line">1限開始</div>' +
        '</div>',
        { direction: 'top', offset: [0, -12], className: 'station-tooltip-wrapper', opacity: 1 }
      );
    },

    _getDestinationRingStyles: function () {
      var style = getComputedStyle(document.documentElement);
      var config = CONFIG.destinationRings && CONFIG.destinationRings.styleVars ? CONFIG.destinationRings.styleVars : {};
      var fillVar = config.fill || '--dest-ring-fill';
      var strokeByRadius = config.strokeByRadius || {};
      return {
        fillColor: style.getPropertyValue(fillVar).trim() || 'rgba(255, 107, 107, 0.08)',
        strokeByRadius: strokeByRadius,
      };
    },

    _createDestinationRings: function () {
      var dest = CONFIG.destination;
      var rings = CONFIG.destinationRings;
      if (!dest || !rings || !rings.radiiMeters || !rings.radiiMeters.length) return;

      var ringStyles = this._getDestinationRingStyles();
      var style = getComputedStyle(document.documentElement);
      this._destRings = rings.radiiMeters.map(function (radius) {
        var strokeVar = ringStyles.strokeByRadius[radius] || '--dest-ring-15km-stroke';
        var strokeColor = style.getPropertyValue(strokeVar).trim() || 'rgba(255, 107, 107, 0.5)';
        return L.circle([dest.lat, dest.lng], {
          radius: radius,
          color: strokeColor,
          weight: 1.5,
          opacity: 0.9,
          fillColor: ringStyles.fillColor,
          fillOpacity: 0.08,
          interactive: false,
        });
      });
    },

    setDestinationRingsVisible: function (visible) {
      if (!this._destRings || !this._destRings.length) return;
      this._destRings.forEach(function (ring) {
        if (visible) {
          if (!this._map.hasLayer(ring)) ring.addTo(this._map);
        } else if (this._map.hasLayer(ring)) {
          this._map.removeLayer(ring);
        }
      }.bind(this));
    },

    _getMarkerStroke: function () {
      return getComputedStyle(document.documentElement).getPropertyValue('--marker-stroke').trim() || '#fff';
    },

    _createStationMarkers: function (stations) {
      var self = this;
      var targetMinutes = this._meta.targetMinutes;
      var strokeColor = this._getMarkerStroke();

      stations.forEach(function (s) {
        var c = minutesToColor(s.minutes);
        var ts = minutesToTimeStr(s.minutes);
        var travelHtml = s.duration !== undefined ? '<div class="tt-travel">所要 ' + s.duration + '分</div>' : '';
        var isMajor = !!s.major;
        var isOutside = !!s.outside;

        // Circle marker
        var marker = L.circleMarker([s.lat, s.lng], {
          radius: 6,
          fillColor: colorToCSS(c),
          color: strokeColor,
          weight: isMajor ? 2 : 1.5,
          opacity: 1,
          fillOpacity: isOutside ? 0.4 : 0.9,
        }).addTo(self._map);

        // Tooltip
        var outsideBadge = isOutside ? '<div class="tt-outside">※学区外</div>' : '';
        var routeHtml = s.route ? '<div class="tt-line">' + s.route + '</div>' : '';
        var noteHtml = s.note ? '<div class="tt-detail">' + s.note + '</div>' : '';
        var searchDateHtml = s.searchDate ? '<div class="tt-detail">検索日: ' + s.searchDate + '</div>' : '';
        var detailSection = (routeHtml || noteHtml || searchDateHtml)
          ? '<div class="tt-divider"></div>' + routeHtml + noteHtml + searchDateHtml
          : '';

        marker.bindTooltip(
          '<div class="station-tooltip">' +
            outsideBadge +
            '<div class="tt-name">' + s.station + '</div>' +
            '<div class="tt-time" style="color:' + colorToCSS(c) + '">' + ts + '</div>' +
            travelHtml +
            '<div class="tt-divider"></div>' +
            '<div class="tt-line">' + s.line + '</div>' +
            detailSection +
          '</div>',
          { direction: 'top', offset: [0, -8], className: 'station-tooltip-wrapper', opacity: 1 }
        );

        // Label (divIcon marker)
        var label = L.marker([s.lat, s.lng], {
          icon: L.divIcon({
            className: 'station-label' + (isOutside ? ' outside' : ''),
            html: '<span class="sl-name">' + s.station + '</span>' +
              '<br><span class="sl-time">' + ts + '</span>' +
              (isOutside ? '<br><span class="sl-outside">学区外</span>' : '') +
              '<span class="sl-detail" style="display:none"><br>' + s.line + '</span>',
            iconSize: [120, 40],
            iconAnchor: [-8, 15],
          }),
          interactive: false,
        });

        self._markers.push({ marker: marker, label: label, data: s, isMajor: isMajor, isOutside: isOutside });
      });
    },

    _updateDisplay: function () {
      var z = this._map.getZoom();

      this._markers.forEach(function (item) {
        var m = item.marker;
        var isMajor = item.isMajor;

        // Marker radius by zoom
        var r;
        if (isMajor) {
          r = z <= 10 ? 5 : z === 11 ? 6 : z <= 13 ? 7 : 8;
        } else {
          r = z <= 10 ? 3 : z === 11 ? 4 : z <= 13 ? 5 : 6;
        }
        m.setRadius(r);

        // Labels visibility
        var showLabel = false;
        if (this._labelsEnabled) {
          if (z >= 14) {
            showLabel = true;
          } else if (z >= 12) {
            showLabel = true;
          } else if (z <= 11 && isMajor) {
            showLabel = true;
          }
        }

        // Update label detail visibility at zoom >= 14
        if (item.label._icon) {
          var detail = item.label._icon.querySelector('.sl-detail');
          if (detail) {
            detail.style.display = z >= 14 ? '' : 'none';
          }
        }

        if (showLabel && !this._map.hasLayer(item.label)) {
          item.label.addTo(this._map);
        } else if (!showLabel && this._map.hasLayer(item.label)) {
          this._map.removeLayer(item.label);
        }

        // After add, update detail visibility
        if (showLabel && item.label._icon) {
          var d = item.label._icon.querySelector('.sl-detail');
          if (d) d.style.display = z >= 14 ? '' : 'none';
        }
      }.bind(this));
    },

    setLabelsEnabled: function (enabled) {
      this._labelsEnabled = enabled;
      this._updateDisplay();
    },

    findStationMarker: function (name) {
      for (var i = 0; i < this._markers.length; i++) {
        if (this._markers[i].data.station === name) {
          return this._markers[i].marker;
        }
      }
      return null;
    },

    // 既存マーカーを全クリアして再作成
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

      // 目的地同心円の色も更新
      if (this._destRings && this._destRings.length) {
        var ringStyle = this._getDestinationRingStyles();
        var cs = getComputedStyle(document.documentElement);
        this._destRings.forEach(function (ring) {
          var radius = ring.getRadius();
          var strokeVar = ringStyle.strokeByRadius[radius] || '--dest-ring-15km-stroke';
          var strokeColorRing = cs.getPropertyValue(strokeVar).trim() || 'rgba(255, 107, 107, 0.5)';
          ring.setStyle({
            color: strokeColorRing,
            fillColor: ringStyle.fillColor,
          });
        });
      }

      // 学区レイヤーの色も更新
      if (this._gakkuLayer) {
        var cs = getComputedStyle(document.documentElement);
        var sc = cs.getPropertyValue('--gakku-stroke').trim();
        var fc = cs.getPropertyValue('--gakku-fill').trim();
        if (this._gakkuGlow) {
          this._gakkuGlow.eachLayer(function (l) { l.setStyle({ color: sc, fillColor: fc }); });
        }
        this._gakkuLayer.eachLayer(function (l) { l.setStyle({ color: sc }); });
      }
    },

    // 学区境界（アニメーション破線 — Canvas描画で全域レンダリング）
    setGakkuData: function (geojson) {
      if (!geojson) return;
      this._gakkuGeojson = geojson;

      // Canvas レンダラー（padding大きめで画面外も事前描画）
      this._gakkuRendererGlow = L.canvas({ padding: 1.0 });
      this._gakkuRendererDash = L.canvas({ padding: 1.0 });

      var style = getComputedStyle(document.documentElement);
      var strokeColor = style.getPropertyValue('--gakku-stroke').trim() || 'rgba(255,120,0,0.9)';
      var fillColor = style.getPropertyValue('--gakku-fill').trim() || 'rgba(255,100,0,0.10)';

      // 太い背景線（グロー効果）
      this._gakkuGlow = L.geoJSON(geojson, {
        renderer: this._gakkuRendererGlow,
        style: {
          fillColor: fillColor,
          fillOpacity: 0.12,
          color: strokeColor,
          weight: 8,
          opacity: 0.25,
          lineCap: 'round',
          lineJoin: 'round',
        },
      });

      // アニメーションする破線（前面）
      this._gakkuLayer = L.geoJSON(geojson, {
        renderer: this._gakkuRendererDash,
        style: {
          fill: false,
          color: strokeColor,
          weight: 3,
          opacity: 0.9,
          dashArray: '12, 8',
          dashOffset: '0',
          lineCap: 'round',
        },
      });

      // パン中はアニメーション一時停止
      var self = this;
      this._map.on('movestart', function () { self._pauseGakkuAnim = true; });
      this._map.on('moveend', function () { self._pauseGakkuAnim = false; });
    },

    _animateGakku: function () {
      if (!this._gakkuLayer) return;
      var offset = 0;
      var self = this;
      function step() {
        if (!self._pauseGakkuAnim) {
          offset = (offset + 0.3) % 20;
          self._gakkuLayer.eachLayer(function (layer) {
            layer.setStyle({ dashOffset: String(-offset) });
          });
        }
        self._gakkuAnimId = requestAnimationFrame(step);
      }
      step();
    },

    _stopGakkuAnim: function () {
      if (this._gakkuAnimId) {
        cancelAnimationFrame(this._gakkuAnimId);
        this._gakkuAnimId = null;
      }
    },

    setGakkuVisible: function (visible) {
      if (!this._gakkuLayer) return;
      if (visible) {
        if (this._gakkuGlow && !this._map.hasLayer(this._gakkuGlow)) {
          this._gakkuGlow.addTo(this._map);
        }
        if (!this._map.hasLayer(this._gakkuLayer)) {
          this._gakkuLayer.addTo(this._map);
        }
        this._animateGakku();
      } else {
        this._stopGakkuAnim();
        if (this._gakkuGlow && this._map.hasLayer(this._gakkuGlow)) {
          this._map.removeLayer(this._gakkuGlow);
        }
        if (this._map.hasLayer(this._gakkuLayer)) {
          this._map.removeLayer(this._gakkuLayer);
        }
      }
    },
  };

  window.MarkerManager = MarkerManager;
})();
