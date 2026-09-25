import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
SCRIPTS = PLUGIN / "scripts"
sys.path.insert(0, str(SCRIPTS))
os.environ["JEV_MODE"] = "mock"
os.environ["JEV_OTEL"] = "off"
import jev_client as jev  # noqa: E402
import otel  # noqa: E402


class FakeProject:
    """Gecici proje: .claude/jev.json + korunan dosya."""
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".claude").mkdir()
        (self.root / "core").mkdir()
        (self.root / "core" / "engine.go").write_text("package core\n")
        (self.root / "PLAN.md").write_text("# Plan\n\n## M1 onbellek\nsingleflight ile content-report onbellegi.\n\n## M2 loglar\nkisisel veri maskeleme.\n")
        (self.root / ".claude" / "jev.json").write_text(json.dumps({
            "project": {"name": "fake", "description": "test"},
            "guard": {"protected_paths": ["core/**"]},
            "triage": {"fallback_profile": "claude-guvenli", "memory_sources": ["PLAN.md"],
                       "profiles": {"claude-guvenli": "genel", "gx10-yazar": "yerel yazar", "codex-guvenli": "codex"}}}))
        return self

    def __exit__(self, *a):
        self.tmp.cleanup()


def run(script, payload=None, args=(), env=None, root=None):
    e = dict(os.environ, JEV_MODE="mock", JEV_OTEL="off")
    if root:
        e["CLAUDE_PROJECT_DIR"] = str(root)
    if env:
        e.update(env)
    p = subprocess.run([sys.executable, str(SCRIPTS / script), *args], input=json.dumps(payload) if payload is not None else "",
                       capture_output=True, text=True, env=e, timeout=30)
    return p


def perm(p):
    return json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] if p.stdout.strip() else None


class ClientTests(unittest.TestCase):
    def test_mock_roundtrip(self):
        r = jev.ask({"x": 1}, {"a": jev.choice("q", {"k1": "", "k2": ""}), "b": jev.noul("q"),
                               "c": jev.score("q", ["l0", "l1", "l2"])}, cfg=jev.load_config(PLUGIN))
        self.assertTrue(r.ok) and self.assertEqual(r["a"].choice, "k1")
        self.assertAlmostEqual(sum(r["a"].probabilities.values()), 1.0)
        self.assertEqual(r["c"].score, 1.0)

    def test_config_merge_and_optin(self):
        with FakeProject() as fp:
            cfg = jev.load_config(fp.root)
            self.assertTrue(cfg["_project_config"]) and self.assertTrue(jev.enabled(cfg))
            self.assertEqual(cfg["guard"]["protected_paths"], ["core/**"])
            self.assertEqual(cfg["thresholds"]["block"], 0.85)  # defaults korunur
        cfg2 = jev.load_config(Path(tempfile.gettempdir()))
        self.assertFalse(jev.enabled(cfg2))

    def test_trace_id_matches_ecc_tui(self):
        # ecc-tui export-otel'in fb6d9766 oturumu icin urettigi degerler
        self.assertEqual(otel.trace_id("fb6d9766"), "bf075f244f91615387a719ddd25c7fc1")
        self.assertEqual(otel.span_id("session:fb6d9766"), "34a67e95b719c8f5")


