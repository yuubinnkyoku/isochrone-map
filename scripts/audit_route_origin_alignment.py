#!/usr/bin/env python3
"""Audit whether a stored station point is aligned with the route actually boarded.

The station ``line`` field is display metadata and can be stale. This audit instead
looks at the first route segment in ``route``, matches that segment against the
route-specific station records in N02-25, and measures the distance from the stored
station point to the N02 position for the route actually used.

Routes that explicitly begin with walking/bus access are reported separately and are
not treated as direct-board candidates: the access leg is already part of the route.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

from audit_n02_coverage import load_overrides, match_with_overrides
from audit_n02_station_groups import build_n02, fetch_n02, haversine_m

SOURCE_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/N02/N02-25/N02-25_GML.zip"

# Service names that do not contain an N02 line name. Keep station-specific cases
# deliberately narrow where a service maps to a physically remote part of one station.
SERVICE_LINE_OVERRIDES = {
    "musashikosugi": {
        "成田エクスプレス": ["東海道線"],
    },
    # N02 stores the shared/through-running infrastructure under a different
    # line name at these stations. Keep these station-specific so a broad
    # alias does not accidentally match a different physical component at a
    # large interchange.
    "motosumiyoshi": {"東急目黒線": ["東横線"]},
    "nishinippori": {"JR山手線": ["東北線"]},
    "nippori": {"JR山手線": ["東北線"]},
    "hamamatsucho": {"JR山手線": ["東海道線"]},
    "uguisudani": {"JR山手線": ["東北線"]},
    "tamachi": {"JR山手線": ["東海道線"]},
    "takanawa-gateway": {"JR山手線": ["東海道線"]},
    "tokyo23-003575": {"東京メトロ東西線": ["中央線"]},
    "tokyo23-003579": {"東京メトロ東西線": ["中央線"]},
    "tokyo23-003574": {"東京メトロ東西線": ["中央線"]},
    "tokyo23-003573": {"東京メトロ東西線": ["中央線"]},
    "n02-003584": {"東京メトロ東西線": ["中央線"]},
    "n02-003587": {"東京メトロ東西線": ["中央線"]},
    "n02-004675": {"JR横浜線": ["根岸線"]},
    "n02-003090": {"京成臨時ライナー": ["北総線", "成田空港線"]},
    "n02-003125": {"京成臨時ライナー": ["北総線", "成田空港線"]},
}

# Passenger-facing route branding/name -> N02 infrastructure line name(s). Several
# JR services run over infrastructure whose N02 name differs from the timetable name.
# Candidate lines are always restricted to the N02 members of the current station.
ROUTE_LINE_ALIASES = {
    "りんかい線": ["臨海副都心線"],
    "つくばエクスプレス": ["常磐新線"],
    "ゆりかもめ": ["東京臨海新交通臨海線"],
    "日暮里・舎人ライナー": ["日暮里・舎人ライナー"],
    "東京モノレール": ["東京モノレール羽田空港線"],
    "東武アーバンパークライン": ["野田線"],
    "東武スカイツリーライン": ["伊勢崎線"],
    "東武東上線": ["東上本線"],
    "横浜市営地下鉄ブルーライン": ["1号線", "3号線"],
    "横浜市営地下鉄グリーンライン": ["4号線"],
    "多摩モノレール": ["多摩都市モノレール線"],
    "千葉モノレール": ["1号線", "2号線"],
    "埼玉新都市交通ニューシャトル": ["伊奈線"],
    "横浜シーサイドライン": ["金沢シーサイドライン"],
    "江ノ島電鉄": ["江ノ島電鉄線"],
    "東葉高速鉄道": ["東葉高速線"],
    "埼玉高速鉄道": ["埼玉高速鉄道線"],
    "湘南モノレール": ["江の島線"],
    "みなとみらい線": ["みなとみらい21線"],
    "小湊鐵道": ["小湊鉄道線", "小湊鐵道線"],
    "京王新線": ["京王線"],
    "JR総武本線": ["総武線"],
    "JR総武線": ["総武線", "中央線"],
    "JR京浜東北・根岸線": ["東海道線", "東北線", "根岸線"],
    "JR埼京線": ["山手線", "赤羽線", "東北線", "川越線"],
    "JR湘南新宿ライン": ["山手線", "赤羽線", "東北線", "東海道線", "高崎線", "横須賀線"],
    "JR東海道本線": ["東海道線", "東北線", "高崎線"],
    "JR横須賀線": ["横須賀線", "東海道線"],
    "JR中央・青梅線快速": ["中央線", "青梅線"],
    "JR高崎線": ["高崎線", "東北線"],
    "JR武蔵野線": ["武蔵野線", "京葉線"],
    "東京メトロ丸ノ内線": ["4号線丸ノ内線", "4号線丸ノ内線分岐線"],
    "関東鉄道竜ケ崎線": ["竜ヶ崎線"],
    "JR特急しおさい": ["総武線"],
    "JR新幹線こだま": ["東海道新幹線"],
}

OPERATOR_TOKENS = {
    "東日本旅客鉄道": ["jr"],
    "東海旅客鉄道": ["jr"],
    "東京地下鉄": ["東京メトロ"],
    "東京都": ["都営", "東京都"],
    "東急電鉄": ["東急"],
    "西武鉄道": ["西武"],
    "東武鉄道": ["東武"],
    "京王電鉄": ["京王"],
    "京成電鉄": ["京成"],
    "京浜急行電鉄": ["京急"],
    "小田急電鉄": ["小田急"],
    "相模鉄道": ["相鉄"],
    "東京臨海高速鉄道": ["りんかい", "東京臨海高速鉄道"],
    "首都圏新都市鉄道": ["つくばエクスプレス", "tx"],
    "横浜市": ["横浜市営地下鉄", "ブルーライン", "グリーンライン"],
    "東葉高速鉄道": ["東葉高速"],
    "埼玉高速鉄道": ["埼玉高速"],
    "多摩都市モノレール": ["多摩モノレール"],
    "千葉都市モノレール": ["千葉モノレール"],
    "埼玉新都市交通": ["ニューシャトル", "埼玉新都市交通"],
    "横浜シーサイドライン": ["シーサイドライン"],
    "江ノ島電鉄": ["江ノ島電鉄", "江ノ電"],
    "湘南モノレール": ["湘南モノレール"],
    "小湊鐵道": ["小湊鐵道", "小湊鉄道"],
}

ACCESS_PREFIXES = (
    "徒歩",
    "都営バス",
    "西武バス",
    "東武バス",
    "京王バス",
    "国際興業バス",
    "関東バス",
    "小田急バス",
    "立川バス",
    "横浜市営バス",
    "川崎市営バス",
)


def compact(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s・･()（）/／]", "", text)


def line_core(line: str) -> str:
    value = compact(line)
    value = re.sub(r"^\d+号線", "", value)
    return value


def is_access_segment(segment: str) -> bool:
    value = compact(segment)
    # Includes forms such as "同駅内徒歩" as well as plain "徒歩".
    if compact("徒歩") in value:
        return True
    if "バス" in value:
        return True
    return any(value.startswith(compact(prefix)) for prefix in ACCESS_PREFIXES)


def member_matches_segment(station_id: str, segment: str, member: dict) -> tuple[bool, str | None]:
    seg = compact(segment)
    member_line = line_core(member.get("line", ""))

    for service, target_lines in SERVICE_LINE_OVERRIDES.get(station_id, {}).items():
        if compact(service) in seg and member.get("line") in target_lines:
            return True, f"service-override:{service}"

    for route_name, target_lines in ROUTE_LINE_ALIASES.items():
        if compact(route_name) in seg and member.get("line") in target_lines:
            return True, f"route-alias:{route_name}"

    # Strong ordinary match: route segment literally contains the meaningful line name.
    if member_line and len(member_line) >= 3 and member_line not in {"本線", "1号線", "2号線", "3号線", "4号線"}:
        if member_line in seg:
            return True, "line-name"

    # Generic line names need operator evidence as well.
    if member_line in {"本線", "1号線", "2号線", "3号線", "4号線"} and member_line in seg:
        tokens = OPERATOR_TOKENS.get(member.get("operator", ""), [])
        if any(compact(token) in seg for token in tokens):
            return True, "generic-line+operator"

    return False, None


def operator_fallback(segment: str, members: list[dict]) -> list[dict]:
    """Use operator identity only when it leaves one N02 infrastructure line."""
    seg = compact(segment)
    candidates = []
    for member in members:
        tokens = OPERATOR_TOKENS.get(member.get("operator", ""), [])
        if tokens and any(compact(token) in seg for token in tokens):
            candidates.append(member)
    distinct_lines = {member.get("line") for member in candidates}
    if candidates and len(distinct_lines) == 1:
        return candidates
    return []


def nearest_member(point_lat: float, point_lng: float, members: list[dict]) -> tuple[float, dict]:
    member = min(
        members,
        key=lambda row: (
            haversine_m(point_lat, point_lng, row["lat"], row["lng"]),
            row["groupCode"],
            row["stationCode"],
        ),
    )
    return haversine_m(point_lat, point_lng, member["lat"], member["lng"]), member


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stations", default="data/stations.json")
    parser.add_argument("--overrides", default="data/n02-station-group-overrides.json")
    parser.add_argument("--source-url", default=SOURCE_URL)
    parser.add_argument("--distance-threshold-m", type=float, default=100.0)
    parser.add_argument("--gap-threshold-m", type=float, default=40.0)
    parser.add_argument("--json-output", default="data/route-origin-alignment-audit.json")
    parser.add_argument("--markdown-output", default="data/route-origin-alignment-audit.md")
    args = parser.parse_args()

    doc = json.loads(Path(args.stations).read_text(encoding="utf-8"))
    overrides = load_overrides(Path(args.overrides))
    groups, summaries, groups_by_name = build_n02(fetch_n02(args.source_url))

    rows = []
    status_counts = Counter()
    for station in doc["stations"]:
        route = str(station.get("route") or "").strip()
        segments = [segment.strip() for segment in route.split("→") if segment.strip()]
        first = segments[0] if segments else ""
        codes, match_method, _, _ = match_with_overrides(
            station, summaries, groups_by_name, overrides
        )
        members = [member for code in codes for member in groups[code]] if codes else []

        row = {
            "id": station.get("id"),
            "station": station.get("station"),
            "lat": station.get("lat"),
            "lng": station.get("lng"),
            "minutes": station.get("minutes"),
            "line": station.get("line"),
            "route": route,
            "firstSegment": first,
            "groupCodes": codes,
            "groupMatchMethod": match_method,
            "excludeFromIdw": bool(station.get("excludeFromIdw")),
        }

        if not route or not members:
            row["status"] = "no-route-or-group"
            rows.append(row)
            status_counts[row["status"]] += 1
            continue

        if is_access_segment(first):
            row["status"] = "explicit-access"
            row["accessSegment"] = first
            rows.append(row)
            status_counts[row["status"]] += 1
            continue

        matched = []
        reasons = {}
        for member in members:
            ok, reason = member_matches_segment(str(station.get("id", "")), first, member)
            if ok:
                matched.append(member)
                reasons[f"{member['groupCode']}:{member['stationCode']}"] = reason

        if not matched:
            matched = operator_fallback(first, members)
            for member in matched:
                reasons[f"{member['groupCode']}:{member['stationCode']}"] = "unique-operator-line"

        if not matched:
            row["status"] = "unparsed-direct-segment"
            rows.append(row)
            status_counts[row["status"]] += 1
            continue

        lat = float(station["lat"])
        lng = float(station["lng"])
        route_distance, route_member = nearest_member(lat, lng, matched)
        any_distance, any_member = nearest_member(lat, lng, members)
        gap = route_distance - any_distance
        row.update({
            "status": "direct-rail",
            "routeMemberDistanceM": round(route_distance, 2),
            "routeMember": {
                "groupCode": route_member["groupCode"],
                "stationCode": route_member["stationCode"],
                "line": route_member["line"],
                "operator": route_member["operator"],
                "lat": route_member["lat"],
                "lng": route_member["lng"],
                "matchReason": reasons.get(f"{route_member['groupCode']}:{route_member['stationCode']}"),
            },
            "nearestAnyDistanceM": round(any_distance, 2),
            "nearestAnyMember": {
                "groupCode": any_member["groupCode"],
                "stationCode": any_member["stationCode"],
                "line": any_member["line"],
                "operator": any_member["operator"],
            },
            "routeVsNearestGapM": round(gap, 2),
        })
        row["flag"] = route_distance >= args.distance_threshold_m and gap >= args.gap_threshold_m
        rows.append(row)
        status_counts[row["status"]] += 1

    flags = sorted(
        [row for row in rows if row.get("flag")],
        key=lambda row: (-row["routeVsNearestGapM"], -row["routeMemberDistanceM"], row["station"]),
    )
    direct = [row for row in rows if row.get("status") == "direct-rail"]
    unparsed = [row for row in rows if row.get("status") == "unparsed-direct-segment"]
    explicit = [row for row in rows if row.get("status") == "explicit-access"]

    report = {
        "meta": {
            "projectStations": len(doc["stations"]),
            "distanceThresholdM": args.distance_threshold_m,
            "gapThresholdM": args.gap_threshold_m,
            "statusCounts": dict(sorted(status_counts.items())),
            "directRailParsed": len(direct),
            "explicitAccess": len(explicit),
            "unparsedDirectSegment": len(unparsed),
            "flagged": len(flags),
        },
        "flagged": flags,
        "unparsedDirectSegments": sorted(unparsed, key=lambda row: (row["firstSegment"], row["station"])),
        "explicitAccess": explicit,
        "rows": rows,
    }

    Path(args.json_output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    md = [
        "# 経路起点と駅座標の整合監査",
        "",
        "`line` 欄ではなく `route` の最初の乗車区間をN02-25の路線別駅位置へ対応させ、",
        "現在の駅座標が実際に使う乗車位置から不自然に離れていないかを調べる。",
        "",
        f"- 収録駅: **{len(doc['stations'])}**",
        f"- 直接乗車として路線を解決できた駅: **{len(direct)}**",
        f"- 徒歩・バス等の明示アクセスから始まる駅: **{len(explicit)}**",
        f"- 直接乗車だが最初の路線を自動解決できなかった駅: **{len(unparsed)}**",
        f"- 距離 {args.distance_threshold_m:g}m以上かつ最近傍路線との差 {args.gap_threshold_m:g}m以上の要確認: **{len(flags)}**",
        "",
        "## 要確認",
        "",
    ]
    if flags:
        md.append("| 駅 | 最初の経路 | 対応N02路線 | 乗車路線まで(m) | 最近傍路線 | 最近傍(m) | 差(m) |")
        md.append("|---|---|---|---:|---|---:|---:|")
        for row in flags:
            md.append(
                f"| {row['station']} | {row['firstSegment'].replace('|', '/')} | "
                f"{row['routeMember']['line']} | {row['routeMemberDistanceM']} | "
                f"{row['nearestAnyMember']['line']} | {row['nearestAnyDistanceM']} | "
                f"{row['routeVsNearestGapM']} |"
            )
    else:
        md.append("なし")

    md.extend(["", "## 自動解決できなかった直接乗車区間（上位100件）", ""])
    if unparsed:
        counts = Counter(row["firstSegment"] for row in unparsed)
        for segment, count in counts.most_common(100):
            md.append(f"- {segment} — {count}駅")
    else:
        md.append("なし")

    Path(args.markdown_output).write_text("\n".join(md) + "\n", encoding="utf-8")

    print(json.dumps(report["meta"], ensure_ascii=False, indent=2))
    for row in flags:
        print(
            "FLAG",
            row["station"],
            f"first={row['firstSegment']}",
            f"routeLine={row['routeMember']['line']}",
            f"routeDistance={row['routeMemberDistanceM']}m",
            f"nearest={row['nearestAnyMember']['line']}:{row['nearestAnyDistanceM']}m",
            f"gap={row['routeVsNearestGapM']}m",
        )
    for segment, count in Counter(row["firstSegment"] for row in unparsed).most_common(30):
        print("UNPARSED", count, segment)


if __name__ == "__main__":
    main()
