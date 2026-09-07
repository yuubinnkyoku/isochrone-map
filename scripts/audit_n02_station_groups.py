#!/usr/bin/env python3
"""Audit project station grouping against MLIT N02-25.

The script is read-only with respect to ``data/stations.json``.  It downloads the
N02-25 railway data, keeps every route-specific station record, and reports cases
that can be hidden by representing a transfer station with one name/coordinate.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import re
import unicodedata
import urllib.request
import zipfile
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

SOURCE_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/N02/N02-25/N02-25_GML.zip"
USER_AGENT = "isochrone-map-n02-audit/2"


def norm_name(value: object) -> str:
    name = unicodedata.normalize("NFKC", str(value)).strip()
    # Project-only suffixes used to distinguish stations that share a display name.
    name = re.sub(
        r"[（(](?:流鉄|東京メトロ|東西線|都電|都電荒川線|TX|つくばエクスプレス)[）)]$",
        "",
        name,
        flags=re.IGNORECASE,
    )
    return name.replace("ヶ", "ケ").replace("ヵ", "カ").casefold()


def norm_line(value: object) -> str:
    line = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    replacements = {
        "東京さくらトラム": "荒川線",
        "都電荒川線": "荒川線",
    }
    for old, new in replacements.items():
        line = line.replace(old, new)
    for prefix in (
        "東京メトロ",
        "都営地下鉄",
        "都営",
        "jr東日本",
        "jr東海",
        "jr",
        "東急",
        "西武",
        "東武",
        "京王",
        "京成",
        "小田急",
        "相鉄",
    ):
        if line.startswith(prefix):
            line = line[len(prefix) :]
            break
    return re.sub(r"[・･\s]", "", line)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_008.8
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def iter_points(coords):
    if (
        isinstance(coords, list)
        and len(coords) >= 2
        and isinstance(coords[0], (int, float))
        and isinstance(coords[1], (int, float))
    ):
        yield float(coords[1]), float(coords[0])
    elif isinstance(coords, list):
        for child in coords:
            yield from iter_points(child)


def representative_point(geometry: dict) -> tuple[float, float]:
    points = list(iter_points(geometry.get("coordinates", [])))
    if not points:
        raise ValueError("N02 station geometry contains no point")
    return (
        sum(lat for lat, _ in points) / len(points),
        sum(lng for _, lng in points) / len(points),
    )


def fetch_n02(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = response.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        candidates = [
            name
            for name in archive.namelist()
            if name.casefold().endswith("station.geojson")
        ]
        if not candidates:
            raise RuntimeError("N02-25 archive contains no Station GeoJSON")
        candidates.sort(key=lambda name: ("utf-8" not in name.casefold(), len(name), name))
        return json.loads(archive.read(candidates[0]))


def build_n02(geojson: dict):
    groups: dict[str, list[dict]] = defaultdict(list)
    groups_by_name: dict[str, set[str]] = defaultdict(set)

    for feature in geojson.get("features", []):
        props = feature.get("properties") or {}
        station_code = str(props.get("N02_005c", "")).strip()
        group_code = str(props.get("N02_005g", "")).strip()
        name = str(props.get("N02_005", "")).strip()
        if not station_code or not group_code or not name:
            continue
        lat, lng = representative_point(feature.get("geometry") or {})
        member = {
            "stationCode": station_code,
            "groupCode": group_code,
            "name": name,
            "line": str(props.get("N02_003", "")).strip(),
            "operator": str(props.get("N02_004", "")).strip(),
            "lat": lat,
            "lng": lng,
        }
        groups[group_code].append(member)
        groups_by_name[norm_name(name)].add(group_code)

    summaries: dict[str, dict] = {}
    for code, members in groups.items():
        names = Counter(member["name"] for member in members)
        lat = sum(member["lat"] for member in members) / len(members)
        lng = sum(member["lng"] for member in members) / len(members)
        max_distance = 0.0
        max_pair = None
        for a, b in combinations(members, 2):
            distance = haversine_m(a["lat"], a["lng"], b["lat"], b["lng"])
            if distance > max_distance:
                max_distance = distance
                max_pair = (a, b)
        summaries[code] = {
            "groupCode": code,
            "name": names.most_common(1)[0][0],
            "lat": lat,
            "lng": lng,
            "lines": sorted({member["line"] for member in members if member["line"]}),
            "operators": sorted({member["operator"] for member in members if member["operator"]}),
            "stationCodes": sorted({member["stationCode"] for member in members}),
            "memberCount": len(members),
            "maxMemberDistanceM": round(max_distance, 2),
            "maxPair": None if max_pair is None else {
                "a": {key: max_pair[0][key] for key in ("stationCode", "line", "lat", "lng")},
                "b": {key: max_pair[1][key] for key in ("stationCode", "line", "lat", "lng")},
            },
        }
    return groups, summaries, groups_by_name


def encoded_group_codes(station_id: str) -> list[str] | None:
    for prefix in ("tokyo23-", "n02-"):
        if station_id.startswith(prefix):
            codes = station_id[len(prefix) :].split("+")
            return codes if codes and all(code.isdigit() for code in codes) else None
    return None


def match_station(station: dict, summaries: dict, groups_by_name: dict):
    exact = encoded_group_codes(str(station.get("id", "")))
    if exact and all(code in summaries for code in exact):
        return exact, "group-code", 0.0

    candidates = sorted(groups_by_name.get(norm_name(station.get("station", "")), []))
    if not candidates:
        return [], "no-name-match", None
    lat = float(station["lat"])
    lng = float(station["lng"])
    distance, code = min(
        ((haversine_m(lat, lng, summaries[c]["lat"], summaries[c]["lng"]), c) for c in candidates),
        key=lambda item: (item[0], item[1]),
    )
    if distance > 1500:
        return [], "name-too-far", distance
    return [code], "name+coordinate", distance


def nearest_member(lat: float, lng: float, members: list[dict]) -> tuple[float, dict]:
    member = min(
        members,
        key=lambda item: (
            haversine_m(lat, lng, item["lat"], item["lng"]),
            item["stationCode"],
            item["line"],
        ),
    )
    return haversine_m(lat, lng, member["lat"], member["lng"]), member


def max_group_distance(codes: list[str], summaries: dict) -> tuple[float, tuple[str, str] | None]:
    best = (0.0, None)
    for a, b in combinations(codes, 2):
        ga, gb = summaries[a], summaries[b]
        distance = haversine_m(ga["lat"], ga["lng"], gb["lat"], gb["lng"])
        if distance > best[0]:
            best = (distance, (a, b))
    return best


def audit(doc: dict, groups: dict, summaries: dict, groups_by_name: dict, args) -> dict:
    matches = []
    used_groups: set[str] = set()
    for station in doc["stations"]:
        codes, method, distance = match_station(station, summaries, groups_by_name)
        matches.append((station, codes, method, distance))
        used_groups.update(codes)

    spread_groups = [
        summaries[code]
        for code in used_groups
        if len(summaries[code]["lines"]) >= 2
        and summaries[code]["maxMemberDistanceM"] >= args.spread_threshold_m
    ]
    spread_groups.sort(key=lambda row: (-row["maxMemberDistanceM"], row["name"], row["groupCode"]))

    multi_group = []
    coord_mismatch = []
    line_conflicts = []
    collapsed_lines = []
    unmatched = []
    project_matches = []

    for station, codes, method, fallback_distance in matches:
        project_matches.append({
            "id": station.get("id"),
            "station": station.get("station"),
            "groupCodes": codes,
            "method": method,
            "fallbackDistanceM": None if fallback_distance is None else round(fallback_distance, 2),
        })
        if not codes:
            unmatched.append(project_matches[-1])
            continue

        if len(codes) > 1:
            distance, pair = max_group_distance(codes, summaries)
            multi_group.append({
                "id": station.get("id"),
                "station": station.get("station"),
                "groupCodes": codes,
                "groupNames": [summaries[code]["name"] for code in codes],
                "groupLines": {code: summaries[code]["lines"] for code in codes},
                "maxGroupCenterDistanceM": round(distance, 2),
                "maxPair": pair,
            })

        members = [member for code in codes for member in groups[code]]
        lat = float(station["lat"])
        lng = float(station["lng"])
        distance, nearest = nearest_member(lat, lng, members)
        if distance > args.coordinate_threshold_m:
            coord_mismatch.append({
                "id": station.get("id"),
                "station": station.get("station"),
                "groupCodes": codes,
                "nearestMemberDistanceM": round(distance, 2),
                "nearestMember": {key: nearest[key] for key in ("stationCode", "line", "lat", "lng")},
            })

        lines = sorted({member["line"] for member in members if member["line"]})
        if len(lines) >= 2:
            collapsed_lines.append({
                "id": station.get("id"),
                "station": station.get("station"),
                "line": station.get("line"),
                "n02Lines": lines,
                "groupCodes": codes,
            })

        current_line = norm_line(station.get("line"))
        if current_line and len(lines) >= 2:
            current_members = [member for member in members if norm_line(member["line"]) == current_line]
            other_members = [member for member in members if norm_line(member["line"]) != current_line]
            if current_members and other_members:
                d_current, m_current = nearest_member(lat, lng, current_members)
                d_other, m_other = nearest_member(lat, lng, other_members)
                if d_current - d_other >= args.line_conflict_margin_m:
                    line_conflicts.append({
                        "id": station.get("id"),
                        "station": station.get("station"),
                        "currentLine": station.get("line"),
                        "groupCodes": codes,
                        "distanceToCurrentLineM": round(d_current, 2),
                        "currentLineMember": {key: m_current[key] for key in ("stationCode", "line", "lat", "lng")},
                        "closerOtherLine": m_other["line"],
                        "distanceToOtherLineM": round(d_other, 2),
                        "differenceM": round(d_current - d_other, 2),
                    })

    same_name_pairs = []
    for normalized_name, code_set in groups_by_name.items():
        codes = sorted(code_set)
        if len(codes) < 2 or not used_groups.intersection(codes):
            continue
        for a, b in combinations(codes, 2):
            ga, gb = summaries[a], summaries[b]
            distance = haversine_m(ga["lat"], ga["lng"], gb["lat"], gb["lng"])
            if distance <= args.nearby_same_name_m:
                same_name_pairs.append({
                    "name": ga["name"],
                    "normalizedName": normalized_name,
                    "groupA": a,
                    "groupB": b,
                    "distanceM": round(distance, 2),
                    "linesA": ga["lines"],
                    "linesB": gb["lines"],
                    "projectUsesA": a in used_groups,
                    "projectUsesB": b in used_groups,
                })

    line_conflicts.sort(key=lambda row: (-row["differenceM"], row["station"], row["id"]))
    multi_group.sort(key=lambda row: (-row["maxGroupCenterDistanceM"], row["station"], row["id"]))
    same_name_pairs.sort(key=lambda row: (row["distanceM"], row["name"], row["groupA"], row["groupB"]))
    coord_mismatch.sort(key=lambda row: (-row["nearestMemberDistanceM"], row["station"], row["id"]))
    collapsed_lines.sort(key=lambda row: (row["station"], row["id"]))

    spread_counts = {
        str(threshold): sum(
            1
            for code in used_groups
            if len(summaries[code]["lines"]) >= 2
            and summaries[code]["maxMemberDistanceM"] >= threshold
        )
        for threshold in (50, 100, 150, 200, 300)
    }

    return {
        "meta": {
            "source": "国土数値情報 鉄道データ N02-25",
            "sourceUrl": args.source_url,
            "projectStations": len(doc["stations"]),
            "matchedProjectStations": len(doc["stations"]) - len(unmatched),
            "matchedN02Groups": len(used_groups),
            "spreadThresholdM": args.spread_threshold_m,
            "coordinateThresholdM": args.coordinate_threshold_m,
            "lineConflictMarginM": args.line_conflict_margin_m,
            "nearbySameNameM": args.nearby_same_name_m,
            "spreadCounts": spread_counts,
        },
        "multiLineSpreadGroups": spread_groups,
        "lineCoordinateConflicts": line_conflicts,
        "multiGroupProjectStations": multi_group,
        "sameNameNearbyGroups": same_name_pairs,
        "coordinateMismatches": coord_mismatch,
        "collapsedLineMetadata": collapsed_lines,
        "unmatchedProjectStations": unmatched,
        "projectStationMatches": project_matches,
    }


def table(rows: list[dict], columns: list[tuple[str, str]], limit: int = 100) -> str:
    if not rows:
        return "なし\n"
    lines = ["| " + " | ".join(title for title, _ in columns) + " |"]
    lines.append("|" + "|".join("---" for _ in columns) + "|")
    for row in rows[:limit]:
        cells = []
        for _, key in columns:
            value = row.get(key, "")
            if isinstance(value, (dict, list, tuple)):
                value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            cells.append(str(value).replace("|", "\\|"))
        lines.append("| " + " | ".join(cells) + " |")
    if len(rows) > limit:
        lines.append(f"\n上位 {limit} 件のみ表示（全 {len(rows)} 件）。")
    return "\n".join(lines) + "\n"


def render_markdown(report: dict) -> str:
    meta = report["meta"]
    sections = [
        "# N02-25 駅グループ監査\n",
        "`stations.json` とN02-25の路線別駅位置を照合した読み取り専用監査。\n",
        "## 集計\n",
        f"- 収録駅: **{meta['projectStations']}**",
        f"- N02照合成功: **{meta['matchedProjectStations']}**",
        f"- 対応N02グループ: **{meta['matchedN02Groups']}**",
        "- 複数路線グループの路線別位置差: " + ", ".join(
            f"{key}m以上={value}" for key, value in meta["spreadCounts"].items()
        ),
        f"- `line` と代表座標の路線が{meta['lineConflictMarginM']}m以上逆転: **{len(report['lineCoordinateConflicts'])}**",
        f"- 複数N02グループを1駅に統合: **{len(report['multiGroupProjectStations'])}**",
        f"- {meta['nearbySameNameM']}m以内の別N02グループ同名駅ペア: **{len(report['sameNameNearbyGroups'])}**\n",
        "## 路線別位置差が大きいN02駅グループ\n",
        table(report["multiLineSpreadGroups"], [
            ("駅", "name"), ("group", "groupCode"), ("最大差(m)", "maxMemberDistanceM"),
            ("路線", "lines"), ("駅コード", "stationCodes"),
        ]),
        "## 現在の line と代表座標が食い違う候補\n",
        table(report["lineCoordinateConflicts"], [
            ("駅", "station"), ("現在line", "currentLine"), ("近い別路線", "closerOtherLine"),
            ("現在lineまで(m)", "distanceToCurrentLineM"), ("別路線まで(m)", "distanceToOtherLineM"),
            ("差(m)", "differenceM"), ("group", "groupCodes"),
        ]),
        "## 複数N02グループを1駅に統合している収録駅\n",
        table(report["multiGroupProjectStations"], [
            ("駅", "station"), ("id", "id"), ("group", "groupCodes"),
            ("最大中心間距離(m)", "maxGroupCenterDistanceM"), ("N02駅名", "groupNames"),
        ]),
        "## 近接する別N02グループの同名駅\n",
        table(report["sameNameNearbyGroups"], [
            ("駅名", "name"), ("group A", "groupA"), ("group B", "groupB"),
            ("距離(m)", "distanceM"), ("A路線", "linesA"), ("B路線", "linesB"),
        ]),
        "## 収録座標がN02駅位置から離れている候補\n",
        table(report["coordinateMismatches"], [
            ("駅", "station"), ("id", "id"), ("最近傍N02まで(m)", "nearestMemberDistanceM"),
            ("group", "groupCodes"), ("最近傍", "nearestMember"),
        ]),
        "## N02照合できなかった収録駅\n",
        table(report["unmatchedProjectStations"], [
            ("駅", "station"), ("id", "id"), ("理由", "method"), ("距離(m)", "fallbackDistanceM"),
        ]),
    ]
    return "\n".join(sections).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stations", default="data/stations.json")
    parser.add_argument("--source-url", default=SOURCE_URL)
    parser.add_argument("--json-output", default="data/n02-station-group-audit.json")
    parser.add_argument("--markdown-output", default="data/n02-station-group-audit.md")
    parser.add_argument("--spread-threshold-m", type=float, default=50.0)
    parser.add_argument("--coordinate-threshold-m", type=float, default=150.0)
    parser.add_argument("--line-conflict-margin-m", type=float, default=30.0)
    parser.add_argument("--nearby-same-name-m", type=float, default=2000.0)
    args = parser.parse_args()

    doc = json.loads(Path(args.stations).read_text(encoding="utf-8"))
    groups, summaries, groups_by_name = build_n02(fetch_n02(args.source_url))
    report = audit(doc, groups, summaries, groups_by_name, args)

    json_path = Path(args.json_output)
    md_path = Path(args.markdown_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")

    meta = report["meta"]
    print(f"project={meta['projectStations']} matched={meta['matchedProjectStations']} groups={meta['matchedN02Groups']}")
    print("spread_counts=" + json.dumps(meta["spreadCounts"], ensure_ascii=False))
    print(f"line_coordinate_conflicts={len(report['lineCoordinateConflicts'])}")
    print(f"multi_group_project_stations={len(report['multiGroupProjectStations'])}")
    print(f"same_name_nearby_groups={len(report['sameNameNearbyGroups'])}")
    print(f"coordinate_mismatches={len(report['coordinateMismatches'])}")
    print(f"unmatched={len(report['unmatchedProjectStations'])}")
    for row in report["lineCoordinateConflicts"][:30]:
        print(
            f"LINE_CONFLICT {row['station']} current={row['currentLine']} "
            f"closer={row['closerOtherLine']} diff={row['differenceM']:.1f}m "
            f"groups={','.join(row['groupCodes'])}"
        )


if __name__ == "__main__":
    main()
