#!/usr/bin/env python3
"""Conservatively sanitize an exact-adjacency IDW resolution artifact.

Run 34223734409 started before the resolver stopped treating a Yahoo 200/no-route page
as evidence that an adjacent direction was impossible.  This script makes that older
artifact safe without repeating successful Yahoo requests:

- initial include/exclude rows are unchanged;
- exact-N02-adjacent rows are accepted only when *every* recorded adjacent link has
  status ``verified``;
- any ``no-feasible-route`` / ``unresolved`` / missing adjacency status makes the point
  unresolved;
- the summary is recomputed from the sanitized rows.

This is intentionally a one-way conservative transformation: it can turn a second-pass
include/exclude back into unresolved, never the reverse.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def sanitize_row(row: dict) -> dict:
    out = dict(row)
    if str(row.get('resolutionPass') or '') != 'exact-n02-adjacent-via':
        return out

    adjacent = row.get('adjacentRouteResults') or []
    statuses = [str(item.get('status') or '') for item in adjacent]
    all_verified = bool(adjacent) and all(status == 'verified' for status in statuses)
    if all_verified:
        # A fully verified row is still subject to the separate service-topology risk
        # audit; this sanitizer addresses only the old no-route shortcut.
        return out

    out['preSanitizeDecision'] = row.get('decision')
    out['preSanitizeDecisionReason'] = row.get('decisionReason')
    out['decision'] = 'unresolved'
    out['decisionReason'] = 'unverified-adjacent-direction-after-no-route-sanitization'
    out['excludeFromIdw'] = False
    out['error'] = out['decisionReason']
    out['sanitization'] = {
        'rule': 'all exact N02 adjacent links must have status=verified',
        'adjacentLinkCount': len(adjacent),
        'statusCounts': dict(sorted(Counter(statuses).items())),
    }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--markdown-output')
    args = ap.parse_args()

    doc = json.loads(Path(args.input).read_text(encoding='utf-8'))
    rows = doc.get('rows') or []
    if len(rows) != 1563 or len({str(r.get('id')) for r in rows}) != 1563:
        raise SystemExit(f"coverage mismatch rows={len(rows)} unique={len({str(r.get('id')) for r in rows})}")

    sanitized = [sanitize_row(row) for row in rows]
    decisions = Counter(str(r.get('decision')) for r in sanitized)
    excluded = [r for r in sanitized if r.get('decision') == 'exclude']
    unresolved = [r for r in sanitized if r.get('decision') == 'unresolved']
    gains = Counter(
        int(r['gainMinutes']) for r in excluded
        if isinstance(r.get('gainMinutes'), int)
    )
    changed = [r for r in sanitized if r.get('preSanitizeDecision') is not None]
    passes = Counter(str(r.get('resolutionPass') or 'initial') for r in sanitized)

    old_summary = doc.get('summary') or {}
    summary = {
        'physicalPoints': 1563,
        'rows': 1563,
        'uniqueIds': 1563,
        'initialUnresolved': int(old_summary.get('initialUnresolved', 475)),
        'include': decisions.get('include', 0),
        'exclude': decisions.get('exclude', 0),
        'unresolved': decisions.get('unresolved', 0),
        'directComparisons': sum(r.get('directMinutes') is not None for r in sanitized),
        'gainDistribution': {str(k): v for k, v in sorted(gains.items())},
        'resolutionPasses': dict(sorted(passes.items())),
        'sanitizedRows': len(changed),
        'sanitizedFromExclude': sum(r.get('preSanitizeDecision') == 'exclude' for r in changed),
        'sanitizedFromInclude': sum(r.get('preSanitizeDecision') == 'include' for r in changed),
        'noRouteNeverAutoExcludes': True,
        'complete': len(unresolved) == 0,
    }
    out = {
        'summary': summary,
        'sourceSummary': old_summary,
        'sanitizationPolicy': (
            'Exact-adjacency rows are decisive only when every adjacentRouteResults '
            'entry is verified; no-route pages are not impossibility evidence.'
        ),
        'rows': sanitized,
    }
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    if args.markdown_output:
        lines = [
            '# Sanitized physical IDW audit', '',
            f"- include: {summary['include']}",
            f"- exclude: {summary['exclude']}",
            f"- unresolved: {summary['unresolved']}",
            f"- rows reverted to unresolved: {summary['sanitizedRows']}",
            f"- reverted from exclude: {summary['sanitizedFromExclude']}",
            f"- reverted from include: {summary['sanitizedFromInclude']}",
            f"- complete: {summary['complete']}", '',
        ]
        if changed:
            lines += [
                '## Reverted second-pass rows', '',
                '| station | id | previous decision | adjacent statuses |',
                '|---|---|---|---|',
            ]
            for row in changed:
                counts = row.get('sanitization', {}).get('statusCounts', {})
                status_text = ', '.join(f'{k}:{v}' for k, v in counts.items())
                lines.append(
                    f"| {row.get('station')} | `{row.get('id')}` | "
                    f"{row.get('preSanitizeDecision')} | {status_text} |"
                )
        Path(args.markdown_output).write_text('\n'.join(lines) + '\n', encoding='utf-8')

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
