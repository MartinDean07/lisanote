#!/bin/sh
# Build dist/lisanote_<version>_all.deb (pure Python app; uses the system's python3, GTK and WebKit).
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VER="${1:-$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/lisanote.py")}"
B="$(mktemp -d)/lisanote_${VER}_all"
APP="$B/usr/share/lisanote"
mkdir -p "$APP/icons" "$B/usr/bin" "$B/usr/share/applications" "$B/usr/share/icons/hicolor/scalable/apps" \
         "$B/usr/share/icons/hicolor/256x256/apps" "$B/usr/share/doc/lisanote" "$B/DEBIAN"
cp "$ROOT"/lisanote.py "$ROOT"/lisanote_gui.py "$ROOT"/shell_gtk.py "$ROOT"/shell_pywebview.py \
   "$ROOT"/ui.html "$ROOT"/lisanote-launch.sh "$APP/"
cp "$ROOT"/icons/*.svg "$ROOT"/icons/*.png "$APP/icons/"
chmod 755 "$APP/lisanote-launch.sh"
cp "$ROOT/icons/lisanote.svg" "$B/usr/share/icons/hicolor/scalable/apps/lisanote.svg"
cp "$ROOT/icons/lisanote-256.png" "$B/usr/share/icons/hicolor/256x256/apps/lisanote.png"
cp "$ROOT/LICENSE" "$B/usr/share/doc/lisanote/copyright"
printf '#!/bin/sh\nexec /usr/share/lisanote/lisanote-launch.sh --cli "$@"\n' > "$B/usr/bin/lisanote"
chmod 755 "$B/usr/bin/lisanote"
cat > "$B/usr/share/applications/lisanote.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Lisanote
GenericName=Live transcription
Comment=Live transcription of meetings, videos and your mic
Exec=/usr/share/lisanote/lisanote-launch.sh
Icon=lisanote
Terminal=false
Categories=AudioVideo;Audio;Utility;
Keywords=transcribe;transcription;whisper;meeting;subtitles;
StartupWMClass=lisanote-window
Actions=toggle;

[Desktop Action toggle]
Name=Start / stop recording
Exec=/usr/share/lisanote/lisanote-launch.sh --toggle
EOF
SIZE=$(du -sk "$B/usr" | cut -f1)
cat > "$B/DEBIAN/control" <<EOF
Package: lisanote
Version: $VER
Section: sound
Priority: optional
Architecture: all
Installed-Size: $SIZE
Depends: python3 (>= 3.8), python3-gi, gir1.2-gtk-3.0, gir1.2-webkit2-4.1, ffmpeg, pulseaudio-utils
Recommends: gir1.2-ayatanaappindicator3-0.1, gir1.2-xapp-1.0
Maintainer: Lisanote <noreply@github.com>
Homepage: https://github.com/MartinDean07/lisanote
Description: Live transcription of meetings, videos and your microphone
 Transcribes everything you hear (any app, also through headphones) and/or your
 microphone, live, using your own OpenAI / Groq / Mistral key, an OpenAI-compatible
 endpoint, Hermes Agent, or offline faster-whisper. Right-to-left aware UI with a
 tray icon. Transcripts are saved as Markdown in ~/Transcripts.
EOF
cat > "$B/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
EOF
chmod 755 "$B/DEBIAN/postinst"
find "$B" -name __pycache__ -prune -exec rm -rf {} +
mkdir -p "$ROOT/dist"
dpkg-deb --root-owner-group --build "$B" "$ROOT/dist/lisanote_${VER}_all.deb" >/dev/null
echo "$ROOT/dist/lisanote_${VER}_all.deb"
