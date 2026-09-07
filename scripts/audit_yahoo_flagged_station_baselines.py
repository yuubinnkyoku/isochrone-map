#!/usr/bin/env python3
"""Re-query Yahoo for station complexes flagged by route-position geometry.

A large N02 platform/line offset is not itself an error.  This audit asks Yahoo again
from the logical station name using the project's exact date/arrival settings and
compares the returned latest departure with the stored station value.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from audit_yahoo_targeted import fetch_case

ALIGNMENT_PATH = Path("data/route-origin-alignment-audit.json")
OUTPUT_PATH = Path("data/yahoo-flagged-station-baselines.json")

LABEL_OVERRIDES = {
    "橋本": "橋本(神奈川県)",
    "大手町": "大手町(東京都)",
}


def hhmm_to_minutes(value: str | None) -> int | None:
    if not value:
        return None
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def main() -> None:
    report = json.loads(ALIGNMENT_PATH.read_text(encoding="utf-8"))
    rows = []
    for i, flagged in enumerate(report.get("flagged", []), start=1):
        station = str(flagged["station"])
        label = LABEL_OVERRIDES.get(station, station)
        case = {"id": str(flagged["id"]), "label": label}
        try:
            result = fetch_case(case)
            yahoo_minutes = hhmm_to_minutes(result.get("summaryDeparture"))
            stored_minutes = int(flagged["minutes"])
            result.update(
                {
                    "station": station,
                    "storedMinutes": stored_minutes,
                    "storedDeparture": f"{stored_minutes // 60:02d}:{stored_minutes % 60:02d}",
                    "minuteDelta": None if yahoo_minutes is None else yahoo_minutes - stored_minutes,
                    "storedRoute": flagged.get("route"),
                    "firstSegment": flagged.get("firstSegment"),
                    "routeMemberDistanceM": flagged.get("routeMemberDistanceM"),
                }
            )
        except Exception as exc:
            result = {
                **case,
                "station": station,
                "storedMinutes": flagged.get("minutes"),
                "error": f"{type(exc).__name__}: {exc}",
            }
        rows.append(result)
        print(
            f"[{i:02d}/{len(report.get('flagged', [])):02d}] {station}: "
            f"stored={result.get('storedDeparture', flagged.get('minutes'))} "
            f"Yahoo={result.get('summaryDeparture')}->{result.get('summaryArrival')} "
            f"delta={result.get('minuteDelta')} error={result.get('error')}"
        )
        # Keep the targeted audit polite and low-volume.
        time.sleep(0.7)

    summary = {
        "flaggedStations": len(rows),
        "queriedSuccessfully": sum("error" not in row for row in rows),
        "exactDepartureMatches": sum(row.get("minuteDelta") == 0 for row in rows),
        "departureMismatches": sum(
            row.get("minuteDelta") not in (None, 0) for row in rows if "error" not in row
        ),
        "errors": sum("error" in row for row in rows),
    }
    OUTPUT_PATH.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
