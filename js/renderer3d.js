// ============================================================
// renderer3d.js — Three.js 3D地形表示（地図テクスチャ＋グラデーション）
// ============================================================
(function () {
  'use strict';

  // Web Mercator tile math
  function lng2tile(lng, zoom) { return Math.floor((lng + 180) / 360 * Math.pow(2, zoom)); }
  function lat2tile(lat, zoom) {
    return Math.floor((1 - Math.log(Math.tan(lat * Math.PI / 180) + 1 / Math.cos(lat * Math.PI / 180)) / Math.PI) / 2 * Math.pow(2, zoom));
  }
  function tile2lng(x, zoom) { return x / Math.pow(2, zoom) * 360 - 180; }
  function tile2lat(y, zoom) {
    var n = Math.PI - 2 * Math.PI * y / Math.pow(2, zoom);
    return 180 / Math.PI * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));
  }

  var Renderer3D = {
    _scene: null,
    _camera: null,
    _renderer: null,
    _controls: null,
    _mapMesh: null,
    _gradMesh: null,
    _peakMarker: null,
    _active: false,
    _stations: [],
    _requestId: 0,
    _container: null,
    _animFrameId: null,
    _pendingGrid: null,
    _mapMode: 'texture',  // 'texture' or 'flat'
    _flatPlaneHeight: 4,
    _flatMesh: null,
    _lastGrid: null,
    _lastMapTexture: null,
    _lastUV: null,

    init: function () {
      this._container = document.getElementById('canvas-3d');
    },

    _ensureScene: function () {
      if (this._scene) return;

      var w = window.innerWidth;
      var h = window.innerHeight;

      this._scene = new THREE.Scene();
      this._updateBackground();

      this._camera = new THREE.PerspectiveCamera(60, w / h, 0.1, 2000);
      this._camera.position.set(0, 160, 200);

      this._renderer = new THREE.WebGLRenderer({ antialias: true });
      this._renderer.setPixelRatio(window.devicePixelRatio);
      this._renderer.setSize(w, h);
      this._container.appendChild(this._renderer.domElement);

      this._controls = new THREE.OrbitControls(this._camera, this._renderer.domElement);
      this._controls.enableDamping = true;
      this._controls.dampingFactor = 0.08;
      this._controls.minDistance = 30;
      this._controls.maxDistance = 600;
      this._controls.maxPolarAngle = Math.PI / 2.1;
      this._controls.target.set(0, 20, 0);

      this._scene.add(new THREE.AmbientLight(0xffffff, 0.7));
      var dir = new THREE.DirectionalLight(0xffffff, 0.6);
      dir.position.set(100, 200, 80);
      this._scene.add(dir);

      this._boundResize = this._onResize.bind(this);
      window.addEventListener('resize', this._boundResize);
    },

    _updateBackground: function () {
      if (!this._scene) return;
      var theme = document.documentElement.getAttribute('data-theme');
      this._scene.background = new THREE.Color(theme === 'dark' ? 0x1a1a2e : 0xf5f3ef);
    },

    _onResize: function () {
      if (!this._renderer) return;
      var w = window.innerWidth;
      var h = window.innerHeight;
      this._camera.aspect = w / h;
      this._camera.updateProjectionMatrix();
      this._renderer.setSize(w, h);
    },

    setStations: function (stations) {
      this._stations = stations;
      if (this._active) this._requestGrid();
    },

    _requestGrid: function () {
      if (this._stations.length === 0) return;

      var latMin = Infinity, latMax = -Infinity;
      var lngMin = Infinity, lngMax = -Infinity;
      for (var i = 0; i < this._stations.length; i++) {
        var s = this._stations[i];
        if (s.lat < latMin) latMin = s.lat;
        if (s.lat > latMax) latMax = s.lat;
        if (s.lng < lngMin) lngMin = s.lng;
        if (s.lng > lngMax) lngMax = s.lng;
      }
      var dLat = (latMax - latMin) * 0.1;
      var dLng = (lngMax - lngMin) * 0.1;
      latMin -= dLat; latMax += dLat;
      lngMin -= dLng; lngMax += dLng;

      this._gridExtent = { latMin: latMin, latMax: latMax, lngMin: lngMin, lngMax: lngMax };

      if (!window.PrecomputedGrid || !PrecomputedGrid.ready) return;
      this._requestId++;
      var cols = 200, rows = 200;
      var grid = new Float32Array(cols * rows);
      var rowStep = (latMax - latMin) / (rows - 1);
      var colStep = (lngMax - lngMin) / (cols - 1);
      for (var r = 0; r < rows; r++) {
        var lat = latMax - r * rowStep;
        for (var c = 0; c < cols; c++) {
          var lng = lngMin + c * colStep;
          var value = PrecomputedGrid.sample(lat, lng);
          grid[r * cols + c] = value === null ? 0 : value;
        }
      }
      this._pendingGrid = { grid: grid, cols: cols, rows: rows };
      this._loadMapTiles();
    },

    // --- Map tile loading ---
    _loadMapTiles: function () {
      var ext = this._gridExtent;
      var zoom = 11;
      var txMin = lng2tile(ext.lngMin, zoom);
      var txMax = lng2tile(ext.lngMax, zoom);
      var tyMin = lat2tile(ext.latMax, zoom); // note: lat↑ = tileY↓
      var tyMax = lat2tile(ext.latMin, zoom);

      var tilesX = txMax - txMin + 1;
      var tilesY = tyMax - tyMin + 1;

      // Canvas for stitched map
      var tileSize = 256;
      var cv = document.createElement('canvas');
      cv.width = tilesX * tileSize;
      cv.height = tilesY * tileSize;
      var ctx = cv.getContext('2d');

      // Get current tile URL template
      var settings = UIManager.getSettings();
      var tileId = settings.tileId || CONFIG.defaultTile[settings.theme];
      var tileDef = CONFIG.tiles[tileId] || CONFIG.tiles['gsi-pale'];
      var urlTemplate = tileDef.url;
      var tileFilter = tileDef.invert
        ? 'invert(1) hue-rotate(180deg) brightness(0.95) contrast(1.1)'
        : 'none';
      ctx.filter = tileFilter;

      var total = tilesX * tilesY;
      var loaded = 0;
      var self = this;

      for (var ty = tyMin; ty <= tyMax; ty++) {
        for (var tx = txMin; tx <= txMax; tx++) {
          (function (tx2, ty2) {
            var img = new Image();
            img.crossOrigin = 'anonymous';
            img.onload = function () {
              var dx = (tx2 - txMin) * tileSize;
              var dy = (ty2 - tyMin) * tileSize;
              ctx.drawImage(img, dx, dy, tileSize, tileSize);
              loaded++;
              if (loaded >= total) {
                self._onTilesLoaded(cv, zoom, txMin, tyMin, txMax, tyMax);
              }
            };
            img.onerror = function () {
              loaded++;
              if (loaded >= total) {
                self._onTilesLoaded(cv, zoom, txMin, tyMin, txMax, tyMax);
              }
            };
            var url = urlTemplate.replace('{z}', zoom).replace('{x}', tx2).replace('{y}', ty2);
            img.src = url;
          })(tx, ty);
        }
      }
    },

    _onTilesLoaded: function (cv, zoom, txMin, tyMin, txMax, tyMax) {
      if (!this._pendingGrid || !this._scene) return;

      var pg = this._pendingGrid;
      this._pendingGrid = null;

      // Tile extent in geographic coords
      var tileLngMin = tile2lng(txMin, zoom);
      var tileLngMax = tile2lng(txMax + 1, zoom);
      var tileLatMin = tile2lat(tyMax + 1, zoom);
      var tileLatMax = tile2lat(tyMin, zoom);

      // Compute UV mapping: how the grid extent maps into the tile canvas
      var uMin = (this._gridExtent.lngMin - tileLngMin) / (tileLngMax - tileLngMin);
      var uMax = (this._gridExtent.lngMax - tileLngMin) / (tileLngMax - tileLngMin);
      var vMin = 1 - (this._gridExtent.latMax - tileLatMin) / (tileLatMax - tileLatMin);
      var vMax = 1 - (this._gridExtent.latMin - tileLatMin) / (tileLatMax - tileLatMin);

      // Create texture
      var texture = new THREE.CanvasTexture(cv);
      texture.minFilter = THREE.LinearFilter;
      texture.magFilter = THREE.LinearFilter;
      if (this._renderer && this._renderer.capabilities) {
        var maxAnisotropy = this._renderer.capabilities.getMaxAnisotropy();
        texture.anisotropy = Math.max(1, Math.min(8, maxAnisotropy || 1));
      }

      // Cache for mode switching
      this._lastGrid = { grid: pg.grid, cols: pg.cols, rows: pg.rows };
      this._lastMapTexture = texture;
      this._lastUV = { uMin: uMin, uMax: uMax, vMin: vMin, vMax: vMax };

      this._buildMesh(pg.grid, pg.cols, pg.rows, texture, uMin, uMax, vMin, vMax);
    },

    _buildMesh: function (grid, cols, rows, mapTexture, uMin, uMax, vMin, vMax) {
      // Clean up old meshes (don't dispose the cached map texture)
      var cachedTex = this._lastMapTexture;
      var toRemove = [this._mapMesh, this._gradMesh, this._peakMarker, this._flatMesh];
      for (var ri = 0; ri < toRemove.length; ri++) {
        if (toRemove[ri]) {
          this._scene.remove(toRemove[ri]);
          toRemove[ri].geometry.dispose();
          if (toRemove[ri].material.map && toRemove[ri].material.map !== cachedTex) {
            toRemove[ri].material.map.dispose();
          }
          toRemove[ri].material.dispose();
        }
      }
      this._mapMesh = null;
      this._gradMesh = null;
      this._peakMarker = null;
      this._flatMesh = null;

      // Min/max
      var minMin = Infinity, maxMin = -Infinity;
      for (var i = 0; i < grid.length; i++) {
        var v = grid[i];
        if (v > 0) {
          if (v < minMin) minMin = v;
          if (v > maxMin) maxMin = v;
        }
      }
      if (minMin < 380) minMin = 380;
      if (maxMin > 500) maxMin = 500;
      var range = maxMin - minMin;
      if (range < 1) range = 1;

      // Scale
      var centerLat = (this._gridExtent.latMin + this._gridExtent.latMax) / 2;
      var cosLat = Math.cos(centerLat * Math.PI / 180);
      var latSpan = this._gridExtent.latMax - this._gridExtent.latMin;
      var lngSpan = this._gridExtent.lngMax - this._gridExtent.lngMin;

      var scaleBase = 200;
      var lngScale = scaleBase;
      var latScale = scaleBase * (latSpan / (lngSpan * cosLat));
      var heightScale = scaleBase * 0.6 / range;

      // Create terrain geometry (raised)
      var geom = new THREE.PlaneGeometry(lngScale, latScale, cols - 1, rows - 1);
      geom.rotateX(-Math.PI / 2);

      var posAttr = geom.attributes.position;
      var uvAttr = geom.attributes.uv;
      var colorArr = new Float32Array(posAttr.count * 3);
      var peakY = -Infinity, peakX = 0, peakZ = 0;

      for (var r = 0; r < rows; r++) {
        for (var c = 0; c < cols; c++) {
          var idx = r * cols + c;
          var val = grid[idx];
          if (val < minMin) val = minMin;
          if (val > maxMin) val = maxMin;

          var height = (val - minMin) * heightScale;
          posAttr.setY(idx, height);

          // Remap UVs to match tile texture
          var u = uMin + (c / (cols - 1)) * (uMax - uMin);
          // CanvasTexture uses flipY=true, so convert image-space v to UV-space.
          var vv = 1 - (vMin + (r / (rows - 1)) * (vMax - vMin));
          uvAttr.setXY(idx, u, vv);

          // Vertex colors for gradient overlay
          var rgb = minutesToColor(val);
          colorArr[idx * 3] = rgb[0] / 255;
          colorArr[idx * 3 + 1] = rgb[1] / 255;
          colorArr[idx * 3 + 2] = rgb[2] / 255;

          if (height > peakY) {
            peakY = height;
            peakX = posAttr.getX(idx);
            peakZ = posAttr.getZ(idx);
          }
        }
      }

      geom.setAttribute('color', new THREE.Float32BufferAttribute(colorArr, 3));
      geom.computeVertexNormals();

      if (this._mapMode === 'flat') {
        // === FLAT mode: adjustable flat map plane + colored terrain above ===

        // Flat map plane
        var flatGeom = new THREE.PlaneGeometry(lngScale, latScale, 1, 1);
        flatGeom.rotateX(-Math.PI / 2);
        // Remap UVs for the flat plane
        var flatUV = flatGeom.attributes.uv;
        var uvTop = 1 - vMin;
        var uvBottom = 1 - vMax;
        flatUV.setXY(0, uMin, uvTop);     // top-left
        flatUV.setXY(1, uMax, uvTop);     // top-right
        flatUV.setXY(2, uMin, uvBottom);  // bottom-left
        flatUV.setXY(3, uMax, uvBottom);  // bottom-right
        var flatMat = new THREE.MeshLambertMaterial({
          map: mapTexture,
          side: THREE.DoubleSide
        });
        this._flatMesh = new THREE.Mesh(flatGeom, flatMat);
        this._flatMesh.position.y = this._flatPlaneHeight;
        this._scene.add(this._flatMesh);

        // Terrain mesh with opaque vertex colors
        var terrainMat = new THREE.MeshLambertMaterial({
          vertexColors: true,
          transparent: true,
          opacity: 0.85,
          side: THREE.DoubleSide
        });
        this._mapMesh = new THREE.Mesh(geom, terrainMat);
        this._scene.add(this._mapMesh);

      } else {
        // === TEXTURE mode: map texture on terrain + semi-transparent gradient ===

        // Mesh 1: Map texture (base)
        var mapMat = new THREE.MeshLambertMaterial({
          map: mapTexture,
          side: THREE.FrontSide,
          polygonOffset: true,
          polygonOffsetFactor: 1,
          polygonOffsetUnits: 1
        });
        this._mapMesh = new THREE.Mesh(geom, mapMat);
        this._mapMesh.renderOrder = 1;
        this._scene.add(this._mapMesh);

        // Mesh 2: Gradient overlay (semi-transparent)
        var gradGeom = geom.clone();
        var gradMat = new THREE.MeshLambertMaterial({
          vertexColors: true,
          transparent: true,
          opacity: 0.5,
          side: THREE.FrontSide,
          depthWrite: false,
          polygonOffset: true,
          polygonOffsetFactor: -1,
          polygonOffsetUnits: -1
        });
        this._gradMesh = new THREE.Mesh(gradGeom, gradMat);
        // Keep draw order stable between base map and transparent overlay.
        this._gradMesh.renderOrder = 2;
        this._scene.add(this._gradMesh);
      }

      // Peak marker
      var sphereGeom = new THREE.SphereGeometry(2.5, 16, 16);
      var sphereMat = new THREE.MeshLambertMaterial({ color: 0xff6b6b, emissive: 0xff3333, emissiveIntensity: 0.3 });
      this._peakMarker = new THREE.Mesh(sphereGeom, sphereMat);
      this._peakMarker.position.set(peakX, peakY + 3, peakZ);
      this._scene.add(this._peakMarker);

      if (this._controls) {
        this._controls.target.set(0, peakY * 0.3, 0);
        this._controls.update();
      }
    },

    setMapMode: function (mode) {
      if (this._mapMode === mode) return;
      this._mapMode = mode;
      // Rebuild if we have cached data
      if (this._lastGrid && this._lastMapTexture && this._scene) {
        var g = this._lastGrid;
        var uv = this._lastUV;
        // Clone the texture since _buildMesh disposes old textures
        var tex = this._lastMapTexture.clone();
        tex.needsUpdate = true;
        this._lastMapTexture = tex;
        this._buildMesh(g.grid, g.cols, g.rows, tex, uv.uMin, uv.uMax, uv.vMin, uv.vMax);
      }
    },

    refreshMapTexture: function () {
      // Reuse cached terrain data and rebuild only the map texture with current tile setting.
      if (!this._scene || !this._gridExtent || !this._lastGrid) return;
      this._pendingGrid = {
        grid: this._lastGrid.grid,
        cols: this._lastGrid.cols,
        rows: this._lastGrid.rows
      };
      this._loadMapTiles();
    },

    setFlatPlaneHeight: function (height) {
      var n = parseFloat(height);
      if (!isFinite(n)) return;
      this._flatPlaneHeight = n;
      if (this._flatMesh) {
        this._flatMesh.position.y = n;
      }
    },

    show: function () {
      try {
        this._ensureScene();
      } catch (e) {
        console.error('WebGL初期化失敗:', e.message);
        alert('WebGLが利用できません。\nabout:config で webgl.force-enabled を true にしてください。');
        return;
      }
      this._container.style.display = 'block';
      this._active = true;
      this._updateBackground();
      this._startLoop();
      if (this._stations.length > 0 && !this._mapMesh) {
        this._requestGrid();
      }
      this._onResize();
    },

    hide: function () {
      this._container.style.display = 'none';
      this._active = false;
      this._stopLoop();
    },

    updateTheme: function () {
      this._updateBackground();
    },

    _startLoop: function () {
      if (this._animFrameId) return;
      if (!this._renderer || !this._controls) return;
      var self = this;
      function loop() {
        if (!self._active) return;
        self._animFrameId = requestAnimationFrame(loop);
        self._controls.update();
        self._renderer.render(self._scene, self._camera);
      }
      loop();
    },

    _stopLoop: function () {
      if (this._animFrameId) {
        cancelAnimationFrame(this._animFrameId);
        this._animFrameId = null;
      }
    },
  };

  window.Renderer3D = Renderer3D;
})();
