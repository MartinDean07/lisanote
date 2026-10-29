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

__version__ = "1.2.0"
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
TRACK_TITLES = {"mix": "Everything (mixed)", "system": "What I hear (other people / apps)", "mic": "My microphone"}


class FFmpegCapture:
    """Linux (pulse) and macOS (avfoundation) → 16 kHz mono s16le on stdout.

    With record_to=<file.m4a> the same ffmpeg process also writes the audio to ONE .m4a file with
    separate tracks: 1 = everything mixed (what players play by default), 2 = what you hear,
    3 = your microphone. It is written as fragmented MP4 so a crash keeps what was recorded;
    finalize() turns it into a normal .m4a (and joins parts if the output device changed).
    """

    def __init__(self, source: str, log, record_to: Path | None = None):
        self.source, self.log, self.proc, self.sink = source, log, None, None
        self.ff = which_ffmpeg()
        self.record_to = Path(record_to) if record_to else None
        self.parts: list[Path] = []

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
            cmd += sys_in + mic_in
        pcm = ["-ac", "1", "-ar", str(SR), "-f", "s16le", "pipe:1"]
        if not self.record_to:
            if self.source == "both":
                cmd += ["-filter_complex", "amix=inputs=2:duration=longest:normalize=0"]
            cmd += pcm
        else:
            part = self.record_to.with_name(f".{self.record_to.stem}.part{len(self.parts) + 1}.m4a")
            self.parts.append(part)
            mono = "aresample=48000,aformat=channel_layouts=mono"
            if self.source == "both":
                fc = (f"[0:a]{mono},asplit=2[s1][s2];[1:a]{mono},asplit=2[m1][m2];"
                      "[s1][m1]amix=inputs=2:duration=longest:normalize=0,asplit=2[x1][x2];"
                      f"[x1]aresample={SR}[pcm]")
                maps, titles = ["-map", "[x2]", "-map", "[s2]", "-map", "[m2]"], ["mix", "system", "mic"]
            else:
                fc = f"[0:a]{mono},asplit=2[a1][a2];[a1]aresample={SR}[pcm]"
                maps, titles = ["-map", "[a2]"], [self.source]
            meta = []
            for i, t in enumerate(titles):
                meta += [f"-metadata:s:a:{i}", f"title={TRACK_TITLES[t]}",
                         f"-disposition:a:{i}", "default" if i == 0 else "0"]
            cmd += ["-filter_complex", fc, "-map", "[pcm]", *pcm,
                    *maps, "-c:a", "aac", "-b:a", "64k", *meta,
                    "-movflags", "+frag_keyframe+empty_moov+default_base_moof", "-frag_duration", "2000000",
                    "-f", "mp4", "-y", str(part)]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        self._last_check = time.time()

    def finalize(self) -> Path | None:
        """Turn the recorded part(s) into one normal .m4a. Leaves the parts in place if that fails."""
        parts = [p for p in self.parts if p.exists() and p.stat().st_size > 0]
        if not self.record_to or not parts:
            return None
        if len(parts) == 1:
            cmd = [self.ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(parts[0])]
        else:
            lst = self.record_to.with_name(f".{self.record_to.stem}.parts.txt")
            lst.write_text("".join(f"file '{p.name}'\n" for p in parts))
            cmd = [self.ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "concat", "-safe", "0",
                   "-i", str(lst)]
        titles = (["mix", "system", "mic"] if self.source == "both" else [self.source])
        meta = []
        for i, t in enumerate(titles):     # stream titles don't survive the fragmented→normal remux; set again
            meta += [f"-metadata:s:a:{i}", f"title={TRACK_TITLES[t]}", f"-metadata:s:a:{i}", f"handler_name={TRACK_TITLES[t]}"]
        cmd += ["-map", "0", "-c", "copy", *meta, "-movflags", "+faststart", "-y", str(self.record_to)]
        ok = subprocess.run(cmd, capture_output=True).returncode == 0 and self.record_to.exists()
        if ok:
            for p in parts:
                p.unlink(missing_ok=True)
            self.record_to.with_name(f".{self.record_to.stem}.parts.txt").unlink(missing_ok=True)
            return self.record_to
        self.log(f"could not finalize audio; raw recording kept as {tilde(parts[0])}")
        return parts[0]

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
            self.proc.terminate()          # SIGTERM: ffmpeg flushes and closes the recording cleanly
            try:
                self.proc.wait(6)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class FileCapture:
    """Decodes an audio/video file (any format ffmpeg reads) to 16 kHz mono PCM."""

    def __init__(self, path, log):
        self.path, self.log, self.proc = str(path), log, None
        self.ff = which_ffmpeg()
        self.duration = self._probe()

    def _probe(self) -> float:
        r = subprocess.run([self.ff, "-hide_banner", "-nostdin", "-i", self.path], capture_output=True, text=True)
        if "Audio:" not in r.stderr:          # no audio stream (or not a media file at all)
            raise RuntimeError("This file can't be read as audio")
        m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
        if not m:
            return 0.0
        h, mi, s = m.groups()
        return int(h) * 3600 + int(mi) * 60 + float(s)

    def start(self):
        self.proc = subprocess.Popen([self.ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", self.path,
                                      "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "pipe:1"],
                                     stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)

    def read(self) -> bytes:
        return self.proc.stdout.read(BLOCK_BYTES)

    def stop(self):
        if self.proc and self.proc.poll() is None:
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
    """One recording or file job.

    mode: "text" (transcribe), "text_audio" (transcribe + save .m4a), "audio" (save .m4a only).
    file: transcribe an existing audio/video file instead of live capture.
    """

    def __init__(self, source="both", engine="auto", lang=None, title="session",
                 outdir=OUTDIR_DEFAULT, on_line=print, on_status=None,
                 min_chunk=4.0, max_chunk=15.0, noise=0.003, mode="text", file=None):
        self.source, self.engine_kind, self.lang = source, engine, (lang or None)
        self.title, self.outdir = title or "session", Path(outdir)
        self.on_line = on_line
        self.on_status = on_status or (lambda s: print(f"[lisanote] {s}", file=sys.stderr, flush=True))
        self.mode = mode if mode in ("text", "text_audio", "audio") else "text"
        self.file = Path(file) if file else None
        if self.file:
            self.mode = "text"
            min_chunk, max_chunk = 8.0, 30.0           # files: longer pieces = better context, fewer requests
        self.transcribe = self.mode != "audio"
        self.save_audio = self.mode != "text"
        self.min_chunk, self.max_chunk, self.noise = min_chunk, max_chunk, noise
        self._stop = threading.Event()
        self.level = 0.0
        self.busy = False
        self.progress = None                           # 0..1 for files
        self.q: queue.Queue = queue.Queue(maxsize=12 if self.file else 0)
        self.path = None                               # transcript .md
        self.audio_path = None                         # recording .m4a
        self.tmp = Path(tempfile.mkdtemp(prefix="lisanote-"))
        self._results, self._next, self._emit_lock = {}, 0, threading.Lock()
        self._busy_n = 0

    def stop(self):
        self._stop.set()

    def _write(self, text):
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(text)

    def _stamp(self, t):
        if self.file:                                  # position in the file
            t = int(t)
            return f"{t // 3600:02d}:{t // 60 % 60:02d}:{t % 60:02d}"
        return dt.datetime.fromtimestamp(t).strftime("%H:%M:%S")

    def _emit_ready(self):
        with self._emit_lock:                          # keep lines in order even with parallel workers
            while self._next in self._results:
                t, text = self._results.pop(self._next)
                self._next += 1
                if text and not HALLUCINATIONS.match(text.strip()):
                    stamp = self._stamp(t)
                    self.on_line(f"[{stamp}] {text}")
                    self._write(f"**[{stamp}]** {text}\n\n")

    def _worker(self):
        while True:
            item = self.q.get()
            if item is None:
                self.q.put(None)                       # let sibling workers see it too
                return
            idx, pcm, t_start = item
            self._busy_n += 1
            self.busy = True
            wav = self.tmp / f"{idx}.wav"
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm)
            text = ""
            try:
                if not self._stop.is_set() or not self.file:
                    text = self.engine.transcribe(str(wav))
            except Exception as e:
                self.on_status(f"chunk failed: {str(e)[:160]}")
            finally:
                wav.unlink(missing_ok=True)
                self._busy_n -= 1
                self.busy = self._busy_n > 0
            self._results[idx] = (t_start, text)
            self._emit_ready()
            if not self.file and self.q.qsize() > 3:
                self.on_status(f"transcription is {self.q.qsize()} chunks behind")

    def run(self):
        if self.transcribe:
            self.engine = make_engine(self.engine_kind, self.lang, self.on_status)
        else:
            self.engine = None
            self.on_status("using Audio only (no transcription)")
        self.outdir.mkdir(parents=True, exist_ok=True)
        start = dt.datetime.now()
        title = self.title if not self.file or self.title not in ("", "session") else self.file.stem
        slug = re.sub(r"[^\w-]+", "-", title).strip("-") or "session"
        base = self.outdir / f"{start:%Y-%m-%d_%H%M}_{slug}"
        if self.save_audio:
            if IS_WIN:
                raise RuntimeError("Saving audio isn't supported on Windows yet")
            self.audio_path = base.with_suffix(".m4a")
        if self.file:
            cap = FileCapture(self.file, self.on_status)
        elif IS_WIN:
            cap = SoundcardCapture(self.source, self.on_status)
        else:
            cap = FFmpegCapture(self.source, self.on_status, record_to=self.audio_path)
        if self.transcribe:
            self.path = base.with_suffix(".md")
            head = f"# {title}\n\n"
            if self.file:
                dur = getattr(cap, "duration", 0)
                head += (f"Transcribed {start:%Y-%m-%d %H:%M} from file `{self.file.name}`"
                         + (f" ({self._stamp(dur)})" if dur else "") + f" · engine={self.engine.label}\n\n")
            else:
                head += (f"Started {start:%Y-%m-%d %H:%M} · source={self.source} · engine={self.engine.label}"
                         + (f" · audio: `{self.audio_path.name}`" if self.audio_path else "") + "\n\n")
            self._write(head)
        (self.outdir / ".current").write_text(str(self.path or self.audio_path))
        PIDFILE.write_text(str(os.getpid()))
        what = [tilde(p) for p in (self.path, self.audio_path) if p]
        self.on_status(("reading file… " if self.file else "listening… ") + "saving to " + " + ".join(what))
        n_workers = 3 if (self.file and getattr(self.engine, "name", "") != "local") else 1
        workers = [threading.Thread(target=self._worker, daemon=True) for _ in range(n_workers if self.transcribe else 0)]
        for w in workers:
            w.start()
        cap.start()
        buf, voiced, silent_run, t0 = bytearray(), 0, 0, time.time()
        pos, idx = 0.0, 0                      # seconds read (files)
        per_s = 1 / BLOCK_S
        dur_total = getattr(cap, "duration", 0) or 0

        def flush():
            nonlocal idx
            if self.transcribe and voiced >= 0.5 * per_s:   # ≥0.5 s of sound; silence is never sent
                self.q.put((idx, bytes(buf), t0))
                idx += 1
        try:
            while not self._stop.is_set():
                blk = cap.read()
                if not blk:
                    if self._stop.is_set() or self.file:
                        break                      # end of file
                    self.on_status("audio stream ended — restarting capture")
                    cap.stop(); time.sleep(1); cap.start()
                    continue
                if not buf:
                    t0 = pos if self.file else time.time() - BLOCK_S
                if self.file:
                    pos += len(blk) / (SR * 2)
                    if dur_total:
                        self.progress = min(1.0, pos / dur_total)
                lvl = rms(blk)
                self.level = lvl                    # read by the GUI for the live meter
                if not self.transcribe:
                    continue                        # audio only: ffmpeg writes the file, nothing to chunk
                loud = lvl >= self.noise
                buf += blk
                voiced += loud
                silent_run = 0 if loud else silent_run + 1
                d = len(buf) / (SR * 2)
                # cut at a natural pause (≥0.75 s) once we have min_chunk, or force at max_chunk
                if (d >= self.min_chunk and silent_run >= 3) or d >= self.max_chunk:
                    flush()
                    buf, voiced, silent_run = bytearray(), 0, 0
                elif not voiced and d >= 2:          # rolling silence: drop it
                    buf, silent_run = bytearray(), 0
        finally:
            cap.stop()
            if not self._stop.is_set() or not self.file:
                flush()
            if workers:
                self.on_status("finishing last chunks…")
                self.q.put(None)
                for w in workers:
                    w.join(timeout=120)
            if self.audio_path and hasattr(cap, "finalize"):
                self.on_status("saving audio…")
                self.audio_path = cap.finalize()
            if self.path:
                note = "_Stopped early_" if (self.file and self._stop.is_set()) else f"_Ended {dt.datetime.now():%H:%M}_"
                self._write(f"\n{note}\n")
            for p in (self.outdir / ".current", PIDFILE):
                try:
                    p.unlink()
                except OSError:
                    pass
            shutil.rmtree(self.tmp, ignore_errors=True)
            self.on_status("saved " + " + ".join(tilde(p) for p in (self.path, self.audio_path) if p))
        return self.path or self.audio_path


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
    ap.add_argument("--save-audio", action="store_true", help="also save the audio as one .m4a (separate tracks)")
    ap.add_argument("--audio-only", action="store_true", help="save the audio without transcribing")
    ap.add_argument("--file", metavar="PATH", help="transcribe an existing audio/video file instead of live audio")
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
    mode = "audio" if a.audio_only else "text_audio" if a.save_audio else "text"
    kw = {} if a.file else {"max_chunk": a.max_chunk}
    s = Session(a.source, a.engine, a.lang, a.title, a.outdir, mode=mode, file=a.file, **kw)
    signal.signal(signal.SIGINT, lambda *_: s.stop())
    signal.signal(signal.SIGTERM, lambda *_: s.stop())
    s.run()


if __name__ == "__main__":
    main()
