#!/usr/bin/env python3
"""Inspect Yahoo result-page origin form fields for component-specific identifiers."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

from audit_address_origin_resolution import fetch_text
from audit_yahoo_targeted import build_url

LABELS = ['池袋(西武線)', '東京(京葉線)', '新宿(小田急線)', '両国(JR)']


def snippets(body: str, pattern: str, radius: int = 500) -> list[str]:
    out = []
    for m in re.finditer(pattern, body, re.I):
        s = max(0, m.start() - radius)
        e = min(len(body), m.end() + radius)
        chunk = html.unescape(body[s:e]).replace('\n', ' ')
        chunk = re.sub(r'\s+', ' ', chunk)
        out.append(chunk)
        if len(out) >= 30:
            break
    return out


def main() -> None:
    rows = []
    for label in LABELS:
        body, final_url, status = fetch_text(build_url(label))
        row = {
            'label': label,
            'status': status,
            'finalUrl': final_url,
            'fromgidSnippets': snippets(body, r'fromg(?:id|Id|ID)'),
            'fromFieldSnippets': snippets(body, r'name=["\']from(?:gid)?["\']'),
            'stationLabelSnippets': snippets(body, re.escape(label.split('(')[0]), 700),
        }
        rows.append(row)
        print(label, 'fromgid snippets', len(row['fromgidSnippets']), 'from fields', len(row['fromFieldSnippets']))
    Path('data/yahoo-origin-html-diagnostic.json').write_text(
        json.dumps({'rows': rows}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )


if __name__ == '__main__':
    main()
