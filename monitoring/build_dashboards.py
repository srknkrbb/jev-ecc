#!/usr/bin/env python3
"""Grafana panolarini uretir:
  grafana/jev-karar-akisi.json   — ayri detay panosu (oturum → trace/node graph → kararlar)
  grafana/jev-row.json           — mevcut bir panoya eklenecek "Jev karar akisi" satiri (add_row.py ile)
Veri kaynaklari: Prometheus uid=prom, Tempo uid=tempo (ajan-izleme yigini ile ayni).
Metrikler: jev_decisions_total{jev_hook,jev_decision,project_name} (kumulatif), jev_risk, jev_task_risk,
           jev_latency_ms, jev_session_info{ecc_session_id,trace_id}; span'ler resource.service.name="jev".
"""
import json
import sys
from pathlib import Path

PROM = {"type": "prometheus", "uid": "prom"}
TEMPO = {"type": "tempo", "uid": "tempo"}
C = {"allow": "#73BF69", "allow-review": "#B7D75A", "ask": "#FF9830", "deny": "#F2495C", "deny-protected": "#C4162A",
     "skip-readonly": "#5794F2", "dry-run": "#8E8E8E", "error": "#8E8E8E",
     "claude-guvenli": "#D97757", "claude-denetci": "#E0A088", "codex-guvenli": "#10A37F",
     "gx10-yazar": "#A352CC", "gx10-devstral": "#C77DE0", "gx10-kodlayici": "#8A2BE2"}
_id = [1000]


def nid():
    _id[0] += 1
    return _id[0]


def stat(title, expr, x, y, w=4, h=4, color="#73BF69", unit="none", decimals=0, desc="", novalue="0"):
    return {"id": nid(), "type": "stat", "title": title, "description": desc, "datasource": PROM,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "targets": [{"datasource": PROM, "refId": "A", "expr": expr, "instant": False, "range": True}],
            "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals, "noValue": novalue,
                                         "color": {"mode": "fixed", "fixedColor": color},
                                         "thresholds": {"mode": "absolute", "steps": [{"color": color, "value": None}]}},
                            "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": "value",
                        "graphMode": "area", "textMode": "value", "justifyMode": "center", "text": {"valueSize": 36}}}


