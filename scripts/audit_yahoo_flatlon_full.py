#!/usr/bin/env python3
"""Audit every project station from its exact stored point with Yahoo! Transit.

The ordinary station-name lookup is the project's stored baseline.  For the IDW
exclusion rule we additionally need to ask: starting at the *physical point* used by
this map, can a traveller walk / use rail / use a local route bus and depart later
while still reaching 筑波大学附属高等学校 by 08:18?

Yahoo's ``flatlon`` parameter preserves an exact latitude/longitude.  A deliberately
non-station origin label prevents the request from silently collapsing back to the
station-name lookup.  We also require Yahoo's first walking-map origin to reproduce
the requested point (within a small tolerance) before a positive gain can become a
candidate.

The script is shardable so the reproducible audit can finish in a reasonable amount
of time without issuing a large burst from one process.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
from pathlib import Path

from audit_address_origin_resolution import build_flatlon_url, query_yahoo

STATIONS_PATH = Path("data/stations.json")


def hhmm(minutes: object) -> str | None:
    if minutes is None:
        return None
    value = int(minutes)
    return f"{value // 60:02d}:{value % 60:02d}"


def hhmm_to_minutes(value: str | None) -> int | None:
    if not value:
        return None
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def compact_first_route(excerpt: list[str]) -> list[str]:
    """Keep route 1 only and drop obvious UI-only noise."""
    if not excerpt:
        return []
    lines: list[str] = []
    seen_route1 = False
    for raw in excerpt:
        line = str(raw).strip()
        if not line:
            continue
        if line == "ルート1":
            if seen_route1:
                continue
            seen_route1 = True
            lines.append(line)
            continue
        if seen_route1 and line == "ルート2":
            break
        if seen_route1:
            lines.append(line)
    return lines[:120]


def query_with_retry(url: str, lat: float, lng: float, retries: int) -> tuple[dict | None, str | None]:
    last_error: str | None = None
    for attempt in range(retries + 1):
        try:
            return query_yahoo(url, lat, lng), None
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt >= retries:
                break
            time.sleep(2.0 * (attempt + 1))
        except Exception as exc:  # keep one malformed page from aborting a whole shard
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt >= retries:
                break
            time.sleep(2.0 * (attempt + 1))
    return None, last_error


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stations", default=str(STATIONS_PATH))
    parser.add_argument("--output", required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--delay-seconds", type=float, default=0.55)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--origin-tolerance-m", type=float, default=5.0)
    parser.add_argument("--gain-threshold-minutes", type=int, default=1)
    args = parser.parse_args()

    if args.shard_count <= 0 or not (0 <= args.shard_index < args.shard_count):
        raise SystemExit("invalid shard index/count")

    doc = json.loads(Path(args.stations).read_text(encoding="utf-8"))
    all_stations = list(doc.get("stations", []))
    selected = [
        (index, station)
        for index, station in enumerate(all_stations)
        if index % args.shard_count == args.shard_index
    ]

    rows: list[dict] = []
    success = 0
    verified = 0
    candidates = 0
    errors = 0

    for local_index, (global_index, station) in enumerate(selected, start=1):
        station_id = str(station.get("id") or "")
        name = str(station.get("station") or "")
        lat = float(station["lat"])
        lng = float(station["lng"])
        stored_minutes = station.get("minutes")
        point_label = f"{name}駅地点監査-{station_id}"
        url = build_flatlon_url(point_label, lat, lng)

        result, error = query_with_retry(url, lat, lng, args.retries)
        row: dict = {
            "globalIndex": global_index,
            "id": station_id,
            "station": name,
            "lat": lat,
            "lng": lng,
            "storedMinutes": stored_minutes,
            "storedDeparture": hhmm(stored_minutes),
            "alreadyExcluded": bool(station.get("excludeFromIdw")),
        }

        if error or result is None:
            row["error"] = error or "unknown query error"
            errors += 1
            print(
                f"[{local_index:03d}/{len(selected):03d}] {name} {station_id}: ERROR {row['error']}",
                flush=True,
            )
            rows.append(row)
            time.sleep(args.delay_seconds)
            continue

        success += 1
        departure = result.get("summaryDeparture")
        arrival = result.get("summaryArrival")
        departure_minutes = hhmm_to_minutes(departure)
        gain = (
            None
            if departure_minutes is None or stored_minutes is None
            else departure_minutes - int(stored_minutes)
        )
        origin = result.get("firstWalkOrigin") or {}
        origin_distance = origin.get("distanceFromStationM")
        origin_verified = (
            origin_distance is not None
            and float(origin_distance) <= args.origin_tolerance_m
        )
        if origin_verified:
            verified += 1

        candidate = bool(
            origin_verified
            and gain is not None
            and gain >= args.gain_threshold_minutes
        )
        if candidate:
            candidates += 1

        row.update(
            {
                "httpStatus": result.get("httpStatus"),
                "flatlonDeparture": departure,
                "flatlonArrival": arrival,
                "gainMinutes": gain,
                "originDistanceM": origin_distance,
                "originVerified": origin_verified,
                "candidate": candidate,
            }
        )
        if candidate:
            row["firstRoute"] = compact_first_route(result.get("excerpt") or [])

        print(
            f"[{local_index:03d}/{len(selected):03d}] {name} {station_id}: "
            f"stored={row['storedDeparture']} flatlon={departure}->{arrival} "
            f"gain={gain} origin={origin_distance}m verified={origin_verified} "
            f"candidate={candidate}",
            flush=True,
        )
        rows.append(row)
        time.sleep(args.delay_seconds)

    report = {
        "meta": {
            "shardIndex": args.shard_index,
            "shardCount": args.shard_count,
            "projectStations": len(all_stations),
            "stationsInShard": len(selected),
            "queriedSuccessfully": success,
            "originVerified": verified,
            "candidates": candidates,
            "errors": errors,
            "originToleranceM": args.origin_tolerance_m,
            "gainThresholdMinutes": args.gain_threshold_minutes,
            "targetArrival": doc.get("meta", {}).get("targetArrival", "08:18"),
            "searchDate": "2026-08-28",
            "allowedModes": ["徒歩", "鉄道", "路線バス"],
            "highwayBus": False,
            "exactOriginMethod": "Yahoo flatlon + first walking-link fromLat/fromLon verification",
        },
        "rows": rows,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["meta"], ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
