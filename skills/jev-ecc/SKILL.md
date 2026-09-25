---
name: jev-ecc
description: Jev (TypeSafe AI System One) karar katmanini kullanma — projede risk kapisi/triage kurulumu (.claude/jev.json), `jev` CLI ile profil yonlendirme ve review triage, kararlarin Grafana/Tempo'da izlenmesi. Kullanici "jev", "risk kapisi", "triage", "profil yonlendirme", "review triage" veya "jev.json" dediginde kullan.
---

# jev-ecc

Jev metin uretmez: tipli karar + kalibre guven skoru doner. Bu plugin onu ECC/Claude Code'un
dort karar noktasina baglar. Hook'lar yalniz `.claude/jev.json` olan projelerde calisir.

## Projede etkinlestirme
1. `jev init --name <proje> --desc "<kisa teknoloji ozeti>"` → `.claude/jev.json`
2. Duzenle: `guard.protected_paths` (glob), `guard.project_rules`, `triage.profiles` (ecc2.toml profilleri),
   `triage.memory_sources` (plan/denetim/checkpoint dosyalari).
3. `jev status` → mode `live` icin `TYPESAFE_API_KEY` ya da `~/.agent-secrets/typesafe.key`. Anahtar yoksa **dry-run**: hook'lar gecirgen, sorular `.claude/jev/decisions.jsonl`'e yazilir.

## Karar noktalari
| Nokta | Nerede | Ne yapar |
|---|---|---|
| Risk kapisi | PreToolUse (Bash/Edit/Write) | korunan bolge → deterministik deny; salt okunur → gecer; gerisi Jev → allow/ask/deny (esik .35/.60/.85) |
| Triage | UserPromptSubmit | tur, risk, "belirsiz mi", onerilen profil, ilgili hafiza bolumleri → additionalContext |
| Yonlendirme | `jev triage --task-file F --profile-only --default P --allowed a,b` | pipeline'da ajan profili secimi; guven < 0.6 ya da izinli kume disi → default |
| Review triage | `jev review BULGULAR.md --scope "..." --min high --out X.md [--fail-on high]` | severity/actionable/kapsam + tekillestirme |

## Izleme
Her karar OTel span'i (`jev.guard` / `jev.triage` / `jev.review`) olarak `ECC_SESSION_ID`'den turetilen
ecc-tui trace'ine eklenir (`jev trace <session>` id'yi verir). Grafana'da "Jev karar akisi" satiri ve
`jev-karar-akisi` panosu (`monitoring/` altindaki JSON'lar) bunu gosterir. Collector: `otel.endpoint`.

## Kurallar
- Jev'i taban model olarak kullanma; kod/metin uretmez.
- Gerceklesmis bir deny'i hook'u kapatarak asma; kurali ihlal etmeyen yol sec ya da kullaniciya sor.
- `on_error: allow` varsayilandir — sert kurallar settings.deny listesinde kalmali.
