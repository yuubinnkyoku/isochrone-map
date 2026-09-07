#!/usr/bin/env python3
"""Validate address-origin routing before using it for IDW exclusion decisions.

The project stores N02 station coordinates, but Yahoo! Transit does not accept raw
``lat,lng`` as a route origin.  For a small set of known/suspicious stations this
script:

1. reverse-geocodes the N02 point with the GSI reverse-geocoder,
2. queries Yahoo! Transit from that textual address with the project's exact
   2026-08-28 / 08:18-arrival settings,
3. compares the address-origin departure with the stored direct-station departure,
4. scans the Yahoo HTML for coordinate-bearing links/data so we can detect a textual
   address that Yahoo resolves far away from the N02 station point.

It is deliberately targeted (not a 1318-request crawler) until the origin-resolution
method is proven safe on the known Disney outliers and representative candidates.
"""
from __future__ import annotations

import html
import json
import math
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

from audit_yahoo_targeted import TextExtractor, build_url

STATIONS_PATH = Path("data/stations.json")
OUTPUT_PATH = Path("data/address-origin-resolution-audit.json")

TARGETS = [
    "東京ディズニーランド・ステーション",
    "東京ディズニーシー・ステーション",
    "北山田",
    "熊野前",
    "仲御徒町",
    "千駄木",
    "向河原",
    "宮ノ前",
    "流山",
]

GSI_REVERSE = "https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress"
GSI_MUNI = "https://maps.gsi.go.jp/js/muni.js"
UA = (
    "isochrone-map station-audit/1.0 "
    "(+https://github.com/yuubinnkyoku/isochrone-map)"
)


def fetch_text(url: str) -> tuple[str, str, int]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        body = response.read().decode(
            response.headers.get_content_charset() or "utf-8", errors="replace"
        )
        return body, response.geturl(), response.status


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_008.8
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


def load_municipalities() -> dict[str, tuple[str, str]]:
    body, _, _ = fetch_text(GSI_MUNI)
    mapping: dict[str, tuple[str, str]] = {}
    # Current GSI format is e.g. GSI.MUNI_ARRAY["13101"] = '13,東京都,13101,千代田区';
    pattern = re.compile(
        r'MUNI_ARRAY\[?["\'](?P<code>\d{5})["\']\]?\s*=\s*["\']'
        r'(?P<prefcode>\d+),(?P<pref>[^,]+),(?P=code),(?P<muni>[^"\']+)["\']'
    )
    for match in pattern.finditer(body):
        mapping[match.group("code")] = (match.group("pref"), match.group("muni"))
    return mapping


def reverse_geocode(lat: float, lon: float, municipalities: dict[str, tuple[str, str]]) -> dict:
    query = urllib.parse.urlencode({"lat": f"{lat:.8f}", "lon": f"{lon:.8f}"})
    body, final_url, status = fetch_text(f"{GSI_REVERSE}?{query}")
    payload = json.loads(body)
    results = payload.get("results") or {}
    muni_code = str(results.get("muniCd") or "")
    block = str(results.get("lv01Nm") or "")
    pref, municipality = municipalities.get(muni_code, ("", ""))
    address = f"{pref}{municipality}{block}" if (pref or municipality) else block
    return {
        "httpStatus": status,
        "finalUrl": final_url,
        "muniCode": muni_code,
        "prefecture": pref,
        "municipality": municipality,
        "block": block,
        "address": address,
        "raw": results,
    }


def _valid_lat(value: float) -> bool:
    return 20.0 <= value <= 50.0


def _valid_lon(value: float) -> bool:
    return 120.0 <= value <= 155.0


def extract_coordinate_mentions(body: str, station_lat: float, station_lon: float) -> list[dict]:
    # Decode entities and URL escapes because Yahoo often nests map links in query strings.
    decoded = html.unescape(body)
    for _ in range(2):
        decoded = urllib.parse.unquote(decoded)

    candidates: list[tuple[float, float, str, int]] = []
    patterns = [
        # Explicit lat/lon style keys, either order.
        re.compile(
            r'(?i)(?:lat|latitude)[=:"\']+\s*(-?\d{2}\.\d+).{0,180}?'
            r'(?:lon|lng|longitude)[=:"\']+\s*(-?\d{3}\.\d+)'
        ),
        re.compile(
            r'(?i)(?:lon|lng|longitude)[=:"\']+\s*(-?\d{3}\.\d+).{0,180}?'
            r'(?:lat|latitude)[=:"\']+\s*(-?\d{2}\.\d+)'
        ),
        # Common compact map coordinate forms.
        re.compile(r'(?i)(?:latlon|latlng)[=:"\']+\s*(-?\d{2}\.\d+)[,/%20 ]+(-?\d{3}\.\d+)'),
        re.compile(r'(?i)(?:ll|center)[=:"\']+\s*(-?\d{3}\.\d+)[,/%20 ]+(-?\d{2}\.\d+)'),
        # Raw coordinate pairs as a last resort; later we sort by distance to the station.
        re.compile(r'(?<!\d)(-?\d{2}\.\d{4,})\s*[,/]\s*(-?\d{3}\.\d{4,})(?!\d)'),
        re.compile(r'(?<!\d)(-?\d{3}\.\d{4,})\s*[,/]\s*(-?\d{2}\.\d{4,})(?!\d)'),
    ]

    for pattern_index, pattern in enumerate(patterns):
        for match in pattern.finditer(decoded):
            a = float(match.group(1))
            b = float(match.group(2))
            if pattern_index in {1, 3, 5}:
                lon, lat = a, b
            else:
                lat, lon = a, b
            if not (_valid_lat(lat) and _valid_lon(lon)):
                continue
            context = decoded[max(0, match.start() - 100) : min(len(decoded), match.end() + 100)]
            candidates.append((lat, lon, re.sub(r"\s+", " ", context), pattern_index))

    unique: dict[tuple[float, float], dict] = {}
    for lat, lon, context, pattern_index in candidates:
        key = (round(lat, 7), round(lon, 7))
        distance = haversine_m(station_lat, station_lon, lat, lon)
        row = {
            "lat": lat,
            "lng": lon,
            "distanceFromStationM": round(distance, 2),
            "pattern": pattern_index,
            "context": context[:300],
        }
        old = unique.get(key)
        if old is None or pattern_index < old["pattern"]:
            unique[key] = row

    return sorted(
        unique.values(),
        key=lambda row: (row["distanceFromStationM"], row["pattern"], row["lat"], row["lng"]),
    )[:40]


