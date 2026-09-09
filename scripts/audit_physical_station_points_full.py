#!/usr/bin/env python3
"""Recalculate every distinct physical N02 station point with Yahoo exact-point routing.

Every successful exact-point query is a valid spatial accessibility sample and is used
by the final IDW dataset. This audit additionally records a route-selection diagnostic:
``keep`` means the best journey boards rail at the queried physical component (or does
not board rail), while ``exclude`` means it first walks/buses to a different component
or station before boarding. Those historical field names describe only the boarding
origin diagnostic; they are NOT final IDW inclusion/exclusion flags.

The Yahoo request uses the same project conditions: 2026-08-28, arrival by 08:18,
quick walking, local buses enabled and highway buses disabled. The first walking
link must preserve the requested flatlon to within 5 m.
"""
from __future__ import annotations

import argparse
import json
import re
import time
import unicodedata
from pathlib import Path

from audit_address_origin_resolution import build_flatlon_url, hhmm_to_minutes, query_yahoo
from audit_route_origin_alignment import member_matches_segment

PROPOSAL = Path('data/physical-station-points-proposal.json')

UI_SKIP = {
    '地図', '出口', '時刻表', '地図でルートを表示', 'ルート保存', '定期券',
    'ルート共有', '印刷する', 'メールで送信', 'カレンダーに登録',
    '発', '着', '早', '楽', '安', '徒歩',
}


def compact(value: object) -> str:
    return re.sub(r'[\s・･]', '', unicodedata.normalize('NFKC', str(value or '')).casefold())


def station_base(value: object) -> str:
    text = unicodedata.normalize('NFKC', str(value or '')).strip()
    # N02 and Yahoo use both small ヶ and full-size ケ for the same station names.
    # Treat those orthographic variants as identical before comparing boarding points.
    text = text.replace('ヶ', 'ケ').replace('ヵ', 'カ')
    text = text.split('/', 1)[0].strip()
    while re.search(r'[\(（][^\(（））]*[\)）]$', text):
        text = re.sub(r'[\(（][^\(（））]*[\)）]$', '', text).strip()
    text = re.sub(r'駅$', '', text).strip()
    return compact(text)


def is_timeish(line: str) -> bool:
    s = line.strip()
    return bool(
        re.fullmatch(r'\(?\d{1,2}:\d{2}(?:着|発)?\)?', s)
        or re.match(r'^\d{1,2}:\d{2}発?→\d{1,2}:\d{2}着?', s)
        or re.fullmatch(r'\d+分', s)
        or re.fullmatch(r'\d+(?:\.\d+)?km', s)
        or re.fullmatch(r'[\d,]+円', s)
    )


def is_bus_service(line: str) -> bool:
    s = compact(line)
    if not s:
        return False
    if 'バス・' in line or ('バス' in s and ('行' in s or '方面' in s)):
        return True
    # Yahoo labels Bーぐる without the word バス, but it is a community-bus service.
    if any(token in s for token in ('bーぐる', 'b-ぐる', 'bぐる')):
        return '行' in s or '方面' in s
    return False


def is_rail_service(line: str) -> bool:
    s = compact(line)
    if not s or is_bus_service(line):
        return False
    motion = any(token in s for token in ('行', '方面', '当駅始発'))
    # Yahoo service branding often omits the literal suffix 「線」. Examples from the
    # saved 1563-point audit include 埼玉高速鉄道, 東葉高速鉄道, 湘南新宿ライン,
    # スカイツリーライン, アーバンパークライン and JR特急 names.
    rail = any(token in s for token in (
        '線', 'ライン', '鉄道', '特急', '新幹線', 'ライナー', 'モノレール', 'ゆりかもめ',
        'エクスプレス', 'ニューシャトル', 'シーサイドライン',
        '江ノ島電鉄', '江ノ電', '小湊鐵道', '小湊鉄道', 'つくばエクスプレス',
    ))
    return motion and rail


def detailed_route1(excerpt: list[str]) -> list[str]:
    indices = [i for i, line in enumerate(excerpt) if line.strip() == 'ルート1']
    if not indices:
        return []
    start = (indices[1] if len(indices) >= 2 else indices[0]) + 1
    end = len(excerpt)
    for i in range(start, len(excerpt)):
        if excerpt[i].strip() == 'ルート2':
            end = i
            break
    return excerpt[start:end]


def find_board_station(section: list[str], service_index: int) -> str | None:
    for j in range(service_index - 1, -1, -1):
        line = section[j].strip()
        if not line or line in UI_SKIP or is_timeish(line):
            continue
        if line.startswith('出口：') or line.startswith('乗車位置：'):
            continue
        if line.startswith('[発]') or line.startswith('[着]'):
            continue
        if line.startswith('徒歩') or '地図でルート' in line:
            continue
        if line.startswith('IC優先') or line.startswith('乗換：') or line.startswith('定期の種類'):
            continue
        if re.fullmatch(r'\d+駅', line) or re.fullmatch(r'\d+か月.*', line):
            continue
        return line
    return None


