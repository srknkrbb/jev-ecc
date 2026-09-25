#!/usr/bin/env python3
"""Gorev triage + profil yonlendirme + hafiza ilgililik — tek Jev isteginde (speculative fan-out).

hook : UserPromptSubmit (stdin JSON) → additionalContext + OTel span (jev.triage)
CLI  : triage.py --task "..." | --task-file f  [--json] [--profile-only] [--default PROFIL] [--allowed a,b,c] [--min-confidence 0.6]
       --profile-only : yalniz secilen profil adini basar (pipeline betikleri icin)
       --default      : Jev cevap vermezse / guven dusukse / secim izinli kumede degilse basilacak profil
       --allowed      : Jev'in secebilecegi profil alt kumesi (pipeline asamasina gore kisitla)
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jev_client as jev  # noqa: E402
import otel  # noqa: E402


def collect_sections(root: Path, tcfg: dict) -> list[dict]:
    out: list[dict] = []
    maxn = int(tcfg.get("max_sections", 40))
    per_file = int(tcfg.get("max_sections_per_file", 10))
    chars = int(tcfg.get("section_chars", 600))
    for pattern in tcfg.get("memory_sources", []):
        pat = os.path.expanduser(pattern)
        if not os.path.isabs(pat):
            pat = str(root / pat)
        for f in sorted(glob.glob(pat, recursive=True)):
            try:
                text = Path(f).read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            relf = os.path.relpath(f, root)
            n_file = 0
            for part in re.split(r"(?m)^(?=#{1,3}\s)", text):
                if n_file >= per_file:
                    break
                part = part.strip()
                if len(part) < 40:
                    continue
                title = part.splitlines()[0].lstrip("# ").strip()[:120]
                out.append({"file": relf, "title": title, "text": part[:chars]})
                n_file += 1
                if len(out) >= maxn:
                    return out
    return out


def run_triage(task: str, root: Path, *, allowed: list[str] | None = None, hook_input: dict | None = None) -> dict:
    t0 = time.time_ns()
    cfg = jev.load_config(root)
    t = cfg.get("triage", {})
    profiles = dict(t.get("profiles", {}))
    if allowed:
        profiles = {k: v for k, v in profiles.items() if k in allowed} or profiles
    kinds = t.get("task_kinds", {})
    sections = collect_sections(root, t)
    proj = cfg.get("project", {})

    state = {"task": task, "project": f"{proj.get('name','')} — {proj.get('description','')}".strip(" —"),
             "available_profiles": profiles,
             "memory_sections": {f"m{i}": {"file": s["file"], "title": s["title"], "text": s["text"]}
                                 for i, s in enumerate(sections)}}
    questions = {
        "kind": jev.choice("`task` hangi tur is?", kinds),
        "risk": jev.score("`task` yerine getirilirken proje icin risk seviyesi",
                          ["Dusuk: salt okunur ya da kolay geri alinir",
                           "Orta: kod/konfig degisir, testle dogrulanir",
                           "Yuksek: veri hatti, migration, guvenlik ya da genis etkili degisiklik"]),
        "needs_human": jev.noul("`task` belirsiz ya da celiskili; baslamadan once kullaniciya soru sorulmali"),
    }
    if profiles:
        questions["profile"] = jev.choice(
            "`task` icin en uygun ajan profili hangisi? Profil aciklamalari `available_profiles` icinde.", profiles)
    for i in range(len(sections)):
        questions[f"rel_m{i}"] = jev.noul(f"`memory_sections.m{i}` bolumu `task` icin gerekli/isabetli baglam iceriyor")

    r = jev.ask(state, questions, cfg=cfg, tag="triage")
    res = {"ok": r.ok, "mode": r.mode, "error": r.error, "latency_ms": r.latency_ms, "task_head": task[:200],
           "profile": t.get("fallback_profile", ""), "profile_confidence": 0.0, "kind": None, "risk": None,
           "needs_human": None, "memory": [], "sections_scanned": len(sections)}
    if r.ok:
        k = r["kind"]
        res.update({"kind": k.choice, "kind_confidence": round(k.confidence, 3),
                    "risk": round(r["risk"].score, 2), "needs_human": round(r["needs_human"].noul, 3)})
        if "profile" in r:
            p = r["profile"]
            res.update({"profile": p.choice or res["profile"], "profile_confidence": round(p.confidence, 3),
                        "profile_probs": {a: round(b, 3) for a, b in p.probabilities.items()}})
        scored = [{"file": s["file"], "title": s["title"], "relevance": round(r[f"rel_m{i}"].noul, 3), "text": s["text"]}
                  for i, s in enumerate(sections) if r[f"rel_m{i}"].noul >= float(t.get("min_relevance", 0.6))]
        scored.sort(key=lambda x: -x["relevance"])
        res["memory"] = scored[: int(t.get("top_k", 5))]
    otel.emit(cfg, name="jev.triage", start_ns=t0, hook_input=hook_input, ok=r.ok or r.mode != "live",
              attrs={"jev.hook": "triage", "jev.decision": (res["profile"] or res["kind"] or r.mode) if r.ok else r.mode,
                     "jev.profile": res["profile"] or None, "jev.kind": res["kind"], "jev.risk": res["risk"],
                     "jev.confidence": res["profile_confidence"], "jev.needs_human": res["needs_human"],
                     "jev.memory_hits": len(res["memory"]), "jev.sections_scanned": len(sections),
                     "jev.latency_ms": r.latency_ms, "task.head": task[:200],
                     "jev.input_tokens": int(r.usage.get("input_tokens", 0) or 0)},
              gauges={k: v for k, v in {"jev_task_risk": res["risk"], "jev_latency_ms": float(r.latency_ms or 0) or None}.items() if v is not None} or None,
              counters={f"decisions|triage|{res['profile'] or '-'}": 1, f"decisions|kind|{res['kind'] or r.mode}": 1,
                        **({"tokens|triage|input": int(r.usage.get("input_tokens", 0) or 0)} if int(r.usage.get("input_tokens", 0) or 0) > 0 else {})})
    try:
        (jev.log_dir(cfg) / "last_triage.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    return res


def format_context(res: dict, t: dict) -> str:
    lbl = {0: "dusuk", 1: "orta", 2: "yuksek"}
    rk = res.get("risk")
    rk_txt = f"{lbl.get(int(round(rk)), '?')} ({rk})" if rk is not None else "?"
    head = f"[Jev triage] tur={res.get('kind')} ({res.get('kind_confidence', 0)}) · risk={rk_txt}"
    if res.get("profile"):
        head += f" · onerilen profil={res['profile']} ({res.get('profile_confidence', 0)})"
    lines = [head]
    if (res.get("needs_human") or 0) >= 0.6:
        lines.append(f"[Jev triage] Istek belirsiz gorunuyor (p={res['needs_human']}): baslamadan once netlestir.")
    if res["memory"]:
        lines.append("[Jev triage] Ilgili proje baglami (oku):")
        for m in res["memory"]:
            lines.append(f"- {m['file']} › {m['title']} (p={m['relevance']})")
            if t.get("inline_memory"):
                lines.append("  " + m["text"][: int(t.get("inline_chars", 1200))].replace("\n", "\n  "))
    return "\n".join(lines)


def hook_main() -> None:
    hi = jev.read_hook_input()
    root = jev.project_dir(hi)
    cfg = jev.load_config(root)
    t = cfg.get("triage", {})
    if not jev.enabled(cfg) or os.environ.get("JEV_TRIAGE", "").lower() == "off" or not t.get("enabled", True):
        return
    prompt = str(hi.get("prompt", "")).strip()
    if len(prompt) < int(t.get("min_prompt_chars", 24)) or prompt.startswith("/"):
        return
    res = run_triage(prompt, root, hook_input=hi)
    if res["ok"]:
        jev.emit({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                         "additionalContext": format_context(res, t)}})


def cli_main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(description="Jev gorev triage / profil yonlendirme")
    ap.add_argument("--task")
    ap.add_argument("--task-file")
    ap.add_argument("--root")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--profile-only", action="store_true")
    ap.add_argument("--default", dest="default_profile")
    ap.add_argument("--allowed", help="virgulle ayrilmis profil alt kumesi")
    ap.add_argument("--min-confidence", type=float, default=0.6)
    a = ap.parse_args(argv)
    task = a.task or (Path(a.task_file).read_text(encoding="utf-8") if a.task_file else "")
    if not task.strip():
        ap.error("--task ya da --task-file gerekli")
    root = Path(a.root) if a.root else jev.project_dir()
    allowed = [x.strip() for x in a.allowed.split(",")] if a.allowed else None
    res = run_triage(task, root, allowed=allowed)
    chosen = res["profile"]
    if a.default_profile and (not res["ok"] or not chosen or res["profile_confidence"] < a.min_confidence
                              or (allowed and chosen not in allowed)):
        chosen = a.default_profile
    res["chosen_profile"] = chosen
    if a.profile_only:
        print(chosen)
    elif a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(format_context(res, jev.load_config(root).get("triage", {})))
        print(f"secilen profil: {chosen}" + ("" if res["ok"] else f"  (mod={res['mode']} hata={res['error']})"))


if __name__ == "__main__":
    try:
        cli_main(sys.argv[1:]) if len(sys.argv) > 1 else hook_main()
    except SystemExit:
        raise
    except Exception as e:
        sys.stderr.write(f"[jev-triage] hata, gecirgen: {e}\n")
