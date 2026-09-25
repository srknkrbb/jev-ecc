#!/usr/bin/env python3
"""Jev (TypeSafe AI System One) icin minimal istemci — SDK bagimliligi yok, yalniz stdlib.

Yapilandirma: plugin config/defaults.json  ⊕  <proje>/.claude/jev.json (derin birlestirme).
Proje dosyasi yoksa hook'lar KAPALIDIR (JEV_ALWAYS=1 ile zorlanabilir) — plugin kullanici kapsaminda
kurulu olsa bile yalniz opt-in projelerde calisir.

Modlar (JEV_MODE ile zorlanabilir):
  live     TYPESAFE_API_KEY (ya da ~/.agent-secrets/typesafe.key / jev.env) var → POST https://api.typesafe.ai/v1/systemone
           Vercel AI Gateway: TYPESAFE_API_URL=https://ai-gateway.vercel.sh/typesafe/v1/systemone + AI_GATEWAY_API_KEY
           (model otomatik "typesafe-ai/jev"; istek/yanit bicimi birebir ayni)
  dry-run  anahtar yok → soru decisions.jsonl'e yazilir, cevap donmez (hook'lar gecirgen)
  mock     testler: deterministik cevap; JEV_MOCK_ANSWERS='{"soru_id": {...}}' ile ezilir
  off      hicbir sey yapma

API (docs.typesafe.ai/api):
  istek : {"state", "model", "questions": {id: {"type": "choice|score|noul", "instructions", "criteria"}}}
  yanit : {"model", "answers": {id: {"type", "choice", "probabilities", "confidence", "score", "noul"}}, "usage"}
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
DEFAULTS_PATH = PLUGIN_ROOT / "config" / "defaults.json"
SECRET_ENV = Path.home() / ".agent-secrets" / "jev.env"   # TYPESAFE_API_KEY=... TYPESAFE_API_URL=... TYPESAFE_MODEL=...


def _load_secret_env() -> None:
    """~/.agent-secrets/jev.env icindeki KEY=VALUE satirlarini ortam degiskeni olarak yukler (ortam onceliklidir).
    Hook'lar Claude Code / ecc-tui altinda calisirken kabuk ortamina guvenmemek icin."""
    try:
        for line in SECRET_ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v and not os.environ.get(k):
                os.environ[k] = v
    except Exception:
        pass


_load_secret_env()
DEFAULT_API_URL = "https://api.typesafe.ai/v1/systemone"
VERCEL_API_URL = "https://ai-gateway.vercel.sh/typesafe/v1/systemone"
API_URL = os.environ.get("TYPESAFE_API_URL") or (VERCEL_API_URL if os.environ.get("AI_GATEWAY_API_KEY") and not os.environ.get("TYPESAFE_API_KEY") else DEFAULT_API_URL)


def provider() -> str:
    return "vercel-ai-gateway" if "ai-gateway.vercel.sh" in API_URL else "typesafe" if "api.typesafe.ai" in API_URL else "custom"


def default_model(cfg: dict | None = None) -> str:
    """Model adi: TYPESAFE_MODEL > proje config > saglayiciya gore (Vercel: typesafe-ai/jev, TypeSafe: jev-latest)."""
    m = os.environ.get("TYPESAFE_MODEL", "").strip()
    if m:
        return m
    cm = (cfg or {}).get("model")
    if cm and cm != "jev-latest":
        return cm
    return "typesafe-ai/jev" if provider() == "vercel-ai-gateway" else (cm or "jev-latest")

_CFG: dict[str, dict] = {}


# --------------------------------------------------------------------------- proje / config

def project_dir(hook_input: dict | None = None) -> Path:
    for cand in (os.environ.get("JEV_PROJECT_DIR"), os.environ.get("CLAUDE_PROJECT_DIR"),
                 os.environ.get("ECC_PROJECT_DIR"), (hook_input or {}).get("cwd")):
        if cand:
            return Path(cand)
    return Path.cwd()


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def project_config_path(root: Path) -> Path:
    return root / ".claude" / "jev.json"


