#!/usr/bin/env python3
"""Print N02 member lines for route segments the alignment audit could not resolve."""
from __future__ import annotations

import json
from pathlib import Path

from audit_n02_coverage import load_overrides, match_with_overrides
from audit_n02_station_groups import build_n02, fetch_n02, haversine_m

SOURCE_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/N02/N02-25/N02-25_GML.zip"


def main() -> None:
    stations_path = Path("data/stations.json")
    alignment_path = Path("data/route-origin-alignment-audit.json")
    overrides_path = Path("data/n02-station-group-overrides.json")

    doc = json.loads(stations_path.read_text(encoding="utf-8"))
    by_id = {str(row["id"]): row for row in doc["stations"]}
    alignment = json.loads(alignment_path.read_text(encoding="utf-8"))
    overrides = load_overrides(overrides_path)
    groups, summaries, groups_by_name = build_n02(fetch_n02(SOURCE_URL))

    output = []
    for unresolved in alignment["unparsedDirectSegments"]:
        station = by_id[str(unresolved["id"])]
        codes, method, _, _ = match_with_overrides(station, summaries, groups_by_name, overrides)
        members = []
        for code in codes:
            for member in groups[code]:
                distance = haversine_m(
                    float(station["lat"]),
                    float(station["lng"]),
                    member["lat"],
                    member["lng"],
                )
                members.append(
                    {
                        "groupCode": member["groupCode"],
                        "stationCode": member["stationCode"],
                        "line": member["line"],
                        "operator": member["operator"],
                        "lat": member["lat"],
                        "lng": member["lng"],
                        "distanceM": round(distance, 2),
                    }
                )
        members.sort(key=lambda row: (row["distanceM"], row["line"], row["stationCode"]))
        row = {
            "id": station["id"],
            "station": station["station"],
            "storedLine": station.get("line"),
            "firstSegment": unresolved["firstSegment"],
            "route": station.get("route"),
            "groupCodes": codes,
            "groupMatchMethod": method,
            "members": members,
        }
        output.append(row)
        print(f"=== {row['station']} {row['id']} ===")
        print(f"storedLine={row['storedLine']} first={row['firstSegment']} groups={','.join(codes)}")
        for member in members:
            print(
                f"  {member['distanceM']:8.2f}m  {member['line']}  "
                f"group={member['groupCode']} code={member['stationCode']} "
                f"operator={member['operator']}"
            )

    Path("data/unparsed-route-members.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
