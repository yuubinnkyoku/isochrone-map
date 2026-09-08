#!/usr/bin/env python3
"""Resolve unresolved physical IDW comparisons by forcing a same-line neighbour via.

Yahoo station-origin search cannot reliably be constrained to a specific component of a
large interchange: e.g. `池袋（山手線）` may still choose the Yurakucho Line.  For an
unresolved physical point we therefore add a nearby station on the *same N02 line and
operator* as `via01`.  A candidate is accepted only if route 1's first rail boarding
still verifies against the queried physical point.  This makes the via station a route
constraint, not evidence by itself.

The nearest several same-line points are tried because N02 station records do not expose
a simple ordered adjacency field in the Station GeoJSON, and branches/loops can make a
single nearest-neighbour guess unsafe.  Among all verified candidates we retain the
latest departure, i.e. the best route that actually boards the queried component.
"""
from __future__ import annotations

import argparse
import json
import math
import time
import urllib.parse
from pathlib import Path

from audit_address_origin_resolution import query_yahoo
from audit_physical_idw_exclusions import (
    clean_display_label,
    minutes_on_timeline,
    query_with_retry,
)
from audit_physical_station_points_full import classify
from audit_yahoo_targeted import build_url


def haversine_m(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    r = 6_371_008.8
    p1 = math.radians(a_lat)
    p2 = math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def build_via_url(origin: str, via: str) -> str:
    parsed = urllib.parse.urlsplit(build_url(origin))
    params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    params = [(k, v) for k, v in params if k != 'via01']
    params.append(('via01', via))
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(params), parsed.fragment)
    )


def network_overlap(a: dict, b: dict) -> bool:
    a_lines = {str(v) for v in (a.get('lines') or [])}
    b_lines = {str(v) for v in (b.get('lines') or [])}
    a_ops = {str(v) for v in (a.get('operators') or [])}
    b_ops = {str(v) for v in (b.get('operators') or [])}
    return bool(a_lines & b_lines) and bool(a_ops & b_ops)


def neighbour_candidates(point: dict, all_points: list[dict], limit: int) -> list[dict]:
    rows = []
    for other in all_points:
        if str(other.get('id')) == str(point.get('id')):
            continue
        if str(other.get('logicalStationId')) == str(point.get('logicalStationId')):
            continue
        if not network_overlap(point, other):
            continue
        rows.append((
            haversine_m(
                float(point['lat']), float(point['lng']),
                float(other['lat']), float(other['lng']),
            ),
            other,
        ))
    rows.sort(key=lambda item: (item[0], str(item[1].get('logicalStation')), str(item[1].get('id'))))

    # Deduplicate by logical station name so aliases/coincident records do not waste
    # requests.  Keep a few candidates to cover both directions and branch geometry.
    out = []
    seen = set()
    for dist, other in rows:
        name = str(other.get('logicalStation') or '').strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({'distanceM': round(dist, 2), **other})
        if len(out) >= limit:
            break
    return out


def origin_labels(row: dict) -> list[str]:
    values = []
    # The first original attempt is normally the most component-specific display label.
    for attempt in row.get('attempts') or []:
        label = str(attempt.get('label') or '').strip()
        if label:
            values.append(label)
            break
    display = clean_display_label(str(row.get('station') or '')).strip()
    logical = str(row.get('logicalStation') or '').strip()
    for value in (display, logical):
        if value:
            values.append(value)
    return list(dict.fromkeys(values))[:2]


