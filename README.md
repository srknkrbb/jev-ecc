# jev-ecc — a Jev decision layer for ECC / Claude Code

[Türkçe README](README.tr.md)

[Jev](https://typesafe.ai) (TypeSafe AI's *System One* model) does not write text or code. It answers typed
questions — `choice`, `score`, `noul` (yes/no) — with **calibrated confidence** in 70–500 ms, for a fraction of an
LLM call. That makes it a good fit for the *decision points* inside an agent harness, not for the agent itself.

`jev-ecc` is a Claude Code plugin that wires Jev into four of those points, and streams every decision as an
OpenTelemetry span into the same trace as the [ECC 2.0](https://github.com/affaan-m/ECC) (`ecc-tui`) session that
produced it — so Grafana/Tempo shows *session → triage → tool call → gate decision* as one flow.

```
user / ecc-tui task
   │ UserPromptSubmit ──► jev.triage  (task kind, risk, suggested agent profile, relevant memory sections) ──► additionalContext
   ▼
Claude Code / Codex / local-model agent
   │ PreToolUse ────────► jev.guard   (protected paths → deny · read-only → pass · Jev → allow / ask / deny)
   ▼
reviewer agent ──────────► jev review (severity / actionable / in-scope, local de-duplication)
   │
   └── every step ► OTel span (trace = FNV-1a(ECC_SESSION_ID), parent = session span) ► Collector ► Tempo + Prometheus ► Grafana
```

## Install (one command)

```sh
curl -fsSL https://raw.githubusercontent.com/srknkrbb/jev-ecc/main/install.sh | sh
```

or by hand:

```sh
claude plugin marketplace add srknkrbb/jev-ecc
claude plugin install jev-ecc@jev-ecc
cp ~/.claude/plugins/cache/jev-ecc/jev-ecc/*/scripts/jev-cli.sh ~/.local/bin/jev && chmod +x ~/.local/bin/jev
```

Requirements: Claude Code ≥ 2.1, Python 3.10+ (standard library only — no SDK, no pip).

## Enable in a project (opt-in)

The hooks are installed user-wide but stay **silent unless the project has `.claude/jev.json`**.

```sh
cd /your/project
jev init --name myproject --desc "Go + Postgres, Docker Compose"
$EDITOR .claude/jev.json        # protected_paths, project_rules, profiles, memory_sources
jev status                      # mode: live / dry-run
```

**API key — two routes, pick one:**

| Route | Where | Credentials | Notes |
|---|---|---|---|
| TypeSafe direct | [console.typesafe.ai/keys](https://console.typesafe.ai/keys) | `TYPESAFE_API_KEY` | new signups were paused in Sept 2026 — the console shows a waitlist form; join it, then use the Vercel route meanwhile |
| **Vercel AI Gateway** | [vercel.com/ai-gateway](https://vercel.com/ai-gateway) → API keys | `AI_GATEWAY_API_KEY` | no TypeSafe account needed; same request/response shape, same price, billed through Vercel; model id `typesafe-ai/jev` is selected automatically |

Put the credentials in `~/.agent-secrets/jev.env` (loaded by the hooks, so nothing depends on your shell env):

```sh
# ~/.agent-secrets/jev.env  — Vercel route
TYPESAFE_API_URL=https://ai-gateway.vercel.sh/typesafe/v1/systemone
AI_GATEWAY_API_KEY=vck_...
# or — TypeSafe direct
# TYPESAFE_API_KEY=ts_...
```

(`TYPESAFE_API_KEY` / `~/.agent-secrets/typesafe.key` / `TYPESAFE_MODEL` are honoured too.) Without a key everything
runs in **dry-run**: hooks are transparent and only log the questions they *would* have asked to
`.claude/jev/decisions.jsonl` — a good way to watch it for a week before turning it on. `jev status` shows the provider.

## The four decision points

| Point | Where | What happens |
|---|---|---|
| **Risk gate** | `PreToolUse` on Bash / Edit / Write | writes into `protected_paths` → deterministic **deny** (Jev is not asked); read-only shell commands → pass; everything else → Jev answers `action ∈ {allow, review, block}` plus `irreversible`, `rule_violation`, `touches_protected`, `secret_exposure`. `risk = max(...)` → `≥ .85` deny · `≥ .60` ask · `≥ .35` allow + note · else allow |
| **Triage** | `UserPromptSubmit` | one request: task kind, risk level, "is this ambiguous?", suggested agent profile, and a relevance score for every `#` section of your plan/memory files → injected as context |
| **Routing** | `jev triage --task-file F --profile-only --default P --allowed a,b` | picks the agent profile in pipeline scripts; falls back to `--default` when confidence < 0.6, the pick is outside `--allowed`, or Jev is unavailable |
| **Review triage** | `jev review FINDINGS.md --scope "..." --min high --out X.md [--fail-on high]` | severity / actionable / in-scope per finding, with local de-duplication first |

All thresholds mirror ECC 2.0's `risk_thresholds` (review .35 / confirm .60 / block .85).

## Configuration

`config/defaults.json` ⊕ `<project>/.claude/jev.json` (deep merge). See `config/project.example.json`.

| Key | Meaning |
|---|---|
| `thresholds` | review / confirm / block |
| `on_error` | when Jev is unreachable: `allow` (default) or `ask` |
| `guard.ask_mode` | for the .60–.85 band: `ask` (interactive prompt; in `claude -p` this becomes a permission error — deliberately fail-safe), `allow`, `deny` |
| `guard.protected_paths` | globs; any write here is denied without asking Jev |
| `guard.project_rules` | plain-language rules Jev checks against |
| `guard.readonly_bash_regex` | commands that pass without a Jev call |
| `triage.profiles` | `name → description` of your agent profiles (e.g. `ecc2.toml`); empty = no routing question |
| `triage.memory_sources` | globs split into `#` sections and scored for relevance |
| `otel.endpoint` | OTLP/HTTP collector (default `http://127.0.0.1:14318`); `JEV_OTEL=off` disables |

Environment switches: `JEV_MODE=live|dry-run|mock|off`, `JEV_GUARD=off`, `JEV_TRIAGE=off`, `JEV_OTEL=off`,
`JEV_ALWAYS=1` (run without a project config), `JEV_PROJECT_DIR`.

## `jev` CLI

```
jev init | status | selftest ["cmd"] | triage ... | review ... | trace [ECC_SESSION_ID] | log [N]
```

## Observability

Spans: `jev.guard` (`jev.decision` = allow | allow-review | ask | deny | deny-protected | skip-readonly | dry-run),
`jev.triage` (`jev.profile`, `jev.kind`, `jev.risk`), `jev.review` (`jev.sev.*`).
Metrics: `jev_decisions_total{jev_hook, jev_decision, project_name}` (cumulative, kept in `.claude/jev/counters.json`),
`jev_tokens_total`, gauges `jev_risk`, `jev_task_risk`, `jev_latency_ms`, `jev_session_info{ecc_session_id, trace_id}`.

`monitoring/` ships Grafana dashboards (Prometheus uid `prom`, Tempo uid `tempo`):

```sh
python3 monitoring/build_dashboards.py                     # → monitoring/grafana/jev-karar-akisi.json + jev-row.json
python3 monitoring/add_row.py /path/to/your-dashboard.json # adds the "Jev decision flow" row to an existing dashboard
# append monitoring/otel-spanmetrics-dimensions.yaml to your collector's spanmetrics dimensions (optional)
```

Trace IDs are derived exactly like `ecc-tui export-otel` does (FNV-1a of the session id), so Jev spans land under the
ECC session's root span with no shipper involved. Without an ECC session (interactive Claude Code) the trace is
derived from the Claude session id instead.

## Pipeline example

```sh
prof=$(jev triage --task-file "$task" --profile-only --default local-writer --allowed local-writer,local-fast)
id=$(ecc-tui start --no-worktree --profile "$prof" --task "$(cat "$task")")
# ... after the review step
jev review "$review_md" --scope "$(head -1 "$spec")" --out "$triage_md"
```

## What is sent to Jev

The command text, file paths, the first 160 chars of an edit / 300 of a write, the first 600 chars of each memory
section. `.env` contents are never sent; secrets typed on a command line would be — that is what the `secret_exposure`
question flags. Cost: input $0.042/MTok, output free; a gate question is ~300–600 tokens, a triage ~8k.

## Tests

```sh
JEV_MODE=mock python3 -m unittest discover -s tests -v
```

## License

MIT — see [LICENSE](LICENSE).
