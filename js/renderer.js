// ============================================================
// renderer.js — precomputed scalar grid -> Leaflet-managed canvas tiles
// ============================================================
(function () {
  'use strict';

  var TILE_SIZE = 256;
  var GRADIENT_ALPHA = Math.round(255 * 0.28);
  var COLOR_LUT_STEPS_PER_MINUTE = 4;
  var colorLut = null;
  var tileQueue = [];
  var queueScheduled = false;

  function buildContourBreaks(interval) {
    var breaks = [];
    var seen = {};
    var range = CONFIG.timeRange;
    var min = range.contourMin;
    var max = range.contourMax;
    var denseMin = range.denseContourMin || min;
    var earlyInterval = range.earlyContourInterval || 60;

    function addBreak(value) {
      if (value < min || value > max || seen[value]) return;
      seen[value] = true;
      breaks.push(value);
    }

    if (min < denseMin) {
      var earlyFirst = Math.ceil(min / earlyInterval) * earlyInterval;
      for (var e = earlyFirst; e < denseMin; e += earlyInterval) addBreak(e);
    }

    var first = Math.ceil(Math.max(min, denseMin) / interval) * interval;
    for (var m = first; m <= max; m += interval) addBreak(m);

    // Keep labelled 10-minute anchors present for intervals such as 3 minutes.
    var mainFirst = Math.ceil(Math.max(min, denseMin) / 10) * 10;
    for (var main = mainFirst; main <= max; main += 10) addBreak(main);

    breaks.sort(function (a, b) { return a - b; });
    return breaks;
  }

  function scheduleTileRender(fn) {
    tileQueue.push(fn);
    if (queueScheduled) return;
    queueScheduled = true;
    requestAnimationFrame(runTileQueue);
  }

  function runTileQueue() {
    queueScheduled = false;
    var started = performance.now();

    // Keep a frame responsive even when several new tiles become visible at once.
    while (tileQueue.length && performance.now() - started < 7) {
      var fn = tileQueue.shift();
      fn();
    }

    if (tileQueue.length) {
      queueScheduled = true;
      requestAnimationFrame(runTileQueue);
    }
  }

  function worldSize(zoom) {
    return TILE_SIZE * Math.pow(2, zoom);
  }

  function longitudeAtWorldX(x, zoom) {
    return x / worldSize(zoom) * 360 - 180;
  }

  function latitudeAtWorldY(y, zoom) {
    var size = worldSize(zoom);
    var n = Math.PI - 2 * Math.PI * y / size;
    return Math.atan(Math.sinh(n)) * 180 / Math.PI;
  }

  function tileIntersectsGrid(meta, coords) {
    if (!meta) return false;
    var left = coords.x * TILE_SIZE;
    var top = coords.y * TILE_SIZE;
    var west = longitudeAtWorldX(left, coords.z);
    var east = longitudeAtWorldX(left + TILE_SIZE, coords.z);
    var north = latitudeAtWorldY(top, coords.z);
    var south = latitudeAtWorldY(top + TILE_SIZE, coords.z);
    return !(east < meta.west || west > meta.east || south > meta.north || north < meta.south);
  }

  function buildLongitudeAxis(meta, coords, count, step, centered) {
    var indices = new Int32Array(count);
    var fractions = new Float32Array(count);
    var origin = coords.x * TILE_SIZE;

    for (var i = 0; i < count; i++) {
      var local = i * step + (centered ? step / 2 : 0);
      var lng = longitudeAtWorldX(origin + local, coords.z);
      var col = (lng - meta.west) / meta.lngStep;
      if (col < 0 || col > meta.cols - 1) {
        indices[i] = -1;
        continue;
      }
      var c0 = Math.floor(col);
      if (c0 >= meta.cols - 1) c0 = meta.cols - 2;
      indices[i] = c0;
      fractions[i] = Math.max(0, Math.min(1, col - c0));
    }
    return { indices: indices, fractions: fractions };
  }

  function buildLatitudeAxis(meta, coords, count, step, centered) {
    var indices = new Int32Array(count);
    var fractions = new Float32Array(count);
    var origin = coords.y * TILE_SIZE;

    for (var i = 0; i < count; i++) {
      var local = i * step + (centered ? step / 2 : 0);
      var lat = latitudeAtWorldY(origin + local, coords.z);
      var row = (meta.north - lat) / meta.latStep;
      if (row < 0 || row > meta.rows - 1) {
        indices[i] = -1;
        continue;
      }
      var r0 = Math.floor(row);
      if (r0 >= meta.rows - 1) r0 = meta.rows - 2;
      indices[i] = r0;
      fractions[i] = Math.max(0, Math.min(1, row - r0));
    }
    return { indices: indices, fractions: fractions };
  }

  function samplePrepared(meta, values, row0, fr, col0, fc) {
    if (row0 < 0 || col0 < 0) return NaN;
    var base = row0 * meta.cols + col0;
    var v00 = values[base];
    var v10 = values[base + 1];
    var v01 = values[base + meta.cols];
    var v11 = values[base + meta.cols + 1];
    if (v00 === meta.nodata || v10 === meta.nodata || v01 === meta.nodata || v11 === meta.nodata) return NaN;
    var top = v00 + (v10 - v00) * fc;
    var bottom = v01 + (v11 - v01) * fc;
    return (top + (bottom - top) * fr) / meta.scale - (meta.offsetMinutes || 0);
  }

  function sampleTileGrid(gridData, coords, step) {
    var meta = gridData.meta;
    var values = gridData.values;
    var count = Math.floor(TILE_SIZE / step) + 1;
    var xAxis = buildLongitudeAxis(meta, coords, count, step, false);
    var yAxis = buildLatitudeAxis(meta, coords, count, step, false);
    var samples = new Float32Array(count * count);

    for (var r = 0; r < count; r++) {
      var r0 = yAxis.indices[r];
      var fr = yAxis.fractions[r];
      var rowBase = r * count;
      for (var c = 0; c < count; c++) {
        samples[rowBase + c] = samplePrepared(
          meta,
          values,
          r0,
          fr,
          xAxis.indices[c],
          xAxis.fractions[c]
        );
      }
    }

    return { values: samples, count: count, step: step };
  }

  function ensureColorLut() {
    var first = CONFIG.colorStops[0].min;
    var last = CONFIG.colorStops[CONFIG.colorStops.length - 1].min;
    var key = first + ':' + last + ':' + COLOR_LUT_STEPS_PER_MINUTE;
    if (colorLut && colorLut.key === key) return colorLut;

    var count = Math.round((last - first) * COLOR_LUT_STEPS_PER_MINUTE) + 1;
    var rgba = new Uint8ClampedArray(count * 4);
    for (var i = 0; i < count; i++) {
      var minute = first + i / COLOR_LUT_STEPS_PER_MINUTE;
      var color = minutesToColor(minute);
      var j = i * 4;
      rgba[j] = color[0];
      rgba[j + 1] = color[1];
      rgba[j + 2] = color[2];
      rgba[j + 3] = GRADIENT_ALPHA;
    }

    colorLut = { key: key, min: first, max: last, rgba: rgba, count: count };
    return colorLut;
  }

  function writeGradientPixel(data, offset, minute, lut) {
    var index;
    if (minute <= lut.min) index = 0;
    else if (minute >= lut.max) index = lut.count - 1;
    else index = Math.round((minute - lut.min) * COLOR_LUT_STEPS_PER_MINUTE);
    var source = index * 4;
    data[offset] = lut.rgba[source];
    data[offset + 1] = lut.rgba[source + 1];
    data[offset + 2] = lut.rgba[source + 2];
    data[offset + 3] = lut.rgba[source + 3];
  }

  function drawGradientTile(tile, gridData, coords) {
    var meta = gridData.meta;
    if (!tileIntersectsGrid(meta, coords)) return;

    var ctx = tile.getContext('2d', { alpha: true });
    var image = ctx.createImageData(TILE_SIZE, TILE_SIZE);
    var data = image.data;
    var xAxis = buildLongitudeAxis(meta, coords, TILE_SIZE, 1, true);
    var yAxis = buildLatitudeAxis(meta, coords, TILE_SIZE, 1, true);
    var lut = ensureColorLut();

    for (var y = 0; y < TILE_SIZE; y++) {
      var r0 = yAxis.indices[y];
      var fr = yAxis.fractions[y];
      var pixelBase = y * TILE_SIZE * 4;
      for (var x = 0; x < TILE_SIZE; x++) {
        var col0 = xAxis.indices[x];
        if (r0 < 0 || col0 < 0) continue;
        var minute = samplePrepared(meta, gridData.values, r0, fr, col0, xAxis.fractions[x]);
        if (!Number.isFinite(minute)) continue;
        writeGradientPixel(data, pixelBase + x * 4, minute, lut);
      }
    }

    ctx.putImageData(image, 0, 0);
  }

  function contourStepForZoom(zoom) {
    // Tile edges must land on the same sample lattice. Both values divide 256,
    // so neighbouring tiles share identical boundary samples and never drift.
    return zoom <= 10 ? 8 : 4;
  }

  function interpolateEdge(level, a, b) {
    var den = b - a;
    if (Math.abs(den) < 1e-9) return 0.5;
    return Math.max(0, Math.min(1, (level - a) / den));
  }

  function shouldLabelContour(coords, level) {
    var divisor = coords.z <= 10 ? 2 : (coords.z <= 12 ? 3 : 4);
    var hash = Math.abs(coords.x * 31 + coords.y * 17 + Math.round(level) * 13);
    return hash % divisor === 0;
  }

  function appendContourSegment(ctx, a, b, candidate) {
    ctx.moveTo(a[0], a[1]);
    ctx.lineTo(b[0], b[1]);
    if (!candidate) return;

    var x = (a[0] + b[0]) / 2;
    var y = (a[1] + b[1]) / 2;
    if (x < 28 || x > TILE_SIZE - 28 || y < 16 || y > TILE_SIZE - 16) return;
    var dx = x - TILE_SIZE / 2;
    var dy = y - TILE_SIZE / 2;
    var distance = dx * dx + dy * dy;
    if (distance < candidate.distance) {
      candidate.distance = distance;
      candidate.x = x;
      candidate.y = y;
    }
  }

  function drawContourLabel(ctx, candidate, level, color) {
    if (!candidate || !Number.isFinite(candidate.x)) return;
    var text = minutesToTimeStr(level);
    ctx.save();
    ctx.font = '600 11px "JetBrains Mono", monospace';
    ctx.textBaseline = 'alphabetic';
    var width = ctx.measureText(text).width;
    var x = Math.max(3, Math.min(TILE_SIZE - width - 7, candidate.x - width / 2));
    var y = Math.max(13, Math.min(TILE_SIZE - 4, candidate.y + 4));
    var background = getComputedStyle(document.documentElement).getPropertyValue('--contour-label-bg').trim() || 'rgba(255,255,255,0.82)';
    ctx.fillStyle = background;
    ctx.fillRect(x - 3, y - 11, width + 6, 14);
    ctx.fillStyle = colorToCSS(color, 1);
    ctx.fillText(text, x, y);
    ctx.restore();
  }

  function drawContourTile(tile, gridData, coords, interval) {
    var meta = gridData.meta;
    if (!tileIntersectsGrid(meta, coords)) return;

    var step = contourStepForZoom(coords.z);
    var sampled = sampleTileGrid(gridData, coords, step);
    var grid = sampled.values;
    var count = sampled.count;
    var ctx = tile.getContext('2d', { alpha: true });
    var breaks = buildContourBreaks(interval);
    var denseMin = CONFIG.timeRange.denseContourMin || CONFIG.timeRange.contourMin;

    ctx.lineCap = 'butt';
    ctx.lineJoin = 'round';

    for (var bi = 0; bi < breaks.length; bi++) {
      var level = breaks[bi];
      var isEarly = level < denseMin;
      var isMain = isEarly ? (level % 60 === 0) : (level % 10 === 0);
      var color = minutesToColor(level);
      ctx.strokeStyle = colorToCSS(color, isMain ? 0.9 : 0.45);
      ctx.lineWidth = isMain ? 3 : 1.2;
      ctx.beginPath();
      var labelCandidate = isMain && shouldLabelContour(coords, level) ? { distance: Infinity, x: NaN, y: NaN } : null;

      for (var r = 0; r < count - 1; r++) {
        var row0 = r * count;
        var row1 = (r + 1) * count;
        var y0 = r * step;
        for (var c = 0; c < count - 1; c++) {
          var v00 = grid[row0 + c];
          var v10 = grid[row0 + c + 1];
          var v01 = grid[row1 + c];
          var v11 = grid[row1 + c + 1];
          if (!Number.isFinite(v00) || !Number.isFinite(v10) || !Number.isFinite(v01) || !Number.isFinite(v11)) continue;

          var code = (v00 >= level ? 8 : 0) |
            (v10 >= level ? 4 : 0) |
            (v11 >= level ? 2 : 0) |
            (v01 >= level ? 1 : 0);
          if (code === 0 || code === 15) continue;

          var x0 = c * step;
          var top = [x0 + interpolateEdge(level, v00, v10) * step, y0];
          var bottom = [x0 + interpolateEdge(level, v01, v11) * step, y0 + step];
          var left = [x0, y0 + interpolateEdge(level, v00, v01) * step];
          var right = [x0 + step, y0 + interpolateEdge(level, v10, v11) * step];

          switch (code) {
            case 1: case 14:
              appendContourSegment(ctx, left, bottom, labelCandidate); break;
            case 2: case 13:
              appendContourSegment(ctx, bottom, right, labelCandidate); break;
            case 3: case 12:
              appendContourSegment(ctx, left, right, labelCandidate); break;
            case 4: case 11:
              appendContourSegment(ctx, top, right, labelCandidate); break;
            case 5:
              appendContourSegment(ctx, left, top, labelCandidate);
              appendContourSegment(ctx, bottom, right, labelCandidate); break;
            case 6: case 9:
              appendContourSegment(ctx, top, bottom, labelCandidate); break;
            case 7: case 8:
              appendContourSegment(ctx, left, top, labelCandidate); break;
            case 10:
              appendContourSegment(ctx, top, right, labelCandidate);
              appendContourSegment(ctx, left, bottom, labelCandidate); break;
          }
        }
      }
      ctx.stroke();
      drawContourLabel(ctx, labelCandidate, level, color);
    }
  }

  function makeTileCanvas(className) {
    var tile = L.DomUtil.create('canvas', className);
    tile.width = TILE_SIZE;
    tile.height = TILE_SIZE;
    tile.style.width = TILE_SIZE + 'px';
    tile.style.height = TILE_SIZE + 'px';
    tile.style.pointerEvents = 'none';
    return tile;
  }

  function finishTile(done, error, tile) {
    if (typeof done === 'function') done(error || null, tile);
  }

  function syncLayerVisibility(layer) {
    var container = layer.getContainer && layer.getContainer();
    if (container) container.style.display = layer._visible ? '' : 'none';
  }

  var ContourOverlay = L.GridLayer.extend({
    options: {
      pane: 'overlayPane',
      tileSize: TILE_SIZE,
      zIndex: 250,
      opacity: 1,
      noWrap: true,
      keepBuffer: 2,
      updateWhenIdle: true,
      updateWhenZooming: false,
    },

    initialize: function (opts) {
      opts = opts || {};
      L.setOptions(this, opts);
      this._gridData = opts.grid || null;
      this._interval = opts.interval || 5;
      this._visible = opts.visible !== false;
      this._generation = 0;
    },

    onAdd: function (map) {
      L.GridLayer.prototype.onAdd.call(this, map);
      syncLayerVisibility(this);
    },

    createTile: function (coords, done) {
      var tile = makeTileCanvas('isochrone-contour-tile');
      var generation = this._generation;
      var self = this;

      scheduleTileRender(function () {
        try {
          if (generation === self._generation && self._visible && self._gridData && self._gridData.ready) {
            drawContourTile(tile, self._gridData, coords, self._interval);
          }
          finishTile(done, null, tile);
        } catch (err) {
          console.error('等時線タイル描画エラー:', err);
          finishTile(done, err, tile);
        }
      });
      return tile;
    },

    _invalidateTiles: function () {
      this._generation++;
      if (this._map) this.redraw();
    },

    setVisible: function (visible) {
      this._visible = !!visible;
      this._generation++;
      syncLayerVisibility(this);
      if (this._visible && this._map) this.redraw();
    },

    setInterval: function (interval) {
      this._interval = interval;
      this._invalidateTiles();
    },

    setGrid: function (grid) {
      this._gridData = grid;
      this._invalidateTiles();
    },

    refresh: function () {
      this._invalidateTiles();
    },
  });

  var GradientOverlay = L.GridLayer.extend({
    options: {
      pane: 'overlayPane',
      tileSize: TILE_SIZE,
      zIndex: 200,
      opacity: 1,
      noWrap: true,
      keepBuffer: 2,
      updateWhenIdle: true,
      updateWhenZooming: false,
    },

    initialize: function (opts) {
      opts = opts || {};
      L.setOptions(this, opts);
      this._gridData = opts.grid || null;
      this._visible = !!opts.visible;
      this._generation = 0;
    },

    onAdd: function (map) {
      L.GridLayer.prototype.onAdd.call(this, map);
      syncLayerVisibility(this);
    },

    createTile: function (coords, done) {
      var tile = makeTileCanvas('isochrone-gradient-tile');
      var generation = this._generation;
      var self = this;

      scheduleTileRender(function () {
        try {
          if (generation === self._generation && self._visible && self._gridData && self._gridData.ready) {
            drawGradientTile(tile, self._gridData, coords);
          }
          finishTile(done, null, tile);
        } catch (err) {
          console.error('グラデーションタイル描画エラー:', err);
          finishTile(done, err, tile);
        }
      });
      return tile;
    },

    _invalidateTiles: function () {
      this._generation++;
      if (this._map) this.redraw();
    },

    setVisible: function (visible) {
      this._visible = !!visible;
      this._generation++;
      syncLayerVisibility(this);
      if (this._visible && this._map) this.redraw();
    },

    setGrid: function (grid) {
      this._gridData = grid;
      this._invalidateTiles();
    },

    refresh: function () {
      this._invalidateTiles();
    },
  });

  window.ContourOverlay = ContourOverlay;
  window.GradientOverlay = GradientOverlay;
})();
