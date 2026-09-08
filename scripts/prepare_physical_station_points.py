#!/usr/bin/env python3
"""Expand logical project stations into distinct physical N02 station points.

Policy:
- Keep every project logical station / transfer grouping for identity and metadata.
- Within each logical station, expose every distinct N02 route-specific station point
  whose representative coordinate differs.
- N02 station-code records that resolve to the exact same representative coordinate
  are merged into one physical point.

The script accepts both the legacy logical stations.json and the final physical-point
stations.json. In the latter case it reconstructs one logical source row per
logicalStationId, so rerunning the audit does not multiply already-expanded points.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from audit_n02_coverage import load_overrides, match_with_overrides
from audit_n02_station_groups import SOURCE_URL, build_n02, fetch_n02

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
        result[station_code] = {
            "stationCode": station_code,
            "groupCodes": sorted({str(m["groupCode"]) for m in members}),
            "names": sorted({str(m["name"]) for m in members}),
            "lines": sorted({str(m["line"]) for m in members if m.get("line")}),
            "operators": sorted({str(m["operator"]) for m in members if m.get("operator")}),
            "lat": sum(float(m["lat"]) for m in members) / len(members),
            "lng": sum(float(m["lng"]) for m in members) / len(members),
        }
    return result


def point_key(item: dict) -> tuple[float, float]:
    return (round(float(item["lat"]), 8), round(float(item["lng"]), 8))


def logical_source_rows(doc: dict) -> list[dict]:
    stations=doc.get("stations",[])
    if not stations or not any(s.get("logicalStationId") for s in stations):
        return stations
    grouped: dict[str,list[dict]] = defaultdict(list)
    for station in stations:
        grouped[str(station.get("logicalStationId") or station.get("id"))].append(station)
    rows=[]
    for logical_id, points in grouped.items():
        primary=next((p for p in points if p.get("labelPrimary")),points[0])
        row=dict(primary)
        row["id"]=logical_id
        row["station"]=primary.get("station")
        row["_explicitGroupCodes"]=sorted({str(code) for p in points for code in (p.get("n02GroupCodes") or [])})
        rows.append(row)
    # Preserve the first appearance order of logical IDs in stations.json.
    order={}
    for i,s in enumerate(stations): order.setdefault(str(s.get("logicalStationId") or s.get("id")),i)
    rows.sort(key=lambda r:order[str(r["id"])])
    return rows


def main() -> None:
    doc=json.loads(STATIONS.read_text(encoding="utf-8"))
    source_stations=logical_source_rows(doc)
    overrides=load_overrides(OVERRIDES)
    groups,summaries,groups_by_name=build_n02(fetch_n02(SOURCE_URL))
    logical_rows=[]; physical_rows=[]; unresolved=[]; multi_point_logical=[]

    for logical_index,station in enumerate(source_stations):
        explicit=station.get("_explicitGroupCodes") or []
        if explicit and all(code in summaries for code in explicit):
            codes=list(explicit); method="physical-dataset-group-codes"; distance=0.0; override=None
        else:
            codes,method,distance,override=match_with_overrides(station,summaries,groups_by_name,overrides)
        if not codes:
            unresolved.append({"id":station.get("id"),"station":station.get("station"),"method":method,"distanceM":distance})
            continue

        station_codes=aggregate_station_codes(groups,codes)
        coord_groups: dict[tuple[float,float],list[dict]]=defaultdict(list)
        for item in station_codes.values(): coord_groups[point_key(item)].append(item)
        points=[]
        for physical_index,(coord,members) in enumerate(sorted(coord_groups.items())):
            station_code_list=sorted(m["stationCode"] for m in members)
            lines=sorted({line for m in members for line in m["lines"]})
            operators=sorted({op for m in members for op in m["operators"]})
            group_codes=sorted({code for m in members for code in m["groupCodes"]})
            names=sorted({name for m in members for name in m["names"]})
            point_id="n02p-"+"+".join(station_code_list)
            row={
                "id":point_id,"logicalStationId":station.get("id"),"logicalStation":station.get("station"),
                "logicalIndex":logical_index,"physicalIndex":physical_index,
                "n02StationCodes":station_code_list,"n02GroupCodes":group_codes,"n02Names":names,
                "lines":lines,"operators":operators,"lat":coord[0],"lng":coord[1],
                "logicalMinutes":station.get("minutes"),"logicalLine":station.get("line"),"logicalRoute":station.get("route"),
                "logicalExcludeFromIdw":bool(station.get("excludeFromIdw")),
                "passengers":station.get("passengers"),"passengerYear":station.get("passengerYear"),
                "passengerRank":station.get("passengerRank"),"major":bool(station.get("major")),
            }
            points.append(row); physical_rows.append(row)
        logical={
            "id":station.get("id"),"station":station.get("station"),"matchedGroupCodes":codes,
            "matchMethod":method,"overrideReason":None if not override else override.get("reason"),
            "physicalPointCount":len(points),"physicalPointIds":[p["id"] for p in points],
        }
        logical_rows.append(logical)
        if len(points)>1: multi_point_logical.append(logical)

    seen=set(); duplicate_point_ids=[]
    for row in physical_rows:
        if row["id"] in seen: duplicate_point_ids.append(row["id"])
        seen.add(row["id"])
    report={
        "meta":{
            "source":"国土数値情報 鉄道データ N02-25","sourceUrl":SOURCE_URL,
            "logicalStations":len(source_stations),"matchedLogicalStations":len(logical_rows),
            "physicalPoints":len(physical_rows),"logicalStationsWithMultiplePhysicalPoints":len(multi_point_logical),
            "unresolvedLogicalStations":len(unresolved),"duplicatePhysicalPointIds":sorted(set(duplicate_point_ids)),
            "coordinateRule":"within each logical station, distinct N02 station-code representative coordinates are all preserved; exact coordinate aliases are merged",
        },
        "multiPointLogicalStations":multi_point_logical,"unresolved":unresolved,
        "logicalStations":logical_rows,"physicalPoints":physical_rows,
    }
    OUTPUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report["meta"],ensure_ascii=False,indent=2))
    for logical in multi_point_logical[:100]:
        print("MULTI",logical["station"],logical["id"],logical["physicalPointCount"],",".join(logical["physicalPointIds"]))
    if unresolved or duplicate_point_ids:
        raise SystemExit("physical station proposal has unresolved or duplicate identities")

if __name__=="__main__": main()
