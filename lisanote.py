#!/usr/bin/env python3
"""lisanote — live transcription of everything you hear (videos, meetings, any app,
including through headphones) and/or your microphone.

Transcription providers (--engine, or choose in the app's Settings):
  openai | groq | mistral | custom — your own API key (pasted in Settings or via env:
          OPENAI_API_KEY / GROQ_API_KEY / MISTRAL_API_KEY / LISANOTE_API_KEY). Custom = any
          OpenAI-compatible /audio/transcriptions endpoint.
  hermes  — reuse the speech-to-text setup of a local Hermes Agent install.
  local   — offline faster-whisper (pip install faster-whisper).
  auto    — the provider chosen in Settings; otherwise Hermes → any provider with a key → offline.

Capture:
  Linux   — PulseAudio/PipeWire monitor of the current output (speakers OR headphones/Bluetooth);
            follows you if you switch output device mid-session.
  macOS   — BlackHole loopback device (brew install blackhole-2ch + Multi-Output Device), see README.
  Windows — WASAPI loopback via the `soundcard` package (experimental).

Usage:  lisanote [--source both|system|mic] [--lang ar] [--title "Team sync"] [--engine openai]
Stop with Ctrl+C or `lisanote --stop`. Transcripts: ~/Transcripts/<date>_<time>_<title>.md
"""
from __future__ import annotations

import argparse, array, datetime as dt, json, math, os, queue, re, shutil, signal
import subprocess, sys, tempfile, threading, time, urllib.error, urllib.request, uuid, wave
from pathlib import Path

__version__ = "1.1.0"
FROZEN = getattr(sys, "frozen", False)   # True inside the packaged .exe / .app
SR = 16000
BLOCK_S = 0.25
BLOCK_BYTES = int(SR * BLOCK_S) * 2
HOME = Path.home()
HERMES_HOME = Path(os.environ.get("HERMES_HOME", HOME / ".hermes"))
HERMES_SRC = HERMES_HOME / "hermes-agent"
OUTDIR_DEFAULT = HOME / "Transcripts"
PIDFILE = HOME / ".lisanote.pid"
IS_MAC, IS_WIN = sys.platform == "darwin", sys.platform.startswith("win")


# ───────────────────────────── helpers ─────────────────────────────
def rms(pcm: bytes) -> float:
    a = array.array("h", pcm)
    if not a:
        return 0.0
    return math.sqrt(sum(x * x for x in a) / len(a)) / 32768.0


