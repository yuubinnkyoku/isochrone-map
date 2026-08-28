// ============================================================
// devtools.js — 開発者ツール（駅追加・データexport/import）
// ============================================================
(function () {
  'use strict';

  var DevTools = {
    _open: false,
    _panel: null,
    _pickMode: false,
    _previewMarker: null,
    _mapClickHandler: null,

    init: function () {
      this._buildPanel();
      if (location.search.indexOf('dev=1') !== -1) this.toggle();
    },

    toggle: function () {
      this._open = !this._open;
      if (this._panel) this._panel.style.display = this._open ? 'block' : 'none';
      var btn = document.getElementById('btn-devtools');
      if (btn) btn.textContent = this._open ? '🔧 開発者ツール ▲' : '🔧 開発者ツール';
      if (this._open) this._runValidation();
    },

    _buildPanel: function () {
      var panel = document.createElement('div');
      panel.id = 'devtools-panel';
      panel.className = 'devtools-section';
      panel.style.display = 'none';
      var today = new Date().toISOString().slice(0, 10);

      panel.innerHTML =
        '<div class="devtools-group">' +
          '<div class="devtools-group-title">駅の追加</div>' +
          '<label class="devtools-field"><span class="devtools-field-label">地図クリックで座標取得</span><input type="checkbox" id="dev-pick-mode"><span class="ctrl-toggle"></span></label>' +
          '<div class="devtools-field"><span class="devtools-field-label">ID</span><input type="text" id="dev-id" class="devtools-input" placeholder="station-xxx (自動生成)"></div>' +
          '<div class="devtools-field"><span class="devtools-field-label">駅名</span><input type="text" id="dev-station" class="devtools-input" placeholder="駅名"></div>' +
          '<div class="devtools-field"><span class="devtools-field-label">緯度</span><input type="number" id="dev-lat" class="devtools-input" step="0.0001" placeholder="35.xxxx"></div>' +
          '<div class="devtools-field"><span class="devtools-field-label">経度</span><input type="number" id="dev-lng" class="devtools-input" step="0.0001" placeholder="139.xxxx"></div>' +
          '<div class="devtools-field"><span class="devtools-field-label">最遅出発時刻</span><div class="devtools-time-inputs"><input type="number" id="dev-hour" class="devtools-input devtools-input-sm" min="0" max="23" value="7"><span>:</span><input type="number" id="dev-min" class="devtools-input devtools-input-sm" min="0" max="59" value="48"></div></div>' +
          '<div class="devtools-field"><span class="devtools-field-label">路線</span><input type="text" id="dev-line" class="devtools-input" placeholder="丸ノ内線など"></div>' +
          '<div class="devtools-field"><span class="devtools-field-label">経路</span><input type="text" id="dev-route" class="devtools-input" placeholder="徒歩10分など"></div>' +
          '<label class="devtools-field"><span class="devtools-field-label">主要駅</span><input type="checkbox" id="dev-major"><span class="ctrl-toggle"></span></label>' +
          '<div class="devtools-field"><span class="devtools-field-label">メモ</span><input type="text" id="dev-note" class="devtools-input"></div>' +
          '<div class="devtools-field"><span class="devtools-field-label">検索日</span><input type="date" id="dev-searchdate" class="devtools-input" value="' + today + '"></div>' +
          '<div class="devtools-btn-row"><button class="devtools-btn" id="dev-preview">プレビュー</button><button class="devtools-btn devtools-btn-primary" id="dev-confirm">確定</button></div>' +
        '</div>' +
        '<div class="devtools-group"><div class="devtools-group-title">データ管理</div>' +
          '<button class="devtools-btn devtools-btn-full" id="dev-copy-json">JSONをクリップボードにコピー</button>' +
          '<button class="devtools-btn devtools-btn-full" id="dev-export-json">JSONファイルにエクスポート</button>' +
          '<button class="devtools-btn devtools-btn-full" id="dev-import-json">JSONファイルをインポート</button>' +
          '<input type="file" id="dev-import-file" accept=".json" style="display:none"></div>' +
        '<div class="devtools-group"><div class="devtools-group-title">バリデーション</div><div class="devtools-validation" id="dev-validation"></div></div>';

      var btn = document.getElementById('btn-devtools');
      if (btn && btn.parentNode) btn.parentNode.appendChild(panel);
      this._panel = panel;
      this._bindEvents();
      this._runValidation();
    },

    _bindEvents: function () {
      var self = this;
      document.getElementById('dev-pick-mode').addEventListener('change', function () {
        self._pickMode = this.checked;
        var container = app.map.getContainer();
        if (self._pickMode) {
          container.style.cursor = 'crosshair';
          self._mapClickHandler = function (e) {
            document.getElementById('dev-lat').value = e.latlng.lat.toFixed(6);
            document.getElementById('dev-lng').value = e.latlng.lng.toFixed(6);
          };
          app.map.on('click', self._mapClickHandler);
        } else {
          container.style.cursor = '';
          if (self._mapClickHandler) app.map.off('click', self._mapClickHandler);
          self._mapClickHandler = null;
        }
      });
      document.getElementById('dev-preview').addEventListener('click', function () { self._showPreview(); });
      document.getElementById('dev-confirm').addEventListener('click', function () { self._confirmStation(); });
      document.getElementById('dev-copy-json').addEventListener('click', function () { self._copyJSON(); });
      document.getElementById('dev-export-json').addEventListener('click', function () { self._exportJSON(); });
      document.getElementById('dev-import-json').addEventListener('click', function () { document.getElementById('dev-import-file').click(); });
      document.getElementById('dev-import-file').addEventListener('change', function () {
        if (this.files && this.files[0]) self._importJSON(this.files[0]);
        this.value = '';
      });
    },

    _getFormData: function () {
      var h = parseInt(document.getElementById('dev-hour').value, 10) || 0;
      var m = parseInt(document.getElementById('dev-min').value, 10) || 0;
      return {
        id: document.getElementById('dev-id').value.trim() || ('station-' + Date.now()),
        station: document.getElementById('dev-station').value.trim(),
        lat: parseFloat(document.getElementById('dev-lat').value) || 0,
        lng: parseFloat(document.getElementById('dev-lng').value) || 0,
        minutes: h * 60 + m,
        major: document.getElementById('dev-major').checked,
        line: document.getElementById('dev-line').value.trim(),
        route: document.getElementById('dev-route').value.trim(),
        note: document.getElementById('dev-note').value.trim() || undefined,
        searchDate: document.getElementById('dev-searchdate').value || undefined
      };
    },

    _validateStation: function (s, existingStations) {
      var errors = [];
      if (!s.station) errors.push('駅名が空です');
      if (s.lat < 34.5 || s.lat > 37.0) errors.push('緯度が範囲外 (34.5〜37.0)');
      if (s.lng < 138.5 || s.lng > 141.0) errors.push('経度が範囲外 (138.5〜141.0)');
      if (s.minutes < 360 || s.minutes > CONFIG.destination.dataTargetMinutes) errors.push('時刻が範囲外 (06:00〜' + CONFIG.destination.dataTargetTime + ')');
      if (existingStations && existingStations.some(function (st) { return st.id === s.id; })) errors.push('ID が重複しています: ' + s.id);
      return errors;
    },

    _showPreview: function () {
      var data = this._getFormData();
      var errors = this._validateStation(data, DataManager.stations);
      if (errors.length) return this._showValidationErrors(errors);
      this._clearPreview();
      this._previewMarker = L.circleMarker([data.lat, data.lng], {
        radius: 12, fillColor: colorToCSS(minutesToColor(data.minutes)), color: '#ff6b6b',
        weight: 2, opacity: 0.8, fillOpacity: 0.3, dashArray: '5,5'
      }).addTo(app.map);
      this._previewMarker.bindTooltip('プレビュー: ' + data.station + ' ' + minutesToTimeStr(data.minutes), { permanent: true, direction: 'top' });
      app.map.setView([data.lat, data.lng], Math.max(app.map.getZoom(), 12));
    },

    _clearPreview: function () {
      if (this._previewMarker) app.map.removeLayer(this._previewMarker);
      this._previewMarker = null;
    },

    _confirmStation: function () {
      var data = this._getFormData();
      var errors = this._validateStation(data, DataManager.stations);
      if (errors.length) return this._showValidationErrors(errors);
      var station = {};
      ['id','station','lat','lng','minutes','major','line','route','note','searchDate'].forEach(function (k) {
        if (data[k] !== undefined && data[k] !== '') station[k] = data[k];
      });
      DataManager.stations.push(station);
      this._clearPreview();
      this._refreshAll();
      document.getElementById('dev-id').value = '';
      document.getElementById('dev-station').value = '';
      document.getElementById('dev-lat').value = '';
      document.getElementById('dev-lng').value = '';
      this._showValidationSuccess('✓ 駅を追加しました: ' + station.station);
    },

    _refreshAll: function () {
      var stations = DataManager.stations, meta = DataManager.meta;
      if (app.contourOverlay) app.contourOverlay.setStations(stations);
      if (app.gradientOverlay) app.gradientOverlay.setStations(stations);
      if (app.renderer3d) app.renderer3d.setStations(stations);
      if (MarkerManager.refresh) MarkerManager.refresh(stations, meta);
      UIManager.updateDataInfo(meta, stations.length, DataManager.getMajorCount());
      this._runValidation();
    },

    _exportObject: function () { return { meta: DataManager.meta, stations: DataManager.stations }; },

    _copyJSON: function () {
      var btn = document.getElementById('dev-copy-json');
      navigator.clipboard.writeText(JSON.stringify(this._exportObject(), null, 2)).then(function () {
        var orig = btn.textContent; btn.textContent = 'コピーしました ✓';
        setTimeout(function () { btn.textContent = orig; }, 1500);
      });
    },

    _exportJSON: function () {
      var blob = new Blob([JSON.stringify(this._exportObject(), null, 2)], { type: 'application/json' });
      var url = URL.createObjectURL(blob), a = document.createElement('a');
      a.href = url; a.download = 'stations.json'; document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    },

    _importJSON: function (file) {
      var self = this, reader = new FileReader();
      reader.onload = function (e) {
        try {
          var data = JSON.parse(e.target.result);
          if (!data.stations || !Array.isArray(data.stations)) return self._showValidationErrors(['無効なJSON: stations配列がありません']);
          var errors = [], ids = {};
          data.stations.forEach(function (s, i) {
            self._validateStation(s, null).forEach(function (err) { errors.push('駅' + (i + 1) + ': ' + err); });
            if (ids[s.id]) errors.push('ID重複: ' + s.id);
            ids[s.id] = true;
          });
          if (errors.length) return self._showValidationErrors(errors);
          if (data.meta) DataManager.meta = data.meta;
          DataManager.stations = data.stations;
          self._refreshAll();
          self._showValidationSuccess('✓ ' + data.stations.length + '駅をインポートしました');
        } catch (err) { self._showValidationErrors(['JSONパースエラー: ' + err.message]); }
      };
      reader.readAsText(file);
    },

    _runValidation: function () {
      var ids = {}, duplicates = 0, coordErrors = 0;
      DataManager.stations.forEach(function (s) {
        if (ids[s.id]) duplicates++;
        ids[s.id] = true;
        if (s.lat < 34.5 || s.lat > 37.0 || s.lng < 138.5 || s.lng > 141.0) coordErrors++;
      });
      var html = '✓ ' + DataManager.stations.length + '駅 / ' + (duplicates ? 'ID重複 ' + duplicates + '件' : '重複なし') + ' / ' + (coordErrors ? '座標範囲外 ' + coordErrors + '件' : '座標範囲OK');
      var el = document.getElementById('dev-validation');
      if (el) el.innerHTML = '<span class="devtools-success">' + html + '</span>';
    },

    _showValidationErrors: function (errors) {
      var el = document.getElementById('dev-validation');
      if (el) el.innerHTML = errors.map(function (e) { return '<div class="devtools-error">✗ ' + e + '</div>'; }).join('');
    },

    _showValidationSuccess: function (msg) {
      var el = document.getElementById('dev-validation');
      if (el) el.innerHTML = '<div class="devtools-success">' + msg + '</div>';
      var self = this; setTimeout(function () { self._runValidation(); }, 2000);
    }
  };

  window.DevTools = DevTools;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { DevTools.init(); });
  else setTimeout(function () { DevTools.init(); }, 0);
})();
