#!/bin/sh
# jev-ecc tek komutla kurulum / one-command installer
#   curl -fsSL https://raw.githubusercontent.com/srknkrbb/jev-ecc/main/install.sh | sh
# Yaptiklari / what it does:
#   1) Claude Code CLI var mi kontrol eder            2) marketplace 'jev-ecc' (GitHub) ekler + plugin'i kurar
#   3) `jev` komutunu ~/.local/bin'e koyar             4) sonraki adimlari yazar (jev init, anahtar)
# Secenekler / options:  JEV_ECC_REPO=owner/name  JEV_ECC_BIN=~/.local/bin  JEV_ECC_LOCAL=/path/to/checkout (yerel gelistirme)
set -eu
REPO="${JEV_ECC_REPO:-srknkrbb/jev-ecc}"
BIN="${JEV_ECC_BIN:-$HOME/.local/bin}"
SRC="${JEV_ECC_LOCAL:-}"

say() { printf '%s\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { say "HATA/ERROR: '$1' bulunamadi / not found"; exit 1; }; }
need python3
need claude

if [ -n "$SRC" ]; then
  say "→ yerel marketplace / local marketplace: $SRC"
  claude plugin marketplace add "$SRC" >/dev/null 2>&1 || claude plugin marketplace update jev-ecc >/dev/null 2>&1 || true
else
  say "→ marketplace: github.com/$REPO"
  claude plugin marketplace add "$REPO" >/dev/null 2>&1 || claude plugin marketplace update jev-ecc >/dev/null 2>&1 || true
fi
claude plugin install jev-ecc@jev-ecc --scope user 2>/dev/null || claude plugin update jev-ecc@jev-ecc

# plugin cache yolu / installed path
ROOT=$(python3 - <<'PY'
import json, os
d = json.load(open(os.path.expanduser("~/.claude/plugins/installed_plugins.json")))
for k, v in d.get("plugins", {}).items():
    if k.startswith("jev-ecc@"):
        print((v[0] if isinstance(v, list) else v)["installPath"]); break
PY
)
[ -n "$ROOT" ] || { say "HATA/ERROR: plugin kurulumu bulunamadi / plugin install not found"; exit 1; }

mkdir -p "$BIN"
rm -f "$BIN/jev"; cp "$ROOT/scripts/jev-cli.sh" "$BIN/jev" && chmod +x "$BIN/jev"
case ":$PATH:" in *":$BIN:"*) ;; *) say "NOT: $BIN PATH'te degil / not on PATH → export PATH=\"$BIN:\$PATH\"";; esac

say ""
say "✔ jev-ecc kuruldu / installed: $ROOT"
say "Sonraki adimlar / next steps:"
say "  1. cd <proje> && jev init --name <ad> --desc \"<kisa teknoloji ozeti>\"   → .claude/jev.json (protected_paths, project_rules, profiles)"
say "  2. Anahtar / API key → ~/.agent-secrets/jev.env :  TYPESAFE_API_KEY=... (console.typesafe.ai/keys)"
say "     ya da / or Vercel AI Gateway:  TYPESAFE_API_URL=https://ai-gateway.vercel.sh/typesafe/v1/systemone + AI_GATEWAY_API_KEY=..."
say "     (anahtar yoksa dry-run / without a key: dry-run, transparent, log only)"
say "  3. jev status   ·   Claude Code'u yeniden baslat / restart Claude Code"
