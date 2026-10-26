# jev-local — yerel Jev sunucusu

TypeSafe System One API'sini (`/v1/systemone`) taklit eden küçük sunucu; yanıtı yerel Ollama modelinden JSON olarak alır. Ücretli API yok; olasılıklar modelin kendi bildirdiğidir (kalibre değil).

```sh
brew install ollama && brew services start ollama
ollama pull qwen2.5:7b-instruct
sed "s#__JEV_ECC__#$HOME/work/jev-ecc#" server/io.brainsland.jev-local.plist > ~/Library/LaunchAgents/io.brainsland.jev-local.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/io.brainsland.jev-local.plist
curl -s http://127.0.0.1:8765/health
```
`~/.agent-secrets/jev.env`: `TYPESAFE_API_URL=http://127.0.0.1:8765/v1/systemone`, `TYPESAFE_API_KEY=yerel`.
Model değiştirmek: plist'te `JEV_MODEL`. GX10 gibi başka bir Ollama: `JEV_OLLAMA`.

## Linux (systemd --user)

```sh
mkdir -p ~/.config/systemd/user
cp server/jev-local.service ~/.config/systemd/user/
sudo loginctl enable-linger "$USER"     # oturum kapaninca da calissin
systemctl --user daemon-reload && systemctl --user enable --now jev-local
ollama pull qwen2.5:7b-instruct
```

Ayarlar: `JEV_NUM_CTX` (varsayilan 8192) ve `JEV_NUM_PREDICT` (varsayilan 1200). Eski 400 sinirinda triage yaniti
kesiliyor ve JSON cozumu bozuluyordu. Istemci tarafinda projenin `.claude/jev.json` dosyasina `"timeout_s": 45`
yazin; 4 sn varsayilani yerel modelde cogu zaman zaman asimina dusup sessizce varsayilan profile donuyordu.
