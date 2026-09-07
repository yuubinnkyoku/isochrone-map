#!/usr/bin/env python3
"""Prove N02-25 station-group coverage for the 50 km project area.

This audit applies explicit logical-station overrides before comparing the project
station list with the N02-25 station-group population.  It is intentionally read-only.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from audit_n02_station_groups import build_n02, fetch_n02, haversine_m, match_station, norm_name

SCHOOL_LAT = 35.716741
SCHOOL_LNG = 139.7308823


def load_overrides(path: Path) -> dict:
    if not path.exists():
        return {"logicalMerges": [], "nameAliases": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def override_merge_for_station(station: dict, overrides: dict) -> dict | None:
    by_id = {row.get("stationId"): row for row in overrides.get("logicalMerges", []) if row.get("stationId")}
    if station.get("id") in by_id:
        return by_id[station["id"]]

    # Fallback by exact station name is safe only when exactly one override row has that name.
    rows = [row for row in overrides.get("logicalMerges", []) if row.get("station") == station.get("station")]
    return rows[0] if len(rows) == 1 else None


def match_with_overrides(station: dict, summaries: dict, groups_by_name: dict, overrides: dict):
    merge = override_merge_for_station(station, overrides)
    if merge:
        codes = [str(code) for code in merge.get("groupCodes", [])]
        missing = [code for code in codes if code not in summaries]
        if missing:
            return [], "override-missing-group", None, merge
        return codes, "logical-merge-override", 0.0, merge

    alias = overrides.get("nameAliases", {}).get(station.get("id"))
    if alias:
        shadow = dict(station)
        shadow["station"] = alias
        codes, method, distance = match_station(shadow, summaries, groups_by_name)
        return codes, f"alias:{alias}:{method}", distance, None

    codes, method, distance = match_station(station, summaries, groups_by_name)
    return codes, method, distance, None


def group_distance_to_school(group: dict) -> float:
    return haversine_m(SCHOOL_LAT, SCHOOL_LNG, group["lat"], group["lng"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stations", default="data/stations.json")
    parser.add_argument("--overrides", default="data/n02-station-group-overrides.json")
    parser.add_argument("--radius-km", type=float, default=50.0)
    parser.add_argument("--json-output", default="data/n02-coverage-audit.json")
    parser.add_argument("--markdown-output", default="data/n02-coverage-audit.md")
    parser.add_argument(
        "--source-url",
        default="https://nlftp.mlit.go.jp/ksj/gml/data/N02/N02-25/N02-25_GML.zip",
    )
    args = parser.parse_args()

    doc = json.loads(Path(args.stations).read_text(encoding="utf-8"))
    overrides = load_overrides(Path(args.overrides))
    groups, summaries, groups_by_name = build_n02(fetch_n02(args.source_url))

    target = {
        code
        for code, summary in summaries.items()
        if group_distance_to_school(summary) <= args.radius_km * 1000
    }

    represented_by: dict[str, list[dict]] = defaultdict(list)
    matches = []
    unresolved = []
    for station in doc["stations"]:
        codes, method, distance, override = match_with_overrides(
            station, summaries, groups_by_name, overrides
        )
        row = {
            "id": station.get("id"),
            "station": station.get("station"),
            "groupCodes": codes,
            "method": method,
            "distanceM": None if distance is None else round(distance, 2),
        }
        if override:
            row["overrideReason"] = override.get("reason")
        matches.append(row)
        if not codes:
            unresolved.append(row)
            continue
        for code in codes:
            represented_by[code].append({"id": station.get("id"), "station": station.get("station")})

    represented = set(represented_by)
    missing = sorted(target - represented)
    extra = sorted(represented - target)
    duplicates = {
        code: stations
        for code, stations in represented_by.items()
        if len(stations) > 1
    }

    missing_rows = [
        {
            "groupCode": code,
            "station": summaries[code]["name"],
            "distanceKm": round(group_distance_to_school(summaries[code]) / 1000, 3),
            "lines": summaries[code]["lines"],
            "lat": summaries[code]["lat"],
            "lng": summaries[code]["lng"],
        }
        for code in missing
    ]
    extra_rows = [
        {
            "groupCode": code,
            "station": summaries[code]["name"],
            "distanceKm": round(group_distance_to_school(summaries[code]) / 1000, 3),
            "lines": summaries[code]["lines"],
            "representedBy": represented_by[code],
        }
        for code in extra
        if code in summaries
    ]

    methods = Counter(row["method"].split(":", 1)[0] for row in matches)
    expected = doc.get("meta", {}).get("radiusCoverage", {}).get("stationGroups")
    report = {
        "meta": {
            "radiusKm": args.radius_km,
            "school": {"lat": SCHOOL_LAT, "lng": SCHOOL_LNG},
            "projectStations": len(doc["stations"]),
            "targetN02GroupsCalculated": len(target),
            "targetN02GroupsDeclared": expected,
            "representedN02Groups": len(represented),
            "representedTargetGroups": len(target & represented),
            "missingTargetGroups": len(missing),
            "extraRepresentedGroups": len(extra),
            "unresolvedStations": len(unresolved),
            "duplicateRepresentations": len(duplicates),
            "matchMethods": dict(sorted(methods.items())),
            "complete": not missing and not unresolved,
        },
        "missingTargetGroups": missing_rows,
        "extraRepresentedGroups": extra_rows,
        "unresolvedStations": unresolved,
        "duplicateRepresentations": duplicates,
        "logicalMergesApplied": [
            row for row in matches if row["method"] == "logical-merge-override"
        ],
        "matches": matches,
    }

    Path(args.json_output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    meta = report["meta"]
    md = [
        "# N02-25 50kmカバレッジ監査",
        "",
        f"- 収録駅: **{meta['projectStations']}**",
        f"- N02から再計算した50km圏グループ: **{meta['targetN02GroupsCalculated']}**",
        f"- stations.json宣言値: **{meta['targetN02GroupsDeclared']}**",
        f"- 収録駅が表現するN02グループ: **{meta['representedN02Groups']}**",
        f"- 50km圏で未カバー: **{meta['missingTargetGroups']}**",
        f"- 50km圏外を表現: **{meta['extraRepresentedGroups']}**",
        f"- N02未照合駅: **{meta['unresolvedStations']}**",
        f"- 同一N02グループの二重表現: **{meta['duplicateRepresentations']}**",
        f"- 完全性判定: **{'PASS' if meta['complete'] else 'FAIL'}**",
        "",
        "## 明示的に1駅へ統合したN02グループ",
        "",
    ]
    for row in report["logicalMergesApplied"]:
        md.append(f"- {row['station']} (`{row['id']}`): {', '.join(row['groupCodes'])}")
    if not report["logicalMergesApplied"]:
        md.append("- なし")

    if missing_rows:
        md.extend(["", "## 未カバー", ""])
        for row in missing_rows:
            md.append(
                f"- {row['station']} `{row['groupCode']}` {row['distanceKm']}km — {', '.join(row['lines'])}"
            )
    if unresolved:
        md.extend(["", "## N02未照合", ""])
        for row in unresolved:
            md.append(f"- {row['station']} (`{row['id']}`): {row['method']}")
    if extra_rows:
        md.extend(["", "## 50km圏外を表現", ""])
        for row in extra_rows:
            md.append(
                f"- {row['station']} `{row['groupCode']}` {row['distanceKm']}km"
            )

    Path(args.markdown_output).write_text("\n".join(md) + "\n", encoding="utf-8")

    print(json.dumps(meta, ensure_ascii=False, indent=2))
    for row in report["logicalMergesApplied"]:
        print("MERGE", row["station"], ",".join(row["groupCodes"]))
    for row in missing_rows[:30]:
        print("MISSING", row["station"], row["groupCode"], f"{row['distanceKm']}km")
    for row in unresolved:
        print("UNRESOLVED", row["station"], row["id"], row["method"])

    if expected is not None and expected != len(target):
        raise SystemExit(
            f"declared stationGroups={expected} but N02 radius recomputation={len(target)}"
        )
    if missing or unresolved:
        raise SystemExit("N02 coverage audit failed")


if __name__ == "__main__":
    main()
