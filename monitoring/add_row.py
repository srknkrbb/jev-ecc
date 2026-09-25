#!/usr/bin/env python3
"""Mevcut bir Grafana panosuna 'Jev karar akisi' satirini ekler (idempotent: ayni baslikli satir varsa yeniler).
Kullanim: add_row.py <pano.json> [jev-row.json]
"""
import json
import sys
from pathlib import Path

dash = Path(sys.argv[1])
rowp = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent / "grafana" / "jev-row.json"
d = json.loads(dash.read_text(encoding="utf-8"))
new = json.loads(rowp.read_text(encoding="utf-8"))
title = new[0]["title"]
panels = d["panels"]
# eski satiri ve altindaki panelleri (bir sonraki satira kadar) at
out, skipping = [], False
for p in panels:
    if p["type"] == "row":
        skipping = (p.get("title") == title)
        if skipping:
            continue
    if not skipping:
        out.append(p)
maxy = max((p["gridPos"]["y"] + p["gridPos"]["h"] for p in out), default=0)
y0 = new[0]["gridPos"]["y"]
maxid = max((p.get("id", 0) for p in out), default=0)
for i, p in enumerate(new):
    p["gridPos"]["y"] = p["gridPos"]["y"] - y0 + maxy
    p["id"] = maxid + 1 + i
d["panels"] = out + new
d["version"] = int(d.get("version", 1)) + 1
dash.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print(f"{dash}: '{title}' satiri y={maxy}'den itibaren {len(new)} panel ile yazildi")
