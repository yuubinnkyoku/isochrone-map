#!/usr/bin/env node
import fs from 'node:fs';
import os from 'node:os';
import crypto from 'node:crypto';
import zlib from 'node:zlib';
import { Worker, isMainThread, parentPort, workerData } from 'node:worker_threads';
import { fileURLToPath } from 'node:url';

// The access-aware surface starts from the validated IDW field and then
// subtracts an explicit access penalty based on distance to the nearest known
// interpolation anchor. Anchors include every physical station and the school
// destination itself, so the destination remains a true 08:18 endpoint.
const WALKING_SPEED_M_PER_MIN = 80;
const DETOUR_FACTOR = 1.25;
const PENALTY_MIN_PER_M = DETOUR_FACTOR / WALKING_SPEED_M_PER_MIN;
const EARTH_RADIUS_M = 6371008.8;
const PROJECTION_LAT_DEG = 35.7;
const COS0 = Math.cos(PROJECTION_LAT_DEG * Math.PI / 180);

function project(lat, lng) {
  return {
    x: EARTH_RADIUS_M * lng * Math.PI / 180 * COS0,
    y: EARTH_RADIUS_M * lat * Math.PI / 180,
  };
}

function projectAnchors(anchors) {
  return anchors.map((s) => {
    const p = project(Number(s.lat), Number(s.lng));
    return { x: p.x, y: p.y, id: s.id };
  });
}

function buildKdTree(points, depth = 0) {
  if (!points.length) return null;
  const axis = depth & 1;
  const key = axis === 0 ? 'x' : 'y';
  points.sort((a, b) => a[key] - b[key]);
  const mid = points.length >> 1;
  return {
    point: points[mid],
    axis,
    left: buildKdTree(points.slice(0, mid), depth + 1),
    right: buildKdTree(points.slice(mid + 1), depth + 1),
  };
}

function nearestDistanceSq(node, x, y, bestSq = Infinity) {
  if (!node) return bestSq;
  const p = node.point;
  const dx = x - p.x;
  const dy = y - p.y;
  const d2 = dx * dx + dy * dy;
  if (d2 < bestSq) bestSq = d2;

  const delta = node.axis === 0 ? dx : dy;
  const first = delta < 0 ? node.left : node.right;
  const second = delta < 0 ? node.right : node.left;
  bestSq = nearestDistanceSq(first, x, y, bestSq);
  if (delta * delta < bestSq) bestSq = nearestDistanceSq(second, x, y, bestSq);
  return bestSq;
}

function decode(encoded, meta) {
  return encoded / meta.scale - (meta.offsetMinutes || 0);
}

function encode(value, meta) {
  return Math.max(0, Math.min(65534, Math.round((value + (meta.offsetMinutes || 0)) * meta.scale)));
}

function validateInterpolationAnchors(anchorDoc) {
  const anchors = Array.isArray(anchorDoc?.anchors) ? anchorDoc.anchors : [];
  const ids = new Set();
  for (const anchor of anchors) {
    if (!anchor.id || ids.has(anchor.id)) throw new Error(`Invalid or duplicate interpolation anchor id: ${anchor.id}`);
    ids.add(anchor.id);
    for (const key of ['lat', 'lng', 'minutes']) {
      if (!Number.isFinite(Number(anchor[key]))) throw new Error(`Invalid ${key} for interpolation anchor ${anchor.id}`);
    }
  }
  return anchors;
}

