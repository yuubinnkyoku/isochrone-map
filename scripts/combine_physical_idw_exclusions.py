#!/usr/bin/env python3
"""Combine sharded physical-point IDW exclusion audits."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--input-dir', required=True)
    ap.add_argument('--json-output', required=True)
    ap.add_argument('--markdown-output', required=True)
    args = ap.parse_args()

    paths = sorted(Path(args.input_dir).glob('physical-idw-shard-*.json'))
    if not paths:
        raise SystemExit('no shard files')

    rows = []
    for path in paths:
        doc = json.loads(path.read_text(encoding='utf-8'))
        rows.extend(doc.get('rows', []))

    rows.sort(key=lambda r: int(r['globalIndex']))
    ids = [str(r['id']) for r in rows]
    if len(rows) != 1563 or len(set(ids)) != 1563:
        raise SystemExit(f'coverage mismatch rows={len(rows)} unique={len(set(ids))}')
    if [int(r['globalIndex']) for r in rows] != list(range(1563)):
        raise SystemExit('global index coverage mismatch')

    decisions = Counter(str(r.get('decision')) for r in rows)
    unresolved = [r for r in rows if r.get('decision') == 'unresolved']
    excluded = [r for r in rows if r.get('decision') == 'exclude']
    compared = [r for r in rows if r.get('directMinutes') is not None]
    gains = Counter(int(r.get('gainMinutes') or 0) for r in excluded)

    summary = {
        'physicalPoints': 1563,
        'rows': len(rows),
        'uniqueIds': len(set(ids)),
        'include': decisions.get('include', 0),
        'exclude': decisions.get('exclude', 0),
        'unresolved': len(unresolved),
        'directComparisons': len(compared),
        'gainDistribution': {str(k): v for k, v in sorted(gains.items())},
        'complete': len(unresolved) == 0,
    }
    output = {'summary': summary, 'rows': rows}
    Path(args.json_output).write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    lines = [
        '# Physical station point IDW exclusion audit', '',
        f"- physical points: {summary['physicalPoints']}",
        f"- include: {summary['include']}",
        f"- exclude: {summary['exclude']}",
        f"- unresolved: {summary['unresolved']}",
        f"- direct comparisons: {summary['directComparisons']}",
        f"- complete: {summary['complete']}", '',
        '## Excluded physical points', '',
        '| station | id | unrestricted | direct | gain | direct label | reason |',
        '|---|---|---:|---:|---:|---|---|',
    ]
    for r in excluded:
        lines.append(
            f"| {r.get('station')} | `{r.get('id')}` | {r.get('freeDeparture')} | "
            f"{r.get('directDeparture')} | +{r.get('gainMinutes')} | {r.get('directLabel')} | {r.get('freeReason')} |"
        )
    if unresolved:
        lines += ['', '## Unresolved', '', '| station | id | unrestricted reason | attempts |', '|---|---|---|---:|']
        for r in unresolved:
            lines.append(f"| {r.get('station')} | `{r.get('id')}` | {r.get('freeReason')} | {len(r.get('attempts') or [])} |")
    Path(args.markdown_output).write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
