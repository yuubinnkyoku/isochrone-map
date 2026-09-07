#!/usr/bin/env python3
"""Inspect route text and N02 route-specific positions for ambiguous station anchors."""
from __future__ import annotations

import json
from pathlib import Path

from audit_n02_station_groups import (
    build_n02,
    fetch_n02,
    haversine_m,
    match_station,
    norm_line,
)

CANDIDATES = {"熊野前", "目黒", "大井町", "高田馬場", "駒込", "武蔵小杉"}


def main() -> None:
    doc = json.loads(Path("data/stations.json").read_text(encoding="utf-8"))
    groups, summaries, groups_by_name = build_n02(fetch_n02(
        "https://nlftp.mlit.go.jp/ksj/gml/data/N02/N02-25/N02-25_GML.zip"
    ))
    rows = []
    for station in doc["stations"]:
        if station.get("station") not in CANDIDATES:
            continue
        codes, method, _ = match_station(station, summaries, groups_by_name)
        members = [member for code in codes for member in groups[code]]
        details = []
        for member in members:
            details.append({
                "stationCode": member["stationCode"],
                "groupCode": member["groupCode"],
                "line": member["line"],
                "operator": member["operator"],
                "distanceM": round(haversine_m(
                    station["lat"], station["lng"], member["lat"], member["lng"]
                ), 2),
                "lat": member["lat"],
                "lng": member["lng"],
            })
        details.sort(key=lambda row: (row["distanceM"], row["line"]))
        rows.append({
            "id": station.get("id"),
            "station": station.get("station"),
            "lat": station.get("lat"),
            "lng": station.get("lng"),
            "minutes": station.get("minutes"),
            "line": station.get("line"),
            "route": station.get("route"),
            "excludeFromIdw": bool(station.get("excludeFromIdw")),
            "alternateAccess": station.get("alternateAccess"),
            "matchMethod": method,
            "groupCodes": codes,
            "n02Members": details,
        })
    rows.sort(key=lambda row: row["station"])
    out = {"candidates": rows}
    Path("data/route-anchor-candidates.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for row in rows:
        print("===", row["station"], row["id"], "===")
        print("minutes:", row["minutes"], "line:", row["line"], "excluded:", row["excludeFromIdw"])
        print("route:", row["route"])
        if row["alternateAccess"]:
            print("alternateAccess:", json.dumps(row["alternateAccess"], ensure_ascii=False))
        for member in row["n02Members"]:
            print(
                f"  {member['distanceM']:7.2f}m  {member['line']}  "
                f"code={member['stationCode']} operator={member['operator']}"
            )


if __name__ == "__main__":
    main()
