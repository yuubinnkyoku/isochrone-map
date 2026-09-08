#!/usr/bin/env python3
"""Apply a complete direct-vs-unrestricted physical-point audit to stations.json.

This does not remove station markers or replace their exact-point accessibility times.
It only sets ``excludeFromIdw`` for points where moving to another station/component
lets the user leave at least one minute later than a verified route that boards the
queried physical component directly.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--stations', default='data/stations.json')
    ap.add_argument('--audit', required=True)
    ap.add_argument('--output', default='data/stations.json')
    ap.add_argument('--compact-audit-output')
    args = ap.parse_args()

    stations_path = Path(args.stations)
    doc = json.loads(stations_path.read_text(encoding='utf-8'))
    audit = json.loads(Path(args.audit).read_text(encoding='utf-8'))
    summary = audit.get('summary') or {}
    rows = audit.get('rows') or []

    if len(doc.get('stations') or []) != 1563:
        raise SystemExit(f"expected 1563 station rows, got {len(doc.get('stations') or [])}")
    if summary.get('physicalPoints') != 1563 or len(rows) != 1563:
        raise SystemExit(f"audit coverage mismatch summary={summary.get('physicalPoints')} rows={len(rows)}")
    if len({str(r.get('id')) for r in rows}) != 1563:
        raise SystemExit('audit has duplicate/missing IDs')
    if int(summary.get('unresolved', -1)) != 0 or not summary.get('complete'):
        raise SystemExit(f"refusing incomplete audit: unresolved={summary.get('unresolved')} complete={summary.get('complete')}")

    by_id = {str(r['id']): r for r in rows}
    station_ids = {str(s['id']) for s in doc['stations']}
    if station_ids != set(by_id):
        missing = sorted(station_ids - set(by_id))[:20]
        extra = sorted(set(by_id) - station_ids)[:20]
        raise SystemExit(f'audit/station ID mismatch missing={missing} extra={extra}')

    excluded_ids = []
    compact_rows = []
    for station in doc['stations']:
        row = by_id[str(station['id'])]
        decision = row.get('decision')
        if decision not in {'include', 'exclude'}:
            raise SystemExit(f"bad decision for {station['id']}: {decision}")
        exclude = decision == 'exclude'
        station['excludeFromIdw'] = exclude
        if exclude:
            excluded_ids.append(str(station['id']))

        audit_meta = station.setdefault('physicalPointAudit', {})
        audit_meta['idwExclusion'] = {
            'decision': decision,
            'reason': row.get('decisionReason'),
            'unrestrictedDeparture': row.get('freeDeparture'),
            'unrestrictedMinutes': row.get('freeMinutes'),
            'unrestrictedFirstRailReason': row.get('freeReason'),
            'directDeparture': row.get('directDeparture'),
            'directMinutes': row.get('directMinutes'),
            'gainMinutes': row.get('gainMinutes'),
            'directOriginLabel': row.get('directLabel'),
            'directFirstRail': row.get('directFirstRail'),
        }

        compact_rows.append({
            'id': row['id'],
            'station': row.get('station'),
            'logicalStation': row.get('logicalStation'),
            'decision': decision,
            'excludeFromIdw': exclude,
            'unrestrictedDeparture': row.get('freeDeparture'),
            'directDeparture': row.get('directDeparture'),
            'gainMinutes': row.get('gainMinutes'),
            'unrestrictedFirstRailReason': row.get('freeReason'),
            'directOriginLabel': row.get('directLabel'),
            'directFirstRail': row.get('directFirstRail'),
        })

    excluded_ids.sort()
    excluded = len(excluded_ids)
    included = 1563 - excluded
    if excluded != int(summary.get('exclude', -1)) or included != int(summary.get('include', -1)):
        raise SystemExit(
            f"decision count mismatch audit include/exclude={summary.get('include')}/{summary.get('exclude')} "
            f"applied={included}/{excluded}"
        )

    old_policy = doc.get('meta', {}).get('idwExclusionPolicy') or {}
    doc['meta']['idwExclusionPolicy'] = {
        'rule': '座標が異なるN02物理駅地点はすべて表示する。各地点のunrestricted exact-point経路が別駅・別構成駅へ移動してから乗車する場合、同じ物理地点を直接使うことを確認した経路と比較し、unrestricted側が1分以上遅く出発できる地点だけIDWから除外する',
        'comparisonThresholdMinutes': 1,
        'fixedAccessTimeLimit': False,
        'allowedAccessModes': ['徒歩', '鉄道', '路線バス'],
        'auditedPhysicalPoints': 1563,
        'includedPhysicalPoints': included,
        'excludedPhysicalPoints': excluded,
        'excludedStations': excluded,
        'excludedPhysicalPointIds': excluded_ids,
        'directComparisons': int(summary.get('directComparisons', 0)),
        'unresolved': 0,
        'complete': True,
        'gainDistribution': summary.get('gainDistribution') or {},
        'exactPointAudit': old_policy.get('exactPointAudit'),
        'comparisonAudit': {
            'method': 'Yahoo!乗換案内: exact-point unrestricted route vs verified station/component-qualified direct route',
            'searchDate': '2026-08-28',
            'targetArrival': '08:18',
            'directRouteAcceptance': '最初の鉄道乗車駅名とN02路線/事業者対応の両方が当該物理駅地点に一致した経路のみ採用',
        },
    }

    Path(args.output).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    compact = {
        'summary': {
            'physicalPoints': 1563,
            'includedPhysicalPoints': included,
            'excludedPhysicalPoints': excluded,
            'directComparisons': int(summary.get('directComparisons', 0)),
            'gainDistribution': summary.get('gainDistribution') or {},
            'complete': True,
        },
        'rows': compact_rows,
    }
    if args.compact_audit_output:
        Path(args.compact_audit_output).write_text(json.dumps(compact, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    print(json.dumps(compact['summary'], ensure_ascii=False, indent=2))
    for name in ('熊野前', '東京', '武蔵小杉', '池袋', '両国', '弘明寺'):
        for station in doc['stations']:
            if station.get('logicalStation') == name:
                meta = station.get('physicalPointAudit', {}).get('idwExclusion', {})
                print('CHECK', name, station['id'], station['station'], meta.get('decision'), meta.get('unrestrictedDeparture'), meta.get('directDeparture'), meta.get('gainMinutes'))


if __name__ == '__main__':
    main()
