#!/usr/bin/env python3
"""Apply *new* exclusions from a complete Yahoo flatlon audit to stations.json.

This script intentionally refuses to run on an incomplete audit.  Existing exclusions
and their evidence are preserved; only newly confirmed exact-point candidates are
annotated.  Run the IDW grid builder after applying the resulting stations.json.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stations", default="data/stations.json")
    parser.add_argument("--audit", default="data/yahoo-flatlon-full-audit.json")
    parser.add_argument("--output", default="data/stations.json")
    args = parser.parse_args()

    stations_path = Path(args.stations)
    audit_path = Path(args.audit)
    output_path = Path(args.output)

    doc = json.loads(stations_path.read_text(encoding="utf-8"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    summary = audit.get("summary") or {}
    if not summary.get("complete"):
        raise SystemExit(f"refusing incomplete flatlon audit: {summary}")
    if summary.get("errors") or summary.get("unverifiedOrigins"):
        raise SystemExit("refusing audit with query/origin-verification failures")

    by_id = {str(row.get("id")): row for row in doc.get("stations", [])}
    applied = []
    already = []
    missing = []

    for candidate in audit.get("candidates", []):
        station_id = str(candidate.get("id") or "")
        station = by_id.get(station_id)
        if station is None:
            missing.append(station_id)
            continue
        if station.get("excludeFromIdw"):
            already.append(station_id)
            # Keep earlier evidence intact, but record that the exact-point Yahoo audit
            # independently reproduced the exclusion.
            station["flatlonAudit"] = {
                "departureFromPoint": candidate.get("flatlonDeparture"),
                "arrival": candidate.get("flatlonArrival"),
                "gainMinutes": candidate.get("gainMinutes"),
                "originDistanceM": candidate.get("originDistanceM"),
                "searchDate": "2026-08-28",
                "routingBasis": "Yahoo!乗換案内 flatlon exact-point audit",
            }
            continue

        gain = int(candidate.get("gainMinutes"))
        point_departure = candidate.get("flatlonDeparture")
        stored_departure = candidate.get("storedDeparture")
        arrival = candidate.get("flatlonArrival")
        station["excludeFromIdw"] = True
        station["idwExclusionReason"] = (
            f"駅の収録座標をYahoo!乗換案内のflatlonで固定し、徒歩・鉄道・路線バスを"
            f"許可して08:18着で検索すると{point_departure}発（{arrival}着）が得られ、"
            f"駅直接検索の{stored_departure}発より{gain}分遅く出発できるため、"
            "直接検索値はこの地点の時間距離を代表しない。"
        )
        station["alternateAccess"] = {
            "accessMode": "徒歩・鉄道・路線バスを許可した地点発経路",
            "departureFromPoint": point_departure,
            "arrival": arrival,
            "gainMinutes": gain,
            "searchDate": "2026-08-28",
            "routingBasis": "Yahoo!乗換案内 flatlon exact-point audit",
            "originVerification": {
                "method": "Yahoo walking-link fromLat/fromLon",
                "distanceM": candidate.get("originDistanceM"),
                "toleranceM": 5.0,
            },
        }
        first_route = candidate.get("firstRoute")
        if first_route:
            station["alternateAccess"]["yahooRouteExcerpt"] = first_route
        station["note"] = (
            f"IDW補間対象外。収録座標からの地点発検索では、駅直接検索より{gain}分遅く"
            "出ても08:18までに到着できる。"
        )
        applied.append(station_id)

    if missing:
        raise SystemExit(f"candidate IDs missing from stations.json: {missing}")

    excluded = sum(1 for row in doc.get("stations", []) if row.get("excludeFromIdw"))
    policy = doc.setdefault("meta", {}).setdefault("idwExclusionPolicy", {})
    policy["excludedStations"] = excluded
    policy["exactPointAudit"] = {
        "method": "Yahoo!乗換案内 flatlon + fromLat/fromLon verification",
        "searchDate": "2026-08-28",
        "targetArrival": "08:18",
        "gainThresholdMinutes": 1,
        "originToleranceM": 5.0,
        "flatlonCandidates": len(audit.get("candidates", [])),
        "newlyApplied": len(applied),
    }

    output_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "flatlonCandidates": len(audit.get("candidates", [])),
                "newlyApplied": len(applied),
                "alreadyExcluded": len(already),
                "excludedStationsAfterApply": excluded,
                "appliedIds": applied,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
