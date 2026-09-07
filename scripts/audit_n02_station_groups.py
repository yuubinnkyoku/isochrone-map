#!/usr/bin/env python3
"""Audit station grouping in data/stations.json against MLIT N02-25.

This is intentionally read-only: it detects cases where a project station represents
multiple N02 station records / groups, where same-name groups are spatially separated,
and where the current representative coordinate appears to belong to a different line
than the station's `line` field.  It does not change stations.json.

Source: National Land Numerical Information, Railway N02-25 (CC BY 4.0).
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
USER_AGENT = "isochrone-map N02 station-group audit/1.0"


def normalize_name(name: str) -> str:
    name = unicodedata.normalize("NFKC", str(name)).strip()
    # Display-only qualifiers used in this project to keep physically distinct stations apart.
    name = re.sub(
        r"[（(](?:流鉄|東京メトロ|東西線|都電|都電荒川線|TX|つくばエクスプレス)[）)]$",
        "",
        name,
        flags=re.IGNORECASE,
    )
    return name.replace("ヶ", "ケ").replace("ヵ", "カ").casefold()


def normalize_line(name: str | None) -> str | None:
    if not name:
        return None
    value = unicodedata.normalize("NFKC", str(name)).strip().casefold()
    value = value.replace("東京さくらトラム", "荒川線")
    value = value.replace("都電荒川線", "荒川線")
    value = value.replace("都電", "")
    # The N02 line-name field normally omits the operator prefix.
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
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    return re.sub(r"[・･\s]", "", value)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def iter_points(coords):
    if (
        isinstance(coords, list)
        and len(coords) >= 2
        and isinstance(coords[0], (int, float))
        and isinstance(coords[1], (int, float))
    ):
        yield float(coords[1]), float(coords[0])  # lat, lon
        return
    if isinstance(coords, list):
        for child in coords:
            yield from iter_points(child)


def representative_point(geometry: dict) -> tuple[float, float]:
    points = list(iter_points(geometry.get("coordinates")))
    if not points:
        raise ValueError("station geometry has no coordinates")
    # Station geometry is short.  The mean of its vertices is a stable representative
    # point and is sufficient for the 10-1000 m diagnostics in this audit.
    return (
        sum(lat for lat, _ in points) / len(points),
        sum(lon for _, lon in points) / len(points),
    )


def download_n02(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = response.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        candidates = [
            name
            for name in zf.namelist()
            if name.lower().endswith("_station.geojson")
            or name.lower().endswith("station.geojson")
        ]
        if not candidates:
            raise RuntimeError("N02 zip does not contain a Station GeoJSON")
        # Prefer UTF-8 paths when the archive contains more than one representation.
        candidates.sort(key=lambda s: ("utf-8" not in s.casefold(), len(s), s))
        return json.loads(zf.read(candidates[0]))


def build_n02(geojson: dict):
    records: list[dict] = []
    groups: dict[str, list[dict]] = defaultdict(list)
    groups_by_name: dict[str, set[str]] = defaultdict(set)

    for feature in geojson.get("features", []):
        p = feature.get("properties") or {}
        code = str(p.get("N02_005c", "")).strip()
        group = str(p.get("N02_005g", "")).strip()
        name = str(p.get("N02_005", "")).strip()
        if not code or not group or not name:
            continue
        lat, lng = representative_point(feature.get("geometry") or {})
        record = {
            "stationCode": code,
            "groupCode": group,
            "name": name,
            "line": str(p.get("N02_003", "")).strip(),
            "operator": str(p.get("N02_004", "")).strip(),
            "lat": lat,
            "lng": lng,
        }
        records.append(record)
        groups[group].append(record)
        groups_by_name[normalize_name(name)].add(group)

    summaries: dict[str, dict] = {}
    for code, members in groups.items():
        names = Counter(m["name"] for m in members)
        center_lat = sum(m["lat"] for m in members) / len(members)
        center_lng = sum(m["lng"] for m in members) / len(members)
        max_distance = 0.0
        max_pair = None
        for a, b in combinations(members, 2):
            d = haversine_m(a["lat"], a["lng"], b["lat"], b["lng"])
            if d > max_distance:
                max_distance = d
                max_pair = (a, b)
        summaries[code] = {
            "groupCode": code,
            "name": names.most_common(1)[0][0],
            "lat": center_lat,
            "lng": center_lng,
            "lines": sorted({m["line"] for m in members if m["line"]}),
            "operators": sorted({m["operator"] for m in members if m["operator"]}),
            "stationCodes": sorted({m["stationCode"] for m in members}),
            "memberCount": len(members),
            "maxMemberDistanceM": round(max_distance, 2),
            "maxPair": None
            if max_pair is None
            else {
                "a": {
                    "stationCode": max_pair[0]["stationCode"],
                    "line": max_pair[0]["line"],
                    "lat": max_pair[0]["lat"],
                    "lng": max_pair[0]["lng"],
                },
                "b": {
                    "stationCode": max_pair[1]["stationCode"],
                    "line": max_pair[1]["line"],
                    "lat": max_pair[1]["lat"],
                    "lng": max_pair[1]["lng"],
                },
            },
        }
    return records, groups, summaries, groups_by_name


def group_codes_from_id(station_id: str) -> list[str] | None:
    for prefix in ("tokyo23-", "n02-"):
        if station_id.startswith(prefix):
            codes = station_id[len(prefix) :].split("+")
            if codes and all(code.isdigit() for code in codes):
                return codes
    return None


def match_project_station(station: dict, summaries: dict, groups_by_name: dict):
    exact = group_codes_from_id(str(station.get("id", "")))
    if exact and all(code in summaries for code in exact):
        return exact, "group-code", 0.0

    name = normalize_name(station.get("station", ""))
    candidates = sorted(groups_by_name.get(name, []))
    if not candidates:
        return [], "no-name-match", None
    lat, lng = float(station["lat"]), float(station["lng"])
    distance, code = min(
        (
            haversine_m(lat, lng, summaries[c]["lat"], summaries[c]["lng"]),
            c,
        )
        for c in candidates
    )
    if distance > 1500:
        return [], "name-too-far", distance
    return [code], "name+coordinate", distance


def pairwise_group_distance(codes: list[str], summaries: dict) -> tuple[float, tuple[str, str] | None]:
    maximum = 0.0
    pair = None
    for a, b in combinations(codes, 2):
        ga, gb = summaries[a], summaries[b]
        d = haversine_m(ga["lat"], ga["lng"], gb["lat"], gb["lng"])
        if d > maximum:
            maximum = d
            pair = (a, b)
    return maximum, pair


def audit(doc: dict, groups: dict, summaries: dict, groups_by_name: dict, args) -> dict:
    matches = []
    project_group_codes: set[str] = set()
    for station in doc["stations"]:
        codes, method, fallback_distance = match_project_station(station, summaries, groups_by_name)
        matches.append((station, codes, method, fallback_distance))
        project_group_codes.update(codes)

    multi_line_spreads = []
    for code in sorted(project_group_codes):
        g = summaries[code]
        if len(g["lines"]) >= 2 and g["maxMemberDistanceM"] >= args.spread_threshold_m:
            multi_line_spreads.append(g)
    multi_line_spreads.sort(key=lambda g: (-g["maxMemberDistanceM"], g["name"], g["groupCode"]))

    multi_group_project_stations = []
    coordinate_mismatches = []
    line_coordinate_conflicts = []
    collapsed_line_metadata = []
    unmatched = []

    for station, codes, method, fallback_distance in matches:
        if not codes:
            unmatched.append(
                {
                    "id": station.get("id"),
                    "station": station.get("station"),
                    "method": method,
                    "distanceM": None if fallback_distance is None else round(fallback_distance, 2),
                }
            )
            continue

        if len(codes) > 1:
            max_distance, pair = pairwise_group_distance(codes, summaries)
            multi_group_project_stations.append(
                {
                    "id": station.get("id"),
                    "station": station.get("station"),
                    "groupCodes": codes,
                    "groupNames": [summaries[c]["name"] for c in codes],
                    "groupLines": {c: summaries[c]["lines"] for c in codes},
                    "maxGroupCenterDistanceM": round(max_distance, 2),
                    "maxPair": pair,
                }
            )

        members = [m for c in codes for m in groups[c]]
        station_lat, station_lng = float(station["lat"]), float(station["lng"])
        nearest_distance, nearest = min(
            (
                haversine_m(station_lat, station_lng, m["lat"], m["lng"]),
                m,
            )
            for m in members
        )
        if nearest_distance > args.coordinate_threshold_m:
            coordinate_mismatches.append(
                {
                    "id": station.get("id"),
                    "station": station.get("station"),
                    "lat": station_lat,
                    "lng": station_lng,
                    "groupCodes": codes,
                    "nearestMemberDistanceM": round(nearest_distance, 2),
                    "nearestMember": {
                        "stationCode": nearest["stationCode"],
                        "line": nearest["line"],
                        "lat": nearest["lat"],
                        "lng": nearest["lng"],
                    },
                }
            )

        all_lines = sorted({m["line"] for m in members if m["line"]})
        if len(all_lines) >= 2:
            collapsed_line_metadata.append(
                {
                    "id": station.get("id"),
                    "station": station.get("station"),
                    "line": station.get("line"),
                    "n02Lines": all_lines,
                    "groupCodes": codes,
                }
            )

        current_line = normalize_line(station.get("line"))
        if current_line and len(all_lines) >= 2:
            on_current = [m for m in members if normalize_line(m["line"]) == current_line]
            other = [m for m in members if normalize_line(m["line"]) != current_line]
            if on_current and other:
                d_current, m_current = min(
                    (
                        haversine_m(station_lat, station_lng, m["lat"], m["lng"]),
                        m,
                    )
                    for m in on_current
                )
                d_other, m_other = min(
                    (
                        haversine_m(station_lat, station_lng, m["lat"], m["lng"]),
                        m,
                    )
                    for m in other
                )
                if d_current - d_other >= args.line_conflict_margin_m:
                    line_coordinate_conflicts.append(
                        {
                            "id": station.get("id"),
                            "station": station.get("station"),
                            "currentLine": station.get("line"),
                            "groupCodes": codes,
                            "distanceToCurrentLineM": round(d_current, 2),
                            "currentLineMember": {
                                "stationCode": m_current["stationCode"],
                                "line": m_current["line"],
                            },
                            "closerOtherLine": m_other["line"],
                            "distanceToOtherLineM": round(d_other, 2),
                            "differenceM": round(d_current - d_other, 2),
                        }
                    )

    multi_group_project_stations.sort(
        key=lambda r: (-r["maxGroupCenterDistanceM"], r["station"], r["id"])
    )
    coordinate_mismatches.sort(key=lambda r: (-r["nearestMemberDistanceM"], r["station"]))
    line_coordinate_conflicts.sort(key=lambda r: (-r["differenceM"], r["station"]))

    # Distinct N02 groups with the same name can still be close enough to be accidentally
    # collapsed by a name-only join.  Check pairs near any group represented by this project.
    same_name_nearby = []
    seen_pairs: set[tuple[str, str]] = set()
    for normalized_name, code_set in groups_by_name.items():
        codes = sorted(code_set)
        if len(codes) < 2 or not project_group_codes.intersection(codes):
            continue
        for a, b in combinations(codes, 2):
            pair = (a, b)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            ga, gb = summaries[a], summaries[b]
            distance = haversine_m(ga["lat"], ga["lng"], gb["lat"], gb["lng"])
            if distance <= args.nearby_same_name_m:
                same_name_nearby.append(
                    {
                        "name": ga["name"],
                        "normalizedName": normalized_name,
                        "groupA": a,
                        "groupB": b,
                        "distanceM": round(distance, 2),
                        "linesA": ga["lines"],
                        "linesB": gb["lines"],
                        "projectUsesA": a in project_group_codes,
                        "projectUsesB": b in project_group_codes,
                    }
                )
    same_name_nearby.sort(key=lambda r: (r["distanceM"], r["name"], r["groupA"]))

    spread_counts = {}
    for threshold in (50, 100, 150, 200, 300):
        spread_counts[str(threshold)] = sum(
            1
            for code in project_group_codes
            if len(summaries[code]["lines"]) >= 2
            and summaries[code]["maxMemberDistanceM"] >= threshold
        )

    return {
        "meta": {
            "source": "国土数値情報 鉄道データ N02-25",
            "sourceUrl": args.source_url,
            "projectStations": len(doc["stations"]),
            "matchedProjectStations": len(doc["stations"]) - len(unmatched),
            "matchedN02Groups": len(project_group_codes),
            "spreadThresholdM": args.spread_threshold_m,
            "coordinateThresholdM": args.coordinate_threshold_m,
            "lineConflictMarginM": args.line_conflict_margin_m,
            "nearbySameNameM": args.nearby_same_name_m,
            "spreadCounts": spread_counts,
        },
        "multiLineSpreadGroups": multi_line_spreads,
        "multiGroupProjectStations": multi_group_project_stations,
        "sameNameNearbyGroups": same_name_nearby,
        "lineCoordinateConflicts": line_coordinate_conflicts,
        "coordinateMismatches": coordinate_mismatches,
        "collapsedLineMetadata": collapsed_line_metadata,
        "unmatchedProjectStations": unmatched,
    }


def markdown_table(rows: list[dict], columns: list[tuple[str, str]], limit: int = 100) -> str:
    if not rows:
        return "なし\n"
    out = ["| " + " | ".join(title for title, _ in columns) + " |"]
    out.append("|" + "|".join("---" for _ in columns) + "|")
    for row in rows[:limit]:
        values = []
        for _, key in columns:
            value = row.get(key, "")
            if isinstance(value, (list, dict, tuple)):
                value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            values.append(str(value).replace("|", "\\|"))
        out.append("| " + " | ".join(values) + " |")
    if len(rows) > limit:
        out.append(f"\n上位 {limit} 件のみ表示（全 {len(rows)} 件）。")
    return "\n".join(out) + "\n"


def render_markdown(report: dict) -> str:
    m = report["meta"]
    out = [
        "# N02-25 駅グループ監査",
        "",
        "`stations.json` を国土数値情報 N02-25 の駅コード・グループコード・路線別位置と照合した結果。",
        "このレポート自体はデータを書き換えない。",
        "",
        "## 集計",
        "",
        f"- 収録駅: **{m['projectStations']}**",
        f"- N02照合成功: **{m['matchedProjectStations']}**",
        f"- 対応するN02グループ: **{m['matchedN02Groups']}**",
        "- 複数路線グループの路線別位置差: "
        + ", ".join(f"{k}m以上={v}" for k, v in m["spreadCounts"].items()),
        f"- 現在の `line` と代表座標の路線位置が {m['lineConflictMarginM']}m 以上逆転する候補: **{len(report['lineCoordinateConflicts'])}**",
        f"- 複数N02グループを1駅にまとめた収録駅: **{len(report['multiGroupProjectStations'])}**",
        f"- {m['nearbySameNameM']}m以内にある別N02グループの同名駅ペア: **{len(report['sameNameNearbyGroups'])}**",
        "",
        "## 路線別位置差が大きいN02駅グループ",
        "",
        markdown_table(
            report["multiLineSpreadGroups"],
            [
                ("駅", "name"),
                ("group", "groupCode"),
                ("最大差(m)", "maxMemberDistanceM"),
                ("路線", "lines"),
                ("駅コード", "stationCodes"),
            ],
        ),
        "## 現在の line と代表座標が食い違う候補",
        "",
        markdown_table(
            report["lineCoordinateConflicts"],
            [
                ("駅", "station"),
                ("現在line", "currentLine"),
                ("近い別路線", "closerOtherLine"),
                ("現在lineまで(m)", "distanceToCurrentLineM"),
                ("別路線まで(m)", "distanceToOtherLineM"),
                ("差(m)", "differenceM"),
                ("group", "groupCodes"),
            ],
        ),
        "## 複数N02グループを1駅にまとめている収録駅",
        "",
        markdown_table(
            report["multiGroupProjectStations"],
            [
                ("駅", "station"),
                ("id", "id"),
                ("group", "groupCodes"),
                ("最大中心間距離(m)", "maxGroupCenterDistanceM"),
                ("N02駅名", "groupNames"),
            ],
        ),
        "## 近接する別N02グループの同名駅",
        "",
        markdown_table(
            report["sameNameNearbyGroups"],
            [
                ("駅名", "name"),
                ("group A", "groupA"),
                ("group B", "groupB"),
                ("距離(m)", "distanceM"),
                ("A路線", "linesA"),
                ("B路線", "linesB"),
            ],
        ),
        "## 収録座標がN02駅位置から離れている候補",
        "",
        markdown_table(
            report["coordinateMismatches"],
            [
                ("駅", "station"),
                ("id", "id"),
                ("最近傍N02まで(m)", "nearestMemberDistanceM"),
                ("group", "groupCodes"),
                ("最近傍", "nearestMember"),
            ],
        ),
        "## N02照合できなかった収録駅",
        "",
        markdown_table(
            report["unmatchedProjectStations"],
            [("駅", "station"), ("id", "id"), ("理由", "method"), ("距離(m)", "distanceM")],
        ),
    ]
    return "\n".join(out).rstrip() + "\n"


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

    stations_path = Path(args.stations)
    doc = json.loads(stations_path.read_text(encoding="utf-8"))
    _, groups, summaries, groups_by_name = build_n02(download_n02(args.source_url))
    report = audit(doc, groups, summaries, groups_by_name, args)

    json_path = Path(args.json_output)
    markdown_path = Path(args.markdown_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")

    meta = report["meta"]
    print(
        f"project={meta['projectStations']} matched={meta['matchedProjectStations']} "
        f"groups={meta['matchedN02Groups']}"
    )
    print("spread_counts=" + json.dumps(meta["spreadCounts"], ensure_ascii=False))
    print(f"line_coordinate_conflicts={len(report['lineCoordinateConflicts'])}")
    print(f"multi_group_project_stations={len(report['multiGroupProjectStations'])}")
    print(f"same_name_nearby_groups={len(report['sameNameNearbyGroups'])}")
    print(f"coordinate_mismatches={len(report['coordinateMismatches'])}")
    print(f"unmatched={len(report['unmatchedProjectStations'])}")
    for row in report["lineCoordinateConflicts"][:20]:
        print(
            "LINE_CONFLICT "
            f"{row['station']} current={row['currentLine']} closer={row['closerOtherLine']} "
            f"diff={row['differenceM']:.1f}m groups={','.join(row['groupCodes'])}"
        )


if __name__ == "__main__":
    main()
