#!/usr/bin/env python3
"""Combine shard outputs from the full Yahoo flatlon station-point audit."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SHARD_NAME_RE = re.compile(r"(?:flatlon-)?shard-(\d+)\.json")


def discover_shard_files(input_dir: Path) -> list[Path]:
    """Find both local ``shard-N`` and workflow ``flatlon-shard-N`` outputs.

    The GitHub Actions workflow historically uploaded ``flatlon-shard-N.json`` while
    this combiner looked only for ``shard-N.json``.  Treat the two names as aliases
    for the same shard.  If both aliases exist, accept them only when their bytes are
    identical so a copied alias cannot accidentally double-count stations.
    """
    by_index: dict[int, Path] = {}
    for path in sorted(input_dir.glob("*.json")):
        match = SHARD_NAME_RE.fullmatch(path.name)
        if not match:
            continue
        index = int(match.group(1))
        previous = by_index.get(index)
        if previous is None:
            by_index[index] = path
            continue
        if previous.read_bytes() != path.read_bytes():
            raise SystemExit(
                f"conflicting files for shard {index}: {previous.name} and {path.name}"
            )
    return [by_index[index] for index in sorted(by_index)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--json-output", required=True)
    parser.add_argument("--markdown-output", required=True)
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    files = discover_shard_files(input_dir)
    if not files:
        raise SystemExit(f"no shard files under {input_dir}")

    rows: list[dict] = []
    metas = []
    for path in files:
        doc = json.loads(path.read_text(encoding="utf-8"))
        metas.append(doc.get("meta", {}))
        rows.extend(doc.get("rows", []))

    rows.sort(key=lambda row: int(row.get("globalIndex", 10**9)))
    duplicate_ids = []
    seen: set[str] = set()
    for row in rows:
        station_id = str(row.get("id") or "")
        if station_id in seen:
            duplicate_ids.append(station_id)
        seen.add(station_id)

    candidates = [row for row in rows if row.get("candidate")]
    candidates.sort(
        key=lambda row: (
            -int(row.get("gainMinutes") or 0),
            str(row.get("station") or ""),
            str(row.get("id") or ""),
        )
    )
    errors = [row for row in rows if row.get("error")]
    unverified = [
        row
        for row in rows
        if not row.get("error") and not row.get("originVerified")
    ]
    existing = [row for row in rows if row.get("alreadyExcluded")]
    existing_ids = {row.get("id") for row in existing}
    candidate_ids = {row.get("id") for row in candidates}
    new_candidates = [row for row in candidates if row.get("id") not in existing_ids]
    existing_not_flatlon = [row for row in existing if row.get("id") not in candidate_ids]

    expected = max((int(meta.get("projectStations") or 0) for meta in metas), default=0)
    summary = {
        "shards": len(files),
        "expectedProjectStations": expected,
        "rows": len(rows),
        "uniqueStationIds": len(seen),
        "duplicateStationIds": sorted(set(duplicate_ids)),
        "queriedSuccessfully": sum(1 for row in rows if not row.get("error")),
        "originVerified": sum(1 for row in rows if row.get("originVerified")),
        "errors": len(errors),
        "unverifiedOrigins": len(unverified),
        "flatlonCandidates": len(candidates),
        "alreadyExcluded": len(existing),
        "candidateOverlapExisting": len(candidate_ids & existing_ids),
        "newFlatlonCandidates": len(new_candidates),
        "existingNotReproducedByFlatlon": len(existing_not_flatlon),
        "complete": (
            len(rows) == expected
            and len(seen) == expected
            and not duplicate_ids
            and not errors
            and not unverified
        ),
    }

    report = {
        "summary": summary,
        "candidates": candidates,
        "newCandidates": new_candidates,
        "existingNotReproducedByFlatlon": existing_not_flatlon,
        "errors": errors,
        "unverifiedOrigins": unverified,
        "rows": rows,
    }
    Path(args.json_output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    md = [
        "# Yahoo flatlon 全駅監査",
        "",
        "N02/stations.json の収録座標を Yahoo!乗換案内の `flatlon` に直接渡し、",
        "徒歩・鉄道・路線バスを許可した08:18着検索を全収録駅で再実行した監査。",
        "Yahooの最初の徒歩リンクの `fromLat/fromLon` が要求座標と5m以内で一致した場合だけ候補に採用する。",
        "",
        "## 集計",
        "",
    ]
    for key, value in summary.items():
        md.append(f"- {key}: **{value}**")

    md.extend([
        "",
        "## IDW除外候補（flatlonで直接検索値より1分以上遅く出発可能）",
        "",
        "| 駅 | id | 保存値 | exact-point | 改善 | 既存除外 | 起点誤差(m) |",
        "|---|---|---:|---:|---:|---|---:|",
    ])
    for row in candidates:
        md.append(
            f"| {row.get('station')} | `{row.get('id')}` | {row.get('storedDeparture')} | "
            f"{row.get('flatlonDeparture')} | +{row.get('gainMinutes')}分 | "
            f"{'yes' if row.get('alreadyExcluded') else 'no'} | {row.get('originDistanceM')} |"
        )
    if not candidates:
        md.append("| なし | - | - | - | - | - | - |")

    if errors:
        md.extend(["", "## エラー", ""])
        for row in errors:
            md.append(f"- {row.get('station')} (`{row.get('id')}`): {row.get('error')}")
    if unverified:
        md.extend(["", "## 起点を検証できなかった駅", ""])
        for row in unverified:
            md.append(
                f"- {row.get('station')} (`{row.get('id')}`): originDistance={row.get('originDistanceM')}"
            )

    Path(args.markdown_output).write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for row in candidates:
        print(
            "CANDIDATE",
            row.get("station"),
            row.get("id"),
            row.get("storedDeparture"),
            row.get("flatlonDeparture"),
            f"+{row.get('gainMinutes')}",
            "existing" if row.get("alreadyExcluded") else "new",
        )

    if len(rows) != expected or len(seen) != expected or duplicate_ids:
        raise SystemExit("combined shard coverage is incomplete or duplicated")


if __name__ == "__main__":
    main()
