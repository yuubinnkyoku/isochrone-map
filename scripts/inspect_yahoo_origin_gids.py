#!/usr/bin/env python3
"""Diagnostic: test whether Yahoo fromgid can pin ambiguous physical station components.

This is deliberately small and read-only. For a handful of unresolved physical points it
queries qualified station labels, collects Yahoo autocomplete option GIDs near the N02
point, then retries the route with each GID and verifies the first rail boarding against
the physical component.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.parse
from pathlib import Path

from audit_address_origin_resolution import query_yahoo
from audit_physical_idw_exclusions import label_variants
from audit_physical_station_points_full import classify
from audit_yahoo_targeted import build_url

DEFAULT_IDS = [
    'n02p-003418',  # 池袋 西武池袋線
    'n02p-003785',  # 東京 京葉線
    'n02p-003683',  # 新宿 小田急
    'n02p-003654',  # 両国 総武線
]


def build_gid_url(label: str, gid: str) -> str:
    parsed = urllib.parse.urlsplit(build_url(label))
    params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    params = [(k, v) for k, v in params if k not in {'fromgid', 'flatlon'}]
    params.append(('fromgid', gid))
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(params), parsed.fragment)
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--stations', default='data/stations.json')
    ap.add_argument('--id', action='append', dest='ids')
    ap.add_argument('--delay-seconds', type=float, default=0.55)
    ap.add_argument('--max-gids', type=int, default=8)
    ap.add_argument('--output', default='data/yahoo-origin-gid-diagnostic.json')
    args = ap.parse_args()

    doc = json.loads(Path(args.stations).read_text(encoding='utf-8'))
    by_id = {str(s['id']): s for s in doc['stations']}
    targets = args.ids or DEFAULT_IDS
    rows = []

    for target_id in targets:
        station = by_id.get(target_id)
        if not station:
            rows.append({'id': target_id, 'error': 'station-not-found'})
            continue
        lat = float(station['lat'])
        lng = float(station['lng'])
        labels = label_variants(station, station)[:4]
        label_results = []
        gid_candidates: dict[str, dict] = {}

        for label in labels:
            result = query_yahoo(build_url(label), lat, lng)
            route_class = classify(station, result.get('excerpt') or [])
            label_results.append({
                'label': label,
                'departure': result.get('summaryDeparture'),
                'finalUrl': result.get('finalUrl'),
                'classification': route_class.get('classification'),
                'reason': route_class.get('reason'),
                'firstRail': route_class.get('firstRail'),
                'addressOptions': result.get('addressOptions') or [],
            })
            for option in result.get('addressOptions') or []:
                gid = str(option.get('gid') or '').strip()
                if not gid:
                    continue
                old = gid_candidates.get(gid)
                if old is None or float(option.get('distanceFromStationM') or 1e9) < float(old.get('distanceFromStationM') or 1e9):
                    gid_candidates[gid] = dict(option)
            time.sleep(args.delay_seconds)

        candidates = sorted(
            gid_candidates.values(),
            key=lambda o: (float(o.get('distanceFromStationM') or 1e9), str(o.get('label') or ''), str(o.get('gid') or '')),
        )[: args.max_gids]
        gid_results = []
        for option in candidates:
            gid = str(option['gid'])
            label = str(option.get('label') or station.get('logicalStation') or station['station'])
            try:
                result = query_yahoo(build_gid_url(label, gid), lat, lng)
                route_class = classify(station, result.get('excerpt') or [])
                gid_results.append({
                    'gid': gid,
                    'label': label,
                    'optionLat': option.get('lat'),
                    'optionLng': option.get('lng'),
                    'distanceFromPhysicalPointM': option.get('distanceFromStationM'),
                    'departure': result.get('summaryDeparture'),
                    'finalUrl': result.get('finalUrl'),
                    'classification': route_class.get('classification'),
                    'reason': route_class.get('reason'),
                    'firstRail': route_class.get('firstRail'),
                })
            except Exception as exc:
                gid_results.append({
                    'gid': gid,
                    'label': label,
                    'distanceFromPhysicalPointM': option.get('distanceFromStationM'),
                    'error': f'{type(exc).__name__}: {exc}',
                })
            time.sleep(args.delay_seconds)

        row = {
            'id': target_id,
            'station': station.get('station'),
            'logicalStation': station.get('logicalStation'),
            'lat': lat,
            'lng': lng,
            'lines': station.get('lines'),
            'operators': station.get('operators'),
            'labelResults': label_results,
            'gidCandidates': candidates,
            'gidResults': gid_results,
        }
        rows.append(row)
        verified = [r for r in gid_results if r.get('reason') == 'boards-current-physical-point']
        print(target_id, station.get('station'), 'gids', len(candidates), 'verified', len(verified))
        for r in verified:
            print(' VERIFIED', r['gid'], r['label'], r.get('departure'), r.get('firstRail'))

    output = {'targetCount': len(targets), 'rows': rows}
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
