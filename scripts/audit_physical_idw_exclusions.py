#!/usr/bin/env python3
"""Recalculate IDW exclusions for every N02 physical station point.

Policy:
- Every distinct N02 physical station point remains visible on the map.
- The exact-point Yahoo route already stored in data/stations.json is the unrestricted
  route from that physical coordinate (walking / rail / local bus allowed).
- If that unrestricted route first boards the queried physical component, or boards no
  rail at all, the point stays in IDW.
- If it first boards a different component/station, query Yahoo again with station- and
  line-qualified origin labels until a route that actually boards the queried physical
  component is found.  That is the direct-component baseline.
- Exclude only when the unrestricted exact-point departure is at least 1 minute later
  than the best verified direct-component departure.

The direct route is never accepted from the label alone: its first rail service must be
verified with the same N02/service alignment logic used by the exact-point audit.
"""
from __future__ import annotations

import argparse
import json
import re
import time
import unicodedata
from pathlib import Path

from audit_address_origin_resolution import query_yahoo
from audit_physical_station_points_full import classify
from audit_yahoo_targeted import build_url

TARGET_MINUTES = 8 * 60 + 18

LINE_ALIASES = {
    '荒川線': ['都電荒川線', '都電'],
    '日暮里・舎人ライナー': ['舎人ライナー'],
    '13号線副都心線': ['副都心線'],
    '8号線有楽町線': ['有楽町線'],
    '4号線丸ノ内線': ['丸ノ内線'],
    '2号線日比谷線': ['日比谷線'],
    '3号線銀座線': ['銀座線'],
    '5号線東西線': ['東西線'],
    '7号線南北線': ['南北線'],
    '9号線千代田線': ['千代田線'],
    '11号線半蔵門線': ['半蔵門線'],
    '12号線大江戸線': ['大江戸線', '都営大江戸線'],
    '1号線浅草線': ['浅草線', '都営浅草線'],
    '6号線三田線': ['三田線', '都営三田線'],
    '10号線新宿線': ['新宿線', '都営新宿線'],
    '東北線': ['東北本線', '京浜東北線', '宇都宮線', '上野東京ライン'],
    '東海道線': ['東海道線', '横須賀線', '上野東京ライン'],
    '赤羽線': ['埼京線', '赤羽線'],
    '総武線': ['総武線', '総武快速線', '中央・総武線'],
    '中央線': ['中央線', '中央・総武線'],
    '常磐線': ['常磐線', '常磐線快速'],
    '本線': ['本線'],
}

OPERATOR_ALIASES = {
    '東日本旅客鉄道': ['JR', 'JR東日本'],
    '東海旅客鉄道': ['JR', 'JR東海'],
    '東京地下鉄': ['東京メトロ'],
    '東京都': ['都営線'],
    '東京都交通局': ['都営線'],
    '京浜急行電鉄': ['京急線'],
    '京成電鉄': ['京成線'],
    '東武鉄道': ['東武線'],
    '西武鉄道': ['西武線'],
    '小田急電鉄': ['小田急線'],
    '京王電鉄': ['京王線'],
    '相模鉄道': ['相鉄線'],
    '横浜市': ['横浜市営'],
    '横浜市交通局': ['横浜市営'],
    '首都圏新都市鉄道': ['つくばエクスプレス', 'TX'],
    '東京臨海高速鉄道': ['りんかい線'],
    'ゆりかもめ': ['ゆりかもめ'],
}


def minutes_on_timeline(value: str | None) -> int | None:
    if not value or not re.fullmatch(r'\d{1,2}:\d{2}', value):
        return None
    h, m = map(int, value.split(':'))
    raw = h * 60 + m
    return raw - 1440 if raw > TARGET_MINUTES else raw


def clean_display_label(value: str) -> str:
    # Remove deterministic code suffix used only to make map display names unique.
    return re.sub(r'\s*\[[0-9+]+\]\s*$', '', value).strip()


def normalize_line(value: object) -> str:
    text = unicodedata.normalize('NFKC', str(value or '')).strip()
    m = re.match(r'^\d+号線(.+)$', text)
    return m.group(1) if m else text


def label_variants(station: dict, point: dict) -> list[str]:
    name = str(point.get('logicalStation') or station.get('logicalStation') or station['station'])
    qualifiers: list[str] = []

    display = clean_display_label(str(station['station']))
    values = [display]

    for raw_line in point.get('lines') or []:
        line = str(raw_line).strip()
        for candidate in [line, normalize_line(line), *(LINE_ALIASES.get(line) or [])]:
            candidate = str(candidate).strip()
            if candidate and candidate not in qualifiers:
                qualifiers.append(candidate)

    for operator in point.get('operators') or []:
        operator = str(operator).strip()
        for candidate in OPERATOR_ALIASES.get(operator, []):
            if candidate and candidate not in qualifiers:
                qualifiers.append(candidate)

    # Common operator-aware aliases for otherwise ambiguous N02 line names.
    operators = ' '.join(map(str, point.get('operators') or []))
    lines = set(map(str, point.get('lines') or []))
    if '本線' in lines:
        if '京浜急行' in operators:
            qualifiers.insert(0, '京急線')
        elif '京成' in operators:
            qualifiers.insert(0, '京成線')
        elif '東武' in operators:
            qualifiers.insert(0, '東武線')
        elif '西武' in operators:
            qualifiers.insert(0, '西武線')

    for q in qualifiers:
        values.append(f'{name}({q})')
        values.append(f'{name}（{q}）')
    values.append(name)
    return list(dict.fromkeys(v for v in values if v))