def load_config(root: Path | None = None) -> dict:
    root = (root or project_dir()).resolve()
    key = str(root)
    if key in _CFG:
        return _CFG[key]
    try:
        cfg = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    pcp = project_config_path(root)
    exists = pcp.is_file()
    if exists:
        try:
            cfg = _deep_merge(cfg, json.loads(pcp.read_text(encoding="utf-8")))
        except Exception as e:
            sys.stderr.write(f"[jev] {pcp} okunamadi: {e}\n")
    cfg["_root"] = key
    cfg["_project_config"] = exists
    if not cfg.get("project", {}).get("name"):
        cfg.setdefault("project", {})["name"] = root.name
    _CFG[key] = cfg
    return cfg


def enabled(cfg: dict) -> bool:
    return bool(cfg.get("_project_config")) or os.environ.get("JEV_ALWAYS") == "1"


def log_dir(cfg: dict) -> Path:
    d = Path(cfg["_root"]) / ".claude" / "jev"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def api_key() -> str | None:
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if key:
        return key
    if provider() == "vercel-ai-gateway":
        key = os.environ.get("AI_GATEWAY_API_KEY", "").strip()
        if key:
            return key
    try:
        v = (Path.home() / ".agent-secrets" / "typesafe.key").read_text(encoding="utf-8").strip()
        if v:
            return v
    except Exception:
        pass
    return None


def mode() -> str:
    m = os.environ.get("JEV_MODE", "").strip().lower()
    if m in {"live", "dry-run", "mock", "off"}:
        return m
    return "live" if api_key() else "dry-run"


# --------------------------------------------------------------------------- soru kurucular

def choice(instructions: Any, criteria: dict[str, Any]) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions: Any, criteria: list[str]) -> dict:
    if not 2 <= len(criteria) <= 10:
        raise ValueError("score criteria 2-10 seviye olmali")
    return {"type": "score", "instructions": instructions, "criteria": criteria}


def noul(instructions: Any, criteria: dict[str, str] | None = None) -> dict:
    q: dict[str, Any] = {"type": "noul", "instructions": instructions}
    if criteria:
        q["criteria"] = criteria
    return q


# --------------------------------------------------------------------------- yanit

class Answer:
    def __init__(self, raw: dict):
        self.raw = raw or {}

    @property
    def type(self) -> str:
        return self.raw.get("type", "")

    @property
    def choice(self) -> str | None:
        return self.raw.get("choice")

    @property
    def probabilities(self) -> dict[str, float]:
        return self.raw.get("probabilities") or {}

    @property
    def confidence(self) -> float:
        return float(self.raw.get("confidence") or 0.0)

    @property
    def score(self) -> float:
        return float(self.raw.get("score") or 0.0)

    @property
    def noul(self) -> float:
        return float(self.raw.get("noul") or 0.0)

    def p(self, key: str) -> float:
        return float(self.probabilities.get(key, 0.0))


class Result:
    def __init__(self, *, ok: bool, mode: str, answers: dict | None = None, model: str = "",
                 usage: dict | None = None, latency_ms: int = 0, error: str = ""):
        self.ok = ok
        self.mode = mode
        self.answers = {k: Answer(v) for k, v in (answers or {}).items()}
        self.model = model
        self.usage = usage or {}
        self.latency_ms = latency_ms
        self.error = error

    def __getitem__(self, key: str) -> Answer:
        return self.answers.get(key, Answer({}))

    def __contains__(self, key: str) -> bool:
        return key in self.answers


# --------------------------------------------------------------------------- log

