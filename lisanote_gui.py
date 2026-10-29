#!/usr/bin/env python3
"""Lisanote desktop app.

A tiny local web server (127.0.0.1 only, random token) serves ui.html and streams live
events (Server-Sent Events). The window is a native web view:
  Linux   — GTK + WebKit (system python3-gi, shell_gtk.py)
  any OS  — pywebview, if installed
  fallback— Chrome/Edge/Brave "--app" window, else the default browser.
Only one app instance runs; launching again just brings the window back.

Background / tray mode: the app lives in the system tray (Linux panel, macOS menu bar,
Windows notification area). Closing the window only hides it; recording keeps going.
Tray menu: Show · Start/Stop recording · Open folder · Start at login · Quit.
  Linux   — tray is inside shell_gtk.py (XApp status icon on Cinnamon/MATE/Xfce, else AppIndicator)
  mac/Win — pystray (pip install pystray pywebview) in this process; window is a subprocess
Flags: --hidden  start in the tray without opening the window (used at login).
"""
from __future__ import annotations

import json, os, queue, re, secrets, shutil, subprocess, sys, tempfile, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

FROZEN = getattr(sys, "frozen", False)
HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))   # bundled data dir when packaged
sys.path.insert(0, str(HERE))
import lisanote as ls  # noqa: E402

STATE_FILE = Path.home() / ".lisanote-ui.json"
SETTINGS_FILE = Path.home() / ".lisanote-settings.json"
PORTS = [47321, 47322, 47323, 0]
ICONS = HERE / "icons"
DEFAULT_SETTINGS = {"source": "both", "lang": "", "title": "Meeting"}


def load_settings() -> dict:
    try:
        return {**DEFAULT_SETTINGS, **json.loads(SETTINGS_FILE.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULT_SETTINGS)


def save_settings(d: dict):
    try:
        SETTINGS_FILE.write_text(json.dumps({k: d.get(k, v) for k, v in DEFAULT_SETTINGS.items()}))
    except OSError:
        pass


# ───────────────────────── provider settings (keys never leave this machine) ─────────────────────────
def _mask(k: str) -> str:
    return (k[:3] + "…" + k[-4:]) if len(k) > 10 else ("set" if k else "")


def public_config() -> dict:
    """What the UI may see: which provider is active and *masked* keys — never the key itself."""
    cfg = ls.load_config()
    provs = []
    for pid, meta in ls.PROVIDERS.items():
        key = ls.provider_key(pid, cfg)
        provs.append({"id": pid, "label": meta["label"], "models": meta["models"], "key_url": meta["key_url"],
                      "model": cfg["models"].get(pid) or meta["model"], "has_key": bool(key),
                      "masked": _mask(key), "from_env": bool(key and not cfg["keys"].get(pid))})
    return {"provider": cfg.get("provider", "auto"), "base_url": cfg.get("base_url", ""),
            "providers": provs, "hermes": ls.hermes_available(), "local": ls.local_available()}


def update_config(body: dict):
    cfg = ls.load_config()
    if body.get("provider") in ls.ENGINE_IDS:
        cfg["provider"] = body["provider"]
    pid = body.get("provider")
    if pid in ls.PROVIDERS:
        if isinstance(body.get("key"), str) and body["key"].strip():
            cfg["keys"][pid] = body["key"].strip()
        if body.get("clear_key"):
            cfg["keys"].pop(pid, None)
        if isinstance(body.get("model"), str) and body["model"].strip():
            cfg["models"][pid] = body["model"].strip()
        if pid == "custom" and isinstance(body.get("base_url"), str):
            cfg["base_url"] = body["base_url"].strip()
    ls.save_config(cfg)


# ───────────────────────── start at login ─────────────────────────
MAC_APP = Path.home() / "Applications" / "Lisanote.app"


def _autostart_path() -> Path:
    if ls.IS_MAC:
        return Path.home() / "Library" / "LaunchAgents" / "io.github.martindean07.lisanote.plist"
    if ls.IS_WIN:
        return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Lisanote.bat"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart" / "lisanote.desktop"


def autostart_enabled() -> bool:
    return _autostart_path().exists()


def set_autostart(on: bool):
    p = _autostart_path()
    if not on:
        try:
            p.unlink()
        except OSError:
            pass
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    if ls.IS_MAC:
        bundle = Path(sys.executable).resolve().parents[2] if FROZEN else MAC_APP
        args = (["/usr/bin/open", "-a", str(bundle), "--args", "--hidden"] if bundle.suffix == ".app" and bundle.exists()
                else [str(HERE / "lisanote-launch.sh"), "--hidden"])
        items = "".join(f"<string>{a}</string>" for a in args)
        p.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                     '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict>'
                     '<key>Label</key><string>io.github.martindean07.lisanote</string>'
                     f'<key>ProgramArguments</key><array>{items}</array>'
                     '<key>RunAtLoad</key><true/></dict></plist>\n')
    elif ls.IS_WIN and FROZEN:
        p.write_text(f'@start "" "{sys.executable}" --hidden\r\n')
    elif ls.IS_WIN:
        pyw = Path(sys.executable).with_name("pythonw.exe")
        p.write_text(f'@start "" "{pyw if pyw.exists() else sys.executable}" "{HERE / "lisanote_gui.py"}" --hidden\r\n')
    else:
        p.write_text("[Desktop Entry]\nType=Application\nName=Lisanote\n"
                     f"Exec=\"{HERE / 'lisanote-launch.sh'}\" --hidden\n"
                     f"Icon={ICONS / 'lisanote.svg'}\nX-GNOME-Autostart-enabled=true\n"
                     "Comment=Live transcription in the background\n")


