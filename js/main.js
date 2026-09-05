// ============================================================
// main.js — エントリ: 初期化・イベント接続
// ============================================================
(function () {
  'use strict';

  var app = {};
  window.app = app;

  UIManager.init(onSettingChanged);

  var settings = UIManager.getSettings();

  var map = MapManager.init();
  app.map = map;

  if (settings.tileId) {
    MapManager.setTileByUser(settings.tileId);
  } else {
    MapManager.onThemeChanged(settings.theme);
  }

  var contourOverlay = new ContourOverlay({
    grid: null,
    interval: settings.contourInterval,
    visible: settings.contourEnabled,
  });
  contourOverlay.addTo(map);
  app.contourOverlay = contourOverlay;

  var gradientOverlay = new GradientOverlay({
    grid: null,
    visible: settings.gradientEnabled,
  });
  gradientOverlay.addTo(map);
  app.gradientOverlay = gradientOverlay;

  Renderer3D.init();
  Renderer3D.setFlatPlaneHeight(settings.threeDFlatHeight);
  app.renderer3d = Renderer3D;

  Promise.all([
    DataManager.load(),
    PrecomputedGrid.load().catch(function (err) {
      console.error('事前計算グリッド読み込みエラー:', err);
      return null;
    })
  ]).then(function () {
    var stations = DataManager.stations;
    var meta = DataManager.meta;

    if (window.updateTimeRangeFromStations) updateTimeRangeFromStations(stations);
    UIManager.refreshLegend();

    if (PrecomputedGrid.ready) {
      contourOverlay.setGrid(PrecomputedGrid);
      gradientOverlay.setGrid(PrecomputedGrid);
    }
    Renderer3D.setStations(stations);

    MarkerManager.init(map, stations, meta);
    MarkerManager.setLabelsEnabled(settings.labelsEnabled);
    MarkerManager.setDestinationRingsVisible(settings.radiusRingsEnabled);

    UIManager.updateDataInfo(meta, stations.length, DataManager.getMajorCount());

    if (settings.threeDEnabled) {
      Renderer3D.setMapMode(settings.threeDMapMode || 'texture');
      Renderer3D.setFlatPlaneHeight(settings.threeDFlatHeight);
      Renderer3D.show();
      document.getElementById('map').style.display = 'none';
    }

    initSearch(stations, map);
  }).catch(function (err) {
    console.error('データ読み込みエラー:', err);
  });

  function initSearch(stations, map) {
    var dataList = document.getElementById('station-list');
    var searchInput = document.getElementById('station-search');

    if (dataList) {
      var uniqueStations = {};
      stations.forEach(function (s) {
        if (!uniqueStations[s.station]) {
          uniqueStations[s.station] = true;
          var option = document.createElement('option');
          option.value = s.station;
          dataList.appendChild(option);
        }
      });
    }

    if (searchInput) {
      searchInput.addEventListener('change', function (e) {
        var val = e.target.value.trim();
        if (!val) return;
        var marker = MarkerManager.findStationMarker(val);
        if (marker) {
          map.setView(marker.getLatLng(), 15, { animate: true });
          marker.openTooltip();
          searchInput.blur();
        }
      });

      searchInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') searchInput.blur();
      });
    }
  }

  function onSettingChanged(key, value) {
    switch (key) {
      case 'theme':
        MapManager.onThemeChanged(value);
        MarkerManager.updateTheme();
        contourOverlay.refresh();
        Renderer3D.updateTheme();
        if (UIManager.getSettings().threeDEnabled) {
          Renderer3D.refreshMapTexture();
        }
        break;
      case 'tile':
        MapManager.setTileByUser(value);
        if (UIManager.getSettings().threeDEnabled) {
          Renderer3D.refreshMapTexture();
        }
        break;
      case 'contour':
        contourOverlay.setVisible(value);
        break;
      case 'interval':
        contourOverlay.setInterval(value);
        break;
      case 'gradient':
        gradientOverlay.setVisible(value);
        break;
      case 'labels':
        MarkerManager.setLabelsEnabled(value);
        break;
      case 'radiusRings':
        MarkerManager.setDestinationRingsVisible(value);
        break;
      case 'threeD':
        if (value) {
          Renderer3D.setMapMode(UIManager.getSettings().threeDMapMode || 'texture');
          Renderer3D.setFlatPlaneHeight(UIManager.getSettings().threeDFlatHeight);
          Renderer3D.show();
          document.getElementById('map').style.display = 'none';
        } else {
          Renderer3D.hide();
          document.getElementById('map').style.display = '';
          map.invalidateSize();
        }
        break;
      case 'threeDMapMode':
        Renderer3D.setMapMode(value);
        break;
      case 'threeDFlatHeight':
        Renderer3D.setFlatPlaneHeight(value);
        break;
    }
  }
})();