def _log(cfg: dict, entry: dict) -> None:
    try:
        with (log_dir(cfg) / "decisions.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _state_preview(cfg: dict, state: Any) -> dict:
    s = json.dumps(state, ensure_ascii=False) if not isinstance(state, str) else state
    n = int(cfg.get("log_state_chars", 400))
    return {"sha1": hashlib.sha1(s.encode("utf-8")).hexdigest()[:12], "chars": len(s), "head": s[:n]}


# --------------------------------------------------------------------------- mock

def _mock_answers(questions: dict) -> dict:
    try:
        override = json.loads(os.environ.get("JEV_MOCK_ANSWERS", "") or "{}")
    except Exception:
        override = {}
    out = {}
    for qid, q in questions.items():
        t = q["type"]
        if qid in override:
            a = dict(override[qid])
            a.setdefault("type", t)
            if t == "choice" and "probabilities" not in a and "choice" in a:
                keys = list(q["criteria"].keys())
                conf = a.setdefault("confidence", 0.9)
                rest = (1.0 - conf) / max(1, len(keys) - 1)
                a["probabilities"] = {k: (conf if k == a["choice"] else rest) for k in keys}
            out[qid] = a
            continue
        if t == "choice":
            keys = list(q["criteria"].keys())
            out[qid] = {"type": t, "choice": keys[0], "probabilities": {k: 1.0 / len(keys) for k in keys},
                        "confidence": 1.0 / len(keys)}
        elif t == "score":
            n = len(q["criteria"])
            out[qid] = {"type": t, "score": (n - 1) / 2.0, "confidence": 0.5,
                        "legend": {str(i): c for i, c in enumerate(q["criteria"])}}
        else:
            out[qid] = {"type": t, "noul": 0.5}
    return out


# --------------------------------------------------------------------------- ana cagri

def ask(state: Any, questions: dict[str, dict], *, cfg: dict | None = None, tag: str = "",
        model: str | None = None, timeout: float | None = None) -> Result:
    cfg = cfg or load_config()
    m = mode()
    model = model or default_model(cfg)
    timeout = timeout or float(cfg.get("timeout_s", 4))
    retries = int(cfg.get("retries", 2))
    t0 = time.time()
    entry: dict[str, Any] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tag": tag, "mode": m,
                             "ecc_session": os.environ.get("ECC_SESSION_ID", ""),
                             "questions": list(questions.keys()), "state": _state_preview(cfg, state)}
    if m == "off":
        return Result(ok=False, mode=m, error="off")
    if m == "dry-run":
        entry["note"] = "TYPESAFE_API_KEY yok — soru gonderilmedi"
        _log(cfg, entry)
        return Result(ok=False, mode=m, error="dry-run")
    if m == "mock":
        answers = _mock_answers(questions)
        entry.update({"ok": True, "answers": answers, "latency_ms": 0})
        _log(cfg, entry)
        return Result(ok=True, mode=m, answers=answers, model="mock")

    payload = json.dumps({"state": state, "model": model, "questions": questions}).encode("utf-8")
    req = urllib.request.Request(API_URL, data=payload, method="POST", headers={
        "Authorization": f"Bearer {api_key()}", "Content-Type": "application/json",
        "User-Agent": "jev-ecc-plugin/0.2"})
    err = ""
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            answers = body.get("answers", {})
            lat = int((time.time() - t0) * 1000)
            entry.update({"ok": True, "answers": answers, "usage": body.get("usage"),
                          "model": body.get("model"), "latency_ms": lat, "attempt": attempt})
            _log(cfg, entry)
            return Result(ok=True, mode=m, answers=answers, model=body.get("model", ""),
                          usage=body.get("usage"), latency_ms=lat)
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code}"
            try:
                err += " " + e.read().decode("utf-8")[:300]
            except Exception:
                pass
            if e.code in (429, 529) and attempt < retries:
                time.sleep(0.5 * (2 ** attempt))
                continue
            break
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            if attempt < retries:
                time.sleep(0.3 * (2 ** attempt))
                continue
            break
    lat = int((time.time() - t0) * 1000)
    entry.update({"ok": False, "error": err, "latency_ms": lat})
    _log(cfg, entry)
    return Result(ok=False, mode=m, error=err, latency_ms=lat)


# --------------------------------------------------------------------------- hook yardimcilari

def read_hook_input() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    text = sys.argv[1] if len(sys.argv) > 1 else "docker compose down -v"
    cfg = load_config()
    r = ask({"command": text}, {
        "risk": choice("Bu komut ne kadar riskli?", {"safe": "Salt okunur / geri alinabilir",
                                                     "risky": "Veri veya durum kaybi olasi"}),
        "irreversible": noul("Komut geri alinamaz veri kaybina yol acabilir")}, cfg=cfg, tag="selftest")
    print(json.dumps({"project": cfg["project"]["name"], "project_config": cfg["_project_config"],
                      "provider": provider(), "url": API_URL, "model": default_model(cfg),
                      "mode": r.mode, "ok": r.ok, "error": r.error, "latency_ms": r.latency_ms,
                      "risk": r["risk"].raw, "irreversible": r["irreversible"].raw}, ensure_ascii=False, indent=2))