def query_with_retry(url: str, lat: float, lng: float, retries: int = 2) -> dict:
    error = None
    for attempt in range(retries + 1):
        try:
            return query_yahoo(url, lat, lng)
        except Exception as exc:  # network failures are retried, never converted to a decision
            error = exc
            if attempt < retries:
                time.sleep(1.0 + attempt)
    assert error is not None
    raise error


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--stations', default='data/stations.json')
    ap.add_argument('--proposal', default='data/physical-station-points-proposal.json')
    ap.add_argument('--shard-index', type=int, required=True)
    ap.add_argument('--shard-count', type=int, required=True)
    ap.add_argument('--delay-seconds', type=float, default=0.55)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()

    station_doc = json.loads(Path(args.stations).read_text(encoding='utf-8'))
    proposal = json.loads(Path(args.proposal).read_text(encoding='utf-8'))
    stations = {str(s['id']): s for s in station_doc['stations']}
    points = proposal['physicalPoints']
    by_id = {str(p['id']): p for p in points}
    if len(stations) != 1563 or len(by_id) != 1563 or set(stations) != set(by_id):
        raise SystemExit(f'physical point mismatch stations={len(stations)} proposal={len(by_id)}')

    selected = [
        (i, stations[str(point['id'])], point)
        for i, point in enumerate(points)
        if i % args.shard_count == args.shard_index
    ]

    rows = []
    for ordinal, (global_index, station, point) in enumerate(selected, 1):
        free_minutes = int(station['minutes'])
        diagnostic = station.get('physicalPointAudit') or {}
        free_reason = str(diagnostic.get('reason') or '')
        row = {
            'globalIndex': global_index,
            'id': station['id'],
            'station': station['station'],
            'logicalStation': station.get('logicalStation'),
            'lat': station['lat'],
            'lng': station['lng'],
            'lines': point.get('lines', []),
            'operators': point.get('operators', []),
            'freeDeparture': station.get('departureDisplay'),
            'freeMinutes': free_minutes,
            'freeReason': free_reason,
            'freeFirstRail': diagnostic.get('firstRail'),
            'excludeFromIdw': False,
            'gainMinutes': 0,
            'directDeparture': None,
            'directMinutes': None,
            'directLabel': None,
            'attempts': [],
        }

        # No comparison is necessary if unrestricted routing already boards this
        # component (or never boards rail): it cannot be a "move elsewhere first" case.
        if free_reason in {'boards-current-physical-point', 'no-rail-boarded'}:
            row['decision'] = 'include'
            row['decisionReason'] = free_reason
            rows.append(row)
            print(f"[{ordinal}/{len(selected)}] {station['id']} include {free_reason}")
            continue

        direct = None
        for label in label_variants(station, point):
            attempt = {'label': label}
            try:
                result = query_with_retry(build_url(label), float(station['lat']), float(station['lng']))
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
                if dep_minutes is not None and route_class.get('reason') == 'boards-current-physical-point':
                    direct = {
                        'label': label,
                        'departure': dep,
                        'minutes': dep_minutes,
                        'firstRail': route_class.get('firstRail'),
                    }
                    row['attempts'].append(attempt)
                    break
            except Exception as exc:
                attempt['error'] = f'{type(exc).__name__}: {exc}'
            row['attempts'].append(attempt)
            time.sleep(args.delay_seconds)

        if direct is None:
            row['decision'] = 'unresolved'
            row['decisionReason'] = 'no-verified-direct-component-route'
            row['error'] = row['decisionReason']
        else:
            gain = free_minutes - int(direct['minutes'])
            row['directLabel'] = direct['label']
            row['directDeparture'] = direct['departure']
            row['directMinutes'] = direct['minutes']
            row['directFirstRail'] = direct['firstRail']
            row['gainMinutes'] = gain
            row['excludeFromIdw'] = gain >= 1
            row['decision'] = 'exclude' if gain >= 1 else 'include'
            row['decisionReason'] = 'alternate-route-later-departure' if gain >= 1 else 'alternate-route-not-later'

        rows.append(row)
        print(
            f"[{ordinal}/{len(selected)}] {station['id']} {station.get('logicalStation')} "
            f"free={free_minutes} direct={row.get('directMinutes')} gain={row.get('gainMinutes')} "
            f"decision={row.get('decision')} label={row.get('directLabel')}"
        )
        time.sleep(args.delay_seconds)

    summary = {
        'shardIndex': args.shard_index,
        'shardCount': args.shard_count,
        'physicalPoints': 1563,
        'rows': len(rows),
        'include': sum(r.get('decision') == 'include' for r in rows),
        'exclude': sum(r.get('decision') == 'exclude' for r in rows),
        'unresolved': sum(r.get('decision') == 'unresolved' for r in rows),
        'directComparisons': sum(r.get('directMinutes') is not None for r in rows),
    }
    Path(args.output).write_text(json.dumps({'summary': summary, 'rows': rows}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