def which_ffmpeg() -> str:
    for p in (shutil.which("ffmpeg"), "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if p and os.path.exists(p):
            return p
    raise RuntimeError("ffmpeg not found (Linux: apt install ffmpeg · macOS: brew install ffmpeg)")


def read_dotenv(path: Path) -> dict:
    out = {}
    try:
        for line in path.read_text(errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip().removeprefix("export ").strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


# ───────────────────────────── capture ─────────────────────────────
class FFmpegCapture:
    """Linux (pulse) and macOS (avfoundation) → 16 kHz mono s16le on stdout."""

    def __init__(self, source: str, log):
        self.source, self.log, self.proc, self.sink = source, log, None, None
        self.ff = which_ffmpeg()

    # Linux --------------------------------------------------------
    def _pactl(self, *a):
        return subprocess.run(["pactl", *a], capture_output=True, text=True).stdout.strip()

    def _linux_inputs(self):
        self.sink = self._pactl("get-default-sink")
        mon, mic = f"{self.sink}.monitor", self._pactl("get-default-source")
        self.log(f"output device: {self.sink}")
        return ["-f", "pulse", "-i", mon], ["-f", "pulse", "-i", mic]

    # macOS --------------------------------------------------------
    def _mac_devices(self):
        r = subprocess.run([self.ff, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                           capture_output=True, text=True)
        audio, names = False, []
        for line in r.stderr.splitlines():
            if "audio devices" in line:
                audio = True
                continue
            m = re.search(r"\[(\d+)\] (.+)$", line)
            if audio and m:
                names.append(m.group(2).strip())
        return names

    def _mac_inputs(self):
        names = self._mac_devices()
        loop = next((n for n in names if "blackhole" in n.lower() or "loopback" in n.lower()), None)
        mic = next((n for n in names if "microphone" in n.lower()), None) or \
              next((n for n in names if n != loop and "blackhole" not in n.lower()), None)
        if self.source in ("both", "system") and not loop:
            raise RuntimeError("No BlackHole device found. Run: brew install blackhole-2ch, then create a "
                               "Multi-Output Device (headphones + BlackHole) in Audio MIDI Setup and select it "
                               "as output. See README.")
        if self.source in ("both", "mic") and not mic:
            raise RuntimeError("No microphone found")
        self.log(f"loopback: {loop} · mic: {mic}")
        return ["-f", "avfoundation", "-i", f":{loop}"], ["-f", "avfoundation", "-i", f":{mic}"]

    def start(self):
        sys_in, mic_in = self._mac_inputs() if IS_MAC else self._linux_inputs()
        cmd = [self.ff, "-hide_banner", "-loglevel", "error", "-nostdin"]
        if self.source == "system":
            cmd += sys_in
        elif self.source == "mic":
            cmd += mic_in
        else:
            cmd += sys_in + mic_in + ["-filter_complex", "amix=inputs=2:duration=longest:normalize=0"]
        cmd += ["-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        self._last_check = time.time()

    def read(self) -> bytes:
        # Linux: follow the default output (e.g. you plug in headphones mid-meeting)
        if not IS_MAC and self.source != "mic" and time.time() - self._last_check > 2:
            self._last_check = time.time()
            if self._pactl("get-default-sink") != self.sink:
                self.log("output device changed → re-attaching")
                self.stop()
                self.start()
        return self.proc.stdout.read(BLOCK_BYTES)

    def stop(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(2)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class SoundcardCapture:
    """Windows WASAPI loopback (pip install soundcard numpy). Experimental."""

    def __init__(self, source, log):
        import soundcard as sc  # noqa
        self.sc, self.source, self.log, self.recs = sc, source, log, []

    def start(self):
        try:  # WASAPI needs COM initialised in the recording thread
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 0)
        except Exception:
            pass
        sc, frames = self.sc, int(SR * BLOCK_S)
        if self.source in ("both", "system"):
            spk = sc.default_speaker()
            self.log(f"loopback: {spk.name}")
            self.recs.append(sc.get_microphone(str(spk.name), include_loopback=True)
                             .recorder(samplerate=SR, channels=1, blocksize=frames))
        if self.source in ("both", "mic"):
            self.recs.append(sc.default_microphone().recorder(samplerate=SR, channels=1, blocksize=frames))
        for r in self.recs:
            r.__enter__()

    def read(self) -> bytes:
        import numpy as np
        frames = int(SR * BLOCK_S)
        mix = sum(r.record(numframes=frames)[:, 0] for r in self.recs)
        return (np.clip(mix, -1, 1) * 32767).astype("<i2").tobytes()

    def stop(self):
        for r in self.recs:
            r.__exit__(None, None, None)
        self.recs = []


# ───────────────────────────── config ─────────────────────────────
# Settings + API keys live only on this computer, in a file readable by the current user (0600).
CONFIG_DIR = (Path(os.environ["APPDATA"]) / "Lisanote" if IS_WIN and os.environ.get("APPDATA")
              else Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "lisanote")
CONFIG_FILE = CONFIG_DIR / "config.json"

# Cloud providers: all speak the OpenAI-compatible POST {base}/audio/transcriptions API.
PROVIDERS = {
    "openai":  {"label": "OpenAI", "base": "https://api.openai.com/v1", "model": "gpt-4o-transcribe",
                "models": ["gpt-4o-transcribe", "gpt-4o-mini-transcribe", "whisper-1"],
                "env": "OPENAI_API_KEY", "key_url": "https://platform.openai.com/api-keys"},
    "groq":    {"label": "Groq", "base": "https://api.groq.com/openai/v1", "model": "whisper-large-v3-turbo",
                "models": ["whisper-large-v3-turbo", "whisper-large-v3"],
                "env": "GROQ_API_KEY", "key_url": "https://console.groq.com/keys"},
    "mistral": {"label": "Mistral", "base": "https://api.mistral.ai/v1", "model": "voxtral-mini-latest",
                "models": ["voxtral-mini-latest"],
                "env": "MISTRAL_API_KEY", "key_url": "https://console.mistral.ai/api-keys"},
    "custom":  {"label": "Custom (OpenAI-compatible)", "base": "", "model": "whisper-1", "models": [],
                "env": "LISANOTE_API_KEY", "key_url": ""},
}
ENGINE_IDS = ["auto", "hermes", *PROVIDERS, "local"]


def load_config() -> dict:
    try:
        cfg = json.loads(CONFIG_FILE.read_text())
    except (OSError, ValueError):
        cfg = {}
    cfg.setdefault("provider", "auto")
    cfg.setdefault("keys", {})
    cfg.setdefault("models", {})
    cfg.setdefault("base_url", "")
    return cfg


def save_config(cfg: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_FILE)
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass


def provider_key(pid: str, cfg: dict | None = None) -> str:
    cfg = cfg or load_config()
    return (cfg["keys"].get(pid) or os.environ.get(PROVIDERS[pid]["env"], "")).strip()


def provider_settings(pid: str, cfg: dict | None = None) -> tuple[str, str, str]:
    """(api_key, base_url, model) for a cloud provider."""
    cfg = cfg or load_config()
    meta = PROVIDERS[pid]
    base = (cfg.get("base_url") if pid == "custom" else meta["base"]) or ""
    model = cfg["models"].get(pid) or meta["model"]
    return provider_key(pid, cfg), base.rstrip("/"), model


def tilde(p) -> str:
    """Show paths as ~/... so messages never reveal the local username or folder layout."""
    p = str(p)
    h = str(HOME)
    return "~" + p[len(h):] if p.startswith(h) else p


def hermes_available() -> bool:
    if FROZEN:   # packaged builds can't import a Hermes install's Python modules
        return False
    return (HERMES_SRC / "tools" / "transcription_tools.py").exists()


def local_available() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("faster_whisper") is not None
    except Exception:
        return False


def _http_error(e) -> str:
    try:
        body = json.loads(e.read())
        msg = body.get("error", body)
        msg = msg.get("message", msg) if isinstance(msg, dict) else msg
        return f"HTTP {e.code}: {str(msg)[:160]}"
    except Exception:
        return f"HTTP {getattr(e, 'code', '?')}: {getattr(e, 'reason', e)}"


def test_provider(pid: str, key: str = "", base: str = "", model: str = "") -> tuple[bool, str]:
    """Cheap credential check (lists models; no audio is sent, nothing is billed)."""
    if pid == "hermes":
        if not hermes_available():
            return False, f"Hermes Agent not found at {tilde(HERMES_SRC)}"
        try:
            eng = HermesEngine(None, lambda *_: None)
            return True, eng.label
        except Exception as e:
            return False, str(e)[:200]
    if pid == "local":
        return (True, "faster-whisper is installed") if local_available() else \
               (False, "Install it first: pip install faster-whisper")
    cfg = load_config()
    k, b, m = provider_settings(pid, cfg)
    key, base, model = key or k, (base or b).rstrip("/"), model or m
    if not key:
        return False, "Paste an API key first"
    if not base:
        return False, "Enter the API base URL (e.g. https://host/v1)"
    req = urllib.request.Request(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            ids = [x.get("id") for x in json.loads(r.read()).get("data", [])]
        if ids and model not in ids:
            return True, f"Key works, but model '{model}' was not listed by this provider"
        return True, f"Connected · {model}"
    except urllib.error.HTTPError as e:
        return False, "Invalid API key" if e.code in (401, 403) else _http_error(e)
    except Exception as e:
        return False, f"Could not reach {base}: {e}"


# ───────────────────────────── engines ─────────────────────────────
class HermesEngine:
    """Uses the speech-to-text settings of a local Hermes Agent install (https://hermes-agent.nousresearch.com)."""
    name = "hermes"

    def __init__(self, lang, log):
        if not hermes_available():
            raise RuntimeError(f"Hermes Agent not found at {tilde(HERMES_SRC)}")
        if lang:  # per-run override; Hermes reads it when stt.language is blank
            os.environ["HERMES_LOCAL_STT_LANGUAGE"] = lang
        if str(HERMES_SRC) not in sys.path:
            sys.path.insert(0, str(HERMES_SRC))
        from hermes_cli.env_loader import load_hermes_dotenv
        load_hermes_dotenv()
        from tools.transcription_tools import transcribe_audio, _get_provider, _load_stt_config
        cfg = _load_stt_config()
        prov = _get_provider(cfg)
        if not prov or prov == "none":
            raise RuntimeError("Hermes has no speech-to-text provider configured")
        sect = cfg.get(prov) if isinstance(cfg.get(prov), dict) else {}
        self.label = f"Hermes · {prov} {sect.get('model', '')}".strip()
        log(f"using {self.label}")
        self._t = transcribe_audio

    def transcribe(self, wav: str) -> str:
        r = self._t(wav, source="lisanote")
        if not r.get("success"):
            raise RuntimeError(r.get("error") or "transcription failed")
        return (r.get("transcript") or "").strip()


class CloudEngine:
    """Any OpenAI-compatible /audio/transcriptions endpoint (OpenAI, Groq, Mistral, custom)."""

    def __init__(self, pid, lang, log, cfg=None):
        self.name = pid
        self.key, self.base, self.model = provider_settings(pid, cfg)
        if not self.key:
            raise RuntimeError(f"No {PROVIDERS[pid]['label']} API key — add it in Settings "
                               f"or set {PROVIDERS[pid]['env']}")
        if not self.base:
            raise RuntimeError("No API base URL set for the custom provider")
        self.lang = lang
        self.label = f"{PROVIDERS[pid]['label'].split(' (')[0]} · {self.model}"
        log(f"using {self.label}")

    def transcribe(self, wav: str) -> str:
        b = uuid.uuid4().hex
        fields = {"model": self.model, "response_format": "json"}
        if self.lang:
            fields["language"] = self.lang
        body = b"".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                        for k, v in fields.items())
        body += (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
                 f"Content-Type: audio/wav\r\n\r\n").encode() + Path(wav).read_bytes() + f"\r\n--{b}--\r\n".encode()
        req = urllib.request.Request(f"{self.base}/audio/transcriptions", data=body, headers={
            "Authorization": f"Bearer {self.key}", "Content-Type": f"multipart/form-data; boundary={b}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return (json.loads(resp.read()).get("text") or "").strip()
        except urllib.error.HTTPError as e:
            raise RuntimeError(_http_error(e)) from None


class LocalEngine:
    name = "local"

    def __init__(self, lang, log, model=None):
        from faster_whisper import WhisperModel
        model = model or os.environ.get("LISANOTE_LOCAL_MODEL", "small")
        self.m = WhisperModel(model, device="cpu", compute_type="int8",
                              cpu_threads=max(2, (os.cpu_count() or 4) - 2))
        self.lang, self.label = lang, f"Offline · whisper {model}"
        log(f"using {self.label}")

    def transcribe(self, wav: str) -> str:
        segs, _ = self.m.transcribe(wav, language=self.lang, beam_size=1, vad_filter=True,
                                    condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segs).strip()


def _build(kind, lang, log, cfg):
    if kind == "hermes":
        return HermesEngine(lang, log)
    if kind == "local":
        return LocalEngine(lang, log)
    return CloudEngine(kind, lang, log, cfg)


def make_engine(kind, lang, log, cfg=None):
    """kind 'auto' = provider chosen in Settings; if none chosen: Hermes → any cloud key → offline."""
    cfg = cfg or load_config()
    if kind == "auto" and cfg.get("provider") not in (None, "", "auto"):
        kind = cfg["provider"]
    if kind != "auto":
        return _build(kind, lang, log, cfg)
    order = (["hermes"] if hermes_available() else []) + \
            [p for p in PROVIDERS if provider_key(p, cfg)] + (["local"] if local_available() else [])
    errors = []
    for k in order:
        try:
            return _build(k, lang, log, cfg)
        except Exception as e:
            errors.append(f"{k}: {e}")
    raise RuntimeError("No transcription provider set up — open Settings and paste an API key"
                       + (f" ({'; '.join(errors)})" if errors else ""))


# ───────────────────────────── session ─────────────────────────────
# Whisper-family models invent these on silence/music; drop when they are the whole chunk.
HALLUCINATIONS = re.compile(r"^(thank you\.?|thanks for watching!?|you\.?|\.+|subtitles by .*|"
                            r"ترجمة .*|اشترك.*|teksting av .*)$", re.I)


class Session:
    def __init__(self, source="both", engine="auto", lang=None, title="session",
                 outdir=OUTDIR_DEFAULT, on_line=print, on_status=None,
                 min_chunk=4.0, max_chunk=15.0, noise=0.003):
        self.source, self.engine_kind, self.lang = source, engine, (lang or None)
        self.title, self.outdir = title or "session", Path(outdir)
        self.on_line = on_line
        self.on_status = on_status or (lambda s: print(f"[lisanote] {s}", file=sys.stderr, flush=True))
        self.min_chunk, self.max_chunk, self.noise = min_chunk, max_chunk, noise
        self._stop = threading.Event()
        self.level = 0.0
        self.busy = False
        self.q: queue.Queue = queue.Queue()
        self.path = None
        self.tmp = Path(tempfile.mkdtemp(prefix="lisanote-"))

    def stop(self):
        self._stop.set()

    def _write(self, text):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(text)

    def _worker(self):
        while True:
            item = self.q.get()
            if item is None:
                return
            pcm, t_start = item
            self.busy = True
            wav = self.tmp / f"{int(t_start * 1000)}.wav"
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm)
            try:
                text = self.engine.transcribe(str(wav))
            except Exception as e:
                self.on_status(f"chunk failed: {str(e)[:160]}")
                text = ""
            finally:
                wav.unlink(missing_ok=True)
                self.busy = False
            if text and not HALLUCINATIONS.match(text.strip()):
                stamp = dt.datetime.fromtimestamp(t_start).strftime("%H:%M:%S")
                self.on_line(f"[{stamp}] {text}")
                self._write(f"**[{stamp}]** {text}\n\n")
            if self.q.qsize() > 3:
                self.on_status(f"transcription is {self.q.qsize()} chunks behind")

    def run(self):
        self.engine = make_engine(self.engine_kind, self.lang, self.on_status)
        cap = SoundcardCapture(self.source, self.on_status) if IS_WIN else FFmpegCapture(self.source, self.on_status)
        self.outdir.mkdir(parents=True, exist_ok=True)
        start = dt.datetime.now()
        slug = re.sub(r"[^\w-]+", "-", self.title).strip("-") or "session"
        self.path = self.outdir / f"{start:%Y-%m-%d_%H%M}_{slug}.md"
        self._write(f"# {self.title}\n\nStarted {start:%Y-%m-%d %H:%M} · source={self.source} · "
                    f"engine={self.engine.label}\n\n")
        (self.outdir / ".current").write_text(str(self.path))
        PIDFILE.write_text(str(os.getpid()))
        self.on_status(f"listening… saving to {tilde(self.path)}")
        worker = threading.Thread(target=self._worker, daemon=True)
        worker.start()
        cap.start()
        buf, voiced, silent_run, t0 = bytearray(), 0, 0, time.time()
        per_s = 1 / BLOCK_S
        try:
            while not self._stop.is_set():
                blk = cap.read()
                if not blk:
                    if self._stop.is_set():
                        break
                    self.on_status("audio stream ended — restarting capture")
                    cap.stop(); time.sleep(1); cap.start()
                    continue
                if not buf:
                    t0 = time.time() - BLOCK_S
                lvl = rms(blk)
                self.level = lvl                    # read by the GUI for the live meter
                loud = lvl >= self.noise
                buf += blk
                voiced += loud
                silent_run = 0 if loud else silent_run + 1
                dur = len(buf) / (SR * 2)
                # cut at a natural pause (≥0.75 s) once we have min_chunk, or force at max_chunk
                if (dur >= self.min_chunk and silent_run >= 3) or dur >= self.max_chunk:
                    if voiced >= 0.5 * per_s:     # ≥0.5 s of sound → worth sending; silence is never sent
                        self.q.put((bytes(buf), t0))
                    buf, voiced, silent_run = bytearray(), 0, 0
                elif not voiced and dur >= 2:      # rolling silence: drop it
                    buf, silent_run = bytearray(), 0
        finally:
            cap.stop()
            if voiced >= 0.5 * per_s:
                self.q.put((bytes(buf), t0))
            self.on_status("finishing last chunks…")
            self.q.put(None)
            worker.join(timeout=90)
            self._write(f"\n_Ended {dt.datetime.now():%H:%M}_\n")
            for p in (self.outdir / ".current", PIDFILE):
                try:
                    p.unlink()
                except OSError:
                    pass
            shutil.rmtree(self.tmp, ignore_errors=True)
            self.on_status(f"saved {tilde(self.path)}")
        return self.path


def stop_running():
    try:
        pid = int(PIDFILE.read_text())
        os.kill(pid, signal.SIGTERM if IS_WIN else signal.SIGINT)
        print("stopped")
    except (OSError, ValueError):
        print("not running")


def main():
    ap = argparse.ArgumentParser(description="Live transcription of system audio and/or mic")
    ap.add_argument("--source", choices=["both", "system", "mic"], default="both",
                    help="system = everything you hear (apps, meetings, headphones); mic = you")
    ap.add_argument("--engine", choices=ENGINE_IDS, default="auto")
    ap.add_argument("--set-key", nargs=2, metavar=("PROVIDER", "KEY"),
                    help="save an API key, e.g. --set-key openai sk-...  (stored in %s)" % tilde(CONFIG_FILE))
    ap.add_argument("--lang", default=os.environ.get("LISANOTE_LANG"), help="ar, en, no … (default: auto)")
    ap.add_argument("--title", default="session")
    ap.add_argument("--outdir", default=str(OUTDIR_DEFAULT))
    ap.add_argument("--max-chunk", type=float, default=15.0)
    ap.add_argument("--stop", action="store_true", help="stop a running lisanote")
    ap.add_argument("--version", action="version", version=f"lisanote {__version__}")
    a = ap.parse_args()
    if a.stop:
        return stop_running()
    if a.set_key:
        pid, key = a.set_key
        if pid not in PROVIDERS:
            sys.exit(f"provider must be one of: {', '.join(PROVIDERS)}")
        cfg = load_config()
        cfg["keys"][pid] = key.strip()
        cfg["provider"] = pid
        save_config(cfg)
        ok, msg = test_provider(pid)
        return print(("✓ " if ok else "✗ ") + msg)
    s = Session(a.source, a.engine, a.lang, a.title, a.outdir, max_chunk=a.max_chunk)
    signal.signal(signal.SIGINT, lambda *_: s.stop())
    signal.signal(signal.SIGTERM, lambda *_: s.stop())
    s.run()


if __name__ == "__main__":
    main()