class Hub:
    """Owns the recording session and fans events out to every open window."""

    def __init__(self):
        self.subs: list[queue.Queue] = []
        self.lock = threading.Lock()
        self.session = None
        self.thread = None
        self.state = "idle"
        self.started = None
        self.lines: list[dict] = []
        self.file = None
        self.engine = ""
        self.last_client = time.time()
        self.audio = None
        self.job, self.mode = "live", "text"
        self.show_seq = 0      # bumped when a second launch asks the running app to show its window
        self.tray = False      # True once a tray icon is running -> closing the window keeps the app alive
        threading.Thread(target=self._ticker, daemon=True).start()

    # pub/sub ---------------------------------------------------------
    def publish(self, msg):
        with self.lock:
            for q in self.subs:
                q.put(msg)

    def subscribe(self):
        q = queue.Queue()
        with self.lock:
            self.subs.append(q)
        q.put({"type": "snapshot", "state": self.state, "started": self.started, "lines": self.lines,
               "file": str(self.file) if self.file else None, "engine": self.engine, "job": self.job,
               "mode": self.mode, "audio": str(self.audio) if self.audio else None})
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)
        self.last_client = time.time()

    def _ticker(self):
        while True:
            time.sleep(0.1)
            s = self.session
            if s and self.state == "recording":
                self.publish({"type": "level", "level": round(s.level, 4),
                              "pending": int(s.q.qsize() + (1 if s.busy else 0)),
                              "progress": s.progress, "file": str(s.path) if s.path else None,
                              "audio": str(s.audio_path) if s.audio_path else None})

    # session control --------------------------------------------------
    def _set_state(self, st):
        self.state = st
        self.publish({"type": "state", "state": st, "started": self.started, "job": self.job, "mode": self.mode,
                      "file": str(self.file) if self.file else None,
                      "audio": str(self.audio) if self.audio else None})

    def snapshot_state(self):
        return {"state": self.state, "started": self.started, "show_seq": self.show_seq,
                "autostart": autostart_enabled(), "tray": self.tray,
                "file": str(self.file) if self.file else None, "lines": len(self.lines)}

    def start(self, source="both", lang="", title="session", mode="text", upload=None, upload_name=None):
        """mode: text | text_audio | audio. upload: path of an uploaded file to transcribe instead."""
        if self.session:
            return
        if not upload:
            save_settings({"source": source, "lang": lang, "title": title})   # mode is never remembered (privacy)
        self.lines, self.file, self.audio, self.engine = [], None, None, ""
        self.job, self.mode = ("file", "text") if upload else ("live", mode)
        self.started = time.time()
        self.publish({"type": "reset"})               # every open window starts from a clean feed

        def on_line(s):
            ts, _, body = s.partition("] ")
            item = {"ts": ts.lstrip("["), "text": body}
            self.lines.append(item)
            self.publish({"type": "line", **item})

        def on_status(s):
            if s.startswith("using "):
                self.engine = s[6:]
                self.publish({"type": "engine", "text": self.engine})
            elif s.startswith(("chunk failed", "No ", "no transcription", "ffmpeg")):
                self.publish({"type": "error", "text": s[:180]})

        if upload:
            title = Path(upload_name or "file").stem
        self.session = ls.Session(source if source in ("both", "system", "mic") else "both", "auto",
                                  lang or None, title or "session", on_line=on_line, on_status=on_status,
                                  mode=self.mode, file=upload)
        self._set_state("recording")

        def run():
            try:
                self.session.run()
                self.file, self.audio = self.session.path, self.session.audio_path
            except Exception as e:
                self.publish({"type": "error", "text": str(e)[:200]})
            finally:
                if upload:
                    shutil.rmtree(Path(upload).parent, ignore_errors=True)   # the uploaded copy
                self.session = None
                self.started = None
                self._set_state("idle")

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def stop(self, wait=False):
        if self.session and self.state == "recording":
            self.file, self.audio = self.session.path, self.session.audio_path
            self._set_state("stopping")
            self.session.stop()
        if wait and self.thread:
            self.thread.join(timeout=90)


