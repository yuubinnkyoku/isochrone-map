#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from audit_physical_station_points_full import (
    UI_SKIP,
    classify_route1,
    is_bus_service,
    is_rail_service,
    is_timeish,
    line_matches_point,
    station_base,
)
from build_physical_station_dataset import hhmm_minutes, route_summary

MOTION = ('行', '方面', '当駅始発')
SERVICE_HINTS = (
    '急行', '快速', '各駅停車', '普通', '準急', '特快', '通勤', '特別快速',
    'ライナー', 'ロマンスカー', 'スカイライナー', 'アクセス特急', '新幹線',
    '特急', '鉄道', 'ライン', '線', 'モノレール', 'ゆりかもめ', 'エクスプレス',
    'ニューシャトル', 'シーサイドライン', '江ノ電', '江ノ島電鉄',
)
BAD_BOARD_TOKENS = ('分', '円', 'km', '行', '方面', '乗換', '出口', 'ホーム', '号車', '定期', 'IC', '徒歩', '地図')


def compact_loose(value: object) -> str:
    s = unicodedata.normalize('NFKC', str(value or '')).casefold()
    s = s.replace('ヶ', 'ケ').replace('ヵ', 'カ')
    s = re.sub(r'[\s・･()（）/／<>〈〉《》「」『』【】\[\]駅]', '', s)
    return s


def plausible_unclassified_service(line: str) -> bool:
    s = line.strip()
    if not s or s in UI_SKIP or is_timeish(s) or is_bus_service(s) or is_rail_service(s):
        return False
    c = unicodedata.normalize('NFKC', s)
    if not any(token in c for token in MOTION):
        return False
    if c.startswith(('出口：', '乗車位置：', '[発]', '[着]', '徒歩', 'IC優先', '乗換：', '定期の種類')):
        return False
    if re.fullmatch(r'\d+駅', c) or re.fullmatch(r'\d+か月.*', c):
        return False
    return any(token in c for token in SERVICE_HINTS)


