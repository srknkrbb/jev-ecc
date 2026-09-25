#!/usr/bin/env python3
"""Claude Code PreToolUse hook'u — Jev risk kapisi (jev-ecc plugin).

Katmanlar:
  1) Deterministik: korunan bolgeye yazan Edit/Write/Bash → deny (Jev'e sorulmaz)
  2) Salt okunur Bash (readonly_bash_regex) → gecer (Jev'e sorulmaz)
  3) Gerisi → Jev: action(choice) + irreversible / rule_violation / touches_protected / secret_exposure (noul)
     risk = max(...) → esikler: ≥block deny · ≥confirm ask (guard.ask_mode) · ≥review allow+not · allow
Her adim OTel span olarak (jev.guard) ecc-tui oturum trace'ine eklenir.

Cikti: {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": ..., "permissionDecisionReason": ...}}
Kapatma: JEV_GUARD=off · Proje .claude/jev.json yoksa hook sessizce cikar.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jev_client as jev  # noqa: E402
import otel  # noqa: E402

WRITEISH_BASH = re.compile(
    r"(\bsed\s+-i|\btee\b|>\s*\S|\bmv\b|\bcp\b|\brm\b|\bpatch\b|\bgit\s+checkout\s+--|\bgit\s+restore\b|"
    r"\btruncate\b|\bdd\b|\bchmod\b|\bchown\b|\bln\b|\bcat\s*<<|\bpython3?\s+-c\b|\bperl\s+-[pi])")


def decision(perm: str, reason: str) -> None:
    jev.emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": perm,
                                     "permissionDecisionReason": reason}})


def rel(path: str, root: Path) -> str:
    try:
        p = Path(path)
        if not p.is_absolute():
            p = root / p
        return str(p.resolve().relative_to(root.resolve()))
    except Exception:
        return path


def in_protected(relpath: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(relpath, pat):
            return True
        if pat.endswith("/**") and (relpath == pat[:-3] or relpath.startswith(pat[:-3] + "/")):
            return True
    return False


def summarize_tool(tool: str, ti: dict, root: Path) -> tuple[str, list[str]]:
    paths: list[str] = []
    if tool == "Bash":
        cmd = str(ti.get("command", ""))
        desc = str(ti.get("description", ""))
        for tok in re.findall(r"[\w./-]+\.(?:go|sql|js|ts|svelte|py|sh|yml|yaml|json|toml|env|rs|cs|md)\b|[\w./-]+/", cmd):
            paths.append(rel(tok, root))
        return f"Bash komutu: {cmd}" + (f"\nAciklama: {desc}" if desc else ""), paths
    fp = ti.get("file_path") or ti.get("notebook_path") or ""
    if fp:
        paths.append(rel(str(fp), root))
    if tool == "Write":
        content = str(ti.get("content", ""))
        return f"Write: {paths[0] if paths else fp} ({len(content)} karakter)\nIlk satirlar:\n{content[:300]}", paths
    if tool in ("Edit", "MultiEdit"):
        edits = ti.get("edits") or [ti]
        parts = [f"- ESKI: {str(e.get('old_string',''))[:160]!r}\n  YENI: {str(e.get('new_string',''))[:160]!r}"
                 for e in edits[:3]]
        return f"{tool}: {paths[0] if paths else fp} ({len(edits)} degisiklik)\n" + "\n".join(parts), paths
    return f"{tool}: {json.dumps(ti, ensure_ascii=False)[:400]}", paths


def bucket(risk: float, th: dict) -> str:
    return "block" if risk >= th["block"] else "confirm" if risk >= th["confirm"] else \
        "review" if risk >= th["review"] else "low"


def main() -> None:
    t0 = time.time_ns()
    hi = jev.read_hook_input()
    root = jev.project_dir(hi)
    cfg = jev.load_config(root)
    g = cfg.get("guard", {})
    if not jev.enabled(cfg) or os.environ.get("JEV_GUARD", "").lower() == "off" or not g.get("enabled", True):
        return
    tool = hi.get("tool_name", "")
    ti = hi.get("tool_input", {}) or {}
    if tool not in g.get("tools", []):
        return
    protected = g.get("protected_paths", [])
    text, paths = summarize_tool(tool, ti, root)
    summary = " ".join(text.splitlines()[0].split())[:200]
    th = cfg.get("thresholds", {"review": 0.35, "confirm": 0.6, "block": 0.85})

    def span(decision_label: str, risk: float | None = None, extra: dict | None = None, ok: bool = True):
        attrs = {"jev.hook": "guard", "jev.decision": decision_label, "tool.name": tool,
                 "tool.input_summary": summary, "jev.risk": risk,
                 "jev.risk_bucket": bucket(risk, th) if risk is not None else None}
        attrs.update(extra or {})
        gauges = {}
        if risk is not None:
            gauges["jev_risk"] = risk
        if (extra or {}).get("jev.latency_ms"):
            gauges["jev_latency_ms"] = float(extra["jev.latency_ms"])
        counters = {f"decisions|guard|{decision_label}": 1}
        if (extra or {}).get("jev.input_tokens"):
            counters["tokens|guard|input"] = int(extra["jev.input_tokens"])
        otel.emit(cfg, name="jev.guard", attrs=attrs, start_ns=t0, ok=ok, hook_input=hi,
                  gauges=gauges or None, counters=counters)

    # 1) deterministik korunan bolge
    hit = [p for p in paths if in_protected(p, protected)]
    if hit and (tool != "Bash" or WRITEISH_BASH.search(str(ti.get("command", "")))):
        span("deny-protected", 1.0, {"jev.reason": hit[0]})
        decision("deny", f"[jev-guard] Korunan bolge salt okunur: {hit[0]}")
        return
    # 2) salt okunur bash
    if tool == "Bash" and g.get("skip_readonly_bash", True) and re.match(g.get("readonly_bash_regex", "^$"),
                                                                          str(ti.get("command", ""))):
        span("skip-readonly", 0.0)
        return

    # 3) Jev
    proj = cfg.get("project", {})
    state = {"tool": tool, "operation": text, "touched_paths": paths[:10],
             "project": f"{proj.get('name','')} — {proj.get('description','')}".strip(" —"),
             "project_rules": g.get("project_rules", []), "protected_paths": protected}
    questions = {
        "action": jev.choice(
            "Proje kurallarina gore bu islem icin dogru karar hangisi? `operation` ve `project_rules` alanlarina bak.",
            {"allow": "Rutin, geri alinabilir, kural ihlali yok",
             "review": "Dikkat gerektirir (genis etki, alisilmadik) ama kural ihlali degil",
             "block": "Bir proje kuralini ihlal ediyor ya da geri alinamaz zarar verebilir"}),
        "irreversible": jev.noul("Islem geri alinamaz veri/durum kaybina yol acabilir (volume/veri silme, git hard reset, force push, uretim DB yazma)"),
        "rule_violation": jev.noul("Islem `project_rules` listesindeki kurallardan en az birini ihlal ediyor"),
        "touches_protected": jev.noul("Islem `protected_paths` altindaki bir dosyayi dolayli da olsa degistiriyor (sed/tee/yonlendirme/script uzerinden)"),
        "secret_exposure": jev.noul("Islem parola, token, API anahtari veya .env icerigini loga/ciktiya/aga sizdirabilir"),
    }
    r = jev.ask(state, questions, cfg=cfg, tag=f"guard:{tool}")

    if not r.ok:
        span(r.mode if r.mode in ("dry-run", "off") else "error", None, {"jev.error": r.error}, ok=r.mode != "live")
        if r.mode in ("dry-run", "off"):
            return
        if cfg.get("on_error", "allow") == "ask":
            decision("ask", f"[jev-guard] Jev'e ulasilamadi ({r.error}); onay isteniyor")
        return

    a = r["action"]
    signals = {"block": a.p("block"), "irreversible": r["irreversible"].noul,
               "rule_violation": r["rule_violation"].noul, "touches_protected": r["touches_protected"].noul,
               "secret_exposure": r["secret_exposure"].noul}
    risk = max(signals.values())
    top = max(signals, key=signals.get)
    why = f"[jev-guard] karar={a.choice} guven={a.confidence:.2f} risk={risk:.2f} ({top}) {r.latency_ms}ms"
    extra = {"jev.action": a.choice, "jev.confidence": round(a.confidence, 3), "jev.top_signal": top,
             "jev.latency_ms": r.latency_ms, "jev.input_tokens": int(r.usage.get("input_tokens", 0) or 0)}

    if risk >= th["block"] or (a.choice == "block" and a.confidence >= g.get("min_block_confidence", 0.7)):
        span("deny", risk, extra)
        decision("deny", why + " → ENGELLENDI. Kurali ihlal etmeyen bir yol sec ya da kullaniciya sor.")
    elif risk >= th["confirm"]:
        m = g.get("ask_mode", "ask")
        label = {"allow": "allow-review", "deny": "deny"}.get(m, "ask")
        span(label, risk, extra)
        decision({"allow": "allow", "deny": "deny"}.get(m, "ask"),
                 why + {"allow": " → dikkatli ilerle", "deny": " → belirsiz risk, otomatik modda engellendi"}.get(m, " → onay gerekiyor"))
    elif risk >= th["review"]:
        span("allow-review", risk, extra)
        decision("allow", why + " → izin verildi, gozden gecirilmeli")
    else:
        span("allow", risk, extra)
        decision("allow", why)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.stderr.write(f"[jev-guard] hata, gecirgen: {e}\n")
