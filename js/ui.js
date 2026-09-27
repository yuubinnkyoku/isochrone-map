(function () {
  'use strict';
  var STORAGE_KEY = 'isochrone-settings';

  var UIManager = {
    _settings: null,
    _onUpdate: null,
    _panelOpen: false,
    _dataMeta: null,
    _legendRefreshFrame: null,

    init: function (onUpdate) {
      this._onUpdate = onUpdate;
      this._settings = this._loadSettings();
      var mobile = /iPhone|iPod|Android.*Mobile/i.test(navigator.userAgent);
      this._panelOpen = !mobile && window.innerWidth > 600;
      document.getElementById('settings-panel').classList.toggle('open', this._panelOpen);
      this._applyTheme(this._settings.theme);
      this._bindEvents();
      this._bindLegendRecovery();
      this._syncUI();
      this._buildLegend();
    },

    getSettings: function () { return this._settings; },
    refreshLegend: function () { this._buildLegend(); },

    // Keep the select in sync with the mode that PrecomputedGrid could actually
    // activate without overwriting the user's persisted preference. A transient
    // access-grid failure can therefore recover on the next page load.
    syncInterpolationMode: function (mode) {
      var select = document.getElementById('select-interpolation');
      if (select && (mode === 'access' || mode === 'idw')) select.value = mode;
      this._buildLegend();
    },

    // Runtime failures (for example unavailable WebGL) must be able to turn 3D
    // off without recursively firing the normal setting-change callback.
    setThreeDEnabledSilently: function (enabled) {
      this._settings.threeDEnabled = !!enabled;
      var toggle = document.getElementById('toggle-3d');
      if (toggle) toggle.checked = !!enabled;
      this._sync3DControlRows();
      this._buildLegend();
      this._saveSettings();
    },

    _defaults: function () {
      var dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
      return {
        theme: dark ? 'dark' : 'light', tileId: null,
        threeDEnabled: false, threeDMapMode: 'texture', threeDFlatHeight: 4,
        interpolationMode: CONFIG.defaultInterpolationMode || 'access',
        contourEnabled: CONFIG.defaultContourEnabled,
        contourInterval: CONFIG.defaultContourInterval,
        gradientEnabled: CONFIG.defaultGradientEnabled,
        departureThresholdEnabled: !!CONFIG.defaultDepartureThresholdEnabled,
        departureThresholdMinutes: CONFIG.defaultDepartureThresholdMinutes || 420,
        labelsEnabled: CONFIG.defaultLabelsEnabled,
        legendEnabled: CONFIG.defaultLegendEnabled,
        // 互換性のため設定キーは旧名のまま。現在は「最適経路でこの地点から乗らない駅」の表示方法。
        excludedStationMode: CONFIG.defaultExcludedStationMode || 'hollow',
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
        if (['access', 'idw'].indexOf(parsed.interpolationMode) === -1) {
          parsed.interpolationMode = defaults.interpolationMode;
        }
        if (['highlight', 'hollow', 'hidden'].indexOf(parsed.excludedStationMode) === -1) {
          parsed.excludedStationMode = defaults.excludedStationMode;
        }
        parsed.departureThresholdEnabled = !!parsed.departureThresholdEnabled;
        var thresholdMinutes = Number(parsed.departureThresholdMinutes);
        if (!Number.isFinite(thresholdMinutes)) thresholdMinutes = defaults.departureThresholdMinutes;
        parsed.departureThresholdMinutes = Math.max(0, Math.min(CONFIG.destination.dataTargetMinutes, Math.round(thresholdMinutes)));
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
      document.getElementById('select-interpolation').value = s.interpolationMode || 'access';
      document.getElementById('toggle-contour').checked = s.contourEnabled;
      document.getElementById('toggle-gradient').checked = s.gradientEnabled;
      document.getElementById('toggle-departure-threshold').checked = s.departureThresholdEnabled;
      document.getElementById('input-departure-threshold').value = this._minutesToTimeInputValue(s.departureThresholdMinutes);
      document.getElementById('departure-threshold-row').style.display = s.departureThresholdEnabled ? '' : 'none';
      document.getElementById('toggle-labels').checked = s.labelsEnabled;
      document.getElementById('select-excluded-stations').value = s.excludedStationMode || 'hollow';
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
      bind('select-interpolation', 'change', function () {
        self._settings.interpolationMode = this.value;
        self._buildLegend();
        self._commit('interpolationMode', this.value);
      });
      bind('toggle-contour', 'change', function () {
        self._settings.contourEnabled = this.checked;
        document.getElementById('interval-row').style.display = this.checked ? '' : 'none';
        self._buildLegend(); self._commit('contour', this.checked);
      });
      bind('select-interval', 'change', function () {
        self._settings.contourInterval = parseInt(this.value, 10); self._buildLegend(); self._commit('interval', self._settings.contourInterval);
      });
      bind('toggle-gradient', 'change', function () { self._settings.gradientEnabled = this.checked; self._buildLegend(); self._commit('gradient', this.checked); });
      bind('toggle-departure-threshold', 'change', function () {
        self._settings.departureThresholdEnabled = this.checked;
        document.getElementById('departure-threshold-row').style.display = this.checked ? '' : 'none';
        self._buildLegend();
        self._commit('departureThresholdEnabled', this.checked);
      });
      bind('input-departure-threshold', 'change', function () {
        var parsed = timeStrToMinutes(this.value);
        if (!Number.isFinite(parsed)) {
          this.value = self._minutesToTimeInputValue(self._settings.departureThresholdMinutes);
          return;
        }
        parsed = Math.max(0, Math.min(CONFIG.destination.dataTargetMinutes, parsed));
        self._settings.departureThresholdMinutes = parsed;
        this.value = self._minutesToTimeInputValue(parsed);
        self._buildLegend();
        self._commit('departureThresholdMinutes', parsed);
      });
      bind('toggle-labels', 'change', function () { self._settings.labelsEnabled = this.checked; self._commit('labels', this.checked); });
      bind('select-excluded-stations', 'change', function () {
        self._settings.excludedStationMode = this.value;
        self._buildLegend();
        self._commit('excludedStationMode', this.value);
      });
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

    _bindLegendRecovery: function () {
      var self = this;
      function refreshSoon() {
        if (!self._settings || !self._settings.legendEnabled) return;
        if (self._legendRefreshFrame !== null) cancelAnimationFrame(self._legendRefreshFrame);
        self._legendRefreshFrame = requestAnimationFrame(function () {
          self._legendRefreshFrame = null;
          self._buildLegend();
        });
      }

      // Mobile browsers can restore a page from BFCache or change the visual
      // viewport when their address bar appears/disappears. Rebuild the legend
      // after those transitions so a partially restored dynamic legend cannot
      // remain as a title-only card.
      window.addEventListener('pageshow', refreshSoon);
      window.addEventListener('resize', refreshSoon);
      document.addEventListener('visibilitychange', function () {
        if (!document.hidden) refreshSoon();
      });
      refreshSoon();
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

    _minutesToTimeInputValue: function (minutes) {
      var total = Math.max(0, Math.min(1439, Math.round(Number(minutes) || 0)));
      return String(Math.floor(total / 60)).padStart(2, '0') + ':' + String(total % 60).padStart(2, '0');
    },

    _alternateRouteCount: function (meta) {
      var policy = meta && meta.idwExclusionPolicy;
      var diagnostics = policy && policy.exactPointAudit && policy.exactPointAudit.routeSelectionDiagnostics;
      if (!diagnostics) return 0;
      return Number(diagnostics['boards-different-component'] || 0) +
        Number(diagnostics['boards-different-station'] || 0) +
        Number(diagnostics['no-rail-boarded'] || 0);
    },

    _effectiveInterpolationMode: function () {
      if (window.PrecomputedGrid && PrecomputedGrid.ready && (PrecomputedGrid.mode === 'idw' || PrecomputedGrid.mode === 'access')) {
        return PrecomputedGrid.mode;
      }
      return (this._settings && this._settings.interpolationMode) || 'access';
    },

    _interpolationLabel: function () {
      return this._effectiveInterpolationMode() === 'idw'
        ? '従来IDW'
        : '徒歩アクセス考慮';
    },

    updateDataInfo: function (meta, stationCount, majorCount) {
      this._dataMeta = meta || null;
      var stationModel = meta && meta.stationModel;
      var html;
      if (stationModel && stationModel.physicalPoints) {
        html = '<span>' + stationModel.physicalPoints + '</span> 物理駅地点 ｜ <span>' + stationModel.logicalStations + '</span> 論理駅';
      } else {
        html = '<span>' + stationCount + '</span> 駅 ｜ <span>' + majorCount + '</span> 主要';
      }
      if (meta && meta.lastUpdated) html += '<br>データ更新: ' + meta.lastUpdated;
      if (meta && meta.targetArrival && meta.targetArrival !== CONFIG.destination.dataTargetTime) {
        html += '<br><strong>※ 駅時刻は旧 ' + meta.targetArrival + ' 到着基準。高校版 ' + CONFIG.destination.dataTargetTime + ' 到着基準への再調査前です。</strong>';
      } else if (meta && meta.targetArrival) {
        html += '<br>検索上の学校到着: ' + meta.targetArrival + '（1限開始 ' + CONFIG.destination.classStartTime + '）';
      }

      var alternateCount = this._alternateRouteCount(meta);
      if (alternateCount > 0) {
        html += '<br>最適経路でこの地点の鉄道を使わない: <span>' + alternateCount + '</span> 地点';
      }
      html += '<br>補間表示: <span>' + this._interpolationLabel() + '</span>';
      document.getElementById('data-info').innerHTML = html;

      var excludedSelect = document.getElementById('select-excluded-stations');
      if (excludedSelect && excludedSelect.parentElement) {
        excludedSelect.parentElement.style.display = alternateCount > 0 ? '' : 'none';
      }
      this._buildLegend();
    },

    _buildLegend: function () {
      var s = this._settings;
      var r = CONFIG.timeRange;
      var legend = document.getElementById('legend');
      if (!legend || !s) return;

      legend.style.display = s.legendEnabled ? '' : 'none';
      if (!s.legendEnabled) return;

      try {
        var lines = document.getElementById('legend-lines');
        var title = document.getElementById('legend-title');
        var bar = document.getElementById('legend-grad');
        var labels = document.getElementById('legend-grad-labels');
        var threshold = document.getElementById('legend-threshold');
        var excluded = document.getElementById('legend-excluded');
        if (!lines || !title || !bar || !labels || !threshold || !excluded) {
          throw new Error('凡例DOMが不足しています');
        }

        var requiredRangeValues = [r && r.min, r && r.max, r && r.contourMin, r && r.contourMax];
        if (requiredRangeValues.some(function (v) { return !Number.isFinite(Number(v)); })) {
          throw new Error('凡例の時刻範囲が不正です');
        }

        // Build every dynamic part off-DOM first. The previous implementation
        // cleared legend-lines before rebuilding it; if a rebuild was interrupted
        // on a mobile restore/resize, the page could be left with only the title.
        // Commit only after all calculations succeed.
        var modeSuffix = this._effectiveInterpolationMode() === 'idw' ? '・IDW' : '・徒歩アクセス考慮';
        var titleText = (s.threeDEnabled ? '出発時刻（3D地形）' :
          (s.contourEnabled && s.gradientEnabled ? '出発時刻（等時線＋グラデーション）' :
          (s.contourEnabled ? '出発時刻（' + s.contourInterval + '分刻み等時線）' :
          (s.gradientEnabled ? '出発時刻（グラデーション）' : '出発時刻')))) + modeSuffix;

        var lineFragment = document.createDocumentFragment();
        if (s.contourEnabled) {
          var denseMin = Number(r.denseContourMin) || Number(r.contourMin);
          if (Number(r.contourMin) < denseMin) {
            var early = document.createElement('div');
            early.className = 'legend-item';
            early.innerHTML = '<span class="legend-swatch thick" style="background:' +
              colorToCSS(minutesToColor(Number(r.contourMin))) +
              '"></span><span class="legend-time">06:30以前（1時間刻み）</span>';
            lineFragment.appendChild(early);
          }
          for (var m = Math.ceil(denseMin / 10) * 10; m <= Number(r.contourMax); m += 10) {
            var item = document.createElement('div');
            item.className = 'legend-item';
            item.innerHTML = '<span class="legend-swatch thick" style="background:' +
              colorToCSS(minutesToColor(m)) +
              '"></span><span class="legend-time">' + minutesToTimeStr(m) + '</span>';
            lineFragment.appendChild(item);
          }
        }

        var showGrad = s.gradientEnabled || s.threeDEnabled;
        var gradientBackground = '';
        var gradientLabelsHtml = '';
        if (showGrad) {
          var stops = [];
          var span = Number(r.max) - Number(r.min);
          var x;
          for (x = Number(r.min); x <= Number(r.max); x += 2) {
            stops.push(colorToCSS(minutesToColor(x)) + ' ' +
              (span > 0 ? (((x - Number(r.min)) / span) * 100).toFixed(1) : '0') + '%');
          }
          if (!stops.length || x - 2 < Number(r.max)) {
            stops.push(colorToCSS(minutesToColor(Number(r.max))) + ' 100%');
          }
          gradientBackground = 'linear-gradient(90deg,' + stops.join(',') + ')';

          var labelValues = [Number(r.min), 420, 450, 480, Number(r.max)];
          var seenLabels = {};
          gradientLabelsHtml = labelValues.filter(function (v) {
            if (v < Number(r.min) || v > Number(r.max) || seenLabels[v]) return false;
            seenLabels[v] = true;
            return true;
          }).map(function (v) {
            var pct = span > 0 ? ((v - Number(r.min)) / span) * 100 : 0;
            pct = Math.max(0, Math.min(100, pct));
            var transform = pct <= 0 ? 'translateX(0)' : (pct >= 100 ? 'translateX(-100%)' : 'translateX(-50%)');
            return '<span style="left:' + pct.toFixed(2) + '%;transform:' + transform + '">' +
              minutesToTimeStr(v) + '</span>';
          }).join('');
        }

        var showThreshold = !!s.departureThresholdEnabled;
        var thresholdHtml = showThreshold
          ? '<span class="legend-threshold-symbol" style="color:' +
            colorToCSS(minutesToColor(s.departureThresholdMinutes)) + '"></span>' +
            '<span class="legend-threshold-text">指定した時刻（' +
            minutesToTimeStr(s.departureThresholdMinutes) +
            '）に出て間に合う範囲を強調</span>'
          : '';

        var alternateCount = this._alternateRouteCount(this._dataMeta);
        var showExcluded = alternateCount > 0 && !s.threeDEnabled &&
          (s.excludedStationMode || 'hollow') !== 'hidden';
        var excludedHtml = showExcluded
          ? '<span class="legend-excluded-symbol">○</span>' +
            '<span class="legend-excluded-text">最適経路でこの地点の鉄道を使わない（' +
            alternateCount + '地点）</span>'
          : '';

        // Atomic-ish commit: no destructive mutation happens before the complete
        // next legend has been prepared.
        title.textContent = titleText;
        lines.replaceChildren(lineFragment);
        lines.style.display = (s.contourEnabled && !s.threeDEnabled) ? '' : 'none';

        bar.style.display = showGrad ? 'block' : 'none';
        labels.style.display = showGrad ? 'block' : 'none';
        if (showGrad) {
          bar.style.background = gradientBackground;
          labels.innerHTML = gradientLabelsHtml;
        }

        threshold.style.display = showThreshold ? 'flex' : 'none';
        threshold.innerHTML = thresholdHtml;

        excluded.style.display = showExcluded ? 'flex' : 'none';
        excluded.innerHTML = excludedHtml;
      } catch (err) {
        // Keep the last successfully rendered legend instead of clearing it.
        console.error('凡例の再構築エラー:', err);
      }
    }
  };

  window.UIManager = UIManager;
})();
