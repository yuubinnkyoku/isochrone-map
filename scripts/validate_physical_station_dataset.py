#!/usr/bin/env python3
"""Validate the final physical-point stations.json against its N02 proposal."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def main()->None:
    ap=argparse.ArgumentParser()
    ap.add_argument('--stations',default='data/stations.json')
    ap.add_argument('--proposal',default='data/physical-station-points-proposal.json')
    args=ap.parse_args()
    doc=json.loads(Path(args.stations).read_text(encoding='utf-8'))
    proposal=json.loads(Path(args.proposal).read_text(encoding='utf-8'))
    stations=doc['stations']; points=proposal['physicalPoints']
    expected_ids={p['id'] for p in points}; actual_ids={s['id'] for s in stations}
    if len(stations)!=1563 or len(actual_ids)!=1563 or actual_ids!=expected_ids:
        raise SystemExit(f'physical ID coverage mismatch total={len(stations)} missing={len(expected_ids-actual_ids)} extra={len(actual_ids-expected_ids)}')
    logical=defaultdict(list)
    for s in stations: logical[s['logicalStationId']].append(s)
    if len(logical)!=1318:
        raise SystemExit(f'logical station count {len(logical)} != 1318')
    proposal_counts=Counter(p['logicalStationId'] for p in points)
    actual_counts=Counter(s['logicalStationId'] for s in stations)
    if proposal_counts!=actual_counts:
        raise SystemExit('per-logical physical counts differ from proposal')
    group_codes={str(c) for s in stations for c in s.get('n02GroupCodes',[])}
    if len(group_codes)!=1322:
        raise SystemExit(f'N02 represented group count {len(group_codes)} != 1322')
    excluded=[s for s in stations if s.get('excludeFromIdw')]
    included=[s for s in stations if not s.get('excludeFromIdw')]
    policy=doc['meta']['idwExclusionPolicy']
    for key,value in {
        'excludedStations':len(excluded),
        'excludedPhysicalPoints':len(excluded),
        'includedPhysicalPoints':len(included),
        'auditedPhysicalPoints':len(stations),
    }.items():
        if policy.get(key)!=value:
            raise SystemExit(f'policy {key} {policy.get(key)} != {value}')
    if sum(1 for s in stations if s.get('labelPrimary'))!=1318:
        raise SystemExit('each logical station must have exactly one primary label')
    for logical_id,items in logical.items():
        if sum(1 for s in items if s.get('labelPrimary'))!=1:
            raise SystemExit(f'labelPrimary count invalid for {logical_id}')
    bad=[s['id'] for s in stations if not isinstance(s.get('minutes'),int) or not isinstance(s.get('lat'),(int,float)) or not isinstance(s.get('lng'),(int,float))]
    if bad: raise SystemExit(f'invalid numeric fields: {bad[:10]}')

    kuma={code:next((s for s in stations if code in s.get('n02StationCodes',[])),None) for code in ('003266','003270')}
    if not all(kuma.values()): raise SystemExit('熊野前 physical components missing')
    if kuma['003266']['logicalStationId']!='kumanomae' or kuma['003270']['logicalStationId']!='kumanomae':
        raise SystemExit('熊野前 logical grouping broken')
    # The exact-point probe establishes the desired component behavior: the tram-side
    # point walks to the Nippori-Toneri component; the Nippori-Toneri point boards itself.
    if not kuma['003266'].get('excludeFromIdw') or kuma['003270'].get('excludeFromIdw'):
        raise SystemExit('熊野前 component exclusion result is inconsistent with exact-point routing')

    summary={
        'physicalPoints':len(stations),'logicalStations':len(logical),'n02Groups':len(group_codes),
        'multiPointLogicalStations':sum(1 for v in actual_counts.values() if v>1),
        'included':len(included),'excluded':len(excluded),
        'kumanomae':{
            'arakawa':{'id':kuma['003266']['id'],'departure':kuma['003266']['departureDisplay'],'excluded':kuma['003266']['excludeFromIdw']},
            'toneri':{'id':kuma['003270']['id'],'departure':kuma['003270']['departureDisplay'],'excluded':kuma['003270']['excludeFromIdw']},
        },
    }
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
