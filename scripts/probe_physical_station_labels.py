#!/usr/bin/env python3
"""Probe Yahoo station-label disambiguation for a few multi-point stations."""
from __future__ import annotations

import json
import time
from pathlib import Path

from audit_address_origin_resolution import query_yahoo
from audit_yahoo_targeted import build_url

PROPOSAL = Path('data/physical-station-points-proposal.json')

ALIASES = {
    '荒川線': ['都電荒川線', '都電'],
    '日暮里・舎人ライナー': ['舎人ライナー'],
    '13号線副都心線': ['副都心線'],
    '8号線有楽町線': ['有楽町線'],
    '4号線丸ノ内線': ['丸ノ内線'],
    '東海道線': ['横須賀線', '東海道線'],
    '京葉線': ['京葉線'],
}

TARGET_IDS = {
    'n02p-003270', 'n02p-003266',
    'n02p-003785', 'n02p-003379', 'n02p-004301',
    'n02p-003654', 'n02p-003646',
}


def variants(row: dict) -> list[str]:
    name = str(row['logicalStation'])
    values = [name]
    for line in row.get('lines', []):
        labels = [line] + ALIASES.get(line, [])
        for label in labels:
            values += [f'{name}({label})', f'{name}（{label}）']
    # preserve order
    return list(dict.fromkeys(values))


def main() -> None:
    doc = json.loads(PROPOSAL.read_text(encoding='utf-8'))
    rows = [r for r in doc['physicalPoints'] if r['id'] in TARGET_IDS]
    report = []
    for point in rows:
        print('\n###', point['id'], point['logicalStation'], point['lines'], point['lat'], point['lng'])
        for label in variants(point):
            try:
                result = query_yahoo(build_url(label), float(point['lat']), float(point['lng']))
                opts = result.get('addressOptions') or []
                nearest = opts[0] if opts else None
                out = {
                    'id': point['id'], 'label': label,
                    'departure': result.get('summaryDeparture'),
                    'arrival': result.get('summaryArrival'),
                    'finalUrl': result.get('finalUrl'),
                    'nearestOption': nearest,
                    'excerpt': result.get('excerpt', [])[:45],
                }
                print(label, '=>', out['departure'], out['arrival'], 'nearest=', nearest)
            except Exception as exc:
                out = {'id': point['id'], 'label': label, 'error': f'{type(exc).__name__}: {exc}'}
                print(label, 'ERROR', out['error'])
            report.append(out)
            time.sleep(0.45)
    Path('data/physical-station-label-probe.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

if __name__ == '__main__':
    main()
