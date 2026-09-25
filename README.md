# jev-ecc — Jev × ECC karar katmani (Claude Code plugin'i)

Jev (TypeSafe AI System One) tipli karar + kalibre guven skoru donen bir modeldir; kod/metin uretmez.
Bu plugin onu ECC/Claude Code'un karar noktalarina baglar ve her karari OTel span'i olarak
`ecc-tui` oturum trace'ine ekler → Grafana/Tempo'da tek akis.

```
kullanici / ecc-tui gorevi
   │ UserPromptSubmit ──► jev.triage  (tur, risk, profil, hafiza ilgililigi) ──► additionalContext
   ▼
Claude Code / Codex / GX10 ajan
   │ PreToolUse ────────► jev.guard   (korunan bolge → deny · salt okunur → gec · Jev → allow/ask/deny)
   ▼
denetim (claude-denetci) ──► jev review (severity/actionable/kapsam, tekillestirme)
   │
   └── her adim ► OTel span (trace = FNV(ECC_SESSION_ID), parent = session span) ► Collector ► Tempo/Prometheus ► Grafana
```

## Kurulum (bir kez, kullanici kapsami)
```zsh
claude plugin marketplace add ~/AgentWorkspace/plugins/jev-ecc     # yerel marketplace "serkan"
claude plugin install jev-ecc@serkan
ln -sf ~/AgentWorkspace/plugins/jev-ecc/scripts/jev-cli.sh ~/.local/bin/jev   # `jev` komutu
```
Guncelleme: depoda degisiklik + `.claude-plugin/{plugin,marketplace}.json` icinde `version` artir → `claude plugin marketplace update serkan && claude plugin update jev-ecc@serkan` (ayni surum yeniden kopyalanmaz).

## Projede etkinlestirme (opt-in)
```zsh
cd /proje && jev init --name proje --desc "Go + Postgres, Docker Compose"
$EDITOR .claude/jev.json        # protected_paths, project_rules, profiles, memory_sources
jev status                      # mode: live / dry-run
```
`.claude/jev.json` yoksa hook'lar o projede sessizce cikar. Ornek: `config/project.example.json`.
Anahtar: `TYPESAFE_API_KEY` ya da `~/.agent-secrets/typesafe.key`. Yokken dry-run (gecirgen, yalniz log).

## Yapilandirma (defaults ⊕ proje)
| Alan | Anlam |
|---|---|
| `thresholds` | review .35 · confirm .60 · block .85 (ecc2 `risk_thresholds` ile ayni) |
| `on_error` | Jev'e ulasilamazsa: `allow` (varsayilan) / `ask` |
| `guard.ask_mode` | .60–.85 arasi: `ask` (interaktif onay; `claude -p`'de izin hatasi) / `allow` / `deny` |
| `guard.protected_paths` | glob; buraya yazan her sey Jev'siz deny |
| `guard.readonly_bash_regex` | Jev'e sorulmadan gecen komutlar |
| `triage.profiles` | ecc2.toml profil adi → aciklama; bos ise profil sorusu sorulmaz |
| `triage.memory_sources` | glob; `#` bolumlerine ayrilir, her bolum icin ilgililik sorulur |
| `otel.endpoint` | OTLP/HTTP collector (varsayilan 127.0.0.1:14318); `JEV_OTEL=off` kapatir |

Ortam anahtarlari: `JEV_MODE=live|dry-run|mock|off`, `JEV_GUARD=off`, `JEV_TRIAGE=off`, `JEV_OTEL=off`, `JEV_ALWAYS=1` (proje config'i olmadan da calistir), `JEV_PROJECT_DIR`.

## Pipeline'da kullanim
```zsh
# yazar adiminda profil secimi (guven < .6 ya da kume disi → default)
prof=$(jev triage --task-file $D/$n.task.txt --profile-only --default gx10-yazar --allowed gx10-yazar,gx10-devstral,codex-guvenli)
id=$(ecc-tui start --no-worktree --profile "$prof" --task "$(cat $D/$n.task.txt)")
# denetim sonrasi
jev review $D/$n.review.md --scope "$(head -1 $spec)" --out $D/$n.triage.md
```

## Izleme
- Span'ler: `jev.guard` (`jev.decision` = allow | allow-review | ask | deny | deny-protected | skip-readonly | dry-run), `jev.triage` (`jev.profile`, `jev.kind`, `jev.risk`), `jev.review` (`jev.sev.*`).
- Gauge'lar: `jev_risk`, `jev_task_risk`, `jev_review_*`, `jev_session_info{ecc_session_id, trace_id}` (Grafana degiskeni icin).
- Sayaclar: `jev_decisions_total{jev_hook=guard|triage|kind|review, jev_decision}` (proje-yerel `.claude/jev/counters.json`'dan kumulatif), `jev_tokens_total`.
- `monitoring/build_dashboards.py` → `grafana/jev-karar-akisi.json` (ayri pano) + `grafana/jev-row.json`; `monitoring/add_row.py <pano.json>` mevcut panoya 'Jev karar akisi' satirini ekler (idempotent); `otel-spanmetrics-dimensions.yaml` collector'a eklenecek boyutlar.
- Baska bir projede: proje `.claude/jev.json` alir, ayni collector'a yazar; panolar `project_name` label'i ile ayrisir (satir/pano sorgularina `project_name="x"` eklenebilir).

## Test
`JEV_MODE=mock python3 -m unittest discover -s tests -v`

## Maliyet / veri
Girdi $0.042/MTok, cikti ucretsiz; guard ~300–600 token, triage ~8k token. Jev'e giden: komut metni, dosya yolu, Edit'in ilk 160 / Write'in ilk 300 karakteri, hafiza bolumlerinin ilk 600 karakteri. `.env` icerigi gonderilmez; komut satirindaki sirlar gider (`secret_exposure` sorusu bunu isaretler).
