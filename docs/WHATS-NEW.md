# What's new in Lisanote 2.0

Lisanote 2.0 adds two big features — **recording the sound** and **transcribing audio files** — and a
clearer options panel. Download it from the
[2.0.0 release](https://github.com/MartinDean07/lisanote/releases/tag/v2.0.0).

<p align="center"><img src="screenshot-options.png" width="380" alt="The new options: Listen to, Save, Language"></p>

## 🔴 Record the sound (new)

You can now keep the actual audio of a session, for example when a meeting needs to be documented.

- In the **Save** row, tick **Record**. You can tick **Transcript**, **Record**, or both:
  - **Transcript** only — like before: text only, no sound is kept.
  - **Transcript + Record** — text *and* a recording.
  - **Record** only — records without transcribing, so no API key or cost is needed.
- **Record is off every time the app starts**, so you never record by accident. While recording, the
  badge shows a red **● REC**, the Record option turns red, and a reminder appears:
  *"Recording is on. Tell participants that you are recording."*
- The recording is **one `.m4a` file** saved next to the transcript in `~/Transcripts/`, with separate
  tracks inside the same file:
  1. **Everything (mixed)** — what normal players play
  2. **What I hear** — other people, videos, apps
  3. **My microphone** — you

  (When you listen to only one source, the file has one track.) Separate tracks make it easy to hear who
  said what, without ending up with a pile of audio files.
- The file is written **continuously**, so if the computer crashes you keep everything recorded up to then.
  Silences are kept too, so the recording is complete.
- When you stop, the window shows **Open transcript** and **Play recording**.

Size: roughly 85 MB per hour with all three tracks. Not available on Windows yet.

## 📂 Transcribe an audio file (new)

Record on your phone with any voice-recorder app, then transcribe it on your computer:

- Click the **upload** button at the top of the window, or **drag and drop** the file onto the window.
- Works with phone voice memos and most formats: m4a, mp3, wav, ogg/opus, aac, flac, amr, 3gp, webm,
  and video files such as mp4, mkv and mov.
- A progress bar shows how far it has come. Timestamps are positions in the recording (`00:12:41`),
  not the clock time.
- Files that don't contain audio are refused with a clear message. Your original file is never changed;
  the temporary copy is deleted afterwards.
- Files are sent in parallel pieces, so they transcribe quickly (a 42-second memo took about 4 seconds in testing).

## 🧭 Clearer options

The options are now three labelled rows with icons, so it is obvious what each one does:

| Row | Choices |
|---|---|
| **Listen to** | 🎧+🎤 Both · 🎧 What I hear · 🎤 My mic |
| **Save** | ☑ Transcript · ☐ Record |
| **Language** | Auto, English, العربية, Norsk, Svenska, Türkçe |

## ⌨️ Command line

```sh
lisanote --save-audio                    # transcribe + record (.m4a with separate tracks)
lisanote --audio-only --title "Hearing"  # record only, no transcription
lisanote --file voice-memo.m4a           # transcribe an existing file
```

## Notes

- Tested end-to-end on Linux. The macOS and Windows builds pass automated start-up checks; recording
  uses the same method on macOS but has not yet been tested on a real Mac.
- Packaged builds are unsigned: macOS → right-click → **Open** the first time; Windows → **More info → Run anyway**.

## Disclaimer

Lisanote is a tool. **You alone are responsible for how you use it.** Recording or transcribing
conversations, meetings, calls or media may be restricted by law where you live (for example consent,
privacy/GDPR, wiretapping or copyright rules). It is your responsibility to know and follow those rules and
to inform or get consent from the people involved. The author and contributors accept **no responsibility or
liability** for any use or misuse of this software, for recordings or transcripts made with it, or for any
damage, loss or legal consequences arising from its use. Transcriptions are produced by third-party or local
AI models and may contain errors; do not rely on them as an exact record without checking.
The software is provided "as is", without warranty of any kind (see [LICENSE](LICENSE)).
