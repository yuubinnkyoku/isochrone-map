#!/usr/bin/env python3
from __future__ import annotations

import argparse, json
from collections import defaultdict
from pathlib import Path

from audit_n02_station_groups import SOURCE_URL, build_n02, fetch_n02
from audit_physical_station_points_full import classify_route1, line_matches_point
from audit_route_origin_alignment import member_matches_segment


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--audit', required=True)
    ap.add_argument('--source-url', default=SOURCE_URL)
    args = ap.parse_args()
    rows = json.loads(Path(args.audit).read_text(encoding='utf-8'))['rows']
    groups, _, _ = build_n02(fetch_n02(args.source_url))
    by_station_code = defaultdict(list)
    for members in groups.values():
        for m in members:
            by_station_code[str(m['stationCode'])].append(m)

    logical = defaultdict(list)
    for r in rows:
        logical[str(r['logicalStationId'])].append(r)

    false_positive_current = []
    loose_strict_disagreements = []
    for r in rows:
        parsed = classify_route1(r, r.get('route1') or [], logical[str(r['logicalStationId'])])
        first = parsed.get('firstRail') or {}
        service = first.get('service')
        if not service:
            continue
        loose, loose_reason = line_matches_point(r, service)
        strict_matches = []
        seen = set()
        for code in r.get('n02StationCodes') or []:
            for m in by_station_code[str(code)]:
                key = (str(m.get('stationCode')), str(m.get('line')), str(m.get('operator')))
                if key in seen:
                    continue
                seen.add(key)
                ok, why = member_matches_segment(str(r.get('logicalStationId') or ''), service, m)
                if ok:
                    strict_matches.append({'stationCode': key[0], 'line': key[1], 'operator': key[2], 'reason': why})
        strict = bool(strict_matches)
        if loose != strict:
            item = {
                'id': r['id'], 'station': r['logicalStation'], 'service': service,
                'lines': r.get('lines'), 'operators': r.get('operators'),
                'n02StationCodes': r.get('n02StationCodes'), 'loose': loose,
                'looseReason': loose_reason, 'strict': strict, 'strictMatches': strict_matches,
                'classification': parsed.get('reason'),
            }
            loose_strict_disagreements.append(item)
            if loose and not strict and parsed.get('reason') == 'boards-current-physical-point':
                false_positive_current.append(item)

    print('ROWS', len(rows))
    print('LOOSE_STRICT_DISAGREEMENTS', len(loose_strict_disagreements))
    print('FALSE_POSITIVE_CURRENT', len(false_positive_current))
    for x in loose_strict_disagreements:
        print(json.dumps(x, ensure_ascii=False))
    if false_positive_current:
        raise SystemExit('line/operator cross-product caused false current-component matches')

if __name__ == '__main__':
    main()
