#!/usr/bin/env python3
"""Expand logical project stations into distinct physical N02 station points.

Policy:
- Keep every project logical station / transfer grouping for identity and metadata.
- Within each logical station, expose every distinct N02 route-specific station point
  whose representative coordinate differs.
- N02 station-code records that resolve to the exact same representative coordinate
  are merged into one physical point.

This script is intentionally read-only with respect to data/stations.json. It writes
an audit/proposal JSON consumed by the later Yahoo baseline + multimodal recalculation.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from audit_n02_coverage import SOURCE_URL, build_n02, fetch_n02, load_overrides, match_project_station

STATIONS = Path("data/stations.json")
OVERRIDES = Path("data/n02-station-group-overrides.json")
OUTPUT = Path("data/physical-station-points-proposal.json")


def aggregate_station_codes(groups: dict[str, list[dict]], codes: list[str]) -> dict[str, dict]:
    by_station_code: dict[str, list[dict]] = defaultdict(list)
    for group_code in codes:
        for member in groups[group_code]:
            by_station_code[str(member["stationCode"])].append(member)

    result: dict[str, dict] = {}
    for station_code, members in by_station_code.items():
        lat = sum(float(m["lat"]) for m in members) / len(members)
        lng = sum(float(m["lng"]) for m in members) / len(members)
        result[station_code] = {
            "stationCode": station_code,
            "groupCodes": sorted({str(m["groupCode"]) for m in members}),
            "names": sorted({str(m["name"]) for m in members}),
            "lines": sorted({str(m["line"]) for m in members if m.get("line")}),
            "operators": sorted({str(m["operator"]) for m in members if m.get("operator")}),
            "lat": lat,
            "lng": lng,
        }
    return result


def point_key(item: dict) -> tuple[float, float]:
    # N02 geometry is deterministic. Eight decimals is much finer than the source
    # geometry and still lets truly identical route records collapse.
    return (round(float(item["lat"]), 8), round(float(item["lng"]), 8))


def main() -> None:
    doc = json.loads(STATIONS.read_text(encoding="utf-8"))
    overrides = load_overrides(OVERRIDES)
    groups, summaries, groups_by_name = build_n02(fetch_n02(SOURCE_URL))

    logical_rows = []
    physical_rows = []
    unresolved = []
    multi_point_logical = []

    for logical_index, station in enumerate(doc.get("stations", [])):
        codes, method, distance = match_project_station(station, summaries, groups_by_name, overrides)
        if not codes:
            unresolved.append({
                "id": station.get("id"),
                "station": station.get("station"),
                "method": method,
                "distanceM": distance,
            })
            continue

        station_codes = aggregate_station_codes(groups, codes)
        coord_groups: dict[tuple[float, float], list[dict]] = defaultdict(list)
        for item in station_codes.values():
            coord_groups[point_key(item)].append(item)

        points = []
        for physical_index, (coord, members) in enumerate(sorted(coord_groups.items())):
            station_code_list = sorted(m["stationCode"] for m in members)
            lines = sorted({line for m in members for line in m["lines"]})
            operators = sorted({op for m in members for op in m["operators"]})
            group_codes = sorted({code for m in members for code in m["groupCodes"]})
            names = sorted({name for m in members for name in m["names"]})
            point_id = "n02p-" + "+".join(station_code_list)
            row = {
                "id": point_id,
                "logicalStationId": station.get("id"),
                "logicalStation": station.get("station"),
                "logicalIndex": logical_index,
                "physicalIndex": physical_index,
                "n02StationCodes": station_code_list,
                "n02GroupCodes": group_codes,
                "n02Names": names,
                "lines": lines,
                "operators": operators,
                "lat": coord[0],
                "lng": coord[1],
                "logicalMinutes": station.get("minutes"),
                "logicalLine": station.get("line"),
                "logicalRoute": station.get("route"),
                "logicalExcludeFromIdw": bool(station.get("excludeFromIdw")),
                "passengers": station.get("passengers"),
                "passengerYear": station.get("passengerYear"),
                "passengerRank": station.get("passengerRank"),
                "major": bool(station.get("major")),
            }
            points.append(row)
            physical_rows.append(row)

        logical = {
            "id": station.get("id"),
            "station": station.get("station"),
            "matchedGroupCodes": codes,
            "matchMethod": method,
            "physicalPointCount": len(points),
            "physicalPointIds": [p["id"] for p in points],
        }
        logical_rows.append(logical)
        if len(points) > 1:
            multi_point_logical.append(logical)

    duplicate_point_ids = []
    seen = set()
    for row in physical_rows:
        if row["id"] in seen:
            duplicate_point_ids.append(row["id"])
        seen.add(row["id"])

    report = {
        "meta": {
            "source": "国土数値情報 鉄道データ N02-25",
            "sourceUrl": SOURCE_URL,
            "logicalStations": len(doc.get("stations", [])),
            "matchedLogicalStations": len(logical_rows),
            "physicalPoints": len(physical_rows),
            "logicalStationsWithMultiplePhysicalPoints": len(multi_point_logical),
            "unresolvedLogicalStations": len(unresolved),
            "duplicatePhysicalPointIds": sorted(set(duplicate_point_ids)),
            "coordinateRule": "within each logical station, distinct N02 station-code representative coordinates are all preserved; exact coordinate aliases are merged",
        },
        "multiPointLogicalStations": multi_point_logical,
        "unresolved": unresolved,
        "logicalStations": logical_rows,
        "physicalPoints": physical_rows,
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(report["meta"], ensure_ascii=False, indent=2))
    for logical in multi_point_logical[:100]:
        print("MULTI", logical["station"], logical["id"], logical["physicalPointCount"], ",".join(logical["physicalPointIds"]))
    if unresolved or duplicate_point_ids:
        raise SystemExit("physical station proposal has unresolved or duplicate identities")


if __name__ == "__main__":
    main()