class GuardTests(unittest.TestCase):
    def test_no_project_config_is_silent(self):
        p = run("hook_pretool_guard.py", {"tool_name": "Bash", "tool_input": {"command": "docker compose down -v"}},
                root=tempfile.gettempdir())
        self.assertEqual(p.stdout.strip(), "")

    def test_protected_edit_denied_without_jev(self):
        with FakeProject() as fp:
            p = run("hook_pretool_guard.py", {"tool_name": "Edit", "cwd": str(fp.root),
                    "tool_input": {"file_path": str(fp.root / "core/engine.go"), "old_string": "a", "new_string": "b"}},
                    env={"JEV_MODE": "off"}, root=fp.root)
            self.assertEqual(perm(p), "deny")

    def test_protected_bash_denied(self):
        with FakeProject() as fp:
            p = run("hook_pretool_guard.py", {"tool_name": "Bash", "cwd": str(fp.root),
                    "tool_input": {"command": "sed -i 's/a/b/' core/engine.go"}}, env={"JEV_MODE": "off"}, root=fp.root)
            self.assertEqual(perm(p), "deny")

    def test_readonly_passthrough(self):
        with FakeProject() as fp:
            p = run("hook_pretool_guard.py", {"tool_name": "Bash", "cwd": str(fp.root),
                    "tool_input": {"command": "git status --short"}}, root=fp.root)
            self.assertEqual(p.stdout.strip(), "")

    def test_high_risk_denied(self):
        with FakeProject() as fp:
            p = run("hook_pretool_guard.py", {"tool_name": "Bash", "cwd": str(fp.root),
                    "tool_input": {"command": "docker compose down -v"}}, root=fp.root,
                    env={"JEV_MOCK_ANSWERS": json.dumps({"action": {"choice": "block", "confidence": 0.93},
                                                         "irreversible": {"noul": 0.97}})})
            self.assertEqual(perm(p), "deny")

    def test_medium_asks_and_ask_mode_allow(self):
        with FakeProject() as fp:
            ans = {"JEV_MOCK_ANSWERS": json.dumps({"action": {"choice": "review", "confidence": 0.7}, "irreversible": {"noul": 0.65}})}
            p = run("hook_pretool_guard.py", {"tool_name": "Bash", "cwd": str(fp.root),
                    "tool_input": {"command": "docker compose restart api"}}, root=fp.root, env=ans)
            self.assertEqual(perm(p), "ask")
            cfgp = fp.root / ".claude" / "jev.json"
            c = json.loads(cfgp.read_text()); c["guard"]["ask_mode"] = "allow"; cfgp.write_text(json.dumps(c))
            p = run("hook_pretool_guard.py", {"tool_name": "Bash", "cwd": str(fp.root),
                    "tool_input": {"command": "docker compose restart api"}}, root=fp.root, env=ans)
            self.assertEqual(perm(p), "allow")

    def test_dry_run_transparent(self):
        with FakeProject() as fp:
            p = run("hook_pretool_guard.py", {"tool_name": "Bash", "cwd": str(fp.root),
                    "tool_input": {"command": "docker compose down -v"}}, root=fp.root, env={"JEV_MODE": "dry-run"})
            self.assertEqual(p.stdout.strip(), "")
            self.assertTrue((fp.root / ".claude/jev/decisions.jsonl").exists())


class TriageTests(unittest.TestCase):
    def test_hook_context(self):
        with FakeProject() as fp:
            p = run("triage.py", {"prompt": "content-report onbellegini singleflight ile duzelt ve testleri gecir", "cwd": str(fp.root)},
                    root=fp.root, env={"JEV_MOCK_ANSWERS": json.dumps({"profile": {"choice": "gx10-yazar", "confidence": 0.8},
                                                                        "kind": {"choice": "fix", "confidence": 0.85},
                                                                        "rel_m0": {"noul": 0.92}})})
            ctx = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("gx10-yazar", ctx) and self.assertIn("tur=fix", ctx)
            self.assertIn("M1 onbellek", ctx)

    def test_cli_default_and_allowed(self):
        with FakeProject() as fp:
            env = {"JEV_MOCK_ANSWERS": json.dumps({"profile": {"choice": "codex-guvenli", "confidence": 0.9}})}
            p = run("triage.py", args=["--task", "onceki deneme dogrulamayi gecemedi, duzelt", "--profile-only"], root=fp.root, env=env)
            self.assertEqual(p.stdout.strip(), "codex-guvenli")
            # izinli kume disi → default
            p = run("triage.py", args=["--task", "onceki deneme dogrulamayi gecemedi, duzelt", "--profile-only",
                                       "--allowed", "gx10-yazar,gx10-devstral", "--default", "gx10-yazar"], root=fp.root, env=env)
            self.assertEqual(p.stdout.strip(), "gx10-yazar")
            # dusuk guven → default
            env = {"JEV_MOCK_ANSWERS": json.dumps({"profile": {"choice": "codex-guvenli", "confidence": 0.4}})}
            p = run("triage.py", args=["--task", "herhangi bir gorev metni burada", "--profile-only", "--default", "gx10-yazar"], root=fp.root, env=env)
            self.assertEqual(p.stdout.strip(), "gx10-yazar")
            # jev kapali → default
            p = run("triage.py", args=["--task", "herhangi bir gorev metni burada", "--profile-only", "--default", "gx10-yazar"],
                    root=fp.root, env={"JEV_MODE": "off"})
            self.assertEqual(p.stdout.strip(), "gx10-yazar")


