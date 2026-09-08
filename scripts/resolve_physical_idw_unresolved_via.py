#!/usr/bin/env python3
"""Resolve unresolved direct-component baselines with exact adjacent-station vias.

A route that boards a given physical rail component must next reach one of that
component's route-topological adjacent stations. N02 adjacency is derived separately
from RailroadSection geometry by `build_n02_station_adjacency.py`, not guessed from
straight-line proximity.

For every initially unresolved physical point, this script constrains Yahoo route 1 with
`via01` for *each* exact adjacent station of every N02 station code represented by the
physical point. A candidate counts only when route 1's first rail boarding verifies as
the queried physical point. The latest verified departure across all adjacent links is
therefore the direct-component baseline.

An exclusion is emitted only when every topological adjacent link has a verified direct-
component route and the unrestricted exact-point departure is at least one minute later.
A Yahoo 200 response with no route is deliberately NOT treated as proof that a direction
is impossible, because an ambiguous origin/via label could itself be the reason no route
was produced. Missing/unverifiable directions remain unresolved rather than risking a
false exclusion.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.parse
from pathlib import Path

from audit_physical_idw_exclusions import (
    LINE_ALIASES,
    OPERATOR_ALIASES,
    clean_display_label,
    minutes_on_timeline,
    normalize_line,
    query_with_retry,
)
from audit_physical_station_points_full import classify
from audit_yahoo_targeted import build_url


def build_via_url(origin: str, via: str) -> str:
    parsed = urllib.parse.urlsplit(build_url(origin))
    params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    params = [(k, v) for k, v in params if k != 'via01']
    params.append(('via01', via))
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(params), parsed.fragment)
    )


def origin_labels(row: dict) -> list[str]:
    values = []
    # Keep component-specific labels first; the initial audit already generated these
    # from the exact N02 line/operator metadata.
    for attempt in row.get('attempts') or []:
        label = str(attempt.get('label') or '').strip()
        if label and label not in values:
            values.append(label)
        if len(values) >= 3:
            break
    display = clean_display_label(str(row.get('station') or '')).strip()
    if display and display not in values:
        values.append(display)
    return values[:3]


def via_labels(adj: dict) -> list[str]:
    name = str(adj.get('name') or '').strip()
    line = str(adj.get('line') or '').strip()
    operator = str(adj.get('operator') or '').strip()
    qualifiers = []
    for q in [line, normalize_line(line), *(LINE_ALIASES.get(line) or []), *(OPERATOR_ALIASES.get(operator) or [])]:
        q = str(q or '').strip()
        if q and q not in qualifiers:
            qualifiers.append(q)
    values = [name]
    for q in qualifiers[:4]:
        values.extend((f'{name}({q})', f'{name}（{q}）'))
    return list(dict.fromkeys(v for v in values if v))


def load_rows(input_dir: Path) -> list[dict]:
    paths = sorted(input_dir.glob('physical-idw-shard-*.json'))
    if not paths:
        raise SystemExit(f'no initial shard files in {input_dir}')
    rows = []
    for path in paths:
        rows.extend(json.loads(path.read_text(encoding='utf-8')).get('rows') or [])
    rows.sort(key=lambda r: int(r['globalIndex']))
    if len(rows) != 1563 or len({str(r.get('id')) for r in rows}) != 1563:
        raise SystemExit(f'initial audit coverage mismatch rows={len(rows)} unique={len({str(r.get("id")) for r in rows})}')
    return rows


def adjacency_links(point: dict, adjacency: dict[str, list[dict]]) -> list[dict]:
    out = []
    seen = set()
    point_lines = {str(v) for v in (point.get('lines') or [])}
    point_ops = {str(v) for v in (point.get('operators') or [])}
    for code in point.get('n02StationCodes') or []:
        for adj in adjacency.get(str(code), []):
            # The mapping is per station-code route already, but retain this guard in
            # case an input proposal later merges coincident records from several lines.
            if str(adj.get('line')) not in point_lines or str(adj.get('operator')) not in point_ops:
                continue
            key = (str(code), str(adj.get('stationCode')), str(adj.get('line')), str(adj.get('operator')))
            if key in seen:
                continue
            seen.add(key)
            out.append({'fromStationCode': str(code), **adj})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--input-dir', required=True)
    ap.add_argument('--proposal', default='data/physical-station-points-proposal.json')
    ap.add_argument('--adjacency', default='data/n02-station-adjacency.json')
    ap.add_argument('--shard-index', type=int, required=True)
    ap.add_argument('--shard-count', type=int, required=True)
    ap.add_argument('--delay-seconds', type=float, default=0.55)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()

    rows = load_rows(Path(args.input_dir))
    proposal = json.loads(Path(args.proposal).read_text(encoding='utf-8'))
    point_by_id = {str(p['id']): p for p in proposal['physicalPoints']}
    adjacency_doc = json.loads(Path(args.adjacency).read_text(encoding='utf-8'))
    adjacency = adjacency_doc['adjacency']
    if len(point_by_id) != 1563:
        raise SystemExit(f'proposal physical point count {len(point_by_id)} != 1563')

    selected = [
        row for row in rows
        if row.get('decision') == 'unresolved'
        and int(row['globalIndex']) % args.shard_count == args.shard_index
    ]
    resolved_rows = []
    for ordinal, row in enumerate(selected, 1):
        point = point_by_id[str(row['id'])]
        links = adjacency_links(point, adjacency)
        labels = origin_labels(row)
        attempts = []
        link_results = []

        for link in links:
            verified = []
            link_attempts = 0
            saw_departure_unverified = False
            saw_no_route = False
            saw_error = False
            # Start with unqualified adjacent name; use line/operator-qualified via
            # variants only when necessary to disambiguate same-name stations.
            for via in via_labels(link):
                for origin in labels:
                    link_attempts += 1
                    attempt = {
                        'fromStationCode': link['fromStationCode'],
                        'adjacentStationCode': link['stationCode'],
                        'adjacentStation': link['name'],
                        'adjacentLine': link['line'],
                        'adjacentOperator': link['operator'],
                        'networkDistanceM': link['networkDistanceM'],
                        'originLabel': origin,
                        'via': via,
                    }
                    try:
                        result = query_with_retry(
                            build_via_url(origin, via),
                            float(row['lat']), float(row['lng']),
                        )
                        dep = result.get('summaryDeparture')
                        dep_minutes = minutes_on_timeline(dep)
                        route_class = classify(point, result.get('excerpt') or [])
                        attempt.update({
                            'departure': dep,
                            'arrival': result.get('summaryArrival'),
                            'classification': route_class.get('classification'),
                            'reason': route_class.get('reason'),
                            'firstRail': route_class.get('firstRail'),
                        })
                        if dep_minutes is None:
                            saw_no_route = True
                            attempt['attemptStatus'] = 'no-route-summary'
                        elif route_class.get('reason') == 'boards-current-physical-point':
                            attempt['attemptStatus'] = 'verified-direct-component'
                            verified.append({
                                'originLabel': origin,
                                'via': via,
                                'departure': dep,
                                'minutes': dep_minutes,
                                'firstRail': route_class.get('firstRail'),
                            })
                        else:
                            saw_departure_unverified = True
                            attempt['attemptStatus'] = 'route-boards-other-component'
                    except Exception as exc:
                        saw_error = True
                        attempt['attemptStatus'] = 'error'
                        attempt['error'] = f'{type(exc).__name__}: {exc}'
                    attempts.append(attempt)
                    time.sleep(args.delay_seconds)

                # Once this exact adjacent link has a verified direct-component route,
                # extra aliases cannot improve route-set completeness; stop variants.
                if verified:
                    break

            if verified:
                best = max(verified, key=lambda v: (int(v['minutes']), str(v['originLabel']), str(v['via'])))
                link_results.append({
                    **link,
                    'status': 'verified',
                    'attempts': link_attempts,
                    'best': best,
                })
            else:
                # Deliberately conservative: neither a no-route page nor a route that
                # starts on another component proves this adjacent direction is
                # impossible from the target component.
                link_results.append({
                    **link,
                    'status': 'unresolved',
                    'attempts': link_attempts,
                    'sawNoRoute': saw_no_route,
                    'sawDepartureOnOtherComponent': saw_departure_unverified,
                    'sawError': saw_error,
                })

        verified_all = [lr['best'] | {'link': lr} for lr in link_results if lr['status'] == 'verified']
        incomplete_links = [lr for lr in link_results if lr['status'] != 'verified']
        out = dict(row)
        out['resolutionPass'] = 'exact-n02-adjacent-via'
        out['adjacentRouteResults'] = link_results
        out['viaResolutionAttempts'] = attempts

        if not links:
            out['decision'] = 'unresolved'
            out['decisionReason'] = 'no-n02-adjacent-links'
            out['error'] = out['decisionReason']
        elif incomplete_links:
            # Even if a provisional route exists, an unresolved topological direction
            # might contain a later direct-component journey, so do not exclude.
            out['decision'] = 'unresolved'
            out['decisionReason'] = 'unresolved-adjacent-direction'
            out['error'] = out['decisionReason']
        else:
            # Every topological direction has a route that demonstrably starts on this
            # physical component, so the latest candidate is the complete direct
            # baseline for this component.
            best = max(verified_all, key=lambda v: int(v['minutes']))
            gain = int(row['freeMinutes']) - int(best['minutes'])
            link = best['link']
            out.update({
                'directLabel': best['originLabel'],
                'directVia': best['via'],
                'directViaStationCode': link['stationCode'],
                'directViaDistanceM': link['networkDistanceM'],
                'directDeparture': best['departure'],
                'directMinutes': best['minutes'],
                'directFirstRail': best['firstRail'],
                'gainMinutes': gain,
                'excludeFromIdw': gain >= 1,
                'decision': 'exclude' if gain >= 1 else 'include',
                'decisionReason': 'alternate-route-later-departure' if gain >= 1 else 'alternate-route-not-later',
            })
            out.pop('error', None)

        resolved_rows.append(out)
        print(
            f"[{ordinal}/{len(selected)}] {row['id']} {row.get('logicalStation')} "
            f"decision={out.get('decision')} free={row.get('freeMinutes')} "
            f"direct={out.get('directMinutes')} gain={out.get('gainMinutes')} "
            f"links={len(links)} unresolvedLinks={len(incomplete_links)} attempts={len(attempts)}"
        )

    summary = {
        'shardIndex': args.shard_index,
        'shardCount': args.shard_count,
        'initialUnresolvedInShard': len(selected),
        'resolved': sum(r.get('decision') in {'include', 'exclude'} for r in resolved_rows),
        'include': sum(r.get('decision') == 'include' for r in resolved_rows),
        'exclude': sum(r.get('decision') == 'exclude' for r in resolved_rows),
        'unresolved': sum(r.get('decision') == 'unresolved' for r in resolved_rows),
        'exactAdjacencyMethod': True,
        'noRouteNeverAutoExcludes': True,
    }
    Path(args.output).write_text(
        json.dumps({'summary': summary, 'rows': resolved_rows}, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
