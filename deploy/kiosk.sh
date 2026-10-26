#!/bin/sh
# Start PIP-OS and open it on the touchscreen. The desktop underneath stays
# in place, so closing this window returns to it. Alt+F4 closes Chromium.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
LOG="$HOME/.local/share/pipboy/server.log"
mkdir -p "$HOME/.local/share/pipboy" "$HOME/.cache/pipboy"
if ! curl -sf -o /dev/null --max-time 2 http://127.0.0.1:8080/api/status; then
  if ! pgrep -f "/pipboy-interface/server.py" >/dev/null 2>&1; then
    /usr/bin/python3 "$HOME/pipboy-interface/server.py" --host 127.0.0.1 --port 8080 >>"$LOG" 2>&1 &
  fi
fi
i=0
while [ "$i" -lt 40 ]; do
  curl -sf -o /dev/null --max-time 2 http://127.0.0.1:8080/api/status && break
  i=$((i + 1))
  sleep 0.3
done
if pgrep -af "chromium" | grep -q "127.0.0.1:8080"; then
  exit 0
fi
exec /usr/bin/chromium \
  --kiosk \
  --user-data-dir="$HOME/.local/share/pipboy/chromium" \
  --ozone-platform=wayland \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-translate \
  --check-for-update-interval=31536000 \
  --password-store=basic \
  http://127.0.0.1:8080/
