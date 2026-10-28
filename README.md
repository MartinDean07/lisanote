<p align="center">
  <img src="icons/livescribe-256.png" width="112" alt="Livescribe icon">
</p>

<h1 align="center">Livescribe</h1>

<p align="center"><b>Live transcription of meetings, videos and your mic — in any language.</b><br>
Bring your own OpenAI / Groq / Mistral key, use Hermes Agent, or run fully offline.</p>

<p align="center">
  <img src="docs/screenshot-live.png" width="380" alt="Live transcript with Arabic, English and Norwegian lines">
  &nbsp;&nbsp;
  <img src="docs/screenshot-settings.png" width="380" alt="Choosing a transcription provider">
</p>

<p align="center"><sub>Left: a live session — Arabic lines flow right-to-left, English and Norwegian left-to-right.
Right: pick a provider and paste your key.</sub></p>

---

Live transcription of **everything you hear and say** — meetings (Zoom, Meet, Teams), videos,
lectures, any app — even with headphones on. Text appears in a small always-on-top window and is
saved as Markdown with timestamps. Arabic and other right-to-left languages are displayed correctly.

- 🎧 Captures system audio (what you hear) and/or your microphone
- 🔑 Bring your own key: **OpenAI**, **Groq**, **Mistral**, or any **OpenAI-compatible** endpoint
- 🤖 Or reuse the speech-to-text setup of [Hermes Agent](https://hermes-agent.nousresearch.com), or run **fully offline** (faster-whisper)
- ↔️ Per-line right-to-left / left-to-right (Arabic, Hebrew, Persian, Urdu… mixed with English)
- 🗂 Lives in the tray / menu bar, optional start at login
- 🐧 Linux and 🍎 macOS; 🪟 Windows experimental

## Install

```sh
git clone https://github.com/MartinDean07/livescribe.git
cd livescribe
sh install.sh
```

Then open **Livescribe** from your app menu (Linux) or Launchpad / Spotlight (macOS).
On first start the **Settings** panel opens: pick a provider, paste your API key, press **Test & save**.

### Requirements
| | |
|---|---|
| Linux | `ffmpeg`, `pactl` (PipeWire or PulseAudio), and for the window/tray: `python3-gi gir1.2-webkit2-4.1 gir1.2-ayatanaappindicator3-0.1` (preinstalled on Mint/Ubuntu desktops) |
| macOS | `brew install ffmpeg blackhole-2ch` — see *macOS audio setup* below. `install.sh` adds `pystray` + `pywebview` |
| Windows | Python 3, then `py -m pip install soundcard numpy pystray pywebview pillow`; double-click `Livescribe.bat` |

### macOS audio setup (one time)
macOS cannot record "what you hear" by itself, so add the free BlackHole loopback driver:
1. `brew install blackhole-2ch` and log out/in once.
2. Open **Audio MIDI Setup** → `+` → **Create Multi-Output Device** → tick your headphones/speakers
   **and** BlackHole 2ch (headphones as primary, *Drift Correction* on BlackHole).
3. Select that Multi-Output Device as sound output while transcribing.
4. Allow **Microphone** access when asked.

Linux needs nothing extra: it records the monitor of your current output and follows you when you
switch to headphones or Bluetooth.

## Providers

| Provider | Default model | Get a key |
|---|---|---|
| OpenAI | `gpt-4o-transcribe` (also `gpt-4o-mini-transcribe`, `whisper-1`) | https://platform.openai.com/api-keys |
| Groq | `whisper-large-v3-turbo` | https://console.groq.com/keys |
| Mistral | `voxtral-mini-latest` | https://console.mistral.ai/api-keys |
| Custom | any model on an OpenAI-compatible `/v1/audio/transcriptions` endpoint (e.g. a self-hosted faster-whisper server) | — |
| Hermes Agent | whatever `stt.provider` you configured in Hermes | — |
| Offline | `faster-whisper` `small` on CPU (`pip install faster-whisper`) | free |

**Automatic** uses the provider you selected, otherwise Hermes Agent → any provider with a key → offline.

Keys can also come from environment variables: `OPENAI_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`,
`LIVESCRIBE_API_KEY` (custom).

### Privacy
- API keys are stored **only on your computer** in `~/.config/livescribe/config.json`
  (`%APPDATA%\Livescribe\config.json` on Windows), with permissions `600`. The window only ever
  sees a masked version (`sk-…abcd`).
- Audio is cut into short pieces at natural pauses; silence is never sent. With a cloud provider
  those pieces go to that provider only. **Offline** keeps everything on your device.
- No audio is stored — only the text transcript.
- The app's local UI server listens on `127.0.0.1` only and requires a random per-run token.
- Recording other people may require their consent where you live.

## Using it
- **Record**: big round button or <kbd>Space</kbd>. Choose *Hear + mic*, *Hear only* or *Mic*, and a language (or auto).
- **Tray / menu bar**: closing the window keeps Livescribe running; the icon turns red while recording.
  Menu: Show · Start/Stop recording · Open transcripts folder · Start at login · Quit.
- **Transcripts**: `~/Transcripts/<date>_<time>_<title>.md`, written live, one timestamped line per sentence.

### Command line
```sh
livescribe                               # what you hear + mic, provider from Settings
livescribe --source system --lang ar     # only other apps, Arabic
livescribe --engine groq --title "Lecture"
livescribe --set-key openai sk-...       # save a key without the UI
livescribe --stop
```

## How it works
`ffmpeg` (Linux/macOS) or WASAPI loopback (Windows) streams 16 kHz mono audio; Livescribe cuts it into
4–15 s chunks at pauses and sends each chunk to the chosen provider in a background thread. The window
is a small local web UI shown in a native web view (GTK/WebKit on Linux, pywebview on macOS/Windows,
or a Chrome/Edge app window as fallback).

Delay is roughly 5–20 s behind live speech, because each piece waits for a pause.

## License
MIT with an attribution requirement — free for personal and commercial use. If you use Livescribe
(or something built from it) commercially, credit the original author visibly, e.g.
*"Based on Livescribe by Martin Dean (https://github.com/MartinDean07/livescribe)"*.
See [LICENSE](LICENSE).
