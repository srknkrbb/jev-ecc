#!/usr/bin/env python3
"""Denetim bulgularini Jev ile siniflandir: severity, eyleme donusturulebilirlik, kapsam; yerel tekillestirme.

Girdi: markdown (## / ### basliklari ya da "- " / "1." maddeleri bulgu sayilir) veya JSON listesi [{"title","body"}].
Kullanim: review_triage.py BULGULAR.md [--scope "madde tarifi"] [--min high] [--json] [--out cikti.md] [--root DIR]
Cikis kodu: 0; --fail-on critical|high verilirse o seviyede bulgu varsa 3.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jev_client as jev  # noqa: E402
import otel  # noqa: E402

SEV_ORDER = ["critical", "high", "medium", "low", "noise"]


def parse_findings(text: str) -> list[dict]:
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [{"title": str(d.get("title", ""))[:200], "body": str(d.get("body", ""))[:1200]} for d in data]
    except Exception:
        pass
    items: list[dict] = []
    heads = [p for p in re.split(r"(?m)^(?=#{2,4}\s)", text) if p.startswith("#")]
    if len(heads) >= 2:
        for p in heads:
            lines = p.strip().splitlines()
            items.append({"title": lines[0].lstrip("# ").strip()[:200], "body": "\n".join(lines[1:])[:1200]})
        return items
    cur = None
    for line in text.splitlines():
        m = re.match(r"^\s*(?:[-*]|\d+[.)])\s+(.*)", line)
        if m:
            if cur:
                items.append(cur)
            cur = {"title": re.sub(r"\*\*", "", m.group(1)).strip()[:200], "body": ""}
        elif cur and line.strip():
            cur["body"] += line.strip() + "\n"
    if cur:
        items.append(cur)
    return [i for i in items if len(i["title"]) > 8]


def dedupe(items: list[dict], ratio: float) -> list[dict]:
    kept: list[dict] = []
    for it in items:
        key = re.sub(r"\W+", " ", it["title"].lower()).strip()
        dup = next((k for k in kept if difflib.SequenceMatcher(None, key, k["_key"]).ratio() >= ratio), None)
        if dup:
            dup.setdefault("duplicates", []).append(it["title"])
        else:
            it["_key"] = key
            kept.append(it)
    for k in kept:
        k.pop("_key", None)
    return kept


def classify(items: list[dict], scope: str | None, chunk: int, cfg: dict | None = None) -> list[dict]:
    cfg = cfg or jev.load_config()
    for start in range(0, len(items), chunk):
        batch = items[start:start + chunk]
        state = {"findings": {f"f{i}": {"title": it["title"], "body": it["body"]} for i, it in enumerate(batch)}}
        if scope:
            state["scope"] = scope
        q: dict = {}
        for i in range(len(batch)):
            q[f"sev_f{i}"] = jev.choice(f"`findings.f{i}` bulgusunun ciddiyeti", {
                "critical": "Veri kaybi, kimlik dogrulama atlatma, RCE, uretimde kesinti",
                "high": "Guvenlik acigi ya da yanlis sonuc ureten hata; bu paket icinde kapatilmali",
                "medium": "Gercek ama sinirli etkili hata; planlanmali",
                "low": "Kod kalitesi / kucuk iyilestirme",
                "noise": "Yanlis pozitif, stil tercihi ya da bulgu degil"})
            q[f"act_f{i}"] = jev.noul(f"`findings.f{i}` icin somut, uygulanabilir bir duzeltme tarif ediliyor")
            if scope:
                q[f"scope_f{i}"] = jev.noul(f"`findings.f{i}` `scope` ile tarif edilen maddenin kapsaminda")
        t0 = time.time_ns()
        r = jev.ask(state, q, cfg=cfg, tag=f"review:{len(batch)}")
        for i, it in enumerate(batch):
            if not r.ok:
                it.update({"severity": None, "confidence": 0, "actionable": None, "in_scope": None, "mode": r.mode})
                continue
            s = r[f"sev_f{i}"]
            it.update({"severity": s.choice, "confidence": round(s.confidence, 3),
                       "probs": {k: round(v, 3) for k, v in s.probabilities.items()},
                       "actionable": round(r[f"act_f{i}"].noul, 3),
                       "in_scope": round(r[f"scope_f{i}"].noul, 3) if scope else None})
        counts = {s: sum(1 for it in batch if it.get("severity") == s) for s in SEV_ORDER}
        otel.emit(cfg, name="jev.review", start_ns=t0, ok=r.ok or r.mode != "live",
                  attrs={"jev.hook": "review", "jev.decision": ("critical" if counts["critical"] else "high" if counts["high"]
                                                              else "ok") if r.ok else r.mode,
                         "jev.findings": len(batch), **{f"jev.sev.{k}": v for k, v in counts.items()},
                         "jev.latency_ms": r.latency_ms, "jev.input_tokens": int(r.usage.get("input_tokens", 0) or 0)},
                  gauges={"jev_review_findings": float(len(batch)), "jev_review_critical": float(counts["critical"]),
                          "jev_review_high": float(counts["high"]), "jev_latency_ms": float(r.latency_ms or 0)},
                  counters={**{f"decisions|review|{k}": v for k, v in counts.items() if v},
                            **({"tokens|review|input": int(r.usage.get("input_tokens", 0) or 0)} if r.ok else {})})
    return items


def render(items: list[dict], min_sev: str | None) -> str:
    cut = SEV_ORDER.index(min_sev) if min_sev else len(SEV_ORDER)
    counts = {s: sum(1 for it in items if it.get("severity") == s) for s in SEV_ORDER}
    lines = ["# Jev review triage", "",
             "Ozet: " + " · ".join(f"{k}={v}" for k, v in counts.items()) + f" · toplam={len(items)}", ""]
    for it in items:
        sev = it.get("severity") or "?"
        if sev in SEV_ORDER and SEV_ORDER.index(sev) > cut:
            continue
        flag = ""
        if it.get("in_scope") is not None and it["in_scope"] < 0.4:
            flag += " [kapsam disi]"
        if it.get("actionable") is not None and it["actionable"] < 0.4:
            flag += " [eylem tarifi yok]"
        lines.append(f"- **{sev.upper()}** ({it.get('confidence', 0)}) {it['title']}{flag}")
        if it.get("duplicates"):
            lines.append(f"  - tekrar: {len(it['duplicates'])} benzer bulgu birlestirildi")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--scope")
    ap.add_argument("--min", choices=SEV_ORDER)
    ap.add_argument("--fail-on", choices=["critical", "high"])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--root")
    a = ap.parse_args(argv)
    cfg = jev.load_config(Path(a.root) if a.root else None)
    rc = cfg.get("review", {})
    items = dedupe(parse_findings(Path(a.file).read_text(encoding="utf-8", errors="ignore")), float(rc.get("dedupe_ratio", 0.82)))
    items = classify(items, a.scope, int(rc.get("chunk", 40)), cfg)
    items.sort(key=lambda x: (SEV_ORDER.index(x["severity"]) if x.get("severity") in SEV_ORDER else 99,
                              -(x.get("confidence") or 0)))
    out = json.dumps(items, ensure_ascii=False, indent=1) if a.json else render(items, a.min)
    if a.out:
        Path(a.out).write_text(out, encoding="utf-8")
        print(f"yazildi: {a.out} ({len(items)} bulgu)")
    else:
        print(out)
    if a.fail_on:
        lim = SEV_ORDER.index(a.fail_on)
        if any(it.get("severity") in SEV_ORDER and SEV_ORDER.index(it["severity"]) <= lim for it in items):
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
