#!/usr/bin/env python3
"""Attach MLIT S12 station passenger counts to data/stations.json.

Source: National Land Numerical Information, Number of Passengers by Station
S12-25 (2024 data, prepared in 2025). Only records with duplicate code=1
("listed on this line") are summed so the same operator total is not counted
again on duplicate line records.

For the physical-point station model, passenger totals/ranks remain properties of the
logical station.  Every physical component therefore receives the same logical-station
passenger metadata; route-specific points are never counted as separate stations.
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
from collections import defaultdict
from pathlib import Path

SOURCE_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/S12/S12-25/S12-25_GML.zip"
GEOJSON_PATH = "S12-25_GML/UTF-8/S12-25_NumberOfPassengers.geojson"


def normalize_name(name: str) -> str:
    name = unicodedata.normalize("NFKC", name).strip()
    name = re.sub(r"[（(](?:流鉄|東京メトロ|東西線|都電|都電荒川線|TX|つくばエクスプレス)[）)]$", "", name, flags=re.IGNORECASE)
    name = name.replace("ヶ", "ケ").replace("ヵ", "カ")
    aliases = {"赤塚": "下赤塚", "浅草tx": "浅草"}
    compact = re.sub(r"[()（）\s]", "", name).lower()
    return aliases.get(compact, name)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def iter_points(coords):
    if isinstance(coords, list) and len(coords) >= 2 and all(isinstance(x, (int, float)) for x in coords[:2]):
        yield float(coords[1]), float(coords[0])
    elif isinstance(coords, list):
        for child in coords:
            yield from iter_points(child)


def load_s12(url: str):
    with urllib.request.urlopen(url, timeout=60) as response:
        payload = response.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        return json.loads(zf.read(GEOJSON_PATH))


def build_groups(geojson):
    grouped = defaultdict(lambda: {"names": [], "points": [], "passengers": 0, "has_passengers": False})
    for feature in geojson["features"]:
        p = feature["properties"]
        code = str(p["S12_001g"])
        g = grouped[code]
        g["names"].append(p["S12_001"])
        g["points"].extend(iter_points(feature["geometry"]["coordinates"]))
        if p.get("S12_058") == 1 and p.get("S12_059") == 1 and isinstance(p.get("S12_061"), int):
            g["passengers"] += p["S12_061"]
            g["has_passengers"] = True
    result = {}
    for code, g in grouped.items():
        pts = g["points"]
        result[code] = {
            "code": code,
            "name": max(set(g["names"]), key=g["names"].count),
            "lat": sum(p[0] for p in pts) / len(pts),
            "lng": sum(p[1] for p in pts) / len(pts),
            "passengers": g["passengers"] if g["has_passengers"] else None,
        }
    return result


def group_codes_from_id(station_id: str):
    for prefix in ("tokyo23-", "n02-"):
        if station_id.startswith(prefix):
            codes = re.split(r"[+-]", station_id[len(prefix):])
            return codes if codes and all(code.isdigit() for code in codes) else None
    return None


def explicit_group_codes(station):
    codes = station.get("n02GroupCodes")
    if isinstance(codes, list) and codes and all(str(code).isdigit() for code in codes):
        return [str(code) for code in codes]
    return group_codes_from_id(str(station.get("id", "")))


def match_station(station, groups, groups_by_name):
    exact_codes = explicit_group_codes(station)
    if exact_codes and all(code in groups for code in exact_codes):
        return exact_codes, 0.0, "group-code"
    target_name = normalize_name(station["station"])
    candidates = groups_by_name.get(target_name, [])
    if not candidates:
        return [], None, "no-name-match"
    distances = [
        (haversine_km(station["lat"], station["lng"], groups[code]["lat"], groups[code]["lng"]), code)
        for code in candidates
    ]
    distance, code = min(distances)
    if distance > 1.0:
        return [], distance, "too-far"
    return [code], distance, "name+coordinate"


def assign_physical_model(doc, groups, groups_by_name):
    logical = defaultdict(list)
    for station in doc["stations"]:
        logical[str(station.get("logicalStationId") or station["id"])].append(station)
    logical_rows=[]
    for logical_id, points in logical.items():
        codes=sorted({code for p in points for code in (explicit_group_codes(p) or [])})
        method="physical-group-codes"
        distance=0.0
        if not codes or not all(code in groups for code in codes):
            codes,distance,method=match_station(points[0],groups,groups_by_name)
        values=[groups[c]["passengers"] for c in codes if c in groups and groups[c]["passengers"] is not None]
        passengers=sum(values) if values else None
        for p in points:
            p["passengers"]=passengers
            p["passengerYear"]=2024 if passengers is not None else None
        logical_rows.append({"id":logical_id,"stations":points,"codes":codes,"distance":distance,"method":method,"passengers":passengers})
    ranked=[row for row in logical_rows if isinstance(row["passengers"],int)]
    ranked.sort(key=lambda row:(-row["passengers"],row["stations"][0]["station"],row["id"]))
    ranks={row["id"]:rank for rank,row in enumerate(ranked,1)}
    for row in logical_rows:
        for p in row["stations"]:
            p["passengerRank"]=ranks.get(row["id"])
    return logical_rows, ranked


def assign_legacy_model(doc, groups, groups_by_name):
    matches=[]
    for station in doc["stations"]:
        codes,distance,method=match_station(station,groups,groups_by_name)
        values=[groups[c]["passengers"] for c in codes if groups[c]["passengers"] is not None]
        passengers=sum(values) if values else None
        station["passengers"]=passengers
        station["passengerYear"]=2024 if passengers is not None else None
        matches.append((station,codes,distance,method))
    ranked=[s for s in doc["stations"] if isinstance(s.get("passengers"),int)]
    ranked.sort(key=lambda s:(-s["passengers"],s["station"],s["id"]))
    for rank,station in enumerate(ranked,1): station["passengerRank"]=rank
    for station in doc["stations"]:
        if "passengerRank" not in station: station["passengerRank"]=None
    return matches, ranked


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--stations",default="data/stations.json")
    parser.add_argument("--source-url",default=SOURCE_URL)
    args=parser.parse_args()
    path=Path(args.stations)
    doc=json.loads(path.read_text(encoding="utf-8"))
    groups=build_groups(load_s12(args.source_url))
    groups_by_name=defaultdict(list)
    for code,group in groups.items(): groups_by_name[normalize_name(group["name"])].append(code)

    physical=any(s.get("logicalStationId") for s in doc["stations"])
    if physical:
        logical_rows,ranked=assign_physical_model(doc,groups,groups_by_name)
        no_match=[row for row in logical_rows if not row["codes"]]
        no_data=[row for row in logical_rows if row["codes"] and row["passengers"] is None]
        print(f"physical_points={len(doc['stations'])} logical_stations={len(logical_rows)} matched_logical={len(logical_rows)-len(no_match)} passenger_data_logical={len(ranked)}")
    else:
        matches,ranked=assign_legacy_model(doc,groups,groups_by_name)
        no_match=[(s["station"],method,distance) for s,codes,distance,method in matches if not codes]
        no_data=[s["station"] for s,codes,distance,method in matches if codes and s["passengers"] is None]
        print(f"stations={len(doc['stations'])} matched={len(doc['stations'])-len(no_match)} passenger_data={len(ranked)}")

    doc["meta"]["passengerData"]={"source":"国土数値情報 駅別乗降客数 S12-25","year":2024,"license":"CC BY 4.0"}
    path.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(f"unmatched={len(no_match)} no_2024_data={len(no_data)}")
    if no_match:
        print("UNMATCHED")
        for row in no_match: print(row)

if __name__=="__main__": main()
