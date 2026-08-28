#!/usr/bin/env python3
"""Attach MLIT S12 station passenger counts to data/stations.json.

Source: National Land Numerical Information, Number of Passengers by Station
S12-25 (2024 data, prepared in 2025). Only records with duplicate code=1
("listed on this line") are summed so the same operator total is not counted
again on duplicate line records.
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
    # Local display qualifiers used only to disambiguate same-name stations.
    name = re.sub(r"[（(](?:流鉄|東京メトロ|東西線|都電|都電荒川線)[）)]$", "", name)
    name = name.replace("ヶ", "ケ").replace("ヵ", "カ")
    aliases = {
        "赤塚": "下赤塚",
        "浅草tx": "浅草",
    }
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
        yield float(coords[1]), float(coords[0])  # lat, lon
    elif isinstance(coords, list):
        for child in coords:
            yield from iter_points(child)


def load_s12(url: str):
    with urllib.request.urlopen(url, timeout=60) as response:
        payload = response.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        return json.loads(zf.read(GEOJSON_PATH))


def build_groups(geojson):
    grouped = defaultdict(lambda: {
        "names": [], "points": [], "passengers": 0, "has_passengers": False,
    })
    for feature in geojson["features"]:
        p = feature["properties"]
        code = str(p["S12_001g"])
        g = grouped[code]
        g["names"].append(p["S12_001"])
        g["points"].extend(iter_points(feature["geometry"]["coordinates"]))
        # 2024: duplicate code S12_058, data availability S12_059, passengers S12_061.
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
    prefix = "tokyo23-"
    if not station_id.startswith(prefix):
        return None
    codes = station_id[len(prefix):].split("+")
    return codes if all(code.isdigit() for code in codes) else None


def match_station(station, groups, groups_by_name):
    exact_codes = group_codes_from_id(station["id"])
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stations", default="data/stations.json")
    parser.add_argument("--source-url", default=SOURCE_URL)
    args = parser.parse_args()

    path = Path(args.stations)
    doc = json.loads(path.read_text(encoding="utf-8"))
    groups = build_groups(load_s12(args.source_url))
    groups_by_name = defaultdict(list)
    for code, group in groups.items():
        groups_by_name[normalize_name(group["name"])].append(code)

    matches = []
    for station in doc["stations"]:
        codes, distance, method = match_station(station, groups, groups_by_name)
        values = [groups[c]["passengers"] for c in codes if groups[c]["passengers"] is not None]
        passengers = sum(values) if values else None
        station["passengers"] = passengers
        station["passengerYear"] = 2024 if passengers is not None else None
        matches.append((station, codes, distance, method))

    ranked = [s for s in doc["stations"] if isinstance(s.get("passengers"), int)]
    ranked.sort(key=lambda s: (-s["passengers"], s["station"], s["id"]))
    for rank, station in enumerate(ranked, 1):
        station["passengerRank"] = rank
    for station in doc["stations"]:
        if "passengerRank" not in station:
            station["passengerRank"] = None

    doc["meta"]["passengerData"] = {
        "source": "国土数値情報 駅別乗降客数 S12-25",
        "year": 2024,
        "license": "CC BY 4.0",
    }
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    no_match = [(s["station"], method, distance) for s, codes, distance, method in matches if not codes]
    no_data = [s["station"] for s, codes, distance, method in matches if codes and s["passengers"] is None]
    print(f"stations={len(doc['stations'])} matched={len(doc['stations']) - len(no_match)} passenger_data={len(ranked)}")
    print(f"unmatched={len(no_match)} no_2024_data={len(no_data)}")
    if no_match:
        print("UNMATCHED")
        for row in no_match:
            print(row)
    if no_data:
        print("NO_2024_DATA")
        print(", ".join(no_data))
    print("top20")
    for s in ranked[:20]:
        print(f"{s['passengerRank']:>3} {s['station']} {s['passengers']:,}")


if __name__ == "__main__":
    main()
