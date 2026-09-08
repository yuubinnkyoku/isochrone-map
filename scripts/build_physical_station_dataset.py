#!/usr/bin/env python3
"""Build stations.json from the complete physical-point Yahoo audit.

This is deliberately deterministic: all 1563 N02 physical points must be present,
queried successfully, origin-verified, and classified before the output is written.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path

from audit_physical_station_points_full import is_bus_service, is_rail_service

TARGET_MINUTES = 8 * 60 + 18


def hhmm_minutes(value: str | None) -> int | None:
    if not value or not re.fullmatch(r'\d{1,2}:\d{2}', value):
        return None
    h,m=map(int,value.split(':'))
    raw=h*60+m
    # In an arrival-by-08:18 search, a displayed clock time after 08:18 is from
    # the previous day.  Store it on the continuous timeline used by the map.
    return raw-1440 if raw>TARGET_MINUTES else raw


def haversine_m(a_lat: float,a_lng: float,b_lat: float,b_lng: float)->float:
    r=6371008.8
    p1=math.radians(a_lat); p2=math.radians(b_lat)
    dp=math.radians(b_lat-a_lat); dl=math.radians(b_lng-a_lng)
    x=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.asin(math.sqrt(x))


def route_summary(section: list[str]) -> str:
    parts=[]
    for raw in section:
        line=str(raw).strip()
        if not line:
            continue
        if line.startswith('徒歩') and re.match(r'^徒歩\d+分$',line):
            parts.append(line)
        elif is_bus_service(line) or is_rail_service(line):
            parts.append(line)
    # Consecutive duplicate UI-derived entries are not useful in the tooltip.
    out=[]
    for part in parts:
        if not out or out[-1]!=part:
            out.append(part)
    return ' → '.join(out)


def line_display(lines: list[str], operators: list[str]) -> str:
    result=[]
    for line in lines:
        value=line
        # N02 prefixes several Tokyo subway lines with planning-number notation.
        m=re.match(r'^\d+号線(.+)$',value)
        if m:
            value=m.group(1)
        if value in {'1号線','3号線'} and '横浜市' in operators:
            value='横浜市営地下鉄ブルーライン'
        elif value=='4号線' and '横浜市' in operators:
            value='横浜市営地下鉄グリーンライン'
        if value not in result:
            result.append(value)
    return ' / '.join(result) if result else '鉄道'


def main()->None:
    ap=argparse.ArgumentParser()
    ap.add_argument('--old-stations',default='data/stations.json')
    ap.add_argument('--proposal',default='data/physical-station-points-proposal.json')
    ap.add_argument('--audit',required=True)
    ap.add_argument('--output',required=True)
    ap.add_argument('--compact-audit-output')
    args=ap.parse_args()

    old=json.loads(Path(args.old_stations).read_text(encoding='utf-8'))
    proposal=json.loads(Path(args.proposal).read_text(encoding='utf-8'))
    audit=json.loads(Path(args.audit).read_text(encoding='utf-8'))
    rows=audit.get('rows',[])
    expected=int(proposal['meta']['physicalPoints'])
    if expected!=1563 or len(rows)!=expected:
        raise SystemExit(f'physical audit coverage mismatch rows={len(rows)} expected={expected}')
    if len({r.get('id') for r in rows})!=expected:
        raise SystemExit('duplicate/missing physical point IDs')
    bad=[r for r in rows if r.get('error') or not r.get('originVerified') or r.get('classification') not in {'keep','exclude'}]
    if bad:
        raise SystemExit(f'unresolved physical points: {len(bad)}')

    old_by_id={s['id']:s for s in old['stations']}
    grouped=defaultdict(list)
    for r in rows:
        grouped[r['logicalStationId']].append(r)

    primary_ids={}
    for logical_id,items in grouped.items():
        source=old_by_id[logical_id]
        primary=min(items,key=lambda r:(
            haversine_m(float(source['lat']),float(source['lng']),float(r['lat']),float(r['lng'])),
            str(r['id']),
        ))
        primary_ids[logical_id]=primary['id']

    stations=[]
    compact_rows=[]
    excluded=0
    for r in sorted(rows,key=lambda x:int(x['globalIndex'])):
        source=old_by_id[r['logicalStationId']]
        minutes=hhmm_minutes(r.get('departure'))
        arrival_minutes=hhmm_minutes(r.get('arrival'))
        if minutes is None or arrival_minutes is None:
            raise SystemExit(f"bad clock time for {r['id']}: {r.get('departure')} {r.get('arrival')}")
        # Arrival is always target-day; hhmm_minutes would only make it previous-day
        # if Yahoo returned an impossible after-target arrival, which the audit forbids.
        if arrival_minutes < minutes:
            arrival_minutes += 1440
        is_excluded=r['classification']=='exclude'
        excluded += int(is_excluded)
        station={
            'id':r['id'],
            'station':r['logicalStation'],
            'logicalStationId':r['logicalStationId'],
            'n02StationCodes':r.get('n02StationCodes',[]),
            'n02GroupCodes':r.get('n02GroupCodes',[]),
            'lat':r['lat'], 'lng':r['lng'],
            'minutes':minutes,
            'departureDisplay':('前日'+r['departure']) if minutes<0 else r['departure'],
            'line':line_display(r.get('lines') or [],r.get('operators') or []),
            'route':route_summary(r.get('route1') or []),
            'searchDate':'2026-08-28',
            'outside':bool(source.get('outside',False)),
            'duration':arrival_minutes-minutes,
            'major':bool(source.get('major',False)),
            'labelPrimary':r['id']==primary_ids[r['logicalStationId']],
            'excludeFromIdw':is_excluded,
            'physicalPointAudit':{
                'classification':r['classification'],
                'reason':r.get('reason'),
                'originDistanceM':r.get('originDistanceM'),
                'firstRail':r.get('firstRail'),
            },
        }
        for key in ('passengers','passengerYear','passengerRank'):
            if source.get(key) is not None:
                station[key]=source[key]
        if source.get('note'):
            station['note']=source['note']
        stations.append(station)
        compact_rows.append({
            'id':r['id'],'logicalStationId':r['logicalStationId'],'station':r['logicalStation'],
            'lat':r['lat'],'lng':r['lng'],'lines':r.get('lines',[]),
            'departure':r.get('departure'),'arrival':r.get('arrival'),'minutes':minutes,
            'originDistanceM':r.get('originDistanceM'),'classification':r['classification'],
            'reason':r.get('reason'),'firstRail':r.get('firstRail'),'hasBus':r.get('hasBus',False),
        })

    meta=dict(old['meta'])
    meta['version']=3
    meta['stationModel']={
        'logicalStations':proposal['meta']['logicalStations'],
        'physicalPoints':expected,
        'logicalStationsWithMultiplePhysicalPoints':proposal['meta']['logicalStationsWithMultiplePhysicalPoints'],
        'rule':'N02-25の路線別駅コードを基準に、同一乗換駅でも代表座標が異なる物理駅地点はすべて別点として表示する。完全に同一座標の駅コードのみ1点にまとめる',
    }
    meta['radiusCoverage']=dict(meta['radiusCoverage'])
    meta['radiusCoverage']['physicalPoints']=expected
    meta['idwExclusionPolicy']={
        'rule':'各物理駅地点をYahoo!乗換案内flatlonで08:18着検索し、最適ルートが最初に鉄道へ乗る地点が別駅または同一乗換駅の別物理地点なら、その物理地点をIDW補間から除外する。徒歩・路線バスのみで鉄道へ乗らない最適経路は別駅値の借用ではないため残す',
        'fixedAccessTimeLimit':False,
        'allowedAccessModes':['徒歩','鉄道','路線バス'],
        'auditedPhysicalPoints':expected,
        'originToleranceM':5.0,
        'excludedStations':excluded,
        'excludedPhysicalPoints':excluded,
        'includedPhysicalPoints':expected-excluded,
        'exactPointAudit':{
            'method':'Yahoo!乗換案内 flatlon + fromLat/fromLon verification + first-rail boarding component classification',
            'searchDate':'2026-08-28','targetArrival':'08:18','originVerified':expected,
        },
    }
    output={'meta':meta,'stations':stations}
    Path(args.output).write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

    compact_summary={
        'physicalPoints':expected,'logicalStations':proposal['meta']['logicalStations'],
        'multiPointLogicalStations':proposal['meta']['logicalStationsWithMultiplePhysicalPoints'],
        'originVerified':expected,'excludedPhysicalPoints':excluded,
        'includedPhysicalPoints':expected-excluded,
    }
    if args.compact_audit_output:
        Path(args.compact_audit_output).write_text(json.dumps({'summary':compact_summary,'rows':compact_rows},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(compact_summary,ensure_ascii=False,indent=2))
    for name in ('熊野前','東京','武蔵小杉','池袋','両国','早稲田(都電)'):
        for s in stations:
            if s['station']==name:
                first=(s['physicalPointAudit'].get('firstRail') or {})
                print('CHECK',name,s['id'],s['line'],s['departureDisplay'],s['excludeFromIdw'],s['physicalPointAudit']['reason'],first.get('boardStation'),first.get('service'))

if __name__=='__main__': main()
