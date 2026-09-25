#!/bin/sh
# `jev` komutu / command: kurulu jev-ecc plugin'ini bulur ve scripts/jev.py'ye devreder.
# Oncelik / order: JEV_PLUGIN_ROOT > ~/.claude/plugins/installed_plugins.json (jev-ecc@*) > bu dosyanin yanindaki depo
if [ -n "${JEV_PLUGIN_ROOT:-}" ] && [ -f "$JEV_PLUGIN_ROOT/scripts/jev.py" ]; then
  root="$JEV_PLUGIN_ROOT"
else
  root=$(python3 - <<'PY' 2>/dev/null
import json, os
try:
    d = json.load(open(os.path.expanduser("~/.claude/plugins/installed_plugins.json")))
    for k, v in d.get("plugins", {}).items():
        if k.startswith("jev-ecc@"):
            print((v[0] if isinstance(v, list) else v)["installPath"]); break
except Exception:
    pass
PY
)
  if [ -z "$root" ] || [ ! -f "$root/scripts/jev.py" ]; then
    here=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
    [ -f "$here/jev.py" ] && root=$(dirname "$here")
  fi
fi
[ -f "${root:-}/scripts/jev.py" ] || { echo "jev-ecc plugin bulunamadi / not found (claude plugin install jev-ecc@jev-ecc)" >&2; exit 1; }
exec python3 "$root/scripts/jev.py" "$@"