def first_rail_event(section: list[str]) -> dict | None:
    for i, line in enumerate(section):
        if is_rail_service(line):
            return {
                'service': line.strip(),
                'boardStation': find_board_station(section, i),
                'serviceIndex': i,
            }
    return None


def has_bus_service(section: list[str]) -> bool:
    return any(is_bus_service(line) for line in section)


def line_matches_point(point: dict, service: str) -> tuple[bool, str | None]:
    station_id = str(point.get('logicalStationId') or '')
    for line in point.get('lines', []):
        operators = point.get('operators') or ['']
        for operator in operators:
            ok, reason = member_matches_segment(
                station_id,
                service,
                {'line': line, 'operator': operator},
            )
            if ok:
                return True, reason
    return False, None


def classify_route1(point: dict, section: list[str]) -> dict:
    """Classify a saved detailed route-1 section with the current parser."""
    if not section:
        return {'classification': 'unparsed', 'reason': 'route1-section-missing', 'route1': []}
    rail = first_rail_event(section)
    if rail is None:
        return {
            'classification': 'keep',
            'reason': 'no-rail-boarded',
            'firstRail': None,
            'hasBus': has_bus_service(section),
            'route1': section,
        }

    board = rail.get('boardStation')
    same_station = bool(board) and station_base(board) == station_base(point.get('logicalStation'))
    line_match, line_reason = line_matches_point(point, rail['service'])
    if same_station and line_match:
        classification = 'keep'
        reason = 'boards-current-physical-point'
    else:
        classification = 'exclude'
        if not same_station:
            reason = 'boards-different-station'
        else:
            reason = 'boards-different-component'
    return {
        'classification': classification,
        'reason': reason,
        'firstRail': {
            **rail,
            'sameLogicalStationBase': same_station,
            'lineMatchesPhysicalPoint': line_match,
            'lineMatchReason': line_reason,
        },
        'hasBus': has_bus_service(section),
        'route1': section,
    }


def classify(point: dict, excerpt: list[str]) -> dict:
    """Classify where route 1 first boards rail; not whether the point enters IDW."""
    return classify_route1(point, detailed_route1(excerpt))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--proposal', default=str(PROPOSAL))
    parser.add_argument('--shard-index', type=int, required=True)
    parser.add_argument('--shard-count', type=int, required=True)
    parser.add_argument('--delay-seconds', type=float, default=0.60)
    parser.add_argument('--origin-tolerance-m', type=float, default=5.0)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()

    proposal = json.loads(Path(args.proposal).read_text(encoding='utf-8'))
    all_points = proposal['physicalPoints']
    selected = [
        (i, point) for i, point in enumerate(all_points)
        if i % args.shard_count == args.shard_index
    ]
    rows = []
    for ordinal, (global_index, point) in enumerate(selected, 1):
        label = f"{point['logicalStation']}物理駅地点-{point['id']}"
        row = {
            'globalIndex': global_index,
            **point,
        }
        try:
            result = query_yahoo(
                build_flatlon_url(label, float(point['lat']), float(point['lng'])),
                float(point['lat']), float(point['lng']),
            )
            origin = result.get('firstWalkOrigin')
            origin_dist = None if not origin else origin.get('distanceFromStationM')
            departure = result.get('summaryDeparture')
            departure_minutes = hhmm_to_minutes(departure)
            route_class = classify(point, result.get('excerpt') or [])
            row.update({
                'departure': departure,
                'arrival': result.get('summaryArrival'),
                'minutes': departure_minutes,
                'originDistanceM': origin_dist,
                'originVerified': origin_dist is not None and float(origin_dist) <= args.origin_tolerance_m,
                **route_class,
            })
            if departure_minutes is None:
                row['error'] = 'missing-departure'
            elif not row['originVerified']:
                row['error'] = f'origin-not-verified:{origin_dist}'
        except Exception as exc:
            row['classification'] = 'unparsed'
            row['error'] = f'{type(exc).__name__}: {exc}'
        rows.append(row)
        first_rail = row.get('firstRail') or {}
        print(
            f"[{ordinal}/{len(selected)}] {point['id']} {point['logicalStation']} "
            f"{row.get('departure')} {row.get('classification')} {row.get('reason')} "
            f"board={first_rail.get('boardStation')} service={first_rail.get('service')} "
            f"origin={row.get('originDistanceM')} error={row.get('error')}"
        )
        if ordinal < len(selected):
            time.sleep(args.delay_seconds)

    summary = {
        'shardIndex': args.shard_index,
        'shardCount': args.shard_count,
        'projectPhysicalPoints': len(all_points),
        'rows': len(rows),
        'queriedSuccessfully': sum(1 for r in rows if not r.get('error')),
        'originVerified': sum(1 for r in rows if r.get('originVerified')),
        # Diagnostic field names retained for compatibility with the completed audit.
        'keep': sum(1 for r in rows if r.get('classification') == 'keep' and not r.get('error')),
        'exclude': sum(1 for r in rows if r.get('classification') == 'exclude' and not r.get('error')),
        'unparsed': sum(1 for r in rows if r.get('classification') == 'unparsed' or r.get('error')),
    }
    Path(args.output).write_text(
        json.dumps({'meta': summary, 'rows': rows}, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8'
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
