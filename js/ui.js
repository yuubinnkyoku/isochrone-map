(function () {
  'use strict';
  var STORAGE_KEY = 'isochrone-settings';

  var UIManager = {
    _settings: null,
    _onUpdate: null,
    _panelOpen: false,

    init: function (onUpdate) {
      this._onUpdate = onUpdate;
      this._settings = this._loadSettings();
      var mobile = /iPhone|iPod|Android.*Mobile/i.test(navigator.userAgent);
      this._panelOpen = !mobile && window.innerWidth > 600;
      document.getElementById('settings-panel').classList.toggle('open', this._panelOpen);
      this._applyTheme(this._settings.theme);
      this._bindEvents();
      this._syncUI();
      this._buildLegend();
    },

    getSettings: function () { return this._settings; },
    refreshLegend: function () { this._buildLegend(); },

    _defaults: function () {
      var dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
      return {
        theme: dark ? 'dark' : 'light', tileId: null,
        threeDEnabled: false, threeDMapMode: 'texture', threeDFlatHeight: 4,
        contourEnabled: CONFIG.defaultContourEnabled,
        contourInterval: CONFIG.defaultContourInterval,
        gradientEnabled: CONFIG.defaultGradientEnabled,
        labelsEnabled: CONFIG.defaultLabelsEnabled,
        legendEnabled: CONFIG.defaultLegendEnabled,
        radiusRingsEnabled: CONFIG.destinationRings.enabledDefault
      };
    },

    _loadSettings: function () {
      var defaults = this._defaults();
      try {
        var parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
        delete parsed.gakkuEnabled;
        Object.keys(defaults).forEach(function (k) {
          if (!(k in parsed)) parsed[k] = defaults[k];
        });
        return parsed;
      } catch (e) { return defaults; }
    },

    _saveSettings: function () {
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(this._settings)); } catch (e) {}
    },

    _applyTheme: function (theme) {
      document.documentElement.setAttribute('data-theme', theme);
    },

    _syncUI: function () {
      var s = this._settings;
      document.getElementById('toggle-3d').checked = s.threeDEnabled;
      document.getElementById('select-3d-mapmode').value = s.threeDMapMode || 'texture';
      document.getElementById('range-3d-flat-height').value = String(s.threeDFlatHeight);
      document.getElementById('label-3d-flat-height').textContent = this._formatHeightLabel(s.threeDFlatHeight);
      document.getElementById('toggle-contour').checked = s.contourEnabled;
      document.getElementById('toggle-gradient').checked = s.gradientEnabled;
      document.getElementById('toggle-labels').checked = s.labelsEnabled;
      document.getElementById('toggle-legend').checked = s.legendEnabled;
      document.getElementById('legend').style.display = s.legendEnabled ? '' : 'none';
      document.getElementById('toggle-radius-rings').checked = s.radiusRingsEnabled;
      document.getElementById('select-interval').value = String(s.contourInterval);
      document.getElementById('select-tile').value = s.tileId || CONFIG.defaultTile[s.theme];
      document.getElementById('toggle-theme').checked = s.theme === 'dark';
      document.getElementById('interval-row').style.display = s.contourEnabled ? '' : 'none';
      this._sync3DControlRows();
    },

    _bindEvents: function () {
      var self = this;
      function bind(id, event, fn) { document.getElementById(id).addEventListener(event, fn); }

      bind('btn-settings', 'click', function () {
        self._panelOpen = !self._panelOpen;
        document.getElementById('settings-panel').classList.toggle('open', self._panelOpen);
      });
      bind('toggle-theme', 'change', function () {
        self._settings.theme = this.checked ? 'dark' : 'light';
        self._settings.tileId = null;
        self._applyTheme(self._settings.theme);
        document.getElementById('select-tile').value = CONFIG.defaultTile[self._settings.theme];
        self._commit('theme', self._settings.theme);
      });
      bind('select-tile', 'change', function () { self._settings.tileId = this.value; self._commit('tile', this.value); });
      bind('toggle-contour', 'change', function () {
        self._settings.contourEnabled = this.checked;
        document.getElementById('interval-row').style.display = this.checked ? '' : 'none';
        self._buildLegend(); self._commit('contour', this.checked);
      });
      bind('select-interval', 'change', function () {
        self._settings.contourInterval = parseInt(this.value, 10); self._buildLegend(); self._commit('interval', self._settings.contourInterval);
      });
      bind('toggle-gradient', 'change', function () { self._settings.gradientEnabled = this.checked; self._buildLegend(); self._commit('gradient', this.checked); });
      bind('toggle-labels', 'change', function () { self._settings.labelsEnabled = this.checked; self._commit('labels', this.checked); });
      bind('toggle-legend', 'change', function () {
        self._settings.legendEnabled = this.checked;
        document.getElementById('legend').style.display = this.checked ? '' : 'none';
        self._commit('legend', this.checked);
      });
      bind('toggle-radius-rings', 'change', function () { self._settings.radiusRingsEnabled = this.checked; self._commit('radiusRings', this.checked); });
      bind('toggle-3d', 'change', function () { self._settings.threeDEnabled = this.checked; self._sync3DControlRows(); self._buildLegend(); self._commit('threeD', this.checked); });
      bind('select-3d-mapmode', 'change', function () { self._settings.threeDMapMode = this.value; self._sync3DControlRows(); self._commit('threeDMapMode', this.value); });
      bind('range-3d-flat-height', 'input', function () {
        self._settings.threeDFlatHeight = parseInt(this.value, 10);
        document.getElementById('label-3d-flat-height').textContent = self._formatHeightLabel(self._settings.threeDFlatHeight);
        self._commit('threeDFlatHeight', self._settings.threeDFlatHeight);
      });
      bind('btn-devtools', 'click', function () { if (window.DevTools) DevTools.toggle(); });
    },

    _commit: function (key, value) {
      this._saveSettings();
      if (this._onUpdate) this._onUpdate(key, value);
    },

    _sync3DControlRows: function () {
      var s = this._settings, show = !!s.threeDEnabled;
      document.getElementById('3d-mapmode-row').style.display = show ? '' : 'none';
      document.getElementById('3d-flat-height-row').style.display = (show && (s.threeDMapMode || 'texture') === 'flat') ? '' : 'none';
    },

    _formatHeightLabel: function (v) { return (v > 0 ? '+' : '') + String(v); },

    updateDataInfo: function (meta, stationCount, majorCount) {
      var html = '<span>' + stationCount + '</span> 駅 ｜ <span>' + majorCount + '</span> 主要';
      if (meta && meta.lastUpdated) html += '<br>データ更新: ' + meta.lastUpdated;
      if (meta && meta.targetArrival && meta.targetArrival !== CONFIG.destination.dataTargetTime) {
        html += '<br><strong>※ 駅時刻は旧 ' + meta.targetArrival + ' 到着基準。高校版 ' + CONFIG.destination.dataTargetTime + ' 到着基準への再調査前です。</strong>';
      } else if (meta && meta.targetArrival) {
        html += '<br>検索上の学校到着: ' + meta.targetArrival + '（1限開始 ' + CONFIG.destination.classStartTime + '）';
      }
      document.getElementById('data-info').innerHTML = html;
    },

    _buildLegend: function () {
      var s = this._settings, r = CONFIG.timeRange;
      var legend = document.getElementById('legend');
      legend.style.display = s.legendEnabled ? '' : 'none';
      if (!s.legendEnabled) return;
      var lines = document.getElementById('legend-lines');
      var title = document.getElementById('legend-title');
      var bar = document.getElementById('legend-grad');
      var labels = document.getElementById('legend-grad-labels');

      title.textContent = s.threeDEnabled ? '出発時刻（3D地形）' :
        (s.contourEnabled && s.gradientEnabled ? '出発時刻（等時線＋グラデーション）' :
        (s.contourEnabled ? '出発時刻（' + s.contourInterval + '分刻み等時線）' :
        (s.gradientEnabled ? '出発時刻（グラデーション）' : '出発時刻')));

      lines.innerHTML = '';
      lines.style.display = (s.contourEnabled && !s.threeDEnabled) ? '' : 'none';
      if (s.contourEnabled) {
        var denseMin = r.denseContourMin || r.contourMin;
        if (r.contourMin < denseMin) {
          var early = document.createElement('div');
          early.className = 'legend-item';
          early.innerHTML = '<span class="legend-swatch thick" style="background:' + colorToCSS(minutesToColor(r.contourMin)) + '"></span><span class="legend-time">06:30以前（1時間刻み）</span>';
          lines.appendChild(early);
        }
        for (var m = Math.ceil(denseMin / 10) * 10; m <= r.contourMax; m += 10) {
          var item = document.createElement('div');
          item.className = 'legend-item';
          item.innerHTML = '<span class="legend-swatch thick" style="background:' + colorToCSS(minutesToColor(m)) + '"></span><span class="legend-time">' + minutesToTimeStr(m) + '</span>';
          lines.appendChild(item);
        }
      }

      var showGrad = s.gradientEnabled || s.threeDEnabled;
      bar.style.display = showGrad ? 'block' : 'none';
      labels.style.display = showGrad ? 'flex' : 'none';
      if (showGrad) {
        var stops = [];
        for (var x = r.min; x <= r.max; x += 2) {
          stops.push(colorToCSS(minutesToColor(x)) + ' ' + (((x - r.min) / (r.max - r.min)) * 100).toFixed(1) + '%');
        }
        bar.style.background = 'linear-gradient(90deg,' + stops.join(',') + ')';
        labels.innerHTML = [r.min, 420, 450, 480, r.max].map(function (v) { return '<span>' + minutesToTimeStr(v) + '</span>'; }).join('');
      }
    }
  };

  window.UIManager = UIManager;
})();
