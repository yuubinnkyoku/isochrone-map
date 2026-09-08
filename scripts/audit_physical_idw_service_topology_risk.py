#!/usr/bin/env python3
"""Flag exact-N02-adjacency IDW exclusions that need service-level verification.

N02 RailroadSection is infrastructure topology, not a passenger timetable graph.  A
physical station may therefore have a perfectly valid N02 neighbor set that does not
enumerate every passenger service/stopping pattern usable from that platform (JR trunk
lines are the clearest example).  That distinction matters only for *exclusions*: an
incomplete direct-component baseline could make an exclusion too aggressive.

This script is read-only.  It never upgrades a row to exclusion.  It reports structural
warning signals so newly excluded exact-adjacency rows can be targeted with stronger
service-level checks before the generated IDW grid is finalized.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

JR_OPERATORS = {
    '東日本旅客鉄道', '東海旅客鉄道', '西日本旅客鉄道', '北海道旅客鉄道',
    '四国旅客鉄道', '九州旅客鉄道', '日本貨物鉄道',
}

# These N02 names commonly describe infrastructure corridors shared by several public
# service names / stopping patterns in the Tokyo-area data.  Presence is a warning, not
# proof of an error.
INFRASTRUCTURE_SERVICE_RISK_LINES = {
    '東海道線', '東北線', '総武線', '中央線', '山手線', '赤羽線', '常磐線',
    '横須賀線', '武蔵野線', '南武線', '根岸線', '高崎線',
}


def exact_links_for(code: str, line: str, operator: str, adjacency: dict[str, list[dict]]) -> list[dict]:
    return [
        item for item in adjacency.get(code, [])
        if str(item.get('line')) == line and str(item.get('operator')) == operator
    ]


def reverse_exists(src: str, dst: str, line: str, operator: str, adjacency: dict[str, list[dict]]) -> bool:
    return any(
        str(item.get('stationCode')) == src
        and str(item.get('line')) == line
        and str(item.get('operator')) == operator
        for item in adjacency.get(dst, [])
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--audit', required=True)
    ap.add_argument('--adjacency', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--markdown-output')
    args = ap.parse_args()

    audit = json.loads(Path(args.audit).read_text(encoding='utf-8'))
    adjacency_doc = json.loads(Path(args.adjacency).read_text(encoding='utf-8'))
    adjacency = adjacency_doc.get('adjacency') or {}
    rows = audit.get('rows') or []
    if len(rows) != 1563:
        raise SystemExit(f'audit rows {len(rows)} != 1563')

    targets = [
        row for row in rows
        if row.get('decision') == 'exclude'
        and row.get('resolutionPass') == 'exact-n02-adjacent-via'
    ]
    reports = []
    for row in targets:
        warnings = []
        adjacent = row.get('adjacentRouteResults') or []
        gain = row.get('gainMinutes')
        if isinstance(gain, int) and gain <= 2:
            warnings.append({'kind': 'low-margin', 'detail': f'gain={gain} minute(s)'})
        if '+' in str(row.get('id') or ''):
            warnings.append({'kind': 'multi-code-physical-point', 'detail': str(row.get('id'))})

        lines = {str(v) for v in (row.get('lines') or [])}
        operators = {str(v) for v in (row.get('operators') or [])}
        if operators & JR_OPERATORS:
            warnings.append({'kind': 'jr-service-topology', 'detail': ','.join(sorted(operators & JR_OPERATORS))})
        risky_lines = lines & INFRASTRUCTURE_SERVICE_RISK_LINES
        if risky_lines:
            warnings.append({'kind': 'infrastructure-service-name', 'detail': ','.join(sorted(risky_lines))})

        if not adjacent:
            warnings.append({'kind': 'missing-adjacency-evidence', 'detail': 'no adjacentRouteResults'})

        asymmetric = []
        branchy = []
        statuses = Counter()
        for item in adjacent:
            status = str(item.get('status') or '')
            statuses[status] += 1
            src = str(item.get('fromStationCode') or '')
            dst = str(item.get('stationCode') or item.get('adjacentStationCode') or '')
            line = str(item.get('line') or '')
            operator = str(item.get('operator') or '')
            if src and dst and dst in adjacency and not reverse_exists(src, dst, line, operator, adjacency):
                asymmetric.append({
                    'fromStationCode': src,
                    'adjacentStationCode': dst,
                    'adjacentStation': item.get('name') or item.get('adjacentStation'),
                    'line': line,
                    'operator': operator,
                })
            degree = len(exact_links_for(src, line, operator, adjacency)) if src else 0
            if degree > 2:
                branchy.append({
                    'stationCode': src,
                    'line': line,
                    'operator': operator,
                    'adjacentCount': degree,
                })

        if any(status != 'verified' for status in statuses):
            warnings.append({'kind': 'nonverified-adjacency', 'detail': dict(statuses)})
        if asymmetric:
            warnings.append({'kind': 'asymmetric-n02-adjacency', 'detail': asymmetric})
        if branchy:
            warnings.append({'kind': 'n02-junction-degree', 'detail': branchy})

        attempts = row.get('viaResolutionAttempts') or []
        verified_attempts = [a for a in attempts if a.get('reason') == 'boards-current-physical-point']
        verified_vias = sorted({str(a.get('via') or '') for a in verified_attempts if a.get('via')})
        verified_origins = sorted({str(a.get('originLabel') or '') for a in verified_attempts if a.get('originLabel')})
        if len(verified_vias) <= 1:
            warnings.append({'kind': 'single-verified-via-label', 'detail': verified_vias})

        reports.append({
            'id': row.get('id'),
            'station': row.get('station'),
            'logicalStation': row.get('logicalStation'),
            'freeDeparture': row.get('freeDeparture'),
            'directDeparture': row.get('directDeparture'),
            'gainMinutes': gain,
            'directVia': row.get('directVia'),
            'freeReason': row.get('freeReason'),
            'lines': sorted(lines),
            'operators': sorted(operators),
            'adjacencyStatusCounts': dict(sorted(statuses.items())),
            'verifiedViaLabels': verified_vias,
            'verifiedOriginLabels': verified_origins,
            'warningKinds': sorted({w['kind'] for w in warnings}),
            'warnings': warnings,
            'requiresServiceLevelReview': bool(warnings),
        })

    warning_counts = Counter(kind for report in reports for kind in report['warningKinds'])
    clean = [r for r in reports if not r['requiresServiceLevelReview']]
    summary = {
        'exactAdjacencyExclusions': len(reports),
        'requiringServiceLevelReview': len(reports) - len(clean),
        'withoutStructuralWarnings': len(clean),
        'warningCounts': dict(sorted(warning_counts.items())),
        'policy': 'warnings are conservative triage only; no warning proves an exclusion is wrong and no clean row is automatically finalized',
    }
    out = {'summary': summary, 'rows': reports}
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    if args.markdown_output:
        lines_out = [
            '# IDW exclusion service-topology risk audit', '',
            f"- exact-adjacency exclusions: {summary['exactAdjacencyExclusions']}",
            f"- requiring service-level review: {summary['requiringServiceLevelReview']}",
            f"- without structural warnings: {summary['withoutStructuralWarnings']}", '',
            '| station | id | free | direct | gain | direct via | warnings |',
            '|---|---|---:|---:|---:|---|---|',
        ]
        for report in reports:
            lines_out.append(
                f"| {report['station']} | `{report['id']}` | {report.get('freeDeparture')} | "
                f"{report.get('directDeparture')} | {report.get('gainMinutes')} | "
                f"{report.get('directVia') or ''} | {', '.join(report['warningKinds'])} |"
            )
        Path(args.markdown_output).write_text('\n'.join(lines_out) + '\n', encoding='utf-8')

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
