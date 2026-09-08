#!/usr/bin/env python3
"""Apply station-data fixes that have been independently confirmed by Yahoo! Transit.

The script is deliberately strict: it only edits a record when its old values match
what the audit found, so a future data refresh cannot be silently overwritten.
"""
from __future__ import annotations

import json
from pathlib import Path

STATIONS_PATH = Path("data/stations.json")


def main() -> None:
    doc = json.loads(STATIONS_PATH.read_text(encoding="utf-8"))
    matches = [row for row in doc["stations"] if row.get("id") == "n02-004761"]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one n02-004761 record, got {len(matches)}")

    row = matches[0]
    expected = {
        "station": "弘明寺",
        "line": "1号線",
        "minutes": 423,
    }
    for key, value in expected.items():
        if row.get(key) != value:
            # If the record is already corrected, make this script idempotent.
            if row.get("minutes") == 421 and str(row.get("route", "")).startswith("横浜市営地下鉄ブルーライン"):
                print("弘明寺(横浜市営): already corrected")
                return
            raise RuntimeError(
                f"n02-004761 precondition failed: {key}={row.get(key)!r}, expected {value!r}"
            )

    old_route = str(row.get("route") or "")
    if not old_route.startswith("京急本線"):
        raise RuntimeError(f"unexpected old 弘明寺 route: {old_route!r}")

    # Yahoo! Transit, 2026-08-28, 08:18 arrival, 急いで:
    # 弘明寺(横浜市営) 07:01 -> 筑波大学附属高等学校 08:17, 1h16.
    row["minutes"] = 421
    row["duration"] = 76
    row["route"] = (
        "横浜市営地下鉄ブルーライン→横浜→JR東海道本線(上野東京ライン)"
        "→東京→東京メトロ丸ノ内線→茗荷谷→徒歩8分"
    )

    STATIONS_PATH.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        "弘明寺(横浜市営): minutes 423->421, duration->76, "
        "route 京急本線->横浜市営地下鉄ブルーライン"
    )


if __name__ == "__main__":
    main()
