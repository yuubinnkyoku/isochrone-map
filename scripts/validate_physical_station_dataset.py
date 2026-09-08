#!/usr/bin/env python3
"""Validate the physical-point stations.json against its N02 proposal and IDW policy."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

EXPECTED_DIAGNOSTICS = {
    'boards-current-physical-point': 976,
    'boards-different-component': 232,
    'boards-different-station': 343,
    'no-rail-boarded': 12,
}


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
    policy = doc['meta']['idwExclusionPolicy']
    if policy.get('auditedPhysicalPoints') != 1563:
        raise SystemExit(f"policy auditedPhysicalPoints {policy.get('auditedPhysicalPoints')} != 1563")
    if policy.get('excludedPhysicalPoints') != len(excluded):
        raise SystemExit('policy excludedPhysicalPoints does not match station flags')
    if policy.get('includedPhysicalPoints') != len(included):
        raise SystemExit('policy includedPhysicalPoints does not match station flags')
    if policy.get('excludedStations') != len(excluded):
        raise SystemExit('policy excludedStations does not match station flags')

    excluded_ids = sorted(s['id'] for s in excluded)
    declared_ids = sorted(policy.get('excludedPhysicalPointIds') or [])
    # Legacy all-included data did not yet carry excludedPhysicalPointIds.  Once the
    # comparison audit is complete, the field is required and must match exactly.
    if policy.get('complete'):
        if declared_ids != excluded_ids:
            raise SystemExit('policy excludedPhysicalPointIds does not match station flags')
        if policy.get('unresolved') != 0:
            raise SystemExit('complete exclusion policy still has unresolved points')
        if policy.get('comparisonThresholdMinutes') != 1:
            raise SystemExit('comparison threshold must be 1 minute')

        for station in stations:
            decision = station.get('physicalPointAudit', {}).get('idwExclusion') or {}
            if not decision:
                raise SystemExit(f"missing IDW comparison provenance for {station['id']}")
            expected = 'exclude' if station.get('excludeFromIdw') else 'include'
            if decision.get('decision') != expected:
                raise SystemExit(f"IDW decision/flag mismatch for {station['id']}")
            gain = decision.get('gainMinutes')
            if expected == 'exclude':
                if not isinstance(gain, int) or gain < 1 or decision.get('directMinutes') is None:
                    raise SystemExit(f"invalid exclusion evidence for {station['id']}: gain={gain}")
            elif decision.get('directMinutes') is not None and isinstance(gain, int) and gain >= 1:
                raise SystemExit(f"included point has exclusion-sized gain: {station['id']} gain={gain}")

    diagnostics = policy.get('exactPointAudit', {}).get('routeSelectionDiagnostics')
    if diagnostics != EXPECTED_DIAGNOSTICS:
        raise SystemExit(f'route-selection diagnostics changed: {diagnostics}')

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
    ]
    if bad:
        raise SystemExit(f'invalid numeric fields: {bad[:10]}')

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
    if kuma['003270'].get('excludeFromIdw'):
        raise SystemExit('熊野前舎人ライナー側は自身から乗車するためIDWに残す必要がある')

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
        'kumanomae': {
            'arakawa': {
                'id': kuma['003266']['id'],
                'departure': kuma['003266']['departureDisplay'],
                'reason': kuma['003266']['physicalPointAudit']['reason'],
                'excludeFromIdw': bool(kuma['003266'].get('excludeFromIdw')),
            },
            'toneri': {
                'id': kuma['003270']['id'],
                'departure': kuma['003270']['departureDisplay'],
                'reason': kuma['003270']['physicalPointAudit']['reason'],
                'excludeFromIdw': bool(kuma['003270'].get('excludeFromIdw')),
            },
        },
        'gummyoji': [s['station'] for s in gummyoji],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
