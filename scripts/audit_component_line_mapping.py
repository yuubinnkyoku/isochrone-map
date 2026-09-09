#!/usr/bin/env python3
from __future__ import annotations

import argparse, json
from collections import Counter, defaultdict
from pathlib import Path

from audit_physical_station_points_full import classify_route1, line_matches_point


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--audit', required=True)
    args = ap.parse_args()
    rows = json.loads(Path(args.audit).read_text(encoding='utf-8'))['rows']
    grouped = defaultdict(list)
    for r in rows:
        grouped[str(r['logicalStationId'])].append(r)

    bad = []
    for r in rows:
        parsed = classify_route1(r, r.get('route1') or [])
        first = parsed.get('firstRail') or {}
        if not first or not first.get('sameLogicalStationBase') or first.get('lineMatchesPhysicalPoint'):
            continue
        matches = []
        for p in grouped[str(r['logicalStationId'])]:
            ok, why = line_matches_point(p, first.get('service') or '')
            if ok:
                matches.append((p['id'], p.get('lines'), why))
        if not matches:
            bad.append({
                'id': r['id'], 'logicalStationId': r['logicalStationId'], 'station': r['logicalStation'],
                'service': first.get('service'), 'physicalPointCount': len(grouped[str(r['logicalStationId'])]),
                'points': [
                    {'id': p['id'], 'lines': p.get('lines'), 'operators': p.get('operators')}
                    for p in grouped[str(r['logicalStationId'])]
                ],
            })

    print('COUNT', len(bad))
    print('SINGLE_POINT', sum(1 for x in bad if x['physicalPointCount'] == 1))
    print('MULTI_POINT', sum(1 for x in bad if x['physicalPointCount'] > 1))
    print('SERVICES')
    for service, count in Counter(x['service'] for x in bad).most_common():
        print(count, service)
    print('ROWS')
    for x in bad:
        print(json.dumps(x, ensure_ascii=False))

if __name__ == '__main__':
    main()
