#!/usr/bin/env python3
"""Targeted Yahoo! Transit checks for station-name disambiguation failures.

This is intentionally tiny: it performs only a few requests needed to validate known
ambiguous stations, rather than crawling the full station set.
"""
from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

BASE_URL = "https://transit.yahoo.co.jp/search/result"
TARGET = "筑波大学附属高等学校"
SEARCH_DATE = (2026, 8, 28)
ARRIVAL_HOUR = 8
ARRIVAL_MINUTE = 18

CASES = [
    {"id": "n02-004761", "label": "弘明寺(横浜市営)"},
    {"id": "n02-004756", "label": "弘明寺(京急線)"},
]


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript"}:
            self.skip += 1
        elif tag in {"br", "p", "div", "li", "h1", "h2", "h3", "h4", "section", "article"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self.skip:
            self.skip -= 1
        elif tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "section", "article"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)

    def text(self) -> str:
        raw = html.unescape("".join(self.parts))
        lines = []
        for line in raw.splitlines():
            line = re.sub(r"\s+", " ", line).strip()
            if line:
                lines.append(line)
        return "\n".join(lines)


def build_url(origin: str) -> str:
    y, m, d = SEARCH_DATE
    params = {
        "from": origin,
        "to": TARGET,
        "y": str(y),
        "m": f"{m:02d}",
        "d": f"{d:02d}",
        "hh": f"{ARRIVAL_HOUR:02d}",
        "m1": str(ARRIVAL_MINUTE // 10),
        "m2": str(ARRIVAL_MINUTE % 10),
        "type": "4",  # arrival time
        "ticket": "ic",
        "expkind": "1",
        "ws": "1",  # 急いで
        "s": "0",   # earliest arrival / standard ranking
        "al": "0",
        "shin": "1",
        "ex": "1",
        "hb": "0",
        "lb": "1",
        "sr": "0",
    }
    return BASE_URL + "?" + urllib.parse.urlencode(params)


def fetch_case(case: dict) -> dict:
    url = build_url(case["label"])
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/140.0 Safari/537.36",
            "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        body = response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
        final_url = response.geturl()
        status = response.status

    parser = TextExtractor()
    parser.feed(body)
    text = parser.text()
    lines = text.splitlines()

    route_start = next((i for i, line in enumerate(lines) if line == "ルート1"), None)
    excerpt = lines[route_start : route_start + 180] if route_start is not None else lines[:220]
    joined = "\n".join(excerpt)

    summary_match = re.search(r"(\d{1,2}:\d{2})\s*→\s*(\d{1,2}:\d{2})", joined)
    departure_match = re.search(r"(\d{1,2}:\d{2})発", joined)
    arrival_match = re.search(r"(\d{1,2}:\d{2})着", joined)

    return {
        **case,
        "requestedUrl": url,
        "finalUrl": final_url,
        "httpStatus": status,
        "summaryDeparture": summary_match.group(1) if summary_match else None,
        "summaryArrival": summary_match.group(2) if summary_match else None,
        "departure": departure_match.group(1) if departure_match else None,
        "arrival": arrival_match.group(1) if arrival_match else None,
        "excerpt": excerpt,
        "htmlBytes": len(body.encode("utf-8")),
    }


def main() -> None:
    rows = []
    for case in CASES:
        try:
            row = fetch_case(case)
        except Exception as exc:  # report network/block failures without hiding them
            row = {**case, "error": f"{type(exc).__name__}: {exc}"}
        rows.append(row)
        print(f"=== {case['label']} ({case['id']}) ===")
        if "error" in row:
            print(row["error"])
            continue
        print(
            f"status={row['httpStatus']} summary={row['summaryDeparture']}->{row['summaryArrival']} "
            f"departure={row['departure']} arrival={row['arrival']}"
        )
        print(f"final={row['finalUrl']}")
        for line in row["excerpt"][:120]:
            print(line)

    Path("data/yahoo-targeted-audit.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
