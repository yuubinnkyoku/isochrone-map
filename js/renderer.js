// ============================================================
// renderer.js — precomputed IDW grid -> contours / gradient Canvas
// ============================================================
(function () {
  'use strict';

  function buildContourBreaks(interval) {
    var breaks = [];
    var range = CONFIG.timeRange;
    var min = range.contourMin;
    var max = range.contourMax;
    var denseMin = range.denseContourMin || min;
    var earlyInterval = range.earlyContourInterval || 60;

    // Early-morning / previous-day values can be many hours away from the
    // normal school-morning scale. Keep those lines sparse so the map remains
    // readable, while retaining the user-selected interval from 06:30 onward.
    if (min < denseMin) {
      var earlyFirst = Math.ceil(min / earlyInterval) * earlyInterval;
      for (var e = earlyFirst; e < denseMin; e += earlyInterval) breaks.push(e);
    }
    var first = Math.ceil(Math.max(min, denseMin) / interval) * interval;
    for (var m = first; m <= max; m += interval) breaks.push(m);
    return breaks;
  }

  function buildRenderState(map, step) {
    var sz = map.getSize();
    var pad = CONFIG.canvasPadding;
    var padX = Math.round(sz.x * pad);
    var padY = Math.round(sz.y * pad);
    var width = sz.x + padX * 2;
    var height = sz.y + padY * 2;
    return {
      cvWidth: width,
      cvHeight: height,
      pos: map.containerPointToLayerPoint([-padX, -padY]),
      renderZoom: map.getZoom(),
      renderTopLeft: map.containerPointToLatLng([-padX, -padY]),
      padX: padX,
      padY: padY,
      step: step,
      cols: Math.ceil(width / step) + 1,
      rows: Math.ceil(height / step) + 1,
    };
  }

  // Web Mercator is separable: longitude depends only on x and latitude only on y.
  // Convert one coordinate per column/row, then bilinearly sample the static grid.
  function sampleCanvasGrid(map, state, gridData) {
    var cols = state.cols;
    var rows = state.rows;
    var step = state.step;
    var lngs = new Float64Array(cols);
    var lats = new Float64Array(rows);
    var c, r;

    for (c = 0; c < cols; c++) {
      lngs[c] = map.containerPointToLatLng([c * step - state.padX, 0]).lng;
    }
    for (r = 0; r < rows; r++) {
      lats[r] = map.containerPointToLatLng([0, r * step - state.padY]).lat;
    }

    var values = new Float32Array(cols * rows);
    for (r = 0; r < rows; r++) {
      var lat = lats[r];
      var base = r * cols;
      for (c = 0; c < cols; c++) {
        var value = gridData.sample(lat, lngs[c]);
        values[base + c] = value === null ? NaN : value;
      }
    }
    return values;
  }

  var ContourOverlay = L.Layer.extend({
    options: { pane: 'overlayPane' },

    initialize: function (opts) {
      this._gridData = opts.grid || null;
      this._interval = opts.interval || 5;
      this._visible = opts.visible !== false;
      this._debounceTimer = null;
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
    setGrid: function (grid) { this._gridData = grid; this._render(); },
    refresh: function () { if (this._map) this._render(); },

    _clear: function () {
      if (!this._cv) return;
      this._cv.getContext('2d').clearRect(0, 0, this._cv.width, this._cv.height);
    },

    _render: function () {
      var map = this._map;
      if (!map) return;
      if (!this._visible || !this._gridData || !this._gridData.ready) {
        this._clear();
        return;
      }

      var state = buildRenderState(map, CONFIG.gridSize(map.getZoom()));
      var values = sampleCanvasGrid(map, state, this._gridData);
      this._drawContours(values, state);
    },

    _drawContours: function (grid, state) {
      if (!this._visible || !this._map) return;
      var cv = this._cv;
      cv.width = state.cvWidth;
      cv.height = state.cvHeight;
      L.DomUtil.setTransform(cv, state.pos, 1);
      this._renderZoom = state.renderZoom;
      this._renderTopLeft = state.renderTopLeft;

      var ctx = cv.getContext('2d');
      ctx.clearRect(0, 0, cv.width, cv.height);
      var rows = state.rows, cols = state.cols, cell = state.step;
      var breaks = buildContourBreaks(this._interval);
      var r, c;

      for (var bi = 0; bi < breaks.length; bi++) {
        var level = breaks[bi];
        var col = minutesToColor(level);
        var denseMin = CONFIG.timeRange.denseContourMin || CONFIG.timeRange.contourMin;
        var isEarly = level < denseMin;
        var isMain = isEarly ? (level % 60 === 0) : (level % 10 === 0);
        ctx.strokeStyle = colorToCSS(col, isMain ? 0.9 : 0.45);
        ctx.lineWidth = isMain ? 3 : 1.2;
        ctx.beginPath();

        for (r = 0; r < rows - 1; r++) {
          for (c = 0; c < cols - 1; c++) {
            var v00 = grid[r * cols + c], v10 = grid[r * cols + c + 1];
            var v01 = grid[(r + 1) * cols + c], v11 = grid[(r + 1) * cols + c + 1];
            if (!Number.isFinite(v00) || !Number.isFinite(v10) || !Number.isFinite(v01) || !Number.isFinite(v11)) continue;
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
              if (!Number.isFinite(v) || !Number.isFinite(vn)) continue;
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
      this._gridData = opts.grid || null;
      this._visible = opts.visible || false;
      this._debounceTimer = null;
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
    setGrid: function (grid) { this._gridData = grid; this._render(); },
    refresh: function () { if (this._map) this._render(); },

    _clear: function () {
      if (!this._cv) return;
      this._cv.getContext('2d').clearRect(0, 0, this._cv.width, this._cv.height);
    },

    _render: function () {
      var map = this._map;
      if (!map) return;
      if (!this._visible || !this._gridData || !this._gridData.ready) {
        this._clear();
        return;
      }

      var sz = map.getSize();
      var step = Math.max(4, Math.floor(Math.min(sz.x, sz.y) / 180));
      var state = buildRenderState(map, step);
      var values = sampleCanvasGrid(map, state, this._gridData);
      this._drawGradient(values, state);
    },

    _drawGradient: function (values, state) {
      if (!this._visible || !this._map) return;
      var cv = this._cv;
      cv.width = state.cvWidth;
      cv.height = state.cvHeight;
      L.DomUtil.setTransform(cv, state.pos, 1);
      this._renderZoom = state.renderZoom;
      this._renderTopLeft = state.renderTopLeft;

      var ctx = cv.getContext('2d');
      ctx.clearRect(0, 0, cv.width, cv.height);
      var step = state.step;
      var cols = state.cols;
      var rows = state.rows;

      for (var r = 0; r < rows; r++) {
        var y = r * step;
        for (var c = 0; c < cols; c++) {
          var x = c * step;
          var val = values[r * cols + c];
          if (Number.isFinite(val)) {
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
