// ============================================================
// precomputed-grid.js — switchable precomputed scalar-field loader / sampler
// ============================================================
(function () {
  'use strict';

  var PrecomputedGrid = {
    meta: null,
    values: null,
    ready: false,
    mode: 'idw',
    _grids: {},

    load: function () {
      var self = this;
      var idwVersion = (window.CONFIG && CONFIG.gridVersion) || 1;
      var accessVersion = (window.CONFIG && CONFIG.accessGridVersion) || 1;

      return Promise.all([
        self._loadGrid('idw', 'idw-grid', idwVersion),
        self._loadGrid('access', 'access-grid', accessVersion).catch(function (err) {
          // Keep the existing map usable while a newly introduced access grid is
          // being deployed or when an old cached Pages deployment lacks it.
          console.warn('徒歩アクセス考慮グリッドを読み込めないためIDWのみ使用します:', err);
          return null;
        })
      ]).then(function (loaded) {
        self._grids.idw = loaded[0];
        if (loaded[1]) self._grids.access = loaded[1];
        var preferred = (window.CONFIG && CONFIG.defaultInterpolationMode) || 'access';
        self.setMode(preferred);
        return self;
      });
    },

    _loadGrid: function (mode, baseName, version) {
      var self = this;
      var metaUrl = 'data/' + baseName + '.meta.json?v=' + encodeURIComponent(version);
      return fetch(metaUrl, { cache: 'no-cache' }).then(function (res) {
        if (!res.ok) throw new Error(baseName + '.meta.json: ' + res.status);
        return res.json();
      }).then(function (meta) {
        return self._loadData(baseName, version).then(function (buffer) {
          var expectedBytes = meta.rows * meta.cols * 2;
          if (buffer.byteLength !== expectedBytes) {
            throw new Error(baseName + ' data size mismatch: expected ' + expectedBytes + ', got ' + buffer.byteLength);
          }
          return {
            mode: mode,
            meta: meta,
            values: self._decodeUint16LE(buffer),
            ready: true
          };
        });
      });
    },

    _loadData: function (baseName, version) {
      var suffix = '?v=' + encodeURIComponent(version);
      var rawUrl = 'data/' + baseName + '.bin' + suffix;
      var gzipUrl = 'data/' + baseName + '.bin.gz' + suffix;
      var loadRaw = function () {
        return fetch(rawUrl, { cache: 'no-cache' }).then(function (res) {
          if (!res.ok) throw new Error(baseName + '.bin: ' + res.status);
          return res.arrayBuffer();
        });
      };

      if (typeof DecompressionStream !== 'function') return loadRaw();
      return fetch(gzipUrl, { cache: 'no-cache' }).then(function (res) {
        if (!res.ok) throw new Error(baseName + '.bin.gz: ' + res.status);
        return res.arrayBuffer();
      }).then(function (buffer) {
        var bytes = new Uint8Array(buffer);
        if (bytes.length < 2 || bytes[0] !== 0x1f || bytes[1] !== 0x8b) return buffer;
        var stream = new Blob([buffer]).stream().pipeThrough(new DecompressionStream('gzip'));
        return new Response(stream).arrayBuffer();
      }).catch(function (err) {
        console.warn('圧縮' + baseName + 'を読み込めなかったため非圧縮版へフォールバックします:', err);
        return loadRaw();
      });
    },

    _decodeUint16LE: function (buffer) {
      var probe = new Uint8Array(new Uint16Array([0x1234]).buffer);
      var littleEndian = probe[0] === 0x34;
      if (littleEndian) return new Uint16Array(buffer);
      var view = new DataView(buffer);
      var values = new Uint16Array(buffer.byteLength / 2);
      for (var i = 0; i < values.length; i++) values[i] = view.getUint16(i * 2, true);
      return values;
    },

    setMode: function (mode) {
      if (mode !== 'idw' && mode !== 'access') mode = 'access';
      if (!this._grids[mode]) mode = 'idw';
      var grid = this._grids[mode];
      this.mode = mode;
      this.meta = grid ? grid.meta : null;
      this.values = grid ? grid.values : null;
      this.ready = !!(grid && grid.ready);
      return this.mode;
    },

    hasMode: function (mode) {
      return !!(this._grids[mode] && this._grids[mode].ready);
    },

    contains: function (lat, lng) {
      var m = this.meta;
      return !!m && lat >= m.south && lat <= m.north && lng >= m.west && lng <= m.east;
    },

    sample: function (lat, lng) {
      var m = this.meta;
      var values = this.values;
      if (!m || !values || !this.contains(lat, lng)) return null;

      var row = (m.north - lat) / m.latStep;
      var col = (lng - m.west) / m.lngStep;
      var r0 = Math.floor(row);
      var c0 = Math.floor(col);
      if (r0 < 0 || c0 < 0) return null;
      if (r0 >= m.rows - 1) r0 = m.rows - 2;
      if (c0 >= m.cols - 1) c0 = m.cols - 2;
      var fr = Math.max(0, Math.min(1, row - r0));
      var fc = Math.max(0, Math.min(1, col - c0));
      var i00 = r0 * m.cols + c0;
      var v00 = values[i00], v10 = values[i00 + 1];
      var v01 = values[i00 + m.cols], v11 = values[i00 + m.cols + 1];
      if (v00 === m.nodata || v10 === m.nodata || v01 === m.nodata || v11 === m.nodata) return null;
      var top = v00 + (v10 - v00) * fc;
      var bottom = v01 + (v11 - v01) * fc;
      var encoded = (top + (bottom - top) * fr) / m.scale;
      return encoded - (m.offsetMinutes || 0);
    },
  };

  window.PrecomputedGrid = PrecomputedGrid;
})();
