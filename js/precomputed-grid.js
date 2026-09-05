// ============================================================
// precomputed-grid.js — precomputed IDW scalar field loader / sampler
// ============================================================
(function () {
  'use strict';

  var PrecomputedGrid = {
    meta: null,
    values: null,
    ready: false,

    load: function () {
      var self = this;
      var version = (window.CONFIG && CONFIG.gridVersion) || 1;
      var metaUrl = 'data/idw-grid.meta.json?v=' + encodeURIComponent(version);
      return fetch(metaUrl, { cache: 'no-cache' }).then(function (res) {
        if (!res.ok) throw new Error('idw-grid.meta.json: ' + res.status);
        return res.json();
      }).then(function (meta) {
        return self._loadData(version).then(function (buffer) {
          var expectedBytes = meta.rows * meta.cols * 2;
          if (buffer.byteLength !== expectedBytes) {
            throw new Error('idw-grid data size mismatch: expected ' + expectedBytes + ', got ' + buffer.byteLength);
          }
          self.meta = meta;
          self.values = self._decodeUint16LE(buffer);
          self.ready = true;
          return self;
        });
      });
    },

    _loadData: function (version) {
      var suffix = '?v=' + encodeURIComponent(version);
      var rawUrl = 'data/idw-grid.bin' + suffix;
      var gzipUrl = 'data/idw-grid.bin.gz' + suffix;
      var loadRaw = function () {
        return fetch(rawUrl, { cache: 'no-cache' }).then(function (res) {
          if (!res.ok) throw new Error('idw-grid.bin: ' + res.status);
          return res.arrayBuffer();
        });
      };

      // Modern browsers can unpack the ~0.6 MB gzip payload locally.  If the
      // server already applies Content-Encoding, fetch() returns the decoded
      // bytes and the gzip magic check simply skips the second decompression.
      if (typeof DecompressionStream !== 'function') return loadRaw();
      return fetch(gzipUrl, { cache: 'no-cache' }).then(function (res) {
        if (!res.ok) throw new Error('idw-grid.bin.gz: ' + res.status);
        return res.arrayBuffer();
      }).then(function (buffer) {
        var bytes = new Uint8Array(buffer);
        if (bytes.length < 2 || bytes[0] !== 0x1f || bytes[1] !== 0x8b) return buffer;
        var stream = new Blob([buffer]).stream().pipeThrough(new DecompressionStream('gzip'));
        return new Response(stream).arrayBuffer();
      }).catch(function (err) {
        console.warn('圧縮IDWグリッドを読み込めなかったため非圧縮版へフォールバックします:', err);
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
