// ============================================================
// map.js — Leaflet地図初期化・タイル切替
// ============================================================
(function () {
  'use strict';

  // The isochrone overlays are client-rendered GridLayers. Leaflet's keepBuffer
  // only retains tiles that were already created; it does not request tiles just
  // outside the viewport. Expand the requested pixel bounds by one tile for
  // overlayPane GridLayers so the next ring is rendered before a pan or zoom-out
  // exposes it. Base-map TileLayers stay unchanged.
  (function installOverlayTilePrefetch() {
    if (!window.L || !L.GridLayer || !L.GridLayer.prototype._getTiledPixelBounds) return;
    var originalGetTiledPixelBounds = L.GridLayer.prototype._getTiledPixelBounds;
    if (originalGetTiledPixelBounds._isochronePrefetchPatched) return;

    function getPrefetchedBounds(center) {
      var bounds = originalGetTiledPixelBounds.call(this, center);
      if (!this.options || this.options.pane !== 'overlayPane') return bounds;

      var tileSize = this.getTileSize();
      var pad = L.point(tileSize.x, tileSize.y);
      return L.bounds(bounds.min.subtract(pad), bounds.max.add(pad));
    }

    getPrefetchedBounds._isochronePrefetchPatched = true;
    L.GridLayer.prototype._getTiledPixelBounds = getPrefetchedBounds;
  })();

  var MapManager = {
    map: null,
    currentTileId: null,
    tileLayer: null,
    userChangedTile: false,

    init: function () {
      this.map = L.map('map', {
        center: CONFIG.defaultCenter,
        zoom: CONFIG.defaultZoom,
        zoomControl: true,
        attributionControl: true,
      });

      var theme = document.documentElement.getAttribute('data-theme') || 'light';
      var tileId = CONFIG.defaultTile[theme];
      this.setTile(tileId);

      return this.map;
    },

    setTile: function (id) {
      if (this.currentTileId === id) return;
      var def = CONFIG.tiles[id];
      if (!def) return;

      if (this.tileLayer) {
        this.map.removeLayer(this.tileLayer);
      }
      this.tileLayer = L.tileLayer(def.url, {
        maxZoom: def.maxZoom,
        attribution: def.attribution,
      }).addTo(this.map);
      this.currentTileId = id;

      // Apply CSS invert filter for dark tiles derived from light sources
      var tilePane = this.map.getPane('tilePane');
      if (tilePane) {
        tilePane.classList.toggle('invert-tiles', !!def.invert);
      }
    },

    onThemeChanged: function (theme) {
      this.userChangedTile = false;
      var tileId = CONFIG.defaultTile[theme];
      this.setTile(tileId);
    },

    setTileByUser: function (id) {
      this.userChangedTile = true;
      this.setTile(id);
    },
  };

  window.MapManager = MapManager;
})();
