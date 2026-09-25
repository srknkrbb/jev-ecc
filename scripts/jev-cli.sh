#!/bin/zsh
# ~/.local/bin/jev sarmalayicisi: kurulu jev-ecc plugin'ini bulur ve jev.py'ye devreder.
# Oncelik: JEV_PLUGIN_ROOT > ~/.claude/plugins/installed_plugins.json (jev-ecc@serkan) > gelistirme deposu.
if [ -n "$JEV_PLUGIN_ROOT" ] && [ -f "$JEV_PLUGIN_ROOT/scripts/jev.py" ]; then root="$JEV_PLUGIN_ROOT"
else
  root=$(python3 - <<'PY' 2>/dev/null
import json, os
try:
    d = json.load(open(os.path.expanduser("~/.claude/plugins/installed_plugins.json")))
    for k, v in d.get("plugins", {}).items():
        if k.startswith("jev-ecc@"):
            print(v[0]["installPath"] if isinstance(v, list) else v["installPath"]); break
except Exception:
    pass
PY
)
  [ -z "$root" ] && root="$HOME/AgentWorkspace/plugins/jev-ecc"
fi
exec python3 "$root/scripts/jev.py" "$@"
