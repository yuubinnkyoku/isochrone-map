// ============================================================
// renderer.js — IDW補間 → マーチングスクエア → Canvas等時線描画
//               IDW補間 → Canvas グラデーション描画
// ============================================================
(function () {
  'use strict';

  function idw(lat, lng, stations, power) {
    if (!power) power = CONFIG.idwPower;
    var num = 0, den = 0;
    var cosLat = Math.cos(lat * Math.PI / 180);
    var halfPower = power / 2;
    for (var i = 0; i < stations.length; i++) {
      var s = stations[i];
      var dlat = lat - s.lat;
      var dlng = (lng - s.lng) * cosLat;
      var distSq = dlat * dlat + dlng * dlng;
      if (distSq < 0.00000025) return s.minutes;
      var w = 1 / Math.pow(distSq, halfPower);
      num += w * s.minutes;
      den += w;
    }
    return den > 0 ? num / den : null;
  }

  function idwPx(x, y, pixStations, halfPower) {
    var num = 0, den = 0;
    for (var i = 0; i < pixStations.length; i++) {
      var s = pixStations[i];
      var dx = x - s.x;
      var dy = y - s.y;
      var distSq = dx * dx + dy * dy;
      if (distSq < 0.1) return s.minutes;
      var w = 1 / Math.pow(distSq, halfPower);
      num += w * s.minutes;
      den += w;
    }
    return den > 0 ? num / den : null;
  }

  function buildContourBreaks(interval) {
    var breaks = [];
    var min = CONFIG.timeRange.contourMin;
    var max = CONFIG.timeRange.contourMax;
    var first = Math.ceil(min / interval) * interval;
    for (var m = first; m <= max; m += interval) breaks.push(m);
    return breaks;
  }

  var ContourOverlay = L.Layer.extend({
    options: { pane: 'overlayPane' },

    initialize: function (opts) {
      this._stations = opts.stations || [];
      this._interval = opts.interval || 5;
      this._visible = opts.visible !== false;
      this._debounceTimer = null;
      this._requestId = 0;
      this._worker = new Worker('js/worker.js?v=11');
      this._worker.onmessage = this._onWorkerMessage.bind(this);
    },

    _onWorkerMessage: function (e) {
      if (e.data.type === 'GRID_READY') {
        if (e.data.id !== this._requestId) return;
        this._drawContours(e.data.grid);
      }
    },

    onAdd: function (map) {
      this._map = map;
      this._cv = L.DomUtil.create('canvas', 'leaflet-layer leaflet-zoom-animated');
      Object.assign(this._cv.style, { position: 'absolute', pointerEvents: 'none', zIndex: '250' });
      this._cv.style.willChange = 'transform';
      map.getPanes().overlayPane.appendChild(this._cv);
      map.on('moveend zoomend resize', this._debouncedRender, this);
      map.on('zoomanim', this._onZoomAnim, this);
      this._render();
    },

    onRemove: function (map) {
      L.DomUtil.remove(this._cv);
      map.off('moveend zoomend resize', this._debouncedRender, this);
      map.off('zoomanim', this._onZoomAnim, this);
    },

    _onZoomAnim: function (e) {
      var map = this._map;
      if (!this._renderZoom) return;
      var scale = map.getZoomScale(e.zoom, this._renderZoom);
      var newPos = map._latLngToNewLayerPoint(this._renderTopLeft, e.zoom, e.center);
      L.DomUtil.setTransform(this._cv, newPos, scale);
    },

    _debouncedRender: function () {
      var self = this;
      if (this._debounceTimer) clearTimeout(this._debounceTimer);
      this._debounceTimer = setTimeout(function () { self._render(); }, CONFIG.renderDebounceMs);
    },

    setVisible: function (v) { this._visible = v; this._render(); },
    setInterval: function (interval) { this._interval = interval; this._render(); },
    setStations: function (stations) { this._stations = stations; this._render(); },
    refresh: function () { if (this._map) this._render(); },

    _render: function () {
      var map = this._map;
      if (!map) return;
      var sz = map.getSize();
      var pad = CONFIG.canvasPadding;
      var padX = Math.round(sz.x * pad), padY = Math.round(sz.y * pad);
      var pos = map.containerPointToLayerPoint([-padX, -padY]);
      var renderZoom = map.getZoom();
      var renderTopLeft = map.containerPointToLatLng([-padX, -padY]);

      if (!this._visible || this._stations.length === 0) {
        var ctx = this._cv.getContext('2d');
        ctx.clearRect(0, 0, this._cv.width, this._cv.height);
        return;
      }

      var cvWidth = sz.x + padX * 2;
      var cvHeight = sz.y + padY * 2;
      var cell = CONFIG.gridSize(map.getZoom());
      var cols = Math.ceil(cvWidth / cell) + 1;
      var rows = Math.ceil(cvHeight / cell) + 1;
      var stations = this._stations;
      var halfPower = CONFIG.idwPower / 2;
      var pixStations = new Array(stations.length);

      for (var i = 0; i < stations.length; i++) {
        var s = stations[i];
        var p = map.latLngToContainerPoint([s.lat, s.lng]);
        pixStations[i] = { x: p.x + padX, y: p.y + padY, minutes: s.minutes };
      }

      this._requestId++;
      this._worker.postMessage({
        type: 'CALC_GRID',
        id: this._requestId,
        payload: { cols: cols, rows: rows, cell: cell, pixStations: pixStations, halfPower: halfPower }
      });

      this._pendingRenderState = {
        cvWidth: cvWidth, cvHeight: cvHeight, pos: pos,
        renderZoom: renderZoom, renderTopLeft: renderTopLeft,
        cell: cell, cols: cols, rows: rows
      };
    },

    _drawContours: function (grid) {
      if (!this._visible || !this._map) return;
      var state = this._pendingRenderState;
      if (!state) return;

      var cv = this._cv;
      cv.width = state.cvWidth;
      cv.height = state.cvHeight;
      L.DomUtil.setTransform(cv, state.pos, 1);
      this._renderZoom = state.renderZoom;
      this._renderTopLeft = state.renderTopLeft;

      var ctx = cv.getContext('2d');
      ctx.clearRect(0, 0, cv.width, cv.height);
      var rows = state.rows, cols = state.cols, cell = state.cell;
      var breaks = buildContourBreaks(this._interval);
      var r, c;

      for (var bi = 0; bi < breaks.length; bi++) {
        var level = breaks[bi];
        var col = minutesToColor(level);
        var isMain = (level % 10 === 0);
        ctx.strokeStyle = colorToCSS(col, isMain ? 0.9 : 0.45);
        ctx.lineWidth = isMain ? 3 : 1.2;
        ctx.beginPath();

        for (r = 0; r < rows - 1; r++) {
          for (c = 0; c < cols - 1; c++) {
            var v00 = grid[r * cols + c], v10 = grid[r * cols + c + 1];
            var v01 = grid[(r + 1) * cols + c], v11 = grid[(r + 1) * cols + c + 1];
            var ci = (v00 >= level ? 8 : 0) | (v10 >= level ? 4 : 0) | (v11 >= level ? 2 : 0) | (v01 >= level ? 1 : 0);
            if (ci === 0 || ci === 15) continue;

            var x0 = c * cell, y0 = r * cell;
            var lx = function (va, vb) { return x0 + (level - va) / (vb - va) * cell; };
            var ly = function (va, vb) { return y0 + (level - va) / (vb - va) * cell; };
            var tTop = [lx(v00, v10), y0];
            var tBot = [lx(v01, v11), y0 + cell];
            var tLft = [x0, ly(v00, v01)];
            var tRgt = [x0 + cell, ly(v10, v11)];
            var segs = [];

            switch (ci) {
              case 1: case 14: segs.push([tLft, tBot]); break;
              case 2: case 13: segs.push([tBot, tRgt]); break;
              case 3: case 12: segs.push([tLft, tRgt]); break;
              case 4: case 11: segs.push([tTop, tRgt]); break;
              case 5: segs.push([tLft, tTop], [tBot, tRgt]); break;
              case 6: case 9: segs.push([tTop, tBot]); break;
              case 7: case 8: segs.push([tLft, tTop]); break;
              case 10: segs.push([tTop, tRgt], [tLft, tBot]); break;
            }
            for (var si = 0; si < segs.length; si++) {
              ctx.moveTo(segs[si][0][0], segs[si][0][1]);
              ctx.lineTo(segs[si][1][0], segs[si][1][1]);
            }
          }
        }
        ctx.stroke();

        if (isMain) {
          var txt = minutesToTimeStr(level);
          ctx.font = '600 11px "JetBrains Mono", monospace';
          var placed = 0;
          var rowStep = Math.max(1, Math.floor(rows / 4));
          for (r = rowStep; r < rows - 1 && placed < 3; r += rowStep) {
            for (c = 0; c < cols - 1 && placed < 3; c++) {
              var v = grid[r * cols + c], vn = grid[r * cols + c + 1];
              if ((v < level) !== (vn < level)) {
                var px = c * cell, py = r * cell;
                var tw = ctx.measureText(txt).width;
                var labelBg = getComputedStyle(document.documentElement).getPropertyValue('--contour-label-bg').trim() || 'rgba(255,255,255,0.82)';
                ctx.fillStyle = labelBg;
                ctx.fillRect(px - 2, py - 11, tw + 4, 14);
                ctx.fillStyle = colorToCSS(col, 1);
                ctx.fillText(txt, px, py);
                placed++;
                c += Math.floor(cols / 4);
              }
            }
          }
        }
      }
    }
  });

  var GradientOverlay = L.Layer.extend({
    options: { pane: 'overlayPane' },

    initialize: function (opts) {
      this._stations = opts.stations || [];
      this._visible = opts.visible || false;
      this._debounceTimer = null;
      this._requestId = 0;
      this._worker = new Worker('js/worker.js?v=11');
      this._worker.onmessage = this._onWorkerMessage.bind(this);
    },

    _onWorkerMessage: function (e) {
      if (e.data.type === 'GRADIENT_READY') {
        if (e.data.id !== this._requestId) return;
        this._drawGradient(e.data.values);
      }
    },

    onAdd: function (map) {
      this._map = map;
      this._cv = L.DomUtil.create('canvas', 'leaflet-layer leaflet-zoom-animated');
      Object.assign(this._cv.style, { position: 'absolute', pointerEvents: 'none', zIndex: '200' });
      this._cv.style.willChange = 'transform';
      map.getPanes().overlayPane.appendChild(this._cv);
      map.on('moveend zoomend resize', this._debouncedRender, this);
      map.on('zoomanim', this._onZoomAnim, this);
      this._render();
    },

    onRemove: function (map) {
      L.DomUtil.remove(this._cv);
      map.off('moveend zoomend resize', this._debouncedRender, this);
      map.off('zoomanim', this._onZoomAnim, this);
    },

    _onZoomAnim: function (e) {
      var map = this._map;
      if (!this._renderZoom) return;
      var scale = map.getZoomScale(e.zoom, this._renderZoom);
      var newPos = map._latLngToNewLayerPoint(this._renderTopLeft, e.zoom, e.center);
      L.DomUtil.setTransform(this._cv, newPos, scale);
    },

    _debouncedRender: function () {
      var self = this;
      if (this._debounceTimer) clearTimeout(this._debounceTimer);
      this._debounceTimer = setTimeout(function () { self._render(); }, CONFIG.renderDebounceMs);
    },

    setVisible: function (v) { this._visible = v; this._render(); },
    setStations: function (stations) { this._stations = stations; this._render(); },
    refresh: function () { if (this._map) this._render(); },

    _render: function () {
      var map = this._map;
      if (!map) return;
      var sz = map.getSize();
      var pad = CONFIG.canvasPadding;
      var padX = Math.round(sz.x * pad), padY = Math.round(sz.y * pad);
      var pos = map.containerPointToLayerPoint([-padX, -padY]);
      var renderZoom = map.getZoom();
      var renderTopLeft = map.containerPointToLatLng([-padX, -padY]);

      if (!this._visible || this._stations.length === 0) {
        var ctx = this._cv.getContext('2d');
        ctx.clearRect(0, 0, this._cv.width, this._cv.height);
        return;
      }

      var cvWidth = sz.x + padX * 2;
      var cvHeight = sz.y + padY * 2;
      var step = Math.max(4, Math.floor(Math.min(sz.x, sz.y) / 180));
      var halfPower = CONFIG.idwPower / 2;
      var pixStations = new Array(this._stations.length);

      for (var i = 0; i < this._stations.length; i++) {
        var s = this._stations[i];
        var p = map.latLngToContainerPoint([s.lat, s.lng]);
        pixStations[i] = { x: p.x + padX, y: p.y + padY, minutes: s.minutes };
      }

      this._requestId++;
      this._worker.postMessage({
        type: 'CALC_GRADIENT', id: this._requestId,
        payload: { width: cvWidth, height: cvHeight, step: step, pixStations: pixStations, halfPower: halfPower }
      });

      this._pendingRenderState = {
        cvWidth: cvWidth, cvHeight: cvHeight, pos: pos,
        renderZoom: renderZoom, renderTopLeft: renderTopLeft, step: step
      };
    },

    _drawGradient: function (values) {
      if (!this._visible || !this._map) return;
      var state = this._pendingRenderState;
      if (!state) return;

      var cv = this._cv;
      cv.width = state.cvWidth;
      cv.height = state.cvHeight;
      L.DomUtil.setTransform(cv, state.pos, 1);
      this._renderZoom = state.renderZoom;
      this._renderTopLeft = state.renderTopLeft;

      var ctx = cv.getContext('2d');
      ctx.clearRect(0, 0, cv.width, cv.height);
      var step = state.step;
      var cols = Math.ceil(cv.width / step) + 1;
      var rows = Math.ceil(cv.height / step) + 1;

      for (var r = 0; r < rows; r++) {
        var y = r * step;
        for (var c = 0; c < cols; c++) {
          var x = c * step;
          var val = values[r * cols + c];
          if (val && val !== 0) {
            ctx.fillStyle = colorToCSS(minutesToColor(val), 0.28);
            ctx.fillRect(x, y, step, step);
          }
        }
      }
    }
  });

  window.ContourOverlay = ContourOverlay;
  window.GradientOverlay = GradientOverlay;
})();
