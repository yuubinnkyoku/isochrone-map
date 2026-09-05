#!/usr/bin/env node
import fs from 'node:fs';
import os from 'node:os';
import crypto from 'node:crypto';
import zlib from 'node:zlib';
import { Worker, isMainThread, parentPort, workerData } from 'node:worker_threads';
import { fileURLToPath } from 'node:url';

const STEP_DEG = 0.001;
const MARGIN_DEG = 0.60;
const SCALE = 25;
const OFFSET_MINUTES = 1440;
const NODATA = 65535;
const DEFAULT_POWER = 2.5;

function mercatorY(latDeg) {
  const lat = Math.max(-85.05112878, Math.min(85.05112878, latDeg)) * Math.PI / 180;
  return Math.log(Math.tan(Math.PI / 4 + lat / 2));
}

function projectStations(stations) {
  return stations.map((s) => ({
    x: s.lng * Math.PI / 180,
    y: mercatorY(s.lat),
    minutes: s.minutes,
  }));
}

function calcValue(lat, lng, stations, halfPower) {
  const x = lng * Math.PI / 180;
  const y = mercatorY(lat);
  let num = 0;
  let den = 0;
  for (let i = 0; i < stations.length; i++) {
    const s = stations[i];
    const dx = x - s.x;
    const dy = y - s.y;
    const distSq = dx * dx + dy * dy;
    if (distSq < 1e-16) return s.minutes;
    const w = 1 / Math.pow(distSq, halfPower);
    num += w * s.minutes;
    den += w;
  }
  return den > 0 ? num / den : 0;
}

if (!isMainThread) {
  const { rowStart, rowEnd, cols, north, west, step, stations, power, scale, offsetMinutes } = workerData;
  const projected = projectStations(stations);
  const halfPower = power / 2;
  const out = new Uint16Array((rowEnd - rowStart) * cols);
  let k = 0;
  for (let r = rowStart; r < rowEnd; r++) {
    const lat = north - r * step;
    for (let c = 0; c < cols; c++) {
      const lng = west + c * step;
      const value = calcValue(lat, lng, projected, halfPower);
      out[k++] = Math.max(0, Math.min(65534, Math.round((value + offsetMinutes) * scale)));
    }
  }
  parentPort.postMessage({ rowStart, rowEnd, buffer: out.buffer }, [out.buffer]);
} else {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const stationsPath = process.argv[2] || `${root}/data/stations.json`;
  const outputBase = process.argv[3] || `${root}/data/idw-grid`;
  const doc = JSON.parse(fs.readFileSync(stationsPath, 'utf8'));
  const sourceStations = doc.stations;
  const stations = sourceStations.filter((s) => !s.excludeFromIdw);
  const excludedStations = sourceStations.filter((s) => s.excludeFromIdw);
  const power = DEFAULT_POWER;

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
  console.log(`stations=${stations.length}/${sourceStations.length} IDW/source excluded=${excludedStations.length} rows=${rows} cols=${cols} points=${(rows * cols).toLocaleString()} workers=${workerCount}`);
  console.log(`bounds=${south},${west} .. ${north},${east} step=${STEP_DEG}`);

  const chunks = [];
  const started = Date.now();
  for (let i = 0; i < workerCount; i++) {
    const rowStart = Math.floor(rows * i / workerCount);
    const rowEnd = Math.floor(rows * (i + 1) / workerCount);
    chunks.push(new Promise((resolve, reject) => {
      const worker = new Worker(new URL(import.meta.url), {
        workerData: { rowStart, rowEnd, cols, north, west, step: STEP_DEG, stations, power, scale: SCALE, offsetMinutes: OFFSET_MINUTES },
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
    algorithm: 'IDW on Web Mercator coordinates',
    idwPower: power,
    stationCount: stations.length,
    sourceStationCount: sourceStations.length,
    excludedStationCount: excludedStations.length,
    excludedStationIds: excludedStations.map((s) => s.id),
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
