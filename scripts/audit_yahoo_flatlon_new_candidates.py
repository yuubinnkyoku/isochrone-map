#!/usr/bin/env python3
"""Re-query and inspect the 24 new exact-point IDW exclusion candidates.

The full 1318-station audit identifies candidates only from timing + exact-origin
verification.  This second pass keeps the detailed first route so the evidence can be
reviewed before mutating stations.json.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from audit_address_origin_resolution import build_flatlon_url, query_yahoo

STATIONS_PATH = Path("data/stations.json")
OUTPUT_PATH = Path("data/yahoo-flatlon-new-candidates.json")

CANDIDATE_IDS = [
    "n02-004334",          # 北山田
    "tokyo23-003259",      # 宮ノ前
    "kumanomae",           # 熊野前
    "n02-003609",          # 京成船橋
    "tokyo23-003415",      # 千石
    "tokyo23-003514",      # 早稲田(都電)
    "nagareyama",          # 流山
    "tokyo23-003305",      # 豊島園
    "tokyo23-003946",      # モノレール浜松町
    "tokyo23-003370",      # 三ノ輪橋
    "n02-003934",          # 京王八王子
    "tokyo23-003560",      # 仲御徒町
    "tokyo23-003434",      # 千駄木
    "n02-004308",          # 向河原
    "tokyo23-003378",      # 大塚駅前
    "n02-004540",          # 大川
    "n02-003037",          # 小林
    "tokyo23-003351",      # 巣鴨新田
    "n02-003653",          # 新小金井
    "tokyo23-003530",      # 田原町
    "n02-004039",          # 穴川
    "tokyo23-003886",      # 築地市場
    "tokyo23-003357",      # 荒川一中前
    "tokyo23-003141",      # 赤羽岩淵
]


def minute_of_day(value: str | None) -> int | None:
    if not value:
        return None
    h, m = value.split(":", 1)
    return int(h) * 60 + int(m)


def stored_label(minutes: int) -> str:
    if minutes < 0:
        value = minutes % 1440
        return f"前日{value // 60:02d}:{value % 60:02d}"
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def detailed_route(excerpt: list[str]) -> list[str]:
    indexes = [i for i, line in enumerate(excerpt) if line == "ルート1"]
    if not indexes:
        return excerpt[:160]
    # Yahoo first shows a compact list of routes, then a second "ルート1" before
    # the detailed itinerary. Prefer that second occurrence when available.
    start = indexes[1] if len(indexes) >= 2 else indexes[0]
    out: list[str] = []
    for line in excerpt[start:]:
        if out and line == "ルート2":
            break
        out.append(line)
    return out[:180]


def main() -> None:
    doc = json.loads(STATIONS_PATH.read_text(encoding="utf-8"))
    by_id = {str(row.get("id")): row for row in doc.get("stations", [])}
    missing = [station_id for station_id in CANDIDATE_IDS if station_id not in by_id]
    if missing:
        raise SystemExit(f"candidate IDs missing from stations.json: {missing}")

    rows = []
    for index, station_id in enumerate(CANDIDATE_IDS, start=1):
        station = by_id[station_id]
        name = str(station.get("station"))
        lat = float(station["lat"])
        lng = float(station["lng"])
        stored = int(station["minutes"])
        label = f"{name}駅地点監査-{station_id}"
        result = query_yahoo(build_flatlon_url(label, lat, lng), lat, lng)
        dep = minute_of_day(result.get("summaryDeparture"))
        gain = None if dep is None else dep - stored
        origin = result.get("firstWalkOrigin") or {}
        origin_distance = origin.get("distanceFromStationM")
        route = detailed_route(result.get("excerpt") or [])
        route_text = "→".join(route)
        prohibited_bus = any(token in route_text for token in ("高速バス", "夜行バス"))
        row = {
            "id": station_id,
            "station": name,
            "storedMinutes": stored,
            "storedDeparture": stored_label(stored),
            "flatlonDeparture": result.get("summaryDeparture"),
            "flatlonArrival": result.get("summaryArrival"),
            "gainMinutes": gain,
            "originDistanceM": origin_distance,
            "originVerified": origin_distance is not None and float(origin_distance) <= 5.0,
            "prohibitedBusDetected": prohibited_bus,
            "route": route,
        }
        row["confirmed"] = bool(
            row["originVerified"]
            and gain is not None
            and gain >= 1
            and not prohibited_bus
        )
        rows.append(row)
        print(
            f"[{index:02d}/{len(CANDIDATE_IDS):02d}] {name}: "
            f"{row['storedDeparture']} -> {row['flatlonDeparture']} "
            f"gain={gain} origin={origin_distance}m confirmed={row['confirmed']}",
            flush=True,
        )
        print("  " + " / ".join(route[:60]), flush=True)
        time.sleep(0.65)

    summary = {
        "candidateCount": len(CANDIDATE_IDS),
        "confirmed": sum(1 for row in rows if row["confirmed"]),
        "rejected": sum(1 for row in rows if not row["confirmed"]),
        "originVerified": sum(1 for row in rows if row["originVerified"]),
        "prohibitedBusDetected": sum(1 for row in rows if row["prohibitedBusDetected"]),
    }
    OUTPUT_PATH.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["rejected"]:
        raise SystemExit("one or more candidates failed the confirmation pass")


if __name__ == "__main__":
    main()
