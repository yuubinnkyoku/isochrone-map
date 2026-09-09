#!/usr/bin/env python3
"""Build stations.json from the complete physical-point Yahoo audit.

Every distinct N02 physical station point becomes its own map record. The value used
for IDW is the best Yahoo route from that exact coordinate, so a point remains a valid
spatial sample even when its best journey walks to another component or another nearby
station before boarding.

The saved audit is treated as raw Yahoo evidence. Route summaries and first-rail
classifications are always recomputed with the current parser instead of trusting the
classification fields embedded in an older audit artifact. This lets parser bugs be
fixed without issuing another 1563 Yahoo requests.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from audit_physical_station_points_full import classify_route1, is_bus_service, is_rail_service

TARGET_MINUTES = 8 * 60 + 18


def hhmm_minutes(value: str | None) -> int | None:
    if not value or not re.fullmatch(r'\d{1,2}:\d{2}', value):
        return None
    h, m = map(int, value.split(':'))
    raw = h * 60 + m
    # In an arrival-by-08:18 search, a displayed clock time after 08:18 belongs to
    # the previous day. Store it on the continuous timeline used by the map.
    return raw - 1440 if raw > TARGET_MINUTES else raw


def haversine_m(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    r = 6371008.8
    p1 = math.radians(a_lat)
    p2 = math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(x))


def route_summary(section: list[str]) -> str:
    parts = []
    for raw in section:
        line = str(raw).strip()
        if not line:
            continue
        if line.startswith('徒歩') and re.match(r'^徒歩\d+分$', line):
            parts.append(line)
        elif is_bus_service(line) or is_rail_service(line):
            parts.append(line)
    out = []
    for part in parts:
        if not out or out[-1] != part:
            out.append(part)
    return ' → '.join(out)


def line_display(lines: list[str], operators: list[str]) -> str:
    result = []
    for line in lines:
        value = line
        # N02 prefixes several Tokyo subway lines with planning-number notation.
        m = re.match(r'^\d+号線(.+)$', value)
        if m:
            value = m.group(1)
        if value in {'1号線', '3号線'} and '横浜市' in operators:
            value = '横浜市営地下鉄ブルーライン'
        elif value == '4号線' and '横浜市' in operators:
            value = '横浜市営地下鉄グリーンライン'
        if value not in result:
            result.append(value)
    return ' / '.join(result) if result else '鉄道'


def build_display_names(rows: list[dict], grouped: dict[str, list[dict]]) -> dict[str, str]:
    """Return globally unique, readable labels for every physical station point."""
    logical_name_counts = Counter()
    for items in grouped.values():
        if items:
            logical_name_counts[str(items[0]['logicalStation'])] += 1

    candidates: dict[str, str] = {}
    for row in rows:
        logical_id = str(row['logicalStationId'])
        logical_name = str(row['logicalStation'])
        needs_line = len(grouped[logical_id]) > 1 or logical_name_counts[logical_name] > 1
        if needs_line:
            line = line_display(row.get('lines') or [], row.get('operators') or [])
            candidate = f'{logical_name}（{line}）'
        else:
            candidate = logical_name
        candidates[str(row['id'])] = candidate

    counts = Counter(candidates.values())
    result: dict[str, str] = {}
    for row in rows:
        row_id = str(row['id'])
        candidate = candidates[row_id]
        if counts[candidate] == 1:
            result[row_id] = candidate
        else:
            code = '+'.join(row.get('n02StationCodes') or [])
            result[row_id] = f'{candidate} [{code}]'

    if len(set(result.values())) != len(rows):
        collisions = [name for name, count in Counter(result.values()).items() if count > 1]
        raise SystemExit(f'duplicate physical station display names: {collisions[:20]}')
    return result


def parser_signature(row: dict) -> tuple:
    first = row.get('firstRail') or {}
    return (
        row.get('classification'),
        row.get('reason'),
        first.get('service'),
        first.get('boardStation'),
        first.get('sameLogicalStationBase'),
        first.get('lineMatchesPhysicalPoint'),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--old-stations', default='data/stations.json')
    ap.add_argument('--proposal', default='data/physical-station-points-proposal.json')
    ap.add_argument('--audit', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--compact-audit-output')
    args = ap.parse_args()

    old = json.loads(Path(args.old_stations).read_text(encoding='utf-8'))
    proposal = json.loads(Path(args.proposal).read_text(encoding='utf-8'))
    audit = json.loads(Path(args.audit).read_text(encoding='utf-8'))
    rows = audit.get('rows', [])
    expected = int(proposal['meta']['physicalPoints'])
    if expected != 1563 or len(rows) != expected:
        raise SystemExit(f'physical audit coverage mismatch rows={len(rows)} expected={expected}')
    if len({r.get('id') for r in rows}) != expected:
        raise SystemExit('duplicate/missing physical point IDs')
    bad = [r for r in rows if r.get('error') or not r.get('originVerified') or not r.get('route1')]
    if bad:
        raise SystemExit(f'unresolved raw physical points: {len(bad)}')

    reparsed: dict[str, dict] = {}
    legacy_parser_changes = 0
    for r in rows:
        parsed = classify_route1(r, r.get('route1') or [])
        if parsed.get('classification') not in {'keep', 'exclude'}:
            raise SystemExit(f"route parser unresolved for {r.get('id')}: {parsed.get('reason')}")
        reparsed[str(r['id'])] = parsed
        if parser_signature(r) != parser_signature(parsed):
            legacy_parser_changes += 1

    old_by_id = {s['id']: s for s in old['stations']}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        grouped[str(r['logicalStationId'])].append(r)

    primary_ids = {}
    for logical_id, items in grouped.items():
        source = old_by_id[logical_id]
        primary = min(
            items,
            key=lambda r: (
                haversine_m(float(source['lat']), float(source['lng']), float(r['lat']), float(r['lng'])),
                str(r['id']),
            ),
        )
        primary_ids[logical_id] = primary['id']

    display_names = build_display_names(rows, grouped)

    stations = []
    compact_rows = []
    reason_counts = Counter()
    for r in sorted(rows, key=lambda x: int(x['globalIndex'])):
        parsed = reparsed[str(r['id'])]
        source = old_by_id[r['logicalStationId']]
        minutes = hhmm_minutes(r.get('departure'))
        arrival_minutes = hhmm_minutes(r.get('arrival'))
        if minutes is None or arrival_minutes is None:
            raise SystemExit(f"bad clock time for {r['id']}: {r.get('departure')} {r.get('arrival')}")
        if arrival_minutes < minutes:
            arrival_minutes += 1440

        reason_counts[str(parsed.get('reason'))] += 1
        line = line_display(r.get('lines') or [], r.get('operators') or [])
        route = route_summary(r.get('route1') or [])
        if not route:
            raise SystemExit(f"empty route summary for {r['id']}")
        first_rail = parsed.get('firstRail')
        if first_rail and first_rail.get('service') not in route:
            raise SystemExit(f"first rail missing from route summary for {r['id']}: {first_rail.get('service')}")

        station = {
            'id': r['id'],
            'station': display_names[r['id']],
            'logicalStation': r['logicalStation'],
            'logicalStationId': r['logicalStationId'],
            'n02StationCodes': r.get('n02StationCodes', []),
            'n02GroupCodes': r.get('n02GroupCodes', []),
            'lat': r['lat'],
            'lng': r['lng'],
            'minutes': minutes,
            'departureDisplay': ('前日' + r['departure']) if minutes < 0 else r['departure'],
            'line': line,
            'route': route,
            'searchDate': '2026-08-28',
            'outside': bool(source.get('outside', False)),
            'duration': arrival_minutes - minutes,
            'major': bool(source.get('major', False)),
            'labelPrimary': r['id'] == primary_ids[r['logicalStationId']],
            'excludeFromIdw': False,
            'physicalPointAudit': {
                'routeSelectionClassification': parsed['classification'],
                'reason': parsed.get('reason'),
                'originDistanceM': r.get('originDistanceM'),
                'firstRail': parsed.get('firstRail'),
            },
        }
        for key in ('passengers', 'passengerYear', 'passengerRank'):
            if source.get(key) is not None:
                station[key] = source[key]
        if source.get('note'):
            station['note'] = source['note']
        stations.append(station)
        compact_rows.append({
            'id': r['id'],
            'logicalStationId': r['logicalStationId'],
            'station': display_names[r['id']],
            'logicalStation': r['logicalStation'],
            'lat': r['lat'],
            'lng': r['lng'],
            'lines': r.get('lines', []),
            'departure': r.get('departure'),
            'arrival': r.get('arrival'),
            'minutes': minutes,
            'originDistanceM': r.get('originDistanceM'),
            'routeSelectionClassification': parsed['classification'],
            'reason': parsed.get('reason'),
            'firstRail': parsed.get('firstRail'),
            'hasBus': parsed.get('hasBus', False),
        })

    meta = dict(old['meta'])
    meta['version'] = 3
    meta['lastUpdated'] = '2026-09-09'
    meta['stationModel'] = {
        'logicalStations': proposal['meta']['logicalStations'],
        'physicalPoints': expected,
        'logicalStationsWithMultiplePhysicalPoints': proposal['meta']['logicalStationsWithMultiplePhysicalPoints'],
        'rule': 'N02-25の路線別駅コードを基準に、同一乗換駅でも代表座標が異なる物理駅地点はすべて別点として表示する。完全に同一座標の駅コードのみ1点にまとめる',
    }
    meta['radiusCoverage'] = dict(meta['radiusCoverage'])
    meta['radiusCoverage']['physicalPoints'] = expected
    meta['idwExclusionPolicy'] = {
        'rule': '全物理駅地点をYahoo!乗換案内flatlonで08:18着として再検索し、その座標からの最適経路の最遅出発時刻を直接IDW標本にする。別駅・別構成駅へ徒歩移動してから乗る経路も、その地点からの正しいアクセシビリティ値なので除外しない',
        'fixedAccessTimeLimit': False,
        'allowedAccessModes': ['徒歩', '鉄道', '路線バス'],
        'auditedPhysicalPoints': expected,
        'originToleranceM': 5.0,
        'excludedStations': 0,
        'excludedPhysicalPoints': 0,
        'includedPhysicalPoints': expected,
        'exactPointAudit': {
            'method': 'Yahoo!乗換案内 flatlon + fromLat/fromLon verification',
            'searchDate': '2026-08-28',
            'targetArrival': '08:18',
            'originVerified': expected,
            'routeParserVersion': 2,
            'savedAuditRowsReclassified': legacy_parser_changes,
            'routeSelectionDiagnostics': dict(sorted(reason_counts.items())),
        },
    }
    output = {'meta': meta, 'stations': stations}
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    compact_summary = {
        'physicalPoints': expected,
        'logicalStations': proposal['meta']['logicalStations'],
        'multiPointLogicalStations': proposal['meta']['logicalStationsWithMultiplePhysicalPoints'],
        'originVerified': expected,
        'includedPhysicalPoints': expected,
        'excludedPhysicalPoints': 0,
        'routeParserVersion': 2,
        'savedAuditRowsReclassified': legacy_parser_changes,
        'routeSelectionDiagnostics': dict(sorted(reason_counts.items())),
    }
    if args.compact_audit_output:
        Path(args.compact_audit_output).write_text(
            json.dumps({'summary': compact_summary, 'rows': compact_rows}, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8',
        )
    print(json.dumps(compact_summary, ensure_ascii=False, indent=2))
    for name in ('浦和美園', '熊野前', '東京', '武蔵小杉', '池袋', '両国', '弘明寺', '早稲田(都電)'):
        for s in stations:
            if s['logicalStation'] == name:
                first = (s['physicalPointAudit'].get('firstRail') or {})
                print(
                    'CHECK', name, s['id'], s['station'], s['line'], s['departureDisplay'],
                    s['physicalPointAudit']['reason'], first.get('boardStation'), first.get('service'),
                    'route=', s['route']
                )


if __name__ == '__main__':
    main()