def query_yahoo_address(address: str, station_lat: float, station_lon: float) -> dict:
    url = build_url(address)
    body, final_url, status = fetch_text(url)
    parser = TextExtractor()
    parser.feed(body)
    lines = parser.text().splitlines()
    route_start = next((i for i, line in enumerate(lines) if line == "ルート1"), None)
    excerpt = lines[route_start : route_start + 140] if route_start is not None else lines[:180]
    joined = "\n".join(excerpt)
    summary = re.search(r"(\d{1,2}:\d{2})\s*→\s*(\d{1,2}:\d{2})", joined)
    mentions = extract_coordinate_mentions(body, station_lat, station_lon)
    return {
        "requestedUrl": url,
        "finalUrl": final_url,
        "httpStatus": status,
        "summaryDeparture": summary.group(1) if summary else None,
        "summaryArrival": summary.group(2) if summary else None,
        "excerpt": excerpt,
        "coordinateMentions": mentions,
        "nearestCoordinateMention": mentions[0] if mentions else None,
        "htmlBytes": len(body.encode("utf-8")),
    }


def hhmm_to_minutes(value: str | None) -> int | None:
    if not value:
        return None
    h, m = value.split(":", 1)
    return int(h) * 60 + int(m)


def main() -> None:
    doc = json.loads(STATIONS_PATH.read_text(encoding="utf-8"))
    by_name: dict[str, list[dict]] = {}
    for station in doc["stations"]:
        by_name.setdefault(str(station.get("station")), []).append(station)

    municipalities = load_municipalities()
    print(f"GSI municipality entries={len(municipalities)}")
    rows = []

    for index, name in enumerate(TARGETS, start=1):
        matches = by_name.get(name, [])
        if not matches:
            row = {"station": name, "error": "station-not-found"}
            rows.append(row)
            print(f"[{index:02d}/{len(TARGETS):02d}] {name}: station-not-found")
            continue
        if len(matches) > 1:
            # Keep every duplicate visible instead of silently choosing one.
            print(f"[{index:02d}/{len(TARGETS):02d}] {name}: {len(matches)} project records")

        for station in matches:
            lat = float(station["lat"])
            lon = float(station["lng"])
            row = {
                "id": station.get("id"),
                "station": name,
                "lat": lat,
                "lng": lon,
                "storedMinutes": station.get("minutes"),
                "storedDeparture": (
                    f"{int(station['minutes']) // 60:02d}:{int(station['minutes']) % 60:02d}"
                    if station.get("minutes") is not None
                    else None
                ),
                "excludeFromIdw": bool(station.get("excludeFromIdw")),
            }
            try:
                reverse = reverse_geocode(lat, lon, municipalities)
                row["reverseGeocode"] = reverse
                address = reverse.get("address") or ""
                if not address:
                    raise RuntimeError("GSI returned no usable textual address")
                # Small delay between public-service requests.
                time.sleep(0.6)
                yahoo = query_yahoo_address(address, lat, lon)
                row["yahoo"] = yahoo
                yahoo_minutes = hhmm_to_minutes(yahoo.get("summaryDeparture"))
                stored_minutes = station.get("minutes")
                row["gainMinutes"] = (
                    None
                    if yahoo_minutes is None or stored_minutes is None
                    else yahoo_minutes - int(stored_minutes)
                )
                nearest = yahoo.get("nearestCoordinateMention")
                row["nearestYahooCoordinateDistanceM"] = (
                    nearest.get("distanceFromStationM") if nearest else None
                )
                print(
                    f"[{index:02d}/{len(TARGETS):02d}] {name} {station.get('id')}: "
                    f"address={address!r} stored={row['storedDeparture']} "
                    f"Yahoo={yahoo.get('summaryDeparture')}->{yahoo.get('summaryArrival')} "
                    f"gain={row['gainMinutes']} nearestCoord={row['nearestYahooCoordinateDistanceM']}m"
                )
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
                print(
                    f"[{index:02d}/{len(TARGETS):02d}] {name} {station.get('id')}: {row['error']}"
                )
            rows.append(row)
            time.sleep(0.6)

    OUTPUT_PATH.write_text(
        json.dumps({"targetCount": len(TARGETS), "rows": rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
