#!/usr/bin/env python3
"""Jev kararlarini OpenTelemetry (OTLP/HTTP JSON) olarak yayinlar — stdlib-only.

Trace baglami ecc-tui ile uyumludur: ecc-tui `export-otel` bir oturumun trace id'sini
FNV-1a(session_id) ile, oturum kok span'ini FNV-1a("session:<id>") ile turetir (ecc2/src/main.rs).
Hook'lar ECC_SESSION_ID'yi ortamdan alir → Jev span'leri ayni trace'e, kok span'in altina duser;
Grafana/Tempo'da oturum → triage → arac cagrisi → kapi karari tek akis olarak gorunur.

ECC oturumu yoksa (interaktif Claude Code) trace id CLAUDE session_id'den turetilir, ust span yok.
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.request
from typing import Any

FNV_OFFSET = 14695981039346656037
FNV_PRIME = 1099511628211
MASK = (1 << 64) - 1


def fnv1a64(data: bytes, basis: int = FNV_OFFSET) -> int:
    h = basis
    for b in data:
        h ^= b
        h = (h * FNV_PRIME) & MASK
    return h


def trace_id(seed: str) -> str:
    b = seed.encode("utf-8")
    return f"{fnv1a64(b):016x}{fnv1a64(b, FNV_PRIME):016x}"


def span_id(seed: str) -> str:
    return f"{fnv1a64(seed.encode('utf-8')):016x}"


def context(hook_input: dict | None = None) -> dict:
    esid = os.environ.get("ECC_SESSION_ID", "").strip()
    if esid:
        return {"trace_id": trace_id(esid), "parent_span_id": span_id(f"session:{esid}"),
                "ecc_session": esid, "harness": os.environ.get("ECC_HARNESS", "claude")}
    csid = (hook_input or {}).get("session_id") or os.environ.get("CLAUDE_SESSION_ID", "") or "interactive"
    return {"trace_id": trace_id(f"claude:{csid}"), "parent_span_id": None,
            "ecc_session": f"cc-{str(csid)[-8:]}", "harness": "claude"}


def _kv(k: str, v: Any) -> dict:
    if isinstance(v, bool):
        return {"key": k, "value": {"boolValue": v}}
    if isinstance(v, int):
        return {"key": k, "value": {"intValue": str(v)}}
    if isinstance(v, float):
        return {"key": k, "value": {"doubleValue": v}}
    return {"key": k, "value": {"stringValue": str(v)[:600]}}


def _bump_counters(cfg: dict, keys: dict[str, int]) -> dict:
    """Proje-yerel kumulatif sayaclar (.claude/jev/counters.json). Stateless hook'lardan Prometheus
    counter'i uretmenin tek yolu: dosyada biriktir, her seferinde toplam degeri CUMULATIVE sum olarak gonder."""
    import fcntl
    from pathlib import Path
    d = Path(cfg.get("_root", ".")) / ".claude" / "jev"
    try:
        d.mkdir(parents=True, exist_ok=True)
        f = d / "counters.json"
        with open(f, "a+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.seek(0)
            try:
                data = json.loads(fh.read() or "{}")
            except Exception:
                data = {}
            data.setdefault("start_ns", time.time_ns())
            c = data.setdefault("counts", {})
            for k, v in keys.items():
                c[k] = int(c.get(k, 0)) + int(v)
            fh.seek(0)
            fh.truncate()
            fh.write(json.dumps(data))
            fcntl.flock(fh, fcntl.LOCK_UN)
        return data
    except Exception:
        return {"start_ns": time.time_ns(), "counts": {k: v for k, v in keys.items()}}


def _sum_metric(name: str, data: dict, project: str, now_ns: int, prefix: str) -> dict:
    """counters['<prefix>|<hook>|<decision>'] → tek bir monotonic sum metrigi (label: jev.hook, jev.decision)."""
    pts = []
    for k, v in data.get("counts", {}).items():
        parts = k.split("|")
        if len(parts) != 3 or parts[0] != prefix:
            continue
        pts.append({"asInt": str(int(v)), "startTimeUnixNano": str(data.get("start_ns", now_ns)), "timeUnixNano": str(now_ns),
                    "attributes": [_kv("jev.hook", parts[1]), _kv("jev.decision", parts[2]), _kv("project.name", project)]})
    return {"name": name, "sum": {"aggregationTemporality": 2, "isMonotonic": True, "dataPoints": pts}}


def _post(endpoint: str, path: str, body: dict, timeout: float) -> bool:
    try:
        req = urllib.request.Request(endpoint.rstrip("/") + path, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def emit(cfg: dict, *, name: str, attrs: dict[str, Any], start_ns: int, end_ns: int | None = None,
         ok: bool = True, hook_input: dict | None = None, gauges: dict[str, float] | None = None,
         counters: dict[str, int] | None = None) -> bool:
    """Bir span (+ gauge'lar + kumulatif sayaclar) gonderir. Hata durumunda sessizce False doner.
    counters: {"decisions|guard|deny": 1, "tokens|guard|input": 412} gibi; jev_<prefix>_total olarak cikar."""
    o = cfg.get("otel", {})
    if not o.get("enabled", True) or os.environ.get("JEV_OTEL", "").lower() == "off":
        return False
    endpoint = os.environ.get("JEV_OTLP_ENDPOINT") or o.get("endpoint", "http://127.0.0.1:14318")
    timeout = float(o.get("timeout_s", 1.5))
    ctx = context(hook_input)
    end_ns = end_ns or time.time_ns()
    project = cfg.get("project", {}).get("name", "")
    resource = {"attributes": [_kv("service.name", o.get("service_name", "jev")),
                               _kv("project.name", project),
                               _kv("ecc.agent.type", ctx["harness"]),
                               _kv("host.name", socket.gethostname())]}
    base = {"ecc.session.id": ctx["ecc_session"], "ecc.agent.type": ctx["harness"], "project.name": project,
            "jev.mode": os.environ.get("JEV_MODE", "") or ("live" if os.environ.get("TYPESAFE_API_KEY") else "auto")}
    span = {"traceId": ctx["trace_id"], "spanId": os.urandom(8).hex(), "name": name,
            "kind": "SPAN_KIND_INTERNAL", "startTimeUnixNano": str(start_ns), "endTimeUnixNano": str(end_ns),
            "status": {"code": "STATUS_CODE_OK" if ok else "STATUS_CODE_ERROR"},
            "attributes": [_kv(k, v) for k, v in {**base, **attrs}.items() if v is not None]}
    if ctx["parent_span_id"]:
        span["parentSpanId"] = ctx["parent_span_id"]
    sent = _post(endpoint, "/v1/traces", {"resourceSpans": [{"resource": resource,
                 "scopeSpans": [{"scope": {"name": "jev-ecc"}, "spans": [span]}]}]}, timeout)

    # oturum → trace id eslemesi (Grafana degiskeni bunu kullanir) + istege bagli gauge'lar
    g = {"jev_session_info": 1.0}
    g.update(gauges or {})
    labels = [_kv("ecc.session.id", ctx["ecc_session"]), _kv("trace_id", ctx["trace_id"]),
              _kv("project.name", project), _kv("jev.hook", attrs.get("jev.hook", name))]
    metrics = []
    for mname, val in g.items():
        metrics.append({"name": mname, "gauge": {"dataPoints": [{"asDouble": float(val), "timeUnixNano": str(end_ns),
                                                                 "attributes": labels}]}})
    rms = [{"resource": resource, "scopeMetrics": [{"scope": {"name": "jev-ecc"}, "metrics": metrics}]}]
    if counters:
        data = _bump_counters(cfg, counters)
        prefixes = sorted({k.split("|")[0] for k in data.get("counts", {})})
        sums = [_sum_metric(f"jev_{p}_total", data, project, end_ns, p) for p in prefixes]
        # sayaclar proje-geneli: ajan tipi/host label'i olmadan gonder, yoksa ayni toplam birden fazla seride cift sayilir
        rms.append({"resource": {"attributes": [_kv("service.name", o.get("service_name", "jev")), _kv("project.name", project)]},
                    "scopeMetrics": [{"scope": {"name": "jev-ecc-counters"}, "metrics": sums}]})
    _post(endpoint, "/v1/metrics", {"resourceMetrics": rms}, timeout)
    return sent


if __name__ == "__main__":
    import sys
    seed = sys.argv[1] if len(sys.argv) > 1 else "fb6d9766"
    print(json.dumps({"seed": seed, "trace_id": trace_id(seed), "session_span_id": span_id(f"session:{seed}")}))
