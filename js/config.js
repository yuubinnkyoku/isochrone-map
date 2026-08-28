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
    dataVersion: 13,
    // Precomputed IDW grid cache-buster.
    gridVersion: 2,

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

    // カラースケール（OKLCH）。
    // 06:30〜08:10 は10分ごとの OKLab ΔE ≈ 0.082、08:15 は5分なので約半分。
    // L は時刻とともに単調増加し、全区間を補間しても sRGB gamut 内に収まる。
    colorStops: [
      { min: 390, oklch: [0.540000, 0.238411, 305.000000] }, // 06:30
      { min: 400, oklch: [0.555475, 0.219207, 285.303947] }, // 06:40
      { min: 410, oklch: [0.572314, 0.199036, 263.873272] }, // 06:50
      { min: 420, oklch: [0.585142, 0.132457, 247.546840] }, // 07:00
      { min: 430, oklch: [0.612338, 0.091049, 212.933162] }, // 07:10
      { min: 440, oklch: [0.646014, 0.108620, 170.073247] }, // 07:20
      { min: 450, oklch: [0.664125, 0.167673, 147.022348] }, // 07:30
      { min: 460, oklch: [0.685987, 0.137312, 119.198022] }, // 07:40
      { min: 470, oklch: [0.712719, 0.124130, 85.176296] },  // 07:50
      { min: 480, oklch: [0.736421, 0.154971, 55.009547] },  // 08:00
      { min: 490, oklch: [0.760000, 0.121544, 25.000000] },  // 08:10
      { min: 495, oklch: [0.774432, 0.117747, 6.632483] },   // 08:15
    ],

    // 等時線デフォルト
    defaultContourInterval: 5,
    defaultContourEnabled: true,
    defaultGradientEnabled: false,
    defaultLabelsEnabled: true,
    defaultLegendEnabled: true,

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
  function oklchToColor(oklch) {
    var L = oklch[0], C = oklch[1], h = oklch[2] * Math.PI / 180;
    var a = C * Math.cos(h), b = C * Math.sin(h);

    // OKLab -> linear sRGB (Björn Ottosson reference transform).
    var l_ = L + 0.3963377774 * a + 0.2158037573 * b;
    var m_ = L - 0.1055613458 * a - 0.0638541728 * b;
    var s_ = L - 0.0894841775 * a - 1.2914855480 * b;
    var l = l_ * l_ * l_, mm = m_ * m_ * m_, ss = s_ * s_ * s_;
    var linear = [
      4.0767416621 * l - 3.3077115913 * mm + 0.2309699292 * ss,
      -1.2684380046 * l + 2.6097574011 * mm - 0.3413193965 * ss,
      -0.0041960863 * l - 0.7034186147 * mm + 1.7076147010 * ss,
    ];

    return linear.map(function (v) {
      // The configured OKLCH path is in gamut; clamp only for floating-point noise.
      v = Math.max(0, Math.min(1, v));
      var srgb = v <= 0.0031308 ? 12.92 * v : 1.055 * Math.pow(v, 1 / 2.4) - 0.055;
      return Math.round(srgb * 255);
    });
  }

  function interpolateHue(h0, h1, t) {
    var delta = ((h1 - h0 + 540) % 360) - 180;
    return h0 + delta * t;
  }

  function minutesToColor(m) {
    var stops = CONFIG.colorStops;
    if (m <= stops[0].min) return oklchToColor(stops[0].oklch);
    if (m >= stops[stops.length - 1].min) return oklchToColor(stops[stops.length - 1].oklch);
    for (var i = 0; i < stops.length - 1; i++) {
      if (m >= stops[i].min && m <= stops[i + 1].min) {
        var t = (m - stops[i].min) / (stops[i + 1].min - stops[i].min);
        var c0 = stops[i].oklch, c1 = stops[i + 1].oklch;
        return oklchToColor([
          c0[0] + (c1[0] - c0[0]) * t,
          c0[1] + (c1[1] - c0[1]) * t,
          interpolateHue(c0[2], c1[2], t),
        ]);
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
