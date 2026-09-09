#!/usr/bin/env python3
from __future__ import annotations

import argparse, json, re
from collections import Counter
from pathlib import Path

from audit_physical_station_points_full import is_bus_service, is_rail_service


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--audit', required=True)
    args = ap.parse_args()
    rows = json.loads(Path(args.audit).read_text(encoding='utf-8'))['rows']
    unknown = []
    platform_predecessors = Counter()
    for r in rows:
        section = r.get('route1') or []
        for i, line in enumerate(section[:-1]):
            nxt = str(section[i + 1]).strip()
            if not nxt.startswith('[発]'):
                continue
            service = str(line).strip()
            kind = 'rail' if is_rail_service(service) else ('bus' if is_bus_service(service) else 'unknown')
            platform_predecessors[(kind, service)] += 1
            if kind == 'unknown':
                unknown.append({
                    'id': r['id'], 'station': r['logicalStation'], 'index': i,
                    'line': service, 'next': nxt,
                    'context': section[max(0, i-3):min(len(section), i+4)],
                })
    print('ROWS', len(rows))
    print('PLATFORM_PREDECESSORS', sum(platform_predecessors.values()))
    print('UNKNOWN', len(unknown))
    for item in unknown[:200]:
        print(json.dumps(item, ensure_ascii=False))
    print('TOP_KINDS', Counter(k for (k, _), c in platform_predecessors.items() for _ in range(c)))

if __name__ == '__main__':
    main()
