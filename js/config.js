// ============================================================
// config.js — 定数・カラースケール・タイル定義・ユーティリティ
// ============================================================
(function () {
  'use strict';

  var CONFIG = {
    // 地図初期設定
    defaultCenter: [35.72, 139.72],
    defaultZoom: 11,

    // stations.json cache-buster. Bump when station data changes.
    dataVersion: 12,
    // Precomputed IDW grid cache-buster.
    gridVersion: 1,

    // 目的地
    destination: {
      lat: 35.716741,
      lng: 139.7308823,
      name: '筑波大学附属高等学校',
      classStartTime: '08:20',
      // 中学版と同様に、校内移動などを見込み2分前の学校到着を検索基準とする。
      dataTargetTime: '08:18',
      dataTargetMinutes: 498,
    },

    // 表示する時刻範囲
    timeRange: {
      min: 390,          // 06:30
      max: 495,          // 08:15
      contourMin: 390,   // 06:30
      contourMax: 495,   // 08:15
    },

    // カラースケール
    colorStops: [
      { min: 390, color: [90, 40, 180] },    // 06:30
      { min: 420, color: [50, 100, 220] },   // 07:00
      { min: 440, color: [20, 170, 200] },   // 07:20
      { min: 455, color: [40, 200, 120] },   // 07:35
      { min: 470, color: [240, 200, 40] },   // 07:50
      { min: 485, color: [255, 107, 107] },  // 08:05
      { min: 495, color: [230, 60, 100] },   // 08:15
    ],

    // 等時線デフォルト
    defaultContourInterval: 5,
    defaultContourEnabled: true,
    defaultGradientEnabled: false,
    defaultLabelsEnabled: true,

    // 駅名ラベル表示。中間ズームでは2024年の駅別乗降客数順位で段階表示し、
    // さらに画面上で衝突する一般駅ラベルを利用者数の少ない順に省く。
    stationLabels: {
      majorOnlyMaxZoom: 11,
      rankLimits: {
        12: 80,
        13: 220,
        14: 420,
      },
      allLabelsMinZoom: 15,
      collisionMaxZoom: 14,
      collisionBox: { width: 120, height: 40, gap: 3 },
    },

    // 目的地同心円（5km/10km/15km）
    destinationRings: {
      enabledDefault: false,
      radiiMeters: [5000, 10000, 15000],
      styleVars: {
        strokeByRadius: {
          5000: '--dest-ring-5km-stroke',
          10000: '--dest-ring-10km-stroke',
          15000: '--dest-ring-15km-stroke',
        },
        fill: '--dest-ring-fill',
      },
    },

    // IDW補間パラメータ
    idwPower: 2.5,

    // 描画デバウンス
    renderDebounceMs: 50,

    // Canvas描画パディング（ビューポートに対する割合）
    canvasPadding: 0.5,

    // ズーム別グリッドサイズ
    gridSize: function (zoom) {
      if (zoom <= 10) return 8;
      if (zoom <= 12) return 5;
      return 5;
    },

    // タイル定義
    tiles: {
      'gsi-pale': {
        name: '国土地理院 淡色',
        url: 'https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png',
        attribution: '&copy; <a href="https://maps.gsi.go.jp/">国土地理院</a>',
        theme: 'light',
        maxZoom: 18,
      },
      'gsi-std': {
        name: '国土地理院 標準',
        url: 'https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png',
        attribution: '&copy; <a href="https://maps.gsi.go.jp/">国土地理院</a>',
        theme: 'light',
        maxZoom: 18,
      },
      'osm-jp': {
        name: 'OpenStreetMap JP',
        url: 'https://tile.openstreetmap.jp/{z}/{x}/{y}.png',
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        theme: 'light',
        maxZoom: 18,
      },
      'gsi-dark': {
        name: '国土地理院 ダーク',
        url: 'https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png',
        attribution: '&copy; <a href="https://maps.gsi.go.jp/">国土地理院</a>',
        theme: 'dark',
        maxZoom: 18,
        invert: true,
      },
    },

    // テーマ別デフォルトタイル
    defaultTile: { light: 'gsi-pale', dark: 'gsi-dark' },
  };

  // ユーティリティ関数
  function minutesToColor(m) {
    var stops = CONFIG.colorStops;
    if (m <= stops[0].min) return stops[0].color.slice();
    if (m >= stops[stops.length - 1].min) return stops[stops.length - 1].color.slice();
    for (var i = 0; i < stops.length - 1; i++) {
      if (m >= stops[i].min && m <= stops[i + 1].min) {
        var t = (m - stops[i].min) / (stops[i + 1].min - stops[i].min);
        var c0 = stops[i].color, c1 = stops[i + 1].color;
        return [
          Math.round(c0[0] + (c1[0] - c0[0]) * t),
          Math.round(c0[1] + (c1[1] - c0[1]) * t),
          Math.round(c0[2] + (c1[2] - c0[2]) * t),
        ];
      }
    }
    return [128, 128, 128];
  }

  function colorToCSS(c, a) {
    if (a === undefined) a = 1;
    return 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + a + ')';
  }

  function minutesToTimeStr(m) {
    var h = String(Math.floor(m / 60)).padStart(2, '0');
    var min = String(Math.round(m % 60)).padStart(2, '0');
    return h + ':' + min;
  }

  // Export
  window.CONFIG = CONFIG;
  window.minutesToColor = minutesToColor;
  window.colorToCSS = colorToCSS;
  window.minutesToTimeStr = minutesToTimeStr;
})();
