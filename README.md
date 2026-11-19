# Backdrop Video for Anki

Play a soft, blurred video or live stream behind your Anki cards while you study — instead of covering card text with a picture-in-picture window.

![Frosted-glass card over a blurred backdrop](https://i.imgur.com/CY0QPhw.png)

## Features

- **Frosted-glass card plate:** Your card stays centered and legible over moving video; compatible with stock Anki and UI/theme add-ons (Onigiri, Synapse, AMBOSS, AnKing note types).
- **Right-edge quick controls:** Play/Pause, hide picture (keep sound), **Position** scrub bar for regular videos, **Backdrop** strength slider, **Volume** slider, **Stream quality** (`Auto`, `360`, `480`, `720`, `1080`) for live streams, link paste/clipboard offer, and recent links. Does not steal keyboard focus from Space or `1`–`4`.
- **Hold P to peek:** Hold **P** during review or deck overview to see the full video clearly; release to return to your card.
- **Audio ducking:** Automatically drops backdrop volume to 1/4 while card audio, `{{tts}}`, or [Read Aloud](https://ankiweb.net/shared/info/1227298382) is speaking.
- **Live streams & webcams:** Supports YouTube, YouTube Live, Twitch, direct `.webm`/`.mp4` links, and other `yt-dlp`-supported streams (no live chat). Regular videos stream directly; live streams convert in memory via Anki's bundled `mpv` and stop when paused or closed.

![Right-edge quick controls](https://i.imgur.com/9hLfxl1.png)

## Usage

1. Open **Tools → Backdrop Video…** (`Ctrl+Shift+B` on Windows/Linux, `Cmd+Shift+B` on Mac).
2. Paste a YouTube, Twitch, or video link and click **Play**.
3. Use the tab on the right edge while studying to adjust blur, volume, position, or stream quality.

## Development & Tests

- Build `.ankiaddon` packages into `dist/`: `python3 build.py`
- Run tests:
  - `python3 tests/test_source.py`
  - `QT_QPA_PLATFORM=offscreen <anki-python> tests/test_dialog.py .`
  - `QT_QPA_PLATFORM=offscreen <anki-python> tests/test_web.py`

## Bug Reports

Please open an [issue](https://github.com/wilsondong41/anki-backdrop/issues) with your OS, Anki version, the type of link (YouTube, Twitch, video URL), and any error message or theme add-on in use.

---

**COMPLETELY VIBED CODED WITH CLAUDE AI, THE GOAT.**