class ReviewTests(unittest.TestCase):
    def test_parse_dedupe_classify(self):
        import review_triage as rt
        with FakeProject() as fp:
            md = "## SQL enjeksiyonu content-report\nbody\n## SQL enjeksiyon content report\nbody2\n## console.log kaldir\nx\n"
            items = rt.dedupe(rt.parse_findings(md), 0.82)
            self.assertEqual(len(items), 2)
            os.environ["JEV_MOCK_ANSWERS"] = json.dumps({"sev_f0": {"choice": "critical", "confidence": 0.9},
                                                         "sev_f1": {"choice": "low", "confidence": 0.8}})
            try:
                out = rt.classify(items, None, 40, jev.load_config(fp.root))
            finally:
                del os.environ["JEV_MOCK_ANSWERS"]
            self.assertEqual(out[0]["severity"], "critical")
            self.assertIn("CRITICAL", rt.render(out, None))


class OtelTests(unittest.TestCase):
    def test_span_posted_with_ecc_parent(self):
        received = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                received.append((self.path, json.loads(self.rfile.read(n))))
                self.send_response(200); self.end_headers()
            def log_message(self, *a): pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
        try:
            with FakeProject() as fp:
                p = run("hook_pretool_guard.py", {"tool_name": "Bash", "cwd": str(fp.root),
                        "tool_input": {"command": "docker compose down -v"}}, root=fp.root,
                        env={"JEV_OTEL": "on", "JEV_OTLP_ENDPOINT": f"http://127.0.0.1:{srv.server_port}",
                             "ECC_SESSION_ID": "fb6d9766", "ECC_HARNESS": "claude",
                             "JEV_MOCK_ANSWERS": json.dumps({"action": {"choice": "block", "confidence": 0.9}, "irreversible": {"noul": 0.95}})})
                self.assertEqual(perm(p), "deny")
        finally:
            srv.shutdown(); srv.server_close()
        paths = [r[0] for r in received]
        self.assertIn("/v1/traces", paths) and self.assertIn("/v1/metrics", paths)
        span = [r for r in received if r[0] == "/v1/traces"][0][1]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        self.assertEqual(span["traceId"], "bf075f244f91615387a719ddd25c7fc1")
        self.assertEqual(span["parentSpanId"], "34a67e95b719c8f5")
        attrs = {a["key"]: list(a["value"].values())[0] for a in span["attributes"]}
        self.assertEqual(attrs["jev.decision"], "deny") and self.assertEqual(attrs["jev.hook"], "guard")
        m = [r for r in received if r[0] == "/v1/metrics"][0][1]["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
        self.assertIn("jev_session_info", [x["name"] for x in m]) and self.assertIn("jev_risk", [x["name"] for x in m])
        rms = [r for r in received if r[0] == "/v1/metrics"][0][1]["resourceMetrics"]
        sums = [x for x in rms[1]["scopeMetrics"][0]["metrics"] if x["name"] == "jev_decisions_total"]
        self.assertEqual(len(sums), 1)
        pt = sums[0]["sum"]["dataPoints"][0]
        self.assertEqual(pt["asInt"], "1") and self.assertTrue(sums[0]["sum"]["isMonotonic"])
        labels = {a["key"]: list(a["value"].values())[0] for a in pt["attributes"]}
        self.assertEqual(labels["jev.decision"], "deny")
        res_attrs = [a["key"] for a in rms[1]["resource"]["attributes"]]
        self.assertNotIn("ecc.agent.type", res_attrs)  # sayac serisi ajan tipine bolunmemeli


if __name__ == "__main__":
    unittest.main()
