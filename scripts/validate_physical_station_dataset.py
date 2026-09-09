#!/usr/bin/env python3
"""Validate the final exact-point stations.json against its N02 proposal.

All physical points are valid IDW samples because their departure values were queried
from the exact coordinates themselves. Route-selection classifications are retained
only as provenance showing whether the best journey boards at that component, another
component, another station, or never boards rail.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

EXPECTED_DIAGNOSTICS = {
    'boards-current-physical-point': 1157,
    'boards-different-component': 217,
    'boards-different-station': 177,
    'no-rail-boarded': 12,
}
EXPECTED_REPARSED_ROWS = 210


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--stations', default='data/stations.json')
    ap.add_argument('--proposal', default='data/physical-station-points-proposal.json')
    args = ap.parse_args()

    doc = json.loads(Path(args.stations).read_text(encoding='utf-8'))
    proposal = json.loads(Path(args.proposal).read_text(encoding='utf-8'))
    stations = doc['stations']
    points = proposal['physicalPoints']
    expected_ids = {p['id'] for p in points}
    actual_ids = {s['id'] for s in stations}

    if len(stations) != 1563 or len(actual_ids) != 1563 or actual_ids != expected_ids:
        raise SystemExit(
            f'physical ID coverage mismatch total={len(stations)} '
            f'missing={len(expected_ids-actual_ids)} extra={len(actual_ids-expected_ids)}'
        )
    if len({s['station'] for s in stations}) != 1563:
        duplicates = [name for name, count in Counter(s['station'] for s in stations).items() if count > 1]
        raise SystemExit(f'physical display names are not unique: {duplicates[:20]}')

    logical = defaultdict(list)
    for station in stations:
        logical[station['logicalStationId']].append(station)
    if len(logical) != 1318:
        raise SystemExit(f'logical station count {len(logical)} != 1318')

    proposal_counts = Counter(p['logicalStationId'] for p in points)
    actual_counts = Counter(s['logicalStationId'] for s in stations)
    if proposal_counts != actual_counts:
        raise SystemExit('per-logical physical counts differ from proposal')

    group_codes = {str(c) for s in stations for c in s.get('n02GroupCodes', [])}
    if len(group_codes) != 1322:
        raise SystemExit(f'N02 represented group count {len(group_codes)} != 1322')

    excluded = [s for s in stations if s.get('excludeFromIdw')]
    included = [s for s in stations if not s.get('excludeFromIdw')]
    if excluded:
        raise SystemExit(f'exact-point dataset must not exclude recomputed samples: {len(excluded)}')

    policy = doc['meta']['idwExclusionPolicy']
    for key, value in {
        'excludedStations': 0,
        'excludedPhysicalPoints': 0,
        'includedPhysicalPoints': 1563,
        'auditedPhysicalPoints': 1563,
    }.items():
        if policy.get(key) != value:
            raise SystemExit(f'policy {key} {policy.get(key)} != {value}')

    exact = policy.get('exactPointAudit', {})
    diagnostics = exact.get('routeSelectionDiagnostics')
    if diagnostics != EXPECTED_DIAGNOSTICS:
        raise SystemExit(f'route-selection diagnostics changed: {diagnostics}')
    if exact.get('routeParserVersion') != 3:
        raise SystemExit(f"route parser version {exact.get('routeParserVersion')} != 3")
    if exact.get('routeSummaryVersion') != 2:
        raise SystemExit(f"route summary version {exact.get('routeSummaryVersion')} != 2")
    if exact.get('savedAuditRowsReclassified') != EXPECTED_REPARSED_ROWS:
        raise SystemExit(
            f"saved audit reparse changes {exact.get('savedAuditRowsReclassified')} != {EXPECTED_REPARSED_ROWS}"
        )

    if sum(1 for s in stations if s.get('labelPrimary')) != 1318:
        raise SystemExit('each logical station must have exactly one primary label')
    for logical_id, items in logical.items():
        if sum(1 for s in items if s.get('labelPrimary')) != 1:
            raise SystemExit(f'labelPrimary count invalid for {logical_id}')

    bad = [
        s['id'] for s in stations
        if not isinstance(s.get('minutes'), int)
        or not isinstance(s.get('lat'), (int, float))
        or not isinstance(s.get('lng'), (int, float))
        or not s.get('route')
    ]
    if bad:
        raise SystemExit(f'invalid numeric/route fields: {bad[:10]}')

    # The screenshot-triggering regression: service branding omitted 「線」 and the
    # old parser skipped the Saitama Railway leg, incorrectly claiming first boarding
    # was at Korakuen on the Marunouchi Line.
    urawa = next((s for s in stations if s['id'] == 'n02p-002933'), None)
    if not urawa:
        raise SystemExit('浦和美園 missing')
    urawa_first = (urawa['physicalPointAudit'].get('firstRail') or {})
    if urawa['departureDisplay'] != '07:12' or urawa['line'] != '埼玉高速鉄道線':
        raise SystemExit(f'浦和美園 base fields unexpected: {urawa}')
    if urawa['physicalPointAudit'].get('reason') != 'boards-current-physical-point':
        raise SystemExit(f'浦和美園 route diagnosis wrong: {urawa["physicalPointAudit"]}')
    if urawa_first.get('boardStation') != '浦和美園' or '埼玉高速鉄道' not in str(urawa_first.get('service')):
        raise SystemExit(f'浦和美園 first rail wrong: {urawa_first}')
    if '埼玉高速鉄道' not in urawa.get('route', ''):
        raise SystemExit(f'浦和美園 route summary lost first rail: {urawa.get("route")}')

    # Yahoo and N02 differ between ヶ and ケ in many station names. These should not
    # be rendered as fake walk-to-another-station cases.
    for sid in ('n02p-003945', 'n02p-004024', 'n02p-003279', 'n02p-003289', 'n02p-003671'):
        station = next(s for s in stations if s['id'] == sid)
        if station['physicalPointAudit'].get('reason') != 'boards-current-physical-point':
            raise SystemExit(f'{sid} orthographic station-name normalization regressed')

    # Second-pass regressions: Yahoo service branding can differ from the N02
    # infrastructure line, and same-name stations can still be separate stations.
    by_id = {s['id']: s for s in stations}
    expected_current = {
        'n02p-004322': '東急目黒線',       # 元住吉: service on N02 東横線 infrastructure
        'n02p-002891': 'ＪＲ埼京線',       # 指扇: service on N02 川越線
        'n02p-003573': '東京メトロ東西線', # 高円寺: through service on N02 中央線
        'n02p-003928': 'ＪＲ武蔵野線',     # 潮見: service on N02 京葉線
        'n02p-004535': '小湊鐵道',         # spelling variant: 小湊鐵道線
        'n02p-002886': '関東鉄道竜ケ崎線', # ケ/ヶ line-name variant
        'n02p-004675': 'ＪＲ横浜線',       # 桜木町: service on N02 根岸線
        'n02p-003373': 'ＪＲ山手線',       # 西日暮里: service on N02 東北線
    }
    for sid, service_token in expected_current.items():
        s = by_id[sid]
        audit = s['physicalPointAudit']
        first = audit.get('firstRail') or {}
        if audit.get('reason') != 'boards-current-physical-point':
            raise SystemExit(f'{sid} infrastructure/service mapping regressed: {audit}')
        if service_token not in str(first.get('service')):
            raise SystemExit(f'{sid} unexpected first service: {first}')

    asakusa = by_id['n02p-003513']
    asakusa_first = asakusa['physicalPointAudit'].get('firstRail') or {}
    if asakusa['physicalPointAudit'].get('reason') != 'boards-different-station':
        raise SystemExit(f'TX浅草 must be a separate same-name station: {asakusa["physicalPointAudit"]}')
    if 'つくばエクスプレス' not in str(asakusa_first.get('service')) or '浅草' not in str(asakusa_first.get('boardStation')):
        raise SystemExit(f'TX浅草 first boarding unexpected: {asakusa_first}')

    # Route summaries must retain transfer station/stop names, not just service names.
    required_urawa_route_tokens = [
        '浦和美園', '埼玉高速鉄道', '後楽園', '東京メトロ丸ノ内線',
        '茗荷谷', '筑波大学附属高等学校',
    ]
    urawa_route = urawa.get('route', '')
    positions = []
    for token in required_urawa_route_tokens:
        pos = urawa_route.find(token)
        if pos < 0:
            raise SystemExit(f'浦和美園 route summary missing {token}: {urawa_route}')
        positions.append(pos)
    if positions != sorted(positions):
        raise SystemExit(f'浦和美園 route summary order broken: {urawa_route}')

    for station in stations:
        route = station.get('route', '')
        if '筑波大学附属高等学校' not in route:
            raise SystemExit(f"route destination missing: {station['id']} {route}")
        first = (station.get('physicalPointAudit') or {}).get('firstRail') or {}
        if first.get('service') and route.count(' → ') < 2:
            raise SystemExit(f"route station chain missing: {station['id']} {route}")

    # Every parsed first-rail service must be present in the human-readable route too.
    for station in stations:
        first = (station.get('physicalPointAudit') or {}).get('firstRail') or {}
        service = first.get('service')
        if service and service not in station.get('route', ''):
            raise SystemExit(f"first rail absent from route summary: {station['id']} {service}")

    kuma = {
        code: next((s for s in stations if code in s.get('n02StationCodes', [])), None)
        for code in ('003266', '003270')
    }
    if not all(kuma.values()):
        raise SystemExit('熊野前 physical components missing')
    if kuma['003266']['logicalStationId'] != 'kumanomae' or kuma['003270']['logicalStationId'] != 'kumanomae':
        raise SystemExit('熊野前 logical grouping broken')
    if kuma['003266']['physicalPointAudit'].get('reason') != 'boards-different-component':
        raise SystemExit('熊野前荒川線のアクセス診断が変化した')
    if kuma['003270']['physicalPointAudit'].get('reason') != 'boards-current-physical-point':
        raise SystemExit('熊野前舎人ライナーのアクセス診断が変化した')
    if kuma['003266'].get('excludeFromIdw') or kuma['003270'].get('excludeFromIdw'):
        raise SystemExit('exact-point 熊野前は両地点ともIDW標本である必要がある')

    gummyoji = [s for s in stations if s.get('logicalStation') == '弘明寺']
    if len(gummyoji) != 2 or len({s['station'] for s in gummyoji}) != 2:
        raise SystemExit(f'弘明寺の物理駅表示名分離に失敗: {[s.get("station") for s in gummyoji]}')

    summary = {
        'physicalPoints': len(stations),
        'logicalStations': len(logical),
        'n02Groups': len(group_codes),
        'multiPointLogicalStations': sum(1 for v in actual_counts.values() if v > 1),
        'included': len(included),
        'excluded': len(excluded),
        'diagnostics': EXPECTED_DIAGNOSTICS,
        'savedAuditRowsReclassified': EXPECTED_REPARSED_ROWS,
        'urawaMisono': {
            'departure': urawa['departureDisplay'],
            'reason': urawa['physicalPointAudit']['reason'],
            'firstRail': urawa_first,
            'route': urawa['route'],
        },
        'kumanomae': {
            'arakawa': {
                'id': kuma['003266']['id'],
                'departure': kuma['003266']['departureDisplay'],
                'reason': kuma['003266']['physicalPointAudit']['reason'],
            },
            'toneri': {
                'id': kuma['003270']['id'],
                'departure': kuma['003270']['departureDisplay'],
                'reason': kuma['003270']['physicalPointAudit']['reason'],
            },
        },
        'gummyoji': [s['station'] for s in gummyoji],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