def load_rows(input_dir: Path) -> list[dict]:
    paths = sorted(input_dir.glob('physical-idw-shard-*.json'))
    if not paths:
        raise SystemExit(f'no initial shard files in {input_dir}')
    rows = []
    for path in paths:
        doc = json.loads(path.read_text(encoding='utf-8'))
        rows.extend(doc.get('rows') or [])
    rows.sort(key=lambda r: int(r['globalIndex']))
    if len(rows) != 1563 or len({str(r.get('id')) for r in rows}) != 1563:
        raise SystemExit(f'initial audit coverage mismatch rows={len(rows)} unique={len({str(r.get("id")) for r in rows})}')
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--input-dir', required=True)
    ap.add_argument('--proposal', default='data/physical-station-points-proposal.json')
    ap.add_argument('--shard-index', type=int, required=True)
    ap.add_argument('--shard-count', type=int, required=True)
    ap.add_argument('--neighbour-limit', type=int, default=6)
    ap.add_argument('--delay-seconds', type=float, default=0.55)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()

    rows = load_rows(Path(args.input_dir))
    proposal = json.loads(Path(args.proposal).read_text(encoding='utf-8'))
    points = proposal['physicalPoints']
    point_by_id = {str(p['id']): p for p in points}
    if len(point_by_id) != 1563:
        raise SystemExit(f'proposal physical point count {len(point_by_id)} != 1563')

    selected = [
        row for row in rows
        if row.get('decision') == 'unresolved'
        and int(row['globalIndex']) % args.shard_count == args.shard_index
    ]
    resolved_rows = []
    for ordinal, row in enumerate(selected, 1):
        point = point_by_id[str(row['id'])]
        neighbours = neighbour_candidates(point, points, args.neighbour_limit)
        labels = origin_labels(row)
        attempts = []
        verified = []

        for neighbour in neighbours:
            via = str(neighbour['logicalStation'])
            for origin in labels:
                attempt = {
                    'originLabel': origin,
                    'via': via,
                    'viaPointId': neighbour['id'],
                    'viaDistanceM': neighbour['distanceM'],
                }
                try:
                    # query_with_retry accepts arbitrary Yahoo URLs and preserves the
                    # same retry policy as the initial audit.
                    result = query_with_retry(
                        build_via_url(origin, via),
                        float(row['lat']), float(row['lng']),
                    )
                    dep = result.get('summaryDeparture')
                    dep_minutes = minutes_on_timeline(dep)
                    route_class = classify(point, result.get('excerpt') or [])
                    attempt.update({
                        'departure': dep,
                        'arrival': result.get('summaryArrival'),
                        'classification': route_class.get('classification'),
                        'reason': route_class.get('reason'),
                        'firstRail': route_class.get('firstRail'),
                    })
                    if dep_minutes is not None and route_class.get('reason') == 'boards-current-physical-point':
                        verified.append({
                            'originLabel': origin,
                            'via': via,
                            'viaPointId': neighbour['id'],
                            'viaDistanceM': neighbour['distanceM'],
                            'departure': dep,
                            'minutes': dep_minutes,
                            'firstRail': route_class.get('firstRail'),
                        })
                except Exception as exc:
                    attempt['error'] = f'{type(exc).__name__}: {exc}'
                attempts.append(attempt)
                time.sleep(args.delay_seconds)

            # Once a nearby via has produced a verified route, still test one more
            # neighbour when available.  This covers the opposite direction without
            # exploding request counts on large lines.
            if verified and len({v['via'] for v in verified}) >= 2:
                break

        out = dict(row)
        out['viaResolutionAttempts'] = attempts
        out['viaNeighbourCandidates'] = [
            {'id': n['id'], 'station': n['logicalStation'], 'distanceM': n['distanceM']}
            for n in neighbours
        ]
        if verified:
            best = max(verified, key=lambda v: (int(v['minutes']), -float(v['viaDistanceM']), str(v['via'])))
            gain = int(row['freeMinutes']) - int(best['minutes'])
            out.update({
                'directLabel': best['originLabel'],
                'directVia': best['via'],
                'directViaPointId': best['viaPointId'],
                'directViaDistanceM': best['viaDistanceM'],
                'directDeparture': best['departure'],
                'directMinutes': best['minutes'],
                'directFirstRail': best['firstRail'],
                'gainMinutes': gain,
                'excludeFromIdw': gain >= 1,
                'decision': 'exclude' if gain >= 1 else 'include',
                'decisionReason': 'alternate-route-later-departure' if gain >= 1 else 'alternate-route-not-later',
            })
            out.pop('error', None)
        else:
            out['decision'] = 'unresolved'
            out['decisionReason'] = 'no-verified-direct-component-route-after-via'
            out['error'] = out['decisionReason']

        resolved_rows.append(out)
        print(
            f"[{ordinal}/{len(selected)}] {row['id']} {row.get('logicalStation')} "
            f"decision={out.get('decision')} free={row.get('freeMinutes')} "
            f"direct={out.get('directMinutes')} gain={out.get('gainMinutes')} "
            f"via={out.get('directVia')} attempts={len(attempts)}"
        )

    summary = {
        'shardIndex': args.shard_index,
        'shardCount': args.shard_count,
        'initialUnresolvedInShard': len(selected),
        'resolved': sum(r.get('decision') in {'include', 'exclude'} for r in resolved_rows),
        'include': sum(r.get('decision') == 'include' for r in resolved_rows),
        'exclude': sum(r.get('decision') == 'exclude' for r in resolved_rows),
        'unresolved': sum(r.get('decision') == 'unresolved' for r in resolved_rows),
    }
    Path(args.output).write_text(
        json.dumps({'summary': summary, 'rows': resolved_rows}, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
