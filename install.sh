#!/bin/sh
# Installs Livescribe on Linux or macOS: app-menu / Launchpad entry, desktop launcher,
# tray (Linux panel / macOS menu bar) support and the `livescribe` CLI.
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"; DEST="$HOME/.local/share/livescribe"
mkdir -p "$DEST" "$HOME/.local/bin"
if [ "$SRC" != "$DEST" ]; then
  cp "$SRC"/livescribe*.py "$SRC"/shell_*.py "$SRC"/ui.html "$SRC"/livescribe-launch.sh "$SRC"/Livescribe.* "$SRC"/README.md "$SRC"/LICENSE "$DEST"/
  mkdir -p "$DEST/icons"; cp "$SRC"/icons/* "$DEST/icons/"
fi
chmod +x "$DEST"/livescribe-launch.sh "$DEST"/Livescribe.command
printf '#!/bin/sh\nexec "%s/livescribe-launch.sh" --cli "$@"\n' "$DEST" > "$HOME/.local/bin/livescribe"
printf '#!/bin/sh\nexec livescribe --stop\n' > "$HOME/.local/bin/livescribe-stop"
chmod +x "$HOME/.local/bin/livescribe" "$HOME/.local/bin/livescribe-stop"
# Python for extra packages: Hermes Agent's venv if present (keeps the "Hermes Agent" provider working),
# otherwise a private venv inside the install folder.
HP="${HERMES_HOME:-$HOME/.hermes}/hermes-agent/venv/bin/python"
ensure_py() {
  if [ -x "$HP" ]; then return; fi
  if [ ! -x "$DEST/.venv/bin/python" ]; then
    if command -v uv >/dev/null; then uv venv -q "$DEST/.venv"; else python3 -m venv "$DEST/.venv"; fi
  fi
  HP="$DEST/.venv/bin/python"
}
pip_install() {
  if command -v uv >/dev/null; then uv pip install -q --python "$HP" "$@"; else "$HP" -m pip install -q "$@"; fi
}

case "$(uname)" in
Darwin)
  export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
  command -v ffmpeg >/dev/null || echo "!! install ffmpeg:   brew install ffmpeg"
  system_profiler SPAudioDataType 2>/dev/null | grep -qi blackhole || echo "!! install loopback: brew install blackhole-2ch   (then see README -> macOS)"
  # menu-bar icon + native window
  ensure_py
  pip_install pystray pywebview pillow pyobjc-framework-Cocoa || echo "!! could not install pystray/pywebview"
  # ~/Applications/Livescribe.app  (shows in Launchpad / Spotlight, can be kept in the Dock)
  APP="$HOME/Applications/Livescribe.app"; rm -rf "$APP"; mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
  cat > "$APP/Contents/Info.plist" <<P
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>Livescribe</string>
<key>CFBundleDisplayName</key><string>Livescribe</string>
<key>CFBundleIdentifier</key><string>no.livescribe.app</string>
<key>CFBundleExecutable</key><string>Livescribe</string>
<key>CFBundleIconFile</key><string>Livescribe</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>1.2</string>
<key>LSUIElement</key><true/>
<key>NSMicrophoneUsageDescription</key><string>Livescribe transcribes your microphone and the audio you hear.</string>
</dict></plist>
P
  printf '#!/bin/sh\nexec "%s/livescribe-launch.sh" "$@"\n' "$DEST" > "$APP/Contents/MacOS/Livescribe"
  chmod +x "$APP/Contents/MacOS/Livescribe"
  if command -v iconutil >/dev/null && command -v sips >/dev/null; then
    IS="$(mktemp -d)/Livescribe.iconset"; mkdir -p "$IS"
    for s in 16 32 128 256 512; do
      sips -z $s $s "$DEST/icons/livescribe-512.png" --out "$IS/icon_${s}x${s}.png" >/dev/null
      d=$((s*2)); [ $d -le 512 ] && sips -z $d $d "$DEST/icons/livescribe-512.png" --out "$IS/icon_${s}x${s}@2x.png" >/dev/null
    done
    cp "$DEST/icons/livescribe-512.png" "$IS/icon_512x512@2x.png"
    iconutil -c icns "$IS" -o "$APP/Contents/Resources/Livescribe.icns" || true
  fi
  cp "$DEST/Livescribe.command" "$HOME/Desktop/Livescribe.command" 2>/dev/null || true
  echo "App: ~/Applications/Livescribe.app (Launchpad/Spotlight; drag to Dock to pin). Menu-bar icon while running." ;;
*)
  command -v ffmpeg >/dev/null || echo "!! install ffmpeg:  sudo apt install ffmpeg"
  command -v pactl >/dev/null || echo "!! install pactl:   sudo apt install pulseaudio-utils"
  /usr/bin/python3 -c "import gi; gi.require_version('WebKit2','4.1')" 2>/dev/null || \
    echo "!! for the app window: sudo apt install python3-gi gir1.2-webkit2-4.1 gir1.2-ayatanaappindicator3-0.1"
  F="$HOME/.local/share/applications/livescribe.desktop"; mkdir -p "$(dirname "$F")"
  cat > "$F" <<D
[Desktop Entry]
Type=Application
Name=Livescribe
GenericName=Live transcription
Comment=Live transcription of meetings, videos and your mic
Exec="$DEST/livescribe-launch.sh"
Icon=$DEST/icons/livescribe.svg
Terminal=false
Categories=AudioVideo;Audio;Utility;
Keywords=transcribe;transcription;whisper;meeting;subtitles;
StartupWMClass=livescribe-window
Actions=toggle;

[Desktop Action toggle]
Name=Start / stop recording
Exec="$DEST/livescribe-launch.sh" --toggle
D
  chmod +x "$F"
  update-desktop-database "$(dirname "$F")" 2>/dev/null || true
  DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
  cp "$F" "$DESK/livescribe.desktop"; chmod +x "$DESK/livescribe.desktop"
  gio set "$DESK/livescribe.desktop" metadata::trusted true 2>/dev/null || true
  echo "App menu: Livescribe (right-click -> Add to panel / favourites). Desktop: $DESK/livescribe.desktop" ;;
esac
echo "Tray icon appears while it runs; tray menu -> 'Start at login' keeps it ready in the background."
echo "CLI: livescribe --help"
