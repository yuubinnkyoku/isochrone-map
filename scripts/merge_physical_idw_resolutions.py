#!/usr/bin/env python3
"""Overlay via-constrained resolutions onto the initial physical IDW audit.

Initial rows that were already include/exclude are immutable.  A via-resolution row may
replace only an initial unresolved row with the same ID/globalIndex.  Full coverage and
all decision counts are recomputed after the overlay; `complete` is true only when no
unresolved row remains.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def load_initial(input_dir: Path) -> list[dict]:
    paths = sorted(input_dir.glob('physical-idw-shard-*.json'))
    if not paths:
        raise SystemExit(f'no initial shards under {input_dir}')
    rows = []
    for path in paths:
        rows.extend(json.loads(path.read_text(encoding='utf-8')).get('rows') or [])
    rows.sort(key=lambda r: int(r['globalIndex']))
    ids = [str(r.get('id')) for r in rows]
    if len(rows) != 1563 or len(set(ids)) != 1563:
        raise SystemExit(f'initial coverage mismatch rows={len(rows)} unique={len(set(ids))}')
    if [int(r['globalIndex']) for r in rows] != list(range(1563)):
        raise SystemExit('initial global-index coverage mismatch')
    return rows


def load_resolutions(input_dir: Path) -> dict[str, dict]:
    paths = sorted(input_dir.glob('physical-idw-via-resolution-*.json'))
    if not paths:
        raise SystemExit(f'no resolution shards under {input_dir}')
    out: dict[str, dict] = {}
    for path in paths:
        for row in json.loads(path.read_text(encoding='utf-8')).get('rows') or []:
            row_id = str(row.get('id'))
            if row_id in out:
                raise SystemExit(f'duplicate resolution ID {row_id}')
            out[row_id] = row
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--initial-dir', required=True)
    ap.add_argument('--resolution-dir', required=True)
    ap.add_argument('--json-output', required=True)
    ap.add_argument('--markdown-output', required=True)
    args = ap.parse_args()

    initial = load_initial(Path(args.initial_dir))
    resolutions = load_resolutions(Path(args.resolution_dir))
    initial_by_id = {str(r['id']): r for r in initial}
    initial_unresolved = {str(r['id']) for r in initial if r.get('decision') == 'unresolved'}

    extra = sorted(set(resolutions) - initial_unresolved)
    if extra:
        raise SystemExit(f'resolution rows were not initially unresolved: {extra[:20]}')
    missing_resolution = sorted(initial_unresolved - set(resolutions))
    if missing_resolution:
        raise SystemExit(f'missing resolver output rows: {missing_resolution[:20]}')

    rows = []
    for old in initial:
        row_id = str(old['id'])
        if row_id not in resolutions:
            rows.append(old)
            continue
        new = resolutions[row_id]
        if int(new.get('globalIndex', -1)) != int(old['globalIndex']):
            raise SystemExit(f'globalIndex changed for {row_id}')
        # Preserve an explicit audit trail that this row came from second-pass via
        # resolution rather than silently mutating the first pass.
        new = dict(new)
        new['resolutionPass'] = 'same-line-via'
        rows.append(new)

    rows.sort(key=lambda r: int(r['globalIndex']))
    decisions = Counter(str(r.get('decision')) for r in rows)
    unresolved = [r for r in rows if r.get('decision') == 'unresolved']
    excluded = [r for r in rows if r.get('decision') == 'exclude']
    compared = [r for r in rows if r.get('directMinutes') is not None]
    gains = Counter(int(r.get('gainMinutes') or 0) for r in excluded)

    summary = {
        'physicalPoints': 1563,
        'rows': len(rows),
        'uniqueIds': len({str(r['id']) for r in rows}),
        'initialUnresolved': len(initial_unresolved),
        'viaResolved': sum(r.get('decision') in {'include', 'exclude'} for r in resolutions.values()),
        'include': decisions.get('include', 0),
        'exclude': decisions.get('exclude', 0),
        'unresolved': len(unresolved),
        'directComparisons': len(compared),
        'gainDistribution': {str(k): v for k, v in sorted(gains.items())},
        'complete': len(unresolved) == 0,
    }
    Path(args.json_output).write_text(
        json.dumps({'summary': summary, 'rows': rows}, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )

    lines = [
        '# Physical station point IDW exclusion audit', '',
        f"- physical points: {summary['physicalPoints']}",
        f"- initial unresolved: {summary['initialUnresolved']}",
        f"- resolved by same-line via pass: {summary['viaResolved']}",
        f"- include: {summary['include']}",
        f"- exclude: {summary['exclude']}",
        f"- unresolved: {summary['unresolved']}",
        f"- direct comparisons: {summary['directComparisons']}",
        f"- complete: {summary['complete']}", '',
        '## Excluded physical points', '',
        '| station | id | unrestricted | direct | gain | direct label | via | unrestricted first-rail |',
        '|---|---|---:|---:|---:|---|---|---|',
    ]
    for r in excluded:
        lines.append(
            f"| {r.get('station')} | `{r.get('id')}` | {r.get('freeDeparture')} | "
            f"{r.get('directDeparture')} | +{r.get('gainMinutes')} | {r.get('directLabel')} | "
            f"{r.get('directVia') or ''} | {r.get('freeReason')} |"
        )
    if unresolved:
        lines += ['', '## Still unresolved', '', '| station | id | unrestricted reason | via attempts |', '|---|---|---|---:|']
        for r in unresolved:
            lines.append(
                f"| {r.get('station')} | `{r.get('id')}` | {r.get('freeReason')} | "
                f"{len(r.get('viaResolutionAttempts') or [])} |"
            )
    Path(args.markdown_output).write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
