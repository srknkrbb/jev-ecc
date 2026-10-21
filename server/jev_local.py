"""Yerel Jev (TypeSafe System One uyumlu) sunucu: 127.0.0.1:8765/v1/systemone -> yerel Ollama (Mac ya da sunucu).

Istek:  {"state": ..., "model": ..., "questions": {id: {"type": "choice|score|noul", "instructions": ..., "criteria": ...}}}
Yanit:  {"model", "answers": {id: {"type", "choice", "probabilities", "confidence", "score", "noul"}}, "usage"}
NOT: olasiliklar modelin kendi bildirdigi degerlerdir (kalibre degil); karar kapisi olarak degil, golge/danisman olarak kullanilir.
Tek istek aninda tek model cagrisi (kilit); ayni anda gelen istekler siraya girer. Yalniz stdlib."""
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OLLAMA = os.environ.get("JEV_OLLAMA", "http://127.0.0.1:11434")
MODEL = os.environ.get("JEV_MODEL", "qwen2.5:7b-instruct")
PORT = int(os.environ.get("JEV_PORT", "8765"))
_lock = threading.Lock()
_stat = {"requests": 0, "errors": 0, "last_ms": 0, "started": time.time()}

SYSTEM = ("Sen bir IPTV yayin operasyonu karar siniflandiricisisin. Verilen durum (state) ve sorulara yalnizca gecerli JSON ile cevap ver. "
          "Her secenek icin 0-1 arasi olasilik ver; olasiliklar toplami 1 olsun. Emin degilsen olasiliklari esitle. Aciklama yazma.")


def _schema(questions):
    props = {}
    for qid, q in questions.items():
        t, crit = q.get("type"), q.get("criteria")
        if t == "choice":
            opts = list((crit or {}).keys())
            props[qid] = {"type": "object", "properties": {"probabilities": {"type": "object", "properties": {o: {"type": "number"} for o in opts}, "required": opts}}, "required": ["probabilities"]}
        elif t == "score":
            props[qid] = {"type": "object", "properties": {"score": {"type": "number"}, "confidence": {"type": "number"}}, "required": ["score", "confidence"]}
        else:
            props[qid] = {"type": "object", "properties": {"noul": {"type": "number"}}, "required": ["noul"]}
    return {"type": "object", "properties": props, "required": list(props)}


def _prompt(state, questions):
    parts = ["DURUM:", json.dumps(state, ensure_ascii=False, default=str)[:6000], "", "SORULAR:"]
    for qid, q in questions.items():
        t = q.get("type")
        parts.append("- [%s] (%s) %s" % (qid, t, q.get("instructions", "")))
        crit = q.get("criteria")
        if t == "choice" and isinstance(crit, dict):
            for k, v in crit.items():
                parts.append("    * %s: %s" % (k, v))
        elif t == "score" and isinstance(crit, list):
            parts.append("    seviyeler (0..%d): %s" % (len(crit) - 1, " | ".join("%d=%s" % (i, c) for i, c in enumerate(crit))))
        elif t == "noul":
            parts.append("    0=hayir/belirsiz ... 1=evet")
    return "\n".join(parts)


def _shape(questions, raw):
    out = {}
    for qid, q in questions.items():
        r = raw.get(qid) or {}
        t = q.get("type")
        if t == "choice":
            keys = list((q.get("criteria") or {}).keys())
            p = {k: max(float((r.get("probabilities") or {}).get(k, 0) or 0), 0.0) for k in keys}
            s = sum(p.values()) or 1.0
            p = {k: v / s for k, v in p.items()} if sum(p.values()) else {k: 1.0 / len(keys) for k in keys}
            best = max(p, key=p.get)
            out[qid] = {"type": t, "choice": best, "probabilities": p, "confidence": p[best]}
        elif t == "score":
            n = len(q.get("criteria") or [0, 1])
            out[qid] = {"type": t, "score": min(max(float(r.get("score", 0) or 0), 0.0), n - 1.0), "confidence": min(max(float(r.get("confidence", 0.5) or 0), 0.0), 1.0),
                        "legend": {str(i): c for i, c in enumerate(q.get("criteria") or [])}}
        else:
            out[qid] = {"type": t, "noul": min(max(float(r.get("noul", 0) or 0), 0.0), 1.0)}
    return out


def answer(body):
    qs = body.get("questions") or {}
    if not qs:
        raise ValueError("questions bos")
    req = {"model": MODEL, "stream": False, "format": _schema(qs), "keep_alive": os.environ.get("JEV_KEEP_ALIVE", "30m"),
           "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 400},
           "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": _prompt(body.get("state"), qs)}]}
    t0 = time.time()
    with _lock:
        r = urllib.request.Request(OLLAMA + "/api/chat", data=json.dumps(req).encode(), headers={"Content-Type": "application/json"})
        resp = json.loads(urllib.request.urlopen(r, timeout=300).read())
    _stat["last_ms"] = int((time.time() - t0) * 1000)
    raw = json.loads((resp.get("message") or {}).get("content") or "{}")
    return {"model": "yerel/" + MODEL, "answers": _shape(qs, raw),
            "usage": {"prompt_tokens": resp.get("prompt_eval_count", 0), "completion_tokens": resp.get("eval_count", 0), "ms": _stat["last_ms"]}}


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.rstrip("/") in ("/health", "/v1/health", ""):
            return self._send(200, {"ok": True, "model": MODEL, **_stat})
        self._send(404, {"error": "yok"})

    def do_POST(self):
        if not self.path.startswith("/v1/systemone"):
            return self._send(404, {"error": "yok"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            _stat["requests"] += 1
            self._send(200, answer(body))
        except Exception as exc:
            _stat["errors"] += 1
            self._send(500, {"error": "%s: %s" % (type(exc).__name__, str(exc)[:200])})

    def log_message(self, *a):
        pass


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print("jev-local dinliyor 127.0.0.1:%d model=%s" % (PORT, MODEL), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
