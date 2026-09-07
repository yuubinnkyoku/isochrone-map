#!/usr/bin/env python3
"""Validate point-origin routing before using it for IDW exclusions.

Yahoo! Transit accepts ordinary station names, but the exclusion audit needs a route
from the *station point* so that walking to another station or taking a local bus can
compete with boarding there directly.  A broad textual address is unsafe: Yahoo can
resolve a block address hundreds of metres away (or even to a POI inside that block).

For known candidates and the Disney outliers this script therefore compares two
methods:

* GSI reverse-geocoded block address -> Yahoo (diagnostic only), and
* Yahoo ``flatlon`` with the exact N02 latitude/longitude (candidate replacement).

The returned Yahoo HTML is also inspected for the actual ``fromLat``/``fromLon`` used
by its first walking leg.  This lets us prove whether the requested point survived
Yahoo's own location resolution instead of guessing from nearby map links.
"""
from __future__ import annotations

import html
import json
import math
import re
import time
import unicodedata
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


def name_key(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    # NFKC does not consistently make the two Japanese middle-dot codepoints equal.
    return text.replace("･", "・").strip()


def load_municipalities() -> dict[str, tuple[str, str]]:
    body, _, _ = fetch_text(GSI_MUNI)
    mapping: dict[str, tuple[str, str]] = {}
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


def build_flatlon_url(label: str, lat: float, lon: float) -> str:
    """Add Yahoo's point-location parameter while keeping project search settings."""
    base = build_url(label)
    parsed = urllib.parse.urlsplit(base)
    params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    # Replace rather than append if Yahoo changes build_url in the future.
    params = [(key, value) for key, value in params if key not in {"flatlon", "fromgid"}]
    params.extend(
        [
            ("fromgid", ""),
            ("flatlon", f"{lat:.8f},{lon:.8f},{label}"),
        ]
    )
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(params), parsed.fragment)
    )


def extract_walk_origins(body: str, station_lat: float, station_lon: float) -> list[dict]:
    """Extract actual origins from Yahoo map walking links in document order."""
    decoded = html.unescape(body)
    for _ in range(2):
        decoded = urllib.parse.unquote(decoded)
    # JSON embedded in the page uses escaped ampersands after URL decoding.
    decoded = decoded.replace("\\u0026", "&")

    pattern = re.compile(
        r'(?:https?://map\.yahoo\.co\.jp/route/walk\?)?'
        r'from=(?P<label>[^&"<>]{0,300})&fromGid=[^&"<>]*&fromLat=(?P<lat>-?\d{2}\.\d+)'
        r'&fromLon=(?P<lon>-?\d{3}\.\d+)',
        re.IGNORECASE,
    )
    rows: list[dict] = []
    seen: set[tuple[str, float, float]] = set()
    for match in pattern.finditer(decoded):
        lat = float(match.group("lat"))
        lon = float(match.group("lon"))
        label = match.group("label").strip()
        key = (label, round(lat, 8), round(lon, 8))
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "label": label,
                "lat": lat,
                "lng": lon,
                "distanceFromStationM": round(
                    haversine_m(station_lat, station_lon, lat, lon), 2
                ),
                "documentOffset": match.start(),
            }
        )
    return rows


def extract_address_options(body: str, station_lat: float, station_lon: float) -> list[dict]:
    """Capture Yahoo autocomplete candidates to explain broad-address misresolution."""
    decoded = html.unescape(body)
    pattern = re.compile(
        r'<option\s+value="(?P<lat>-?\d{2}\.\d+),(?P<lon>-?\d{3}\.\d+),(?P<label>[^"]*)"'
        r'[^>]*data-gid="(?P<gid>[^"]*)"[^>]*>',
        re.IGNORECASE,
    )
    rows = []
    for match in pattern.finditer(decoded):
        lat = float(match.group("lat"))
        lon = float(match.group("lon"))
        if not (20 <= lat <= 50 and 120 <= lon <= 155):
            continue
        rows.append(
            {
                "label": html.unescape(match.group("label")),
                "gid": match.group("gid"),
                "lat": lat,
                "lng": lon,
                "distanceFromStationM": round(
                    haversine_m(station_lat, station_lon, lat, lon), 2
                ),
            }
        )
    return sorted(rows, key=lambda row: row["distanceFromStationM"])[:30]