if (!isMainThread) {
  const { rowStart, rowEnd, accessAnchors, idwPath, meta } = workerData;
  const projected = projectAnchors(accessAnchors);
  const tree = buildKdTree(projected.slice());
  const idw = fs.readFileSync(idwPath);
  const out = new Uint16Array((rowEnd - rowStart) * meta.cols);
  let k = 0;

  for (let r = rowStart; r < rowEnd; r++) {
    const lat = meta.north - r * meta.latStep;
    for (let c = 0; c < meta.cols; c++) {
      const sourceIndex = r * meta.cols + c;
      const encodedIdw = idw.readUInt16LE(sourceIndex * 2);
      if (encodedIdw === meta.nodata) {
        out[k++] = meta.nodata;
        continue;
      }

      const lng = meta.west + c * meta.lngStep;
      const q = project(lat, lng);
      const nearestM = Math.sqrt(nearestDistanceSq(tree, q.x, q.y));
      const idwMinutes = decode(encodedIdw, meta);
      const accessMinutes = idwMinutes - nearestM * PENALTY_MIN_PER_M;
      out[k++] = encode(accessMinutes, meta);
    }
  }

  parentPort.postMessage({ rowStart, rowEnd, buffer: out.buffer }, [out.buffer]);
} else {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const stationsPath = process.argv[2] || `${root}/data/stations.json`;
  const outputBase = process.argv[3] || `${root}/data/access-grid`;
  const idwBase = process.argv[4] || `${root}/data/idw-grid`;
  const anchorsPath = process.argv[5] || `${root}/data/interpolation-anchors.json`;
  const idwPath = `${idwBase}.bin`;
  const idwMetaPath = `${idwBase}.meta.json`;

  const stationDoc = JSON.parse(fs.readFileSync(stationsPath, 'utf8'));
  const stations = stationDoc.stations;
  const anchorBytes = fs.readFileSync(anchorsPath);
  const interpolationAnchors = validateInterpolationAnchors(JSON.parse(anchorBytes.toString('utf8')));
  const accessAnchors = stations.concat(interpolationAnchors);
  const meta = JSON.parse(fs.readFileSync(idwMetaPath, 'utf8'));
  const expectedBytes = meta.rows * meta.cols * 2;
  if (fs.statSync(idwPath).size !== expectedBytes) {
    throw new Error(`IDW grid size mismatch: expected ${expectedBytes}, got ${fs.statSync(idwPath).size}`);
  }
  if (meta.sourceStationCount !== stations.length || meta.stationCount !== stations.length || meta.excludedStationCount !== 0) {
    throw new Error(`IDW metadata does not represent all ${stations.length} physical stations`);
  }
  if (meta.interpolationAnchorCount !== interpolationAnchors.length || meta.sampleCount !== accessAnchors.length) {
    throw new Error(`IDW metadata does not represent all interpolation anchors: expected ${accessAnchors.length} samples`);
  }

  const stationBytes = fs.readFileSync(stationsPath);
  const stationSha256 = crypto.createHash('sha256').update(stationBytes).digest('hex');
  const anchorSha256 = crypto.createHash('sha256').update(anchorBytes).digest('hex');
  if (meta.stationDataSha256 !== stationSha256) {
    throw new Error(`IDW grid was built from different stations.json: ${meta.stationDataSha256} != ${stationSha256}`);
  }
  if (meta.interpolationAnchorDataSha256 !== anchorSha256) {
    throw new Error(`IDW grid was built from different interpolation anchors: ${meta.interpolationAnchorDataSha256} != ${anchorSha256}`);
  }

  const workerCount = Math.max(1, Math.min(os.availableParallelism?.() || os.cpus().length || 1, 8, meta.rows));
  console.log(`accessAnchors=${accessAnchors.length} stations=${stations.length} extraAnchors=${interpolationAnchors.length} rows=${meta.rows} cols=${meta.cols} points=${(meta.rows * meta.cols).toLocaleString()} workers=${workerCount}`);
  console.log('model=IDW(x) - detourFactor * nearestInterpolationAnchorDistance(x) / walkingSpeed');
  console.log(`walkingSpeed=${WALKING_SPEED_M_PER_MIN}m/min detourFactor=${DETOUR_FACTOR} effective=${(WALKING_SPEED_M_PER_MIN / DETOUR_FACTOR).toFixed(2)}m/min`);

  const chunks = [];
  const started = Date.now();
  for (let i = 0; i < workerCount; i++) {
    const rowStart = Math.floor(meta.rows * i / workerCount);
    const rowEnd = Math.floor(meta.rows * (i + 1) / workerCount);
    chunks.push(new Promise((resolve, reject) => {
      const worker = new Worker(new URL(import.meta.url), {
        workerData: { rowStart, rowEnd, accessAnchors, idwPath, meta },
      });
      worker.on('message', resolve);
      worker.on('error', reject);
      worker.on('exit', (code) => { if (code !== 0) reject(new Error(`worker exited ${code}`)); });
    }));
  }

  const results = (await Promise.all(chunks)).sort((a, b) => a.rowStart - b.rowStart);
  const output = Buffer.allocUnsafe(expectedBytes);
  let offset = 0;
  for (const chunk of results) {
    const values = new Uint16Array(chunk.buffer);
    for (let i = 0; i < values.length; i++, offset += 2) output.writeUInt16LE(values[i], offset);
  }

  fs.writeFileSync(`${outputBase}.bin`, output);
  const compressed = zlib.gzipSync(output, { level: 9 });
  fs.writeFileSync(`${outputBase}.bin.gz`, compressed);

  const accessMeta = {
    ...meta,
    version: 3,
    mode: 'access',
    algorithm: 'IDW with nearest interpolation-anchor walking-access penalty',
    formula: 'IDW(x) - streetDetourFactor * nearestInterpolationAnchorDistanceMeters(x) / walkingSpeedMetersPerMinute',
    approximation: 'Nearest anchor distance is straight-line distance multiplied by a fixed street-detour factor; anchors are physical stations plus the school destination; not a pedestrian-network route search',
    baseGrid: 'idw-grid',
    baseAlgorithm: meta.algorithm,
    walkingSpeedMetersPerMinute: WALKING_SPEED_M_PER_MIN,
    streetDetourFactor: DETOUR_FACTOR,
    effectiveStraightLineSpeedMetersPerMinute: WALKING_SPEED_M_PER_MIN / DETOUR_FACTOR,
    projectionLatitude: PROJECTION_LAT_DEG,
    stationDataSha256: stationSha256,
    interpolationAnchorDataSha256: anchorSha256,
  };
  fs.writeFileSync(`${outputBase}.meta.json`, JSON.stringify(accessMeta, null, 2) + '\n');
  console.log(`wrote ${outputBase}.bin (${output.length.toLocaleString()} bytes)`);
  console.log(`wrote ${outputBase}.bin.gz (${compressed.length.toLocaleString()} bytes)`);
  console.log(`wrote ${outputBase}.meta.json`);
  console.log(`elapsed=${((Date.now() - started) / 1000).toFixed(1)}s stationSha256=${stationSha256} anchorSha256=${anchorSha256}`);
}