def make_handler(hub: Hub, token: str):
    html = (HERE / "ui.html").read_bytes()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _ok_token(self):
            return parse_qs(urlparse(self.path).query).get("t", [""])[0] == token

        def _send(self, code, body=b"", ctype="text/plain"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if not self._ok_token():
                return self._send(403, b"forbidden")
            if path == "/":
                return self._send(200, html, "text/html; charset=utf-8")
            if path == "/config":
                return self._send(200, json.dumps(public_config()).encode(), "application/json")
            if path == "/state":
                return self._send(200, json.dumps(hub.snapshot_state()).encode(), "application/json")
            if path == "/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                q = hub.subscribe()
                try:
                    while True:
                        try:
                            msg = q.get(timeout=15)
                        except queue.Empty:
                            msg = {"type": "ping"}
                        self.wfile.write(f"data: {json.dumps(msg, ensure_ascii=False)}\n\n".encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    hub.unsubscribe(q)
                return
            self._send(404)

        def do_POST(self):
            if not self._ok_token():
                return self._send(403, b"forbidden")
            if urlparse(self.path).path == "/upload":
                return self._upload()
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                body = {}
            path = urlparse(self.path).path
            if path == "/start":
                s = {**load_settings(), **{k: v for k, v in body.items() if k in DEFAULT_SETTINGS}}
                mode = body.get("mode", "text") if body.get("mode") in ("text", "text_audio", "audio") else "text"
                hub.start(s["source"], s["lang"], s["title"] or "session", mode=mode)
            elif path == "/toggle":
                if hub.state == "idle":
                    s = load_settings()
                    hub.start(s["source"], s["lang"], s["title"] or "session")
                else:
                    hub.stop()
            elif path == "/show":
                hub.show_seq += 1
            elif path == "/hello":
                hub.tray = bool(body.get("tray"))
            elif path == "/config":
                update_config(body)
                return self._send(200, json.dumps(public_config()).encode(), "application/json")
            elif path == "/test":
                ok, msg = ls.test_provider(body.get("provider", ""), body.get("key", ""),
                                           body.get("base_url", ""), body.get("model", ""))
                return self._send(200, json.dumps({"ok": ok, "msg": msg}).encode(), "application/json")
            elif path == "/openurl":
                u = str(body.get("url", ""))
                if u.startswith("https://"):
                    import webbrowser
                    webbrowser.open(u)
            elif path == "/autostart":
                set_autostart(bool(body.get("on")))
            elif path == "/stop":
                hub.stop()
            elif path == "/open":
                pick = {"file": hub.file, "audio": hub.audio}.get(body.get("what") or ("file" if body.get("file") else ""))
                target = str(pick) if pick else str(ls.OUTDIR_DEFAULT)
                os.makedirs(ls.OUTDIR_DEFAULT, exist_ok=True)
                open_path(target)
            elif path == "/quit":
                threading.Thread(target=lambda: (hub.stop(wait=True), os._exit(0)), daemon=True).start()
            return self._send(204)

        def _upload(self):
            """Raw request body = the file. Name comes from ?name=. Streamed to a private temp dir."""
            if hub.session:
                return self._send(409, b"busy")
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > 4 * 1024 ** 3:
                return self._send(413, b"file too large (max 4 GB)")
            name = Path(parse_qs(urlparse(self.path).query).get("name", ["audio"])[0]).name or "audio"
            d = Path(tempfile.mkdtemp(prefix="lisanote-upload-"))
            dst = d / re.sub(r"[^\w.\- ]+", "_", name)
            with open(dst, "wb") as f:
                left = n
                while left:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            try:
                ls.FileCapture(dst, lambda *_: None)          # fails fast on non-audio files
            except Exception as e:
                shutil.rmtree(d, ignore_errors=True)
                return self._send(415, str(e).encode())
            body = json.loads(parse_qs(urlparse(self.path).query).get("opts", ["{}"])[0] or "{}")
            hub.start(lang=body.get("lang", ""), upload=str(dst), upload_name=name)
            return self._send(204)

    return H


def open_path(p):
    if ls.IS_WIN:
        os.startfile(p)  # noqa
    else:
        subprocess.Popen(["open" if ls.IS_MAC else "xdg-open", p],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ───────────────────────── window shells ─────────────────────────
def find_chromium():
    cands = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
             "brave-browser", "msedge"]
    if ls.IS_MAC:
        cands = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                 "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                 "/Applications/Chromium.app/Contents/MacOS/Chromium"] + cands
    if ls.IS_WIN:
        pf = [os.environ.get(k, "") for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
        cands = [os.path.join(b, r) for b in pf if b for r in (
            r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe")] + cands
    for c in cands:
        p = c if os.path.isabs(c) else shutil.which(c)
        if p and os.path.exists(p):
            return p
    return None


def gtk_available() -> bool:
    if ls.IS_MAC or ls.IS_WIN or not os.path.exists("/usr/bin/python3"):
        return False
    chk = subprocess.run(["/usr/bin/python3", "-c", "import gi; gi.require_version('Gtk','3.0');"
                          "gi.require_version('WebKit2','4.1')"], capture_output=True)
    return chk.returncode == 0


def open_window(url, gtk_flags=()) -> subprocess.Popen | None:
    """Returns a process whose exit means 'window closed', or None if we can't tell."""
    if gtk_available():
        return subprocess.Popen(["/usr/bin/python3", str(HERE / "shell_gtk.py"), url, *gtk_flags])
    try:
        import webview  # noqa: F401  (pywebview: native window on macOS / Windows)
        if FROZEN:   # packaged app: re-launch ourselves in window mode
            return subprocess.Popen([sys.executable, "--shell-webview", url])
        return subprocess.Popen([sys.executable, str(HERE / "shell_pywebview.py"), url])
    except ImportError:
        pass
    br = find_chromium()
    if br:
        prof = Path.home() / ".lisanote-browser"
        return subprocess.Popen([br, f"--app={url}", f"--user-data-dir={prof}", "--window-size=460,700",
                                 "--no-first-run", "--no-default-browser-check"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import webbrowser
    webbrowser.open(url)
    return None


def existing_instance():
    try:
        info = json.loads(STATE_FILE.read_text())
        st = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{info['port']}/state?t={info['token']}", timeout=1).read())
        return info, st
    except Exception:
        return None, None


# ───────────────────────── tray for macOS / Windows (pystray) ─────────────────────────
def tray_images():
    from PIL import Image, ImageDraw

    def make(rec):
        s = 64
        im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        col = (255, 77, 94, 255) if rec else (139, 155, 255, 255)
        for x, h in zip((10, 21, 32, 43, 54), (14, 30, 44, 30, 14)):
            d.rounded_rectangle((x - 3, 32 - h // 2, x + 3, 32 + h // 2), radius=3, fill=col)
        return im
    return make(False), make(True)


def run_pystray(hub, url, hidden) -> bool:
    try:
        import pystray
        from pystray import Menu, MenuItem as Item
    except ImportError:
        return False
    idle_img, rec_img = tray_images()
    win = {"p": None}

    def show(*_):
        if win["p"] is None or win["p"].poll() is not None:
            win["p"] = open_window(url)

    def toggle_rec(*_):
        if hub.state == "idle":
            s = load_settings()
            hub.start(s["source"], s["lang"], s["title"] or "session")
        else:
            hub.stop()

    def quit_app(icon, _):
        icon.visible = False
        icon.stop()

    menu = Menu(
        Item("Show Lisanote", show, default=True),
        Item(lambda _: "Stop recording" if hub.state == "recording" else "Start recording", toggle_rec,
             enabled=lambda _: hub.state != "stopping"),
        Item("Open transcripts folder", lambda *_: open_path(str(ls.OUTDIR_DEFAULT))),
        Menu.SEPARATOR,
        Item("Start at login", lambda *_: set_autostart(not autostart_enabled()),
             checked=lambda _: autostart_enabled()),
        Menu.SEPARATOR,
        Item("Quit Lisanote", quit_app))
    if ls.IS_MAC:  # menu-bar only: no Dock icon for the background process
        try:
            from AppKit import NSApplication
            NSApplication.sharedApplication().setActivationPolicy_(1)  # Accessory
        except Exception:
            pass
    icon = pystray.Icon("lisanote", idle_img, "Lisanote", menu)
    hub.tray = True

    def watch():
        last, seq = None, hub.show_seq
        while True:
            time.sleep(0.5)
            if hub.state != last:
                last = hub.state
                icon.icon = rec_img if last != "idle" else idle_img
                icon.title = "Lisanote - recording" if last == "recording" else "Lisanote"
                try:
                    icon.update_menu()
                except Exception:
                    pass
            if hub.show_seq != seq:
                seq = hub.show_seq
                show()

    def setup(ic):
        ic.visible = True
        threading.Thread(target=watch, daemon=True).start()
        if not hidden:
            show()

    icon.run(setup=setup)          # blocks on the main thread until "Quit"
    if win["p"] and win["p"].poll() is None:
        win["p"].terminate()
    return True


def self_test():
    """Used by the release builds: check that everything the app needs is bundled/installed."""
    problems = []
    for f in ("ui.html", "icons/lisanote.svg", "icons/tray-idle.svg"):
        if not (HERE / f).exists():
            problems.append(f"missing {f}")
    try:
        if ls.IS_MAC or ls.IS_WIN:
            import webview, pystray  # noqa: F401
            tray_images()
        elif not gtk_available():
            problems.append("GTK/WebKit2 4.1 not available")
        if ls.IS_WIN:
            import soundcard, numpy  # noqa: F401
    except Exception as e:
        problems.append(f"{type(e).__name__}: {e}")
    print(f"lisanote {ls.__version__} self-test: " + ("ok" if not problems else "; ".join(problems)), flush=True)
    sys.exit(1 if problems else 0)


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--shell-webview":   # child window process (packaged app)
        import shell_pywebview
        return shell_pywebview.run(sys.argv[2])
    if "--self-test" in sys.argv:
        return self_test()
    if "--version" in sys.argv:
        return print(f"lisanote {ls.__version__}")
    hidden = "--hidden" in sys.argv
    info, st = existing_instance()
    if "--toggle" in sys.argv:          # app-menu / dock action: start or stop recording
        if not info:
            hidden = True               # launch in the tray and start right away
        else:
            urllib.request.urlopen(urllib.request.Request(
                f"http://127.0.0.1:{info['port']}/toggle?t={info['token']}", data=b"{}"))
            return
    if info:  # already running -> bring its window back (tray mode) or open another window
        base = f"http://127.0.0.1:{info['port']}"
        if st.get("tray"):
            if not hidden:
                urllib.request.urlopen(urllib.request.Request(f"{base}/show?t={info['token']}", data=b"{}"))
            return
        p = open_window(f"{base}/?t={info['token']}")
        if p:
            p.wait()
        return

    token = secrets.token_urlsafe(18)
    hub = Hub()
    srv = None
    for port in PORTS:
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(hub, token))
            break
        except OSError:
            continue
    srv.daemon_threads = True
    port = srv.server_address[1]
    STATE_FILE.write_text(json.dumps({"port": port, "token": token, "pid": os.getpid()}))
    os.chmod(STATE_FILE, 0o600)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/?t={token}"
    print(f"[lisanote] UI at {url}", file=sys.stderr, flush=True)
    if "--toggle" in sys.argv:
        s = load_settings()
        hub.start(s["source"], s["lang"], s["title"] or "session")

    try:
        if gtk_available():
            # Linux: one GTK process = window + tray icon; it exits only on "Quit"
            open_window(url, ["--tray"] + (["--hidden"] if hidden else [])).wait()
        elif not run_pystray(hub, url, hidden):
            win = open_window(url)   # no tray support: classic behaviour
            if win:
                win.wait()
            else:
                while hub.state != "idle" or hub.subs or time.time() - hub.last_client < 30:
                    time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        hub.stop(wait=True)
        try:
            STATE_FILE.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
