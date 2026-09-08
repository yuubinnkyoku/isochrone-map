#!/usr/bin/env python3
"""Combine sharded physical-station Yahoo recalculation reports."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path


def shard_number(path: Path) -> int:
    m=re.search(r'(\d+)(?=\.json$)', path.name)
    return int(m.group(1)) if m else 10**9


def main() -> None:
    ap=argparse.ArgumentParser()
    ap.add_argument('--input-dir', required=True)
    ap.add_argument('--json-output', required=True)
    ap.add_argument('--markdown-output', required=True)
    args=ap.parse_args()
    files=sorted(Path(args.input_dir).glob('physical-shard-*.json'), key=shard_number)
    if not files:
        raise SystemExit('no physical-shard-*.json inputs')
    rows=[]
    metas=[]
    for p in files:
        doc=json.loads(p.read_text(encoding='utf-8'))
        metas.append(doc.get('meta',{}))
        rows.extend(doc.get('rows',[]))
    rows.sort(key=lambda r:int(r.get('globalIndex',10**9)))
    ids=[str(r.get('id') or '') for r in rows]
    duplicates=sorted(k for k,v in Counter(ids).items() if v>1)
    expected=max((int(m.get('projectPhysicalPoints') or 0) for m in metas),default=0)
    errors=[r for r in rows if r.get('error')]
    unparsed=[r for r in rows if r.get('classification')=='unparsed' and not r.get('error')]
    kept=[r for r in rows if r.get('classification')=='keep' and not r.get('error')]
    excluded=[r for r in rows if r.get('classification')=='exclude' and not r.get('error')]
    reason_counts=Counter(r.get('reason') or 'none' for r in rows if not r.get('error'))
    summary={
        'shards':len(files), 'expectedPhysicalPoints':expected, 'rows':len(rows),
        'uniqueIds':len(set(ids)), 'duplicateIds':duplicates,
        'queriedSuccessfully':len(rows)-len(errors),
        'originVerified':sum(1 for r in rows if r.get('originVerified')),
        'keep':len(kept), 'exclude':len(excluded), 'unparsed':len(unparsed),
        'errors':len(errors), 'reasonCounts':dict(sorted(reason_counts.items())),
        'complete':len(rows)==expected and len(set(ids))==expected and not duplicates and not errors and not unparsed,
    }
    report={'summary':summary,'excluded':excluded,'unparsed':unparsed,'errors':errors,'rows':rows}
    Path(args.json_output).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    md=['# 物理駅地点 Yahoo 全件再計算','',
        'N02-25の路線別駅地点を座標差がある限り別点として保持し、各点をYahoo!乗換案内の`flatlon`で08:18着検索した監査。',
        'ルート1が最初に鉄道へ乗る地点が当該物理駅地点ならIDW候補として保持し、別駅・別乗換成分なら除外する。鉄道を使わない徒歩/バスのみの経路は別駅値の借用ではないため保持する。','', '## 集計','']
    for k,v in summary.items(): md.append(f'- {k}: **{v}**')
    md += ['', '## 除外点', '', '| 駅 | 物理ID | 路線 | 出発 | 理由 | 最初の鉄道乗車駅 | 最初の鉄道 |', '|---|---|---|---:|---|---|---|']
    for r in excluded:
        rail=r.get('firstRail') or {}
        md.append(f"| {r.get('logicalStation')} | `{r.get('id')}` | {', '.join(r.get('lines') or [])} | {r.get('departure')} | {r.get('reason')} | {rail.get('boardStation')} | {rail.get('service')} |")
    if unparsed or errors:
        md += ['', '## 未解決', '']
        for r in unparsed+errors:
            md.append(f"- {r.get('logicalStation')} `{r.get('id')}`: {r.get('error') or r.get('reason')}")
    Path(args.markdown_output).write_text('\n'.join(md)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    for r in unparsed+errors:
        rail=r.get('firstRail') or {}
        print('UNRESOLVED',r.get('id'),r.get('logicalStation'),r.get('error'),r.get('reason'),rail.get('boardStation'),rail.get('service'))
    if len(rows)!=expected or duplicates or errors:
        raise SystemExit('physical audit coverage/query failure')

if __name__=='__main__': main()
