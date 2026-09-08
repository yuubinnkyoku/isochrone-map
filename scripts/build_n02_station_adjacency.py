#!/usr/bin/env python3
"""Derive route-topological adjacent stations for project N02 station codes.

N02 represents both Station and RailroadSection as LineStrings.  Every 2025 Station
geometry vertex is also a RailroadSection vertex, so adjacency can be derived without
assuming geographic nearest-neighbour order:

1. Build an undirected graph from consecutive RailroadSection vertices separately for
   each exact (N02_003 line, N02_004 operator) pair.
2. Treat the full LineString vertex set of the target station as the station body.
3. From every graph edge leaving that body, walk along the railway graph until the
   first *other* station geometry is encountered.  Those first hits are exactly the
   neighboring stations in each topological direction/branch.

This handles loops and junctions and can return an adjacent station outside the 50-km
project point set, which is useful as a Yahoo `via01` constraint.
"""
from __future__ import annotations

import argparse
import heapq
import io
import json
import math
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

from audit_n02_station_groups import SOURCE_URL, USER_AGENT

Coord = tuple[float, float]  # (lng, lat)
Key = tuple[str, str]        # (line, operator)


def qcoord(raw) -> Coord:
    return (round(float(raw[0]), 7), round(float(raw[1]), 7))


def edge_m(a: Coord, b: Coord) -> float:
    # Short N02 polyline segments: equirectangular distance is accurate enough for
    # path ordering and substantially cheaper than full haversine.
    lat = math.radians((a[1] + b[1]) / 2)
    dx = math.radians(b[0] - a[0]) * math.cos(lat)
    dy = math.radians(b[1] - a[1])
    return 6_371_008.8 * math.hypot(dx, dy)


def fetch_archive(url: str) -> tuple[dict, dict]:
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=180) as response:
        payload = response.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        stations = [n for n in z.namelist() if n.casefold().endswith('station.geojson')]
        rails = [n for n in z.namelist() if n.casefold().endswith('railroadsection.geojson')]
        if not stations or not rails:
            raise SystemExit('N02 archive missing Station/RailroadSection GeoJSON')
        stations.sort(key=lambda n: ('utf-8' not in n.casefold(), len(n), n))
        rails.sort(key=lambda n: ('utf-8' not in n.casefold(), len(n), n))
        return json.loads(z.read(stations[0])), json.loads(z.read(rails[0]))


def key_for(feature: dict) -> Key:
    p = feature.get('properties') or {}
    return (str(p.get('N02_003') or '').strip(), str(p.get('N02_004') or '').strip())


def first_station_from_boundary(
    graph: dict[Coord, dict[Coord, float]],
    station_at: dict[Coord, set[str]],
    target_code: str,
    target_vertices: set[Coord],
    start: Coord,
    initial_distance: float,
) -> list[tuple[str, float]]:
    heap = [(initial_distance, start)]
    best = {start: initial_distance}
    hit_distance = None
    hits: dict[str, float] = {}
    while heap:
        dist, node = heapq.heappop(heap)
        if dist != best.get(node):
            continue
        if hit_distance is not None and dist > hit_distance + 0.01:
            break
        codes = station_at.get(node, set()) - {target_code}
        if codes:
            hit_distance = dist if hit_distance is None else min(hit_distance, dist)
            for code in codes:
                hits[code] = min(hits.get(code, float('inf')), dist)
            continue
        for nxt, weight in graph.get(node, {}).items():
            if nxt in target_vertices:
                continue
            nd = dist + weight
            if hit_distance is not None and nd > hit_distance + 0.01:
                continue
            if nd < best.get(nxt, float('inf')):
                best[nxt] = nd
                heapq.heappush(heap, (nd, nxt))
    return sorted(hits.items(), key=lambda x: (x[1], x[0]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--proposal', default='data/physical-station-points-proposal.json')
    ap.add_argument('--source-url', default=SOURCE_URL)
    ap.add_argument('--output', default='data/n02-station-adjacency.json')
    args = ap.parse_args()

    proposal = json.loads(Path(args.proposal).read_text(encoding='utf-8'))
    target_codes = {
        str(code)
        for point in proposal.get('physicalPoints', [])
        for code in (point.get('n02StationCodes') or [])
    }
    station_geo, rail_geo = fetch_archive(args.source_url)

    graphs: dict[Key, dict[Coord, dict[Coord, float]]] = defaultdict(lambda: defaultdict(dict))
    for feature in rail_geo.get('features', []):
        key = key_for(feature)
        if not all(key):
            continue
        geom = feature.get('geometry') or {}
        if geom.get('type') != 'LineString':
            continue
        pts = [qcoord(v) for v in geom.get('coordinates', [])]
        for a, b in zip(pts, pts[1:]):
            if a == b:
                continue
            w = edge_m(a, b)
            old = graphs[key][a].get(b)
            if old is None or w < old:
                graphs[key][a][b] = w
                graphs[key][b][a] = w

    station_vertices: dict[Key, dict[str, set[Coord]]] = defaultdict(lambda: defaultdict(set))
    station_at: dict[Key, dict[Coord, set[str]]] = defaultdict(lambda: defaultdict(set))
    metadata: dict[tuple[Key, str], dict] = {}
    code_keys: dict[str, set[Key]] = defaultdict(set)
    for feature in station_geo.get('features', []):
        p = feature.get('properties') or {}
        code = str(p.get('N02_005c') or '').strip()
        key = key_for(feature)
        if not code or not all(key):
            continue
        pts = {qcoord(v) for v in (feature.get('geometry') or {}).get('coordinates', [])}
        station_vertices[key][code].update(pts)
        for pt in pts:
            station_at[key][pt].add(code)
        metadata[(key, code)] = {
            'stationCode': code,
            'groupCode': str(p.get('N02_005g') or '').strip(),
            'name': str(p.get('N02_005') or '').strip(),
            'line': key[0],
            'operator': key[1],
        }
        code_keys[code].add(key)

    output: dict[str, list[dict]] = {}
    unresolved = []
    for code in sorted(target_codes):
        found: dict[tuple[Key, str], dict] = {}
        for key in sorted(code_keys.get(code, set())):
            target = station_vertices[key].get(code, set())
            graph = graphs.get(key, {})
            if not target:
                continue
            boundary = []
            for node in target:
                for nxt, weight in graph.get(node, {}).items():
                    if nxt not in target:
                        boundary.append((nxt, weight))
            for start, initial in boundary:
                for adjacent_code, distance in first_station_from_boundary(
                    graph, station_at[key], code, target, start, initial
                ):
                    meta = metadata.get((key, adjacent_code))
                    if not meta:
                        continue
                    k = (key, adjacent_code)
                    candidate = {**meta, 'networkDistanceM': round(distance, 2)}
                    if k not in found or candidate['networkDistanceM'] < found[k]['networkDistanceM']:
                        found[k] = candidate
        values = sorted(found.values(), key=lambda v: (v['line'], v['operator'], v['networkDistanceM'], v['name'], v['stationCode']))
        output[code] = values
        if not values:
            unresolved.append(code)

    summary = {
        'projectStationCodes': len(target_codes),
        'resolvedStationCodes': sum(bool(output[c]) for c in target_codes),
        'withoutAdjacentStation': unresolved,
        'adjacencyLinks': sum(len(v) for v in output.values()),
        'method': 'first other N02 Station geometry reached from each RailroadSection edge leaving the target station body, per exact line/operator',
    }
    Path(args.output).write_text(
        json.dumps({'summary': summary, 'adjacency': output}, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