def overrides_by_name(names):
    return [{"matcher": {"id": "byName", "options": n}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": C[n]}}]}
            for n in names if n in C]


def timeseries(title, expr, legend, x, y, w, h, desc="", stacked=True, novalue="Jev karari yok"):
    return {"id": nid(), "type": "timeseries", "title": title, "description": desc, "datasource": PROM,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "targets": [{"datasource": PROM, "refId": "A", "expr": expr, "legendFormat": legend, "range": True}],
            "fieldConfig": {"defaults": {"unit": "none", "decimals": 0, "noValue": novalue, "color": {"mode": "palette-classic"},
                                         "custom": {"drawStyle": "bars", "lineWidth": 1, "fillOpacity": 70, "gradientMode": "none",
                                                    "showPoints": "never", "stacking": {"mode": "normal" if stacked else "none", "group": "A"}}},
                            "overrides": overrides_by_name(C.keys())},
            "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True}, "tooltip": {"mode": "multi", "sort": "desc"}}}


def bargauge(title, expr, legend, x, y, w, h, desc=""):
    return {"id": nid(), "type": "bargauge", "title": title, "description": desc, "datasource": PROM,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "targets": [{"datasource": PROM, "refId": "A", "expr": expr, "legendFormat": legend, "instant": True, "range": False}],
            "fieldConfig": {"defaults": {"unit": "none", "decimals": 0, "noValue": "veri yok", "color": {"mode": "palette-classic"},
                                         "thresholds": {"mode": "absolute", "steps": [{"color": "#5794F2", "value": None}]}},
                            "overrides": overrides_by_name(C.keys())},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "orientation": "horizontal",
                        "displayMode": "gradient", "showUnfilled": True, "namePlacement": "left", "sizing": "auto", "minVizHeight": 16}}


def tempo_table(title, query, x, y, w, h, desc="", limit=50):
    cols = ["ecc.session.id", "jev.hook", "jev.decision", "jev.risk", "jev.profile", "tool.name", "tool.input_summary", "task.head"]
    return {"id": nid(), "type": "table", "title": title, "description": desc, "datasource": TEMPO,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "targets": [{"datasource": TEMPO, "refId": "A", "queryType": "traceql", "tableType": "spans", "limit": limit, "spss": limit,
                         "query": query + " | select(" + ", ".join("span." + c for c in cols) + ")"}],
            "fieldConfig": {"defaults": {"custom": {"align": "left"}}, "overrides": [
                {"matcher": {"id": "byName", "options": "Karar"}, "properties": [{"id": "custom.width", "value": 120},
                    {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                    {"id": "mappings", "value": [{"type": "value", "options": {k: {"color": v, "index": i} for i, (k, v) in enumerate(C.items())}}]}]},
                {"matcher": {"id": "byName", "options": "Zaman"}, "properties": [{"id": "custom.width", "value": 170}]},
                {"matcher": {"id": "byName", "options": "Oturum"}, "properties": [{"id": "custom.width", "value": 95}]},
                {"matcher": {"id": "byName", "options": "Nokta"}, "properties": [{"id": "custom.width", "value": 70}]},
                {"matcher": {"id": "byName", "options": "Risk"}, "properties": [{"id": "custom.width", "value": 60}, {"id": "decimals", "value": 2}]},
                {"matcher": {"id": "byName", "options": "Araç"}, "properties": [{"id": "custom.width", "value": 80}]},
                {"matcher": {"id": "byName", "options": "İz"}, "properties": [{"id": "custom.width", "value": 150}]}]},
            "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}},
            "transformations": [
                {"id": "organize", "options": {"excludeByName": {"Trace Service": True, "service.name": True, "Duration": True, "Trace Name": True, "Trace ID": True},
                    "indexByName": {"Start time": 0, "ecc.session.id": 1, "jev.hook": 2, "jev.decision": 3, "jev.risk": 4, "jev.profile": 5, "tool.name": 6, "tool.input_summary": 7, "task.head": 8, "Span ID": 9},
                    "renameByName": {"Start time": "Zaman", "ecc.session.id": "Oturum", "jev.hook": "Nokta", "jev.decision": "Karar", "jev.risk": "Risk",
                                     "jev.profile": "Profil", "tool.name": "Araç", "tool.input_summary": "İşlem", "task.head": "Görev", "Span ID": "İz"}}},
                {"id": "sortBy", "options": {"sort": [{"field": "Zaman", "desc": True}]}}]}


def row(title, y, collapsed=False):
    return {"id": nid(), "type": "row", "title": title, "collapsed": collapsed, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []}


D = 'jev_decisions_total{jev_hook="guard"'


def overview_panels(y0, proj_filter=""):
    """Ozet satiri (mevcut panoya da eklenir). proj_filter: ', project_name=~\"$project\"' gibi."""
    g = 'jev_decisions_total{jev_hook="guard"' + proj_filter
    p = []
    p.append(stat("İzin · 24s", f'sum(increase({g}, jev_decision=~"allow|allow-review"}}[24h])) or vector(0)', 0, y0, color=C["allow"],
                  desc="Jev'in izin verdiği araç çağrıları (allow + allow-review)"))
    p.append(stat("Onay istendi · 24s", f'sum(increase({g}, jev_decision="ask"}}[24h])) or vector(0)', 4, y0, color=C["ask"],
                  desc="Risk .60–.85 arası: interaktifte onay, claude -p'de izin hatası"))
    p.append(stat("Engellendi · 24s", f'sum(increase({g}, jev_decision=~"deny|deny-protected"}}[24h])) or vector(0)', 8, y0, color=C["deny"],
                  desc="Jev deny + korunan bölge deterministik deny"))
    p.append(stat("Korunan bölge · 24s", f'sum(increase({g}, jev_decision="deny-protected"}}[24h])) or vector(0)', 12, y0, color=C["deny-protected"],
                  desc="Jev'e sorulmadan engellenen korunan-bölge yazmaları"))
    p.append(stat("Jev gecikmesi · ort 1s", 'avg(avg_over_time(jev_latency_ms' + ('{' + proj_filter.lstrip(', ') + '}' if proj_filter else '') + '[1h]))',
                  16, y0, color="#5794F2", unit="ms", novalue="—", desc="Jev API yanıt süresi (dry-run'da veri yok)"))
    p.append(stat("Son risk", 'max(max_over_time(jev_risk' + ('{' + proj_filter.lstrip(', ') + '}' if proj_filter else '') + '[10m]))',
                  20, y0, color="#FF9830", decimals=2, novalue="—", desc="Son 10 dk'daki en yüksek kapı riski (0–1)"))
    p.append(timeseries("Kararlar / 10 dk", f'sum by (jev_decision) (increase({g}}}[10m]))', "{{jev_decision}}", 0, y0 + 4, 12, 8,
                        desc="allow · allow-review · ask · deny · deny-protected · skip-readonly · dry-run"))
    p.append(bargauge("Profil yönlendirme · 24s", 'sum by (jev_decision) (increase(jev_decisions_total{jev_hook="triage"' + proj_filter + '}[24h]))',
                      "{{jev_decision}}", 12, y0 + 4, 6, 8, desc="Triage'ın önerdiği ecc2 profili"))
    p.append(bargauge("İş türü · 24s", 'sum by (jev_decision) (increase(jev_decisions_total{jev_hook="kind"' + proj_filter + '}[24h]))',
                      "{{jev_decision}}", 18, y0 + 4, 6, 8, desc="plan · implement · fix · review · security · config · docs · question"))
    p.append(tempo_table("Son Jev kararları — en yeni üstte", '{ resource.service.name = "jev" }', 0, y0 + 12, 24, 10,
                         desc="Her satır bir karar; İz'e tıklayınca ecc-tui oturumunun tüm akışı açılır"))
    return p


def build_row(y0=0):
    panels = [row("Jev karar akışı", y0)] + overview_panels(y0 + 1)
    return panels


def build_dashboard():
    panels = []
    y = 0
    panels.append({"id": nid(), "type": "text", "title": "", "gridPos": {"x": 0, "y": y, "w": 24, "h": 3},
                   "options": {"mode": "markdown", "content":
                       "**Jev karar akışı** — üstte bir oturum seç: Node graph ve iz görünümü ecc-tui oturumunun span'lerini (oturum → triage → araç çağrıları → kapı kararları) gösterir. "
                       "Kararlar `jev.guard` / `jev.triage` / `jev.review` span'leri ve `jev_decisions_total` sayacından gelir. Anahtar yokken (dry-run) yalnız `dry-run` kararları görünür."}})
    y += 3
    panels.append(row("Oturum akışı — $session", y)); y += 1
    panels.append({"id": nid(), "type": "nodeGraph", "title": "Akış grafiği (trace $trace)", "datasource": TEMPO,
                   "description": "Oturumun span'leri düğüm olarak: ecc2 oturum kökü → araç span'leri, jev.* kararları",
                   "gridPos": {"x": 0, "y": y, "w": 10, "h": 14},
                   "targets": [{"datasource": TEMPO, "refId": "A", "queryType": "traceql", "query": "$trace", "limit": 20}],
                   "options": {"nodes": {"mainStatUnit": "ms"}}})
    panels.append({"id": nid(), "type": "traces", "title": "İz — zaman çizelgesi", "datasource": TEMPO,
                   "description": "Aynı trace'in şelale görünümü; Jev span'leri oturum kökünün altında",
                   "gridPos": {"x": 10, "y": y, "w": 14, "h": 14},
                   "targets": [{"datasource": TEMPO, "refId": "A", "queryType": "traceql", "query": "$trace", "limit": 20}]})
    y += 14
    panels.append(row("Oturumun Jev kararları", y)); y += 1
    panels.append(tempo_table("Kararlar — $session", '{ resource.service.name = "jev" && span.ecc.session.id = "$session" }', 0, y, 16, 10, limit=100))
    panels.append({"id": nid(), "type": "timeseries", "title": "Kapı riski — $session", "datasource": PROM,
                   "description": "Her araç çağrısında Jev'in hesapladığı risk (max sinyal); .35 review · .60 confirm · .85 block",
                   "gridPos": {"x": 16, "y": y, "w": 8, "h": 10},
                   "targets": [{"datasource": PROM, "refId": "A", "expr": 'max(jev_risk{ecc_session_id="$session"})', "legendFormat": "risk", "range": True}],
                   "fieldConfig": {"defaults": {"unit": "none", "min": 0, "max": 1, "decimals": 2, "noValue": "bu oturumda Jev kararı yok",
                                                "color": {"mode": "thresholds"},
                                                "thresholds": {"mode": "absolute", "steps": [{"color": "#73BF69", "value": None}, {"color": "#B7D75A", "value": 0.35},
                                                                                             {"color": "#FF9830", "value": 0.6}, {"color": "#F2495C", "value": 0.85}]},
                                                "custom": {"drawStyle": "points", "pointSize": 8, "showPoints": "always", "lineWidth": 0,
                                                           "thresholdsStyle": {"mode": "dashed"}}}, "overrides": []},
                   "options": {"legend": {"showLegend": False}, "tooltip": {"mode": "single"}}})
    y += 10
    panels.append(row("Genel — tüm oturumlar", y)); y += 1
    panels += overview_panels(y)
    return {"uid": "jev-karar-akisi", "title": "Jev karar akışı — ECC oturumları", "tags": ["ecc", "jev", "ajan"],
            "timezone": "browser", "editable": True, "graphTooltip": 1, "refresh": "10s", "schemaVersion": 39, "version": 1,
            "time": {"from": "now-6h", "to": "now"}, "timepicker": {}, "annotations": {"list": []},
            "links": [{"title": "Ajan İzleme", "type": "link", "url": "/d/ecc-ajan-izleme", "icon": "dashboard"}],
            "templating": {"list": [
                {"name": "session", "label": "Oturum", "type": "query", "datasource": PROM, "refresh": 2, "sort": 0,
                 "query": {"query": "label_values(jev_session_info, ecc_session_id)", "refId": "PrometheusVariableQueryEditor-VariableQuery"},
                 "definition": "label_values(jev_session_info, ecc_session_id)", "includeAll": False, "multi": False,
                 "current": {"text": "", "value": ""}, "options": []},
                {"name": "trace", "label": "Trace", "type": "query", "datasource": PROM, "refresh": 2, "sort": 0, "hide": 2,
                 "query": {"query": 'label_values(jev_session_info{ecc_session_id="$session"}, trace_id)', "refId": "PrometheusVariableQueryEditor-VariableQuery"},
                 "definition": 'label_values(jev_session_info{ecc_session_id="$session"}, trace_id)', "includeAll": False, "multi": False,
                 "current": {"text": "", "value": ""}, "options": []}]},
            "panels": panels}


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "grafana"
    out.mkdir(parents=True, exist_ok=True)
    (out / "jev-karar-akisi.json").write_text(json.dumps(build_dashboard(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / "jev-row.json").write_text(json.dumps(build_row(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"yazildi: {out}/jev-karar-akisi.json, {out}/jev-row.json")