def context(section: list[str], index: int, radius: int = 3) -> list[str]:
    a = max(0, index - radius)
    b = min(len(section), index + radius + 1)
    return [f'{i}: {section[i]}' for i in range(a, b)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--audit', required=True)
    ap.add_argument('--stations', default='data/stations.json')
    ap.add_argument('--output', default='route-parser-round2-report.json')
    args = ap.parse_args()

    raw = json.loads(Path(args.audit).read_text(encoding='utf-8'))
    current = json.loads(Path(args.stations).read_text(encoding='utf-8'))
    rows = raw['rows']
    stations = current['stations']
    assert len(rows) == 1563
    assert len(stations) == 1563

    station_by_id = {s['id']: s for s in stations}
    raw_by_id = {r['id']: r for r in rows}
    assert set(station_by_id) == set(raw_by_id)

    logical_points: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        logical_points[str(r['logicalStationId'])].append(r)

    parser_mismatch = []
    missing_summary_services = []
    suspicious_board = []
    possible_same_station = []
    no_component_match = []
    unclassified_before_first = []
    all_unclassified_movers = []
    time_mismatch = []
    empty_or_weird_routes = []

    reason_counts = Counter()
    first_service_counts = Counter()
    board_counts = Counter()

    for r in rows:
        section = r.get('route1') or []
        parsed = classify_route1(r, section)
        s = station_by_id[r['id']]
        audit = s.get('physicalPointAudit') or {}
        first = parsed.get('firstRail') or {}
        reason = parsed.get('reason')
        reason_counts[reason] += 1
        if first.get('service'):
            first_service_counts[first['service']] += 1
        if first.get('boardStation'):
            board_counts[first['boardStation']] += 1

        stored_sig = (
            audit.get('reason'),
            (audit.get('firstRail') or {}).get('service'),
            (audit.get('firstRail') or {}).get('boardStation'),
            (audit.get('firstRail') or {}).get('sameLogicalStationBase'),
            (audit.get('firstRail') or {}).get('lineMatchesPhysicalPoint'),
        )
        parsed_sig = (
            reason,
            first.get('service'),
            first.get('boardStation'),
            first.get('sameLogicalStationBase'),
            first.get('lineMatchesPhysicalPoint'),
        )
        if stored_sig != parsed_sig:
            parser_mismatch.append({'id': r['id'], 'station': r['logicalStation'], 'stored': stored_sig, 'parsed': parsed_sig})

        summary = route_summary(section)
        if summary != s.get('route'):
            empty_or_weird_routes.append({'id': r['id'], 'station': s['station'], 'storedRoute': s.get('route'), 'rebuiltRoute': summary})

        for line in section:
            if is_bus_service(line) or is_rail_service(line):
                if line.strip() not in summary:
                    missing_summary_services.append({'id': r['id'], 'station': s['station'], 'service': line, 'route': summary})

        dep = hhmm_minutes(r.get('departure'))
        arr = hhmm_minutes(r.get('arrival'))
        if dep is not None and arr is not None:
            if arr < dep:
                arr += 1440
            if dep != s.get('minutes') or arr - dep != s.get('duration'):
                time_mismatch.append({'id': r['id'], 'station': s['station'], 'rawDeparture': r.get('departure'), 'rawArrival': r.get('arrival'), 'storedMinutes': s.get('minutes'), 'storedDuration': s.get('duration')})

        first_index = first.get('serviceIndex') if first else None
        for i, line in enumerate(section):
            if plausible_unclassified_service(line):
                item = {'id': r['id'], 'station': r['logicalStation'], 'index': i, 'line': line, 'context': context(section, i)}
                all_unclassified_movers.append(item)
                if first_index is None or i < first_index:
                    unclassified_before_first.append(item)

        board = first.get('boardStation')
        if first:
            if not board or any(token.casefold() in str(board).casefold() for token in BAD_BOARD_TOKENS):
                suspicious_board.append({'id': r['id'], 'station': r['logicalStation'], 'firstRail': first, 'context': context(section, int(first.get('serviceIndex') or 0), 5)})

            if reason == 'boards-different-station' and board:
                a = station_base(board)
                b = station_base(r.get('logicalStation'))
                loose_a = compact_loose(board)
                loose_b = compact_loose(r.get('logicalStation'))
                ratio = SequenceMatcher(None, loose_a, loose_b).ratio() if loose_a and loose_b else 0.0
                if loose_a == loose_b or ratio >= 0.78 or a in b or b in a:
                    possible_same_station.append({'id': r['id'], 'logicalStation': r['logicalStation'], 'boardStation': board, 'ratio': round(ratio, 3), 'service': first.get('service')})

            if first.get('sameLogicalStationBase') and not first.get('lineMatchesPhysicalPoint'):
                matches = []
                for p in logical_points[str(r['logicalStationId'])]:
                    ok, why = line_matches_point(p, first.get('service') or '')
                    if ok:
                        matches.append({'id': p['id'], 'lines': p.get('lines'), 'operators': p.get('operators'), 'why': why})
                if not matches:
                    no_component_match.append({'id': r['id'], 'station': r['logicalStation'], 'service': first.get('service'), 'currentLines': r.get('lines'), 'currentOperators': r.get('operators')})

    report = {
        'summary': {
            'rows': len(rows),
            'parserMismatchWithCurrentStations': len(parser_mismatch),
            'routeSummaryMismatch': len(empty_or_weird_routes),
            'recognizedServicesMissingFromSummary': len(missing_summary_services),
            'timeMismatch': len(time_mismatch),
            'suspiciousBoardStation': len(suspicious_board),
            'possibleSameStationFalseDifferent': len(possible_same_station),
            'sameStationServiceMatchesNoPhysicalComponent': len(no_component_match),
            'plausibleUnclassifiedServiceBeforeFirstRail': len(unclassified_before_first),
            'allPlausibleUnclassifiedServiceLines': len(all_unclassified_movers),
            'reasonCounts': dict(reason_counts),
        },
        'parserMismatchWithCurrentStations': parser_mismatch[:100],
        'routeSummaryMismatch': empty_or_weird_routes[:100],
        'recognizedServicesMissingFromSummary': missing_summary_services[:100],
        'timeMismatch': time_mismatch[:100],
        'suspiciousBoardStation': suspicious_board[:100],
        'possibleSameStationFalseDifferent': possible_same_station[:100],
        'sameStationServiceMatchesNoPhysicalComponent': no_component_match[:100],
        'plausibleUnclassifiedServiceBeforeFirstRail': unclassified_before_first[:200],
        'allPlausibleUnclassifiedServiceLines': all_unclassified_movers[:300],
        'topFirstRailServices': first_service_counts.most_common(30),
        'topBoardStations': board_counts.most_common(30),
    }
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report['summary'], ensure_ascii=False, indent=2))
    for key in (
        'plausibleUnclassifiedServiceBeforeFirstRail',
        'sameStationServiceMatchesNoPhysicalComponent',
        'possibleSameStationFalseDifferent',
        'suspiciousBoardStation',
    ):
        print('\n###', key)
        for item in report[key][:40]:
            print(json.dumps(item, ensure_ascii=False))


if __name__ == '__main__':
    main()
