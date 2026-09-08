#!/usr/bin/env python3
"""Audit route resolution for nearby same-name stations that must stay separate.

This specifically catches the dangerous case where a project entry is tied to one
N02 station group but Yahoo route text actually starts from another nearby station
with the same name (for example two unrelated operators' stations).
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from audit_n02_coverage import load_overrides, match_with_overrides
from audit_n02_station_groups import build_n02, fetch_n02, haversine_m
from audit_route_origin_alignment import is_access_segment, member_matches_segment

SOURCE_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/N02/N02-25/N02-25_GML.zip"


def segment_matches_group(station_id: str, segment: str, members: list[dict]) -> bool:
    return any(member_matches_segment(station_id, segment, member)[0] for member in members)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stations", default="data/stations.json")
    parser.add_argument("--overrides", default="data/n02-station-group-overrides.json")
    parser.add_argument("--source-url", default=SOURCE_URL)
    parser.add_argument("--json-output", default="data/same-name-station-route-audit.json")
    parser.add_argument("--markdown-output", default="data/same-name-station-route-audit.md")
    args = parser.parse_args()

    doc = json.loads(Path(args.stations).read_text(encoding="utf-8"))
    overrides = load_overrides(Path(args.overrides))
    groups, summaries, groups_by_name = build_n02(fetch_n02(args.source_url))

    represented_by: dict[str, list[dict]] = defaultdict(list)
    for station in doc["stations"]:
        codes, method, _, _ = match_with_overrides(station, summaries, groups_by_name, overrides)
        for code in codes:
            represented_by[code].append(station)

    rows = []
    flags = []
    for definition in overrides.get("separateSameNameGroups", []):
        pair_codes = [str(code) for code in definition.get("groupCodes", [])]
        for assigned_code in pair_codes:
            for station in represented_by.get(assigned_code, []):
                siblings = [code for code in pair_codes if code != assigned_code]
                route = str(station.get("route") or "")
                segments = [segment.strip() for segment in route.split("→") if segment.strip()]
                first = segments[0] if segments else ""
                assigned_members = groups.get(assigned_code, [])
                assigned_match = bool(first) and segment_matches_group(
                    str(station.get("id", "")), first, assigned_members
                )
                sibling_matches = []
                for sibling in siblings:
                    if first and segment_matches_group(
                        str(station.get("id", "")), first, groups.get(sibling, [])
                    ):
                        sibling_matches.append(sibling)

                assigned_summary = summaries[assigned_code]
                sibling_distances = {
                    sibling: round(
                        haversine_m(
                            assigned_summary["lat"], assigned_summary["lng"],
                            summaries[sibling]["lat"], summaries[sibling]["lng"],
                        ),
                        2,
                    )
                    for sibling in siblings
                }
                row = {
                    "station": station.get("station"),
                    "id": station.get("id"),
                    "assignedGroup": assigned_code,
                    "assignedLines": summaries[assigned_code]["lines"],
                    "siblingGroups": siblings,
                    "siblingLines": {code: summaries[code]["lines"] for code in siblings},
                    "siblingDistancesM": sibling_distances,
                    "minutes": station.get("minutes"),
                    "route": route,
                    "firstSegment": first,
                    "explicitAccess": is_access_segment(first) if first else False,
                    "firstSegmentMatchesAssigned": assigned_match,
                    "firstSegmentMatchesSibling": sibling_matches,
                    "definitionReason": definition.get("reason"),
                }
                row["wrongSameNameOrigin"] = bool(
                    first
                    and not row["explicitAccess"]
                    and not assigned_match
                    and sibling_matches
                )
                rows.append(row)
                if row["wrongSameNameOrigin"]:
                    flags.append(row)

    # Duplicate route/time across two stations that are intentionally separate is also
    # suspicious even if the first segment parser cannot resolve a brand name.
    duplicates = []
    by_definition = defaultdict(list)
    for row in rows:
        key = row["station"]
        by_definition[key].append(row)
    for name, name_rows in by_definition.items():
        for i, a in enumerate(name_rows):
            for b in name_rows[i + 1 :]:
                if a["assignedGroup"] == b["assignedGroup"]:
                    continue
                if a["minutes"] == b["minutes"] and a["route"] and a["route"] == b["route"]:
                    duplicates.append({
                        "station": name,
                        "groupA": a["assignedGroup"],
                        "groupB": b["assignedGroup"],
                        "minutes": a["minutes"],
                        "route": a["route"],
                    })

    report = {
        "meta": {
            "separateDefinitions": len(overrides.get("separateSameNameGroups", [])),
            "projectEntriesChecked": len(rows),
            "wrongSameNameOrigins": len(flags),
            "identicalRouteTimeAcrossSeparateGroups": len(duplicates),
        },
        "wrongSameNameOrigins": flags,
        "identicalRouteTimeAcrossSeparateGroups": duplicates,
        "rows": rows,
    }
    Path(args.json_output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    md = [
        "# 同名別駅の経路解決監査",
        "",
        f"- 明示的に別駅とした同名組: **{report['meta']['separateDefinitions']}**",
        f"- 確認した収録駅: **{report['meta']['projectEntriesChecked']}**",
        f"- 別の同名駅から経路が始まっている疑い: **{len(flags)}**",
        f"- 別駅なのに時刻・経路が完全一致: **{len(duplicates)}**",
        "",
        "## 誤解決候補",
        "",
    ]
    if flags:
        md.append("| 収録駅 | 割当group | 最初の経路 | 実際に一致した別group | group間距離(m) |")
        md.append("|---|---|---|---|---:|")
        for row in flags:
            sibling = row["firstSegmentMatchesSibling"][0]
            md.append(
                f"| {row['station']} (`{row['id']}`) | {row['assignedGroup']} | "
                f"{row['firstSegment'].replace('|', '/')} | {sibling} | "
                f"{row['siblingDistancesM'][sibling]} |"
            )
    else:
        md.append("なし")

    if duplicates:
        md.extend(["", "## 別駅なのに時刻・経路が完全一致", ""])
        for row in duplicates:
            md.append(
                f"- {row['station']}: {row['groupA']} / {row['groupB']} — "
                f"{row['minutes']}分値、`{row['route']}`"
            )

    Path(args.markdown_output).write_text("\n".join(md) + "\n", encoding="utf-8")

    print(json.dumps(report["meta"], ensure_ascii=False, indent=2))
    for row in flags:
        print(
            "WRONG_SAME_NAME_ORIGIN",
            row["station"], row["id"],
            f"assigned={row['assignedGroup']}",
            f"first={row['firstSegment']}",
            f"matchedSibling={','.join(row['firstSegmentMatchesSibling'])}",
        )
    for row in duplicates:
        print(
            "DUPLICATE_ROUTE_TIME",
            row["station"], row["groupA"], row["groupB"], row["minutes"],
        )


if __name__ == "__main__":
    main()
