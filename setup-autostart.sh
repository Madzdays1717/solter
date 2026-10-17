#!/data/data/com.termux/files/usr/bin/bash
# Optional helper: requires separately installed Termux:Boot app.
set -euo pipefail
cd "$(dirname "$0")"
APP_DIR="$(pwd)"
mkdir -p "$HOME/.termux/boot"
# App path interpolated only at setup time; .env never copied into boot script.
cat > "$HOME/.termux/boot/start-solter-v2" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
sleep 25
termux-wake-lock 2>/dev/null || true
if ! tmux has-session -t solter-v2 2>/dev/null; then
  tmux new-session -d -s solter-v2 'cd "$APP_DIR" && python bot.py run'
fi
EOF
chmod +x "$HOME/.termux/boot/start-solter-v2"
echo 'Termux:Boot helper terpasang (sesi tmux solter-v2). Buka Termux:Boot sekali dan izinkan berjalan di latar belakang.'
echo 'Autostart tidak dijamin: Android tetap bisa menghentikan proses di latar belakang.'
