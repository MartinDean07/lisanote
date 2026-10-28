Live transcription of meetings, videos and your mic — in any language. Download the file for your system below; no need to build from source.

| System | Download | Install |
|---|---|---|
| 🐧 **Linux** (Ubuntu, Debian, Mint, Pop!_OS…) | `lisanote_1.1.0_all.deb` | Double-click it, or `sudo apt install ./lisanote_1.1.0_all.deb`. Open **Lisanote** from the app menu. |
| 🍎 **macOS** (Apple Silicon, M1 and newer) | `Lisanote-1.1.0-macos-arm64.dmg` | Open the .dmg and drag **Lisanote** to Applications. First start: right-click → **Open** (the app isn't notarized yet). |
| 🪟 **Windows 10/11** (experimental) | `Lisanote-1.1.0-windows-setup.exe` | Run the installer. If SmartScreen warns, click **More info → Run anyway** (the app isn't code-signed yet). |

On first start, the **Settings** panel opens: choose a provider (OpenAI, Groq, Mistral, a custom OpenAI-compatible endpoint, or Hermes Agent) and paste your API key.

**macOS:** to transcribe what you *hear* (other apps, meetings), install `brew install ffmpeg blackhole-2ch` and set up a Multi-Output Device — see the [README](https://github.com/MartinDean07/lisanote#macos-audio-setup-one-time). Microphone-only works after installing ffmpeg.

**Notes**
- Your API key is stored only on your computer. Audio pieces go only to the provider you choose.
- The offline (faster-whisper) provider is available when running from source (`pip install faster-whisper`), not in the packaged builds.
- Intel Macs: run from source (`git clone … && sh install.sh`).
- Verify downloads with `SHA256SUMS.txt`.
