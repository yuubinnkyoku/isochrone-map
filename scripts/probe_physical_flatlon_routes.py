#!/usr/bin/env python3
"""Inspect exact-point Yahoo routes for representative multi-point stations."""
from __future__ import annotations

import json
import time
from pathlib import Path

from audit_address_origin_resolution import build_flatlon_url, query_yahoo

PROPOSAL = Path('data/physical-station-points-proposal.json')
TARGET_IDS = {
    'n02p-003270', 'n02p-003266',  # 熊野前
    'n02p-003785',                 # 東京・京葉
    'n02p-003379',                 # 池袋・副都心
    'n02p-004301',                 # 武蔵小杉・横須賀側
    'n02p-003654', 'n02p-003646', # 両国
    'n02p-003514',                 # 早稲田・都電
}


def main() -> None:
    doc = json.loads(PROPOSAL.read_text(encoding='utf-8'))
    rows = [r for r in doc['physicalPoints'] if r['id'] in TARGET_IDS]
    report=[]
    for i, point in enumerate(rows, 1):
        label=f"{point['logicalStation']}物理駅地点-{point['id']}"
        url=build_flatlon_url(label, float(point['lat']), float(point['lng']))
        try:
            result=query_yahoo(url, float(point['lat']), float(point['lng']))
            row={
                'id':point['id'], 'station':point['logicalStation'], 'lines':point['lines'],
                'lat':point['lat'], 'lng':point['lng'],
                'departure':result.get('summaryDeparture'), 'arrival':result.get('summaryArrival'),
                'firstWalkOrigin':result.get('firstWalkOrigin'),
                'walkOrigins':result.get('walkOrigins'),
                'excerpt':result.get('excerpt'),
            }
            print(f"\n=== {i}/{len(rows)} {point['id']} {point['logicalStation']} {point['lines']} {point['lat']},{point['lng']} ===")
            print('summary', row['departure'], '->', row['arrival'], 'origin', row['firstWalkOrigin'])
            for line in (row['excerpt'] or [])[:130]:
                print(line)
        except Exception as exc:
            row={'id':point['id'], 'station':point['logicalStation'], 'error':f'{type(exc).__name__}: {exc}'}
            print(row)
        report.append(row)
        time.sleep(0.7)
    Path('data/physical-flatlon-route-probe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':
    main()
