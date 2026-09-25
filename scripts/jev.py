#!/usr/bin/env python3
"""`jev` komut satiri: jev-ecc plugin'inin tek giris noktasi.

  jev init [--name AD] [--desc ACIKLAMA]   projede .claude/jev.json olustur (ornekten)
  jev status                                mod, anahtar, proje config, otel hedefi
  jev selftest ["komut"]                    tek soru ile saglik kontrolu
  jev triage --task "..." | --task-file F [--profile-only --default P --allowed a,b]
  jev review BULGULAR.md [--scope ...] [--min high] [--out ...]
  jev trace [ECC_SESSION_ID]                oturumun trace id'sini bas (Grafana/Tempo)
  jev log [N]                               son N karar (decisions.jsonl)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import jev_client as jev  # noqa: E402


def cmd_init(argv: list[str]) -> int:
    root = jev.project_dir()
    dst = jev.project_config_path(root)
    if dst.exists():
        print(f"zaten var: {dst}")
        return 0
    name, desc = root.name, ""
    it = iter(argv)
    for a in it:
        if a == "--name":
            name = next(it, name)
        elif a == "--desc":
            desc = next(it, desc)
    ex = json.loads((HERE.parent / "config" / "project.example.json").read_text(encoding="utf-8"))
    ex["project"] = {"name": name, "description": desc}
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(ex, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"olusturuldu: {dst}\n→ protected_paths, project_rules, profiles, memory_sources alanlarini projeye gore duzenle.")
    return 0


def cmd_status(_: list[str]) -> int:
    cfg = jev.load_config()
    import otel
    print(json.dumps({"project": cfg["project"], "root": cfg["_root"], "project_config": cfg["_project_config"],
                      "enabled": jev.enabled(cfg), "mode": jev.mode(), "api_key": bool(jev.api_key()),
                      "provider": jev.provider(), "url": jev.API_URL, "model": jev.default_model(cfg),
                      "secret_env": str(jev.SECRET_ENV) if jev.SECRET_ENV.exists() else None,
                      "otel": cfg.get("otel"), "ecc_session": os.environ.get("ECC_SESSION_ID", ""),
                      "trace_ctx": otel.context(), "plugin": str(HERE.parent)}, ensure_ascii=False, indent=2))
    return 0


def cmd_trace(argv: list[str]) -> int:
    import otel
    sid = argv[0] if argv else os.environ.get("ECC_SESSION_ID", "")
    if not sid:
        print("ECC_SESSION_ID ver", file=sys.stderr)
        return 2
    print(json.dumps({"session": sid, "trace_id": otel.trace_id(sid), "session_span_id": otel.span_id(f"session:{sid}")}))
    return 0


def cmd_log(argv: list[str]) -> int:
    n = int(argv[0]) if argv else 10
    p = jev.log_dir(jev.load_config()) / "decisions.jsonl"
    if not p.exists():
        print("log yok")
        return 0
    for line in p.read_text(encoding="utf-8").splitlines()[-n:]:
        try:
            e = json.loads(line)
            a = e.get("answers", {})
            act = a.get("action", {}).get("choice") or a.get("profile", {}).get("choice") or ""
            print(f"{e.get('ts')} {e.get('tag'):14} {e.get('mode'):7} ok={e.get('ok')} {act:10} {e.get('latency_ms','')}ms  {e['state']['head'][:80]}")
        except Exception:
            print(line[:160])
    return 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "init":
        return cmd_init(rest)
    if cmd == "status":
        return cmd_status(rest)
    if cmd == "trace":
        return cmd_trace(rest)
    if cmd == "log":
        return cmd_log(rest)
    if cmd == "selftest":
        os.execv(sys.executable, [sys.executable, str(HERE / "jev_client.py"), *rest])
    if cmd == "triage":
        os.execv(sys.executable, [sys.executable, str(HERE / "triage.py"), *(rest or ["--help"])])
    if cmd == "review":
        os.execv(sys.executable, [sys.executable, str(HERE / "review_triage.py"), *rest])
    print(f"bilinmeyen komut: {cmd}\n{__doc__}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
