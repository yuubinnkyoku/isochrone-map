#!/usr/bin/env python3
"""Reclassify physical-point routes with N02 station-name aliases.

The query shard records keep both the project display name and the original N02
station names.  Yahoo may use either (e.g. 赤塚 / 下赤塚), so final component
classification accepts every N02 name attached to the physical point rather than
relying on the project display string alone.  No network access is performed here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from audit_physical_station_points_full import line_matches_point, station_base


def reclassify(row: dict) -> tuple[str,str]:
    if row.get('error'):
        return 'unparsed', 'query-error'
    if row.get('classification')=='unparsed':
        return 'unparsed', row.get('reason') or 'unparsed'
    rail=row.get('firstRail')
    if not rail:
        return 'keep','no-rail-boarded'
    board=rail.get('boardStation')
    board_base=station_base(board)
    accepted={station_base(row.get('logicalStation'))}
    accepted.update(station_base(name) for name in (row.get('n02Names') or []))
    same_station=bool(board_base) and board_base in accepted
    line_match,line_reason=line_matches_point(row,str(rail.get('service') or ''))
    rail['sameLogicalStationBase']=same_station
    rail['lineMatchesPhysicalPoint']=line_match
    rail['lineMatchReason']=line_reason
    rail['acceptedStationBases']=sorted(x for x in accepted if x)
    if same_station and line_match:
        return 'keep','boards-current-physical-point'
    if not same_station:
        return 'exclude','boards-different-station'
    return 'exclude','boards-different-component'


def main()->None:
    ap=argparse.ArgumentParser()
    ap.add_argument('--input',required=True)
    ap.add_argument('--output',required=True)
    args=ap.parse_args()
    doc=json.loads(Path(args.input).read_text(encoding='utf-8'))
    changes=[]
    for row in doc.get('rows',[]):
        old=(row.get('classification'),row.get('reason'))
        new=reclassify(row)
        row['classification'],row['reason']=new
        if old!=new:
            changes.append({'id':row.get('id'),'station':row.get('logicalStation'),'old':old,'new':new})
    counts={key:sum(1 for r in doc['rows'] if r.get('classification')==key and not r.get('error')) for key in ('keep','exclude','unparsed')}
    errors=sum(1 for r in doc['rows'] if r.get('error'))
    summary=dict(doc.get('summary') or {})
    summary.update({'keep':counts['keep'],'exclude':counts['exclude'],'unparsed':counts['unparsed'],'errors':errors,'reclassifiedRows':len(changes)})
    summary['complete']=len(doc['rows'])==1563 and errors==0 and counts['unparsed']==0
    doc['summary']=summary
    doc['excluded']=[r for r in doc['rows'] if r.get('classification')=='exclude' and not r.get('error')]
    doc['unparsed']=[r for r in doc['rows'] if r.get('classification')=='unparsed']
    doc['errors']=[r for r in doc['rows'] if r.get('error')]
    doc['reclassificationChanges']=changes
    Path(args.output).write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    for row in changes[:100]: print('RECLASS',row)
    if not summary['complete']:
        raise SystemExit('reclassified physical audit is not complete')

if __name__=='__main__': main()