def query_yahoo(url: str, station_lat: float, station_lon: float) -> dict:
    body, final_url, status = fetch_text(url)
    parser = TextExtractor()
    parser.feed(body)
    lines = parser.text().splitlines()
    route_start = next((i for i, line in enumerate(lines) if line == "ルート1"), None)
    excerpt = lines[route_start : route_start + 140] if route_start is not None else lines[:180]
    joined = "\n".join(excerpt)
    summary = re.search(r"(\d{1,2}:\d{2})\s*→\s*(\d{1,2}:\d{2})", joined)
    walk_origins = extract_walk_origins(body, station_lat, station_lon)
    return {
        "requestedUrl": url,
        "finalUrl": final_url,
        "httpStatus": status,
        "summaryDeparture": summary.group(1) if summary else None,
        "summaryArrival": summary.group(2) if summary else None,
        "excerpt": excerpt,
        "walkOrigins": walk_origins[:30],
        "firstWalkOrigin": walk_origins[0] if walk_origins else None,
        "addressOptions": extract_address_options(body, station_lat, station_lon),
        "htmlBytes": len(body.encode("utf-8")),
    }


def hhmm_to_minutes(value: str | None) -> int | None:
    if not value:
        return None
    h, m = value.split(":", 1)
    return int(h) * 60 + int(m)


def gain_minutes(result: dict, stored_minutes: object) -> int | None:
    yahoo_minutes = hhmm_to_minutes(result.get("summaryDeparture"))
    if yahoo_minutes is None or stored_minutes is None:
        return None
    return yahoo_minutes - int(stored_minutes)


def main() -> None:
    doc = json.loads(STATIONS_PATH.read_text(encoding="utf-8"))
    by_name: dict[str, list[dict]] = {}
    for station in doc["stations"]:
        by_name.setdefault(name_key(station.get("station")), []).append(station)

    municipalities = load_municipalities()
    print(f"GSI municipality entries={len(municipalities)}")
    rows = []

    for index, requested_name in enumerate(TARGETS, start=1):
        matches = by_name.get(name_key(requested_name), [])
        if not matches:
            row = {"station": requested_name, "error": "station-not-found"}
            rows.append(row)
            print(f"[{index:02d}/{len(TARGETS):02d}] {requested_name}: station-not-found")
            continue
        if len(matches) > 1:
            print(
                f"[{index:02d}/{len(TARGETS):02d}] {requested_name}: "
                f"{len(matches)} project records"
            )

        for station in matches:
            lat = float(station["lat"])
            lon = float(station["lng"])
            stored_minutes = station.get("minutes")
            display_name = str(station.get("station"))
            row = {
                "id": station.get("id"),
                "station": display_name,
                "lat": lat,
                "lng": lon,
                "storedMinutes": stored_minutes,
                "storedDeparture": (
                    f"{int(stored_minutes) // 60:02d}:{int(stored_minutes) % 60:02d}"
                    if stored_minutes is not None
                    else None
                ),
                "excludeFromIdw": bool(station.get("excludeFromIdw")),
            }
            try:
                reverse = reverse_geocode(lat, lon, municipalities)
                row["reverseGeocode"] = reverse
                address = reverse.get("address") or ""
                if address:
                    time.sleep(0.6)
                    address_result = query_yahoo(build_url(address), lat, lon)
                    row["addressYahoo"] = address_result
                    row["addressGainMinutes"] = gain_minutes(address_result, stored_minutes)
                    address_origin = address_result.get("firstWalkOrigin")
                    row["addressResolvedOriginDistanceM"] = (
                        address_origin.get("distanceFromStationM") if address_origin else None
                    )

                # Use a deliberately non-station label so a successful result cannot be
                # explained by Yahoo silently treating `from` as the direct station.
                point_label = f"{display_name}駅地点"
                time.sleep(0.6)
                flatlon_result = query_yahoo(build_flatlon_url(point_label, lat, lon), lat, lon)
                row["flatlonYahoo"] = flatlon_result
                row["flatlonGainMinutes"] = gain_minutes(flatlon_result, stored_minutes)
                flatlon_origin = flatlon_result.get("firstWalkOrigin")
                row["flatlonResolvedOriginDistanceM"] = (
                    flatlon_origin.get("distanceFromStationM") if flatlon_origin else None
                )

                print(
                    f"[{index:02d}/{len(TARGETS):02d}] {display_name} {station.get('id')}: "
                    f"stored={row['storedDeparture']} "
                    f"address={address!r} -> {address_result.get('summaryDeparture') if address else None} "
                    f"gain={row.get('addressGainMinutes')} "
                    f"originDist={row.get('addressResolvedOriginDistanceM')}m; "
                    f"flatlon={flatlon_result.get('summaryDeparture')} "
                    f"gain={row.get('flatlonGainMinutes')} "
                    f"originDist={row.get('flatlonResolvedOriginDistanceM')}m"
                )
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
                print(
                    f"[{index:02d}/{len(TARGETS):02d}] {display_name} "
                    f"{station.get('id')}: {row['error']}"
                )
            rows.append(row)
            time.sleep(0.6)

    OUTPUT_PATH.write_text(
        json.dumps({"targetCount": len(TARGETS), "rows": rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
