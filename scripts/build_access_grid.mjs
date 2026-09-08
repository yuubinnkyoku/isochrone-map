#!/usr/bin/env node
import fs from 'node:fs';
import os from 'node:os';
import crypto from 'node:crypto';
import zlib from 'node:zlib';
import { Worker, isMainThread, parentPort, workerData } from 'node:worker_threads';
import { fileURLToPath } from 'node:url';

// Keep the same extent/resolution/encoding as the existing IDW grid so the
// browser can switch methods without changing any rendering code.
const STEP_DEG = 0.001;
const MARGIN_DEG = 0.60;
const SCALE = 25;
const OFFSET_MINUTES = 1440;
const NODATA = 65535;

// Approximate access from an arbitrary point to a physical station point.
// Yahoo's exact-point values remain the authoritative station values; this
// model is only for the space between them. 80 m/min represents brisk walking,
// while the detour factor accounts for streets not following straight lines.
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

function projectStations(stations) {
  return stations.map((s) => {
    const p = project(s.lat, s.lng);
    return { x: p.x, y: p.y, minutes: s.minutes, id: s.id };
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

function nearest(node, x, y, best) {
  if (!node) return best;
  const p = node.point;
  const dx = x - p.x;
  const dy = y - p.y;
  const d2 = dx * dx + dy * dy;
  if (!best || d2 < best.d2) best = { point: p, d2 };

  const delta = node.axis === 0 ? dx : dy;
  const first = delta < 0 ? node.left : node.right;
  const second = delta < 0 ? node.right : node.left;
  best = nearest(first, x, y, best);
  if (delta * delta < best.d2) best = nearest(second, x, y, best);
  return best;
}

function forEachWithin(node, x, y, radiusSq, fn) {
  if (!node) return;
  const p = node.point;
  const dx = x - p.x;
  const dy = y - p.y;
  const d2 = dx * dx + dy * dy;
  if (d2 <= radiusSq) fn(p, d2);

  const delta = node.axis === 0 ? dx : dy;
  if (delta <= 0) {
    forEachWithin(node.left, x, y, radiusSq, fn);
    if (delta * delta <= radiusSq) forEachWithin(node.right, x, y, radiusSq, fn);
  } else {
    forEachWithin(node.right, x, y, radiusSq, fn);
    if (delta * delta <= radiusSq) forEachWithin(node.left, x, y, radiusSq, fn);
  }
}

function calcAccessValue(lat, lng, tree, maxStationMinutes) {
  const q = project(lat, lng);
  const n = nearest(tree, q.x, q.y, null);
  if (!n) return null;

  // Start with the nearest physical station. Once we have any candidate B,
  // a station farther than (maxStationMinutes - B) / penalty cannot beat B,
  // even if it has the globally latest station departure. This gives an exact
  // max-plus result for this distance model without scanning all 1563 stations.
  let best = n.point.minutes - Math.sqrt(n.d2) * PENALTY_MIN_PER_M;
  let radius = Math.max(0, (maxStationMinutes - best) / PENALTY_MIN_PER_M);
  let radiusSq = radius * radius;

  forEachWithin(tree, q.x, q.y, radiusSq, (s, d2) => {
    const value = s.minutes - Math.sqrt(d2) * PENALTY_MIN_PER_M;
    if (value > best) best = value;
  });
  return best;
}

if (!isMainThread) {
  const { rowStart, rowEnd, cols, north, west, step, stations, scale, offsetMinutes } = workerData;
  const projected = projectStations(stations);
  const tree = buildKdTree(projected.slice());
  const maxStationMinutes = Math.max(...projected.map((s) => s.minutes));
  const out = new Uint16Array((rowEnd - rowStart) * cols);
  let k = 0;

  for (let r = rowStart; r < rowEnd; r++) {
    const lat = north - r * step;
    for (let c = 0; c < cols; c++) {
      const lng = west + c * step;
      const value = calcAccessValue(lat, lng, tree, maxStationMinutes);
      if (value === null || !Number.isFinite(value)) {
        out[k++] = NODATA;
      } else {
        out[k++] = Math.max(0, Math.min(65534, Math.round((value + offsetMinutes) * scale)));
      }
    }
  }
  parentPort.postMessage({ rowStart, rowEnd, buffer: out.buffer }, [out.buffer]);
} else {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const stationsPath = process.argv[2] || `${root}/data/stations.json`;
  const outputBase = process.argv[3] || `${root}/data/access-grid`;
  const doc = JSON.parse(fs.readFileSync(stationsPath, 'utf8'));
  // All exact-point physical stations are valid accessibility anchors.
  const stations = doc.stations;

  const minLat = Math.min(...stations.map((s) => s.lat));
  const maxLat = Math.max(...stations.map((s) => s.lat));
  const minLng = Math.min(...stations.map((s) => s.lng));
  const maxLng = Math.max(...stations.map((s) => s.lng));
  const south = Math.floor((minLat - MARGIN_DEG) / STEP_DEG) * STEP_DEG;
  const north = Math.ceil((maxLat + MARGIN_DEG) / STEP_DEG) * STEP_DEG;
  const west = Math.floor((minLng - MARGIN_DEG) / STEP_DEG) * STEP_DEG;
  const east = Math.ceil((maxLng + MARGIN_DEG) / STEP_DEG) * STEP_DEG;
  const rows = Math.round((north - south) / STEP_DEG) + 1;
  const cols = Math.round((east - west) / STEP_DEG) + 1;

  const stationBytes = fs.readFileSync(stationsPath);
  const stationSha256 = crypto.createHash('sha256').update(stationBytes).digest('hex');
  const workerCount = Math.max(1, Math.min(os.availableParallelism?.() || os.cpus().length || 1, 8, rows));
  console.log(`stations=${stations.length} rows=${rows} cols=${cols} points=${(rows * cols).toLocaleString()} workers=${workerCount}`);
  console.log(`model=max_i(t_i - detourFactor * straightLineDistance / walkingSpeed)`);
  console.log(`walkingSpeed=${WALKING_SPEED_M_PER_MIN}m/min detourFactor=${DETOUR_FACTOR} effective=${(WALKING_SPEED_M_PER_MIN / DETOUR_FACTOR).toFixed(2)}m/min`);

  const chunks = [];
  const started = Date.now();
  for (let i = 0; i < workerCount; i++) {
    const rowStart = Math.floor(rows * i / workerCount);
    const rowEnd = Math.floor(rows * (i + 1) / workerCount);
    chunks.push(new Promise((resolve, reject) => {
      const worker = new Worker(new URL(import.meta.url), {
        workerData: {
          rowStart, rowEnd, cols, north, west, step: STEP_DEG,
          stations, scale: SCALE, offsetMinutes: OFFSET_MINUTES,
        },
      });
      worker.on('message', resolve);
      worker.on('error', reject);
      worker.on('exit', (code) => { if (code !== 0) reject(new Error(`worker exited ${code}`)); });
    }));
  }

  const results = (await Promise.all(chunks)).sort((a, b) => a.rowStart - b.rowStart);
  const output = Buffer.allocUnsafe(rows * cols * 2);
  let offset = 0;
  for (const chunk of results) {
    const values = new Uint16Array(chunk.buffer);
    for (let i = 0; i < values.length; i++, offset += 2) output.writeUInt16LE(values[i], offset);
  }

  fs.writeFileSync(`${outputBase}.bin`, output);
  const compressed = zlib.gzipSync(output, { level: 9 });
  fs.writeFileSync(`${outputBase}.bin.gz`, compressed);
  const meta = {
    version: 1,
    mode: 'access',
    algorithm: 'Max-plus station accessibility with approximate walking access',
    formula: 'max_i(stationMinutes_i - detourFactor * straightLineDistanceMeters_i / walkingSpeedMetersPerMinute)',
    approximation: 'Straight-line distance multiplied by a fixed street-detour factor; not a pedestrian-network route search',
    walkingSpeedMetersPerMinute: WALKING_SPEED_M_PER_MIN,
    streetDetourFactor: DETOUR_FACTOR,
    effectiveStraightLineSpeedMetersPerMinute: WALKING_SPEED_M_PER_MIN / DETOUR_FACTOR,
    projectionLatitude: PROJECTION_LAT_DEG,
    stationCount: stations.length,
    sourceStationCount: stations.length,
    excludedStationCount: 0,
    excludedStationIds: [],
    stationDataSha256: stationSha256,
    rows,
    cols,
    north,
    south,
    west,
    east,
    latStep: STEP_DEG,
    lngStep: STEP_DEG,
    valueType: 'uint16-le',
    scale: SCALE,
    offsetMinutes: OFFSET_MINUTES,
    nodata: NODATA,
  };
  fs.writeFileSync(`${outputBase}.meta.json`, JSON.stringify(meta, null, 2) + '\n');
  console.log(`wrote ${outputBase}.bin (${output.length.toLocaleString()} bytes)`);
  console.log(`wrote ${outputBase}.bin.gz (${compressed.length.toLocaleString()} bytes)`);
  console.log(`wrote ${outputBase}.meta.json`);
  console.log(`elapsed=${((Date.now() - started) / 1000).toFixed(1)}s sha256=${stationSha256}`);
}
