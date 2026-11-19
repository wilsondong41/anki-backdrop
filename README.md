# Backdrop Video for Anki

Play a soft, blurred video or live stream behind your Anki cards while you study — instead of covering card text with a picture-in-picture window.

![Frosted-glass card over a blurred backdrop](https://i.imgur.com/CY0QPhw.png)

## Features

- **Frosted-glass card plate:** Your card stays centered and legible over moving video; compatible with stock Anki and UI/theme add-ons (Onigiri, Synapse, AMBOSS, AnKing note types). Includes a **Study card** translucency slider to dial card opacity from clear glass to solid.
- **Multi-view (1–4 videos or streams):** Stack up to 4 videos or live streams behind your cards (2 split evenly, 3 in a 2-top + 1-bottom layout, 4 in a 2×2 grid) with flush center alignment and zero cropping across window resizes and vertical videos. Mute or remove individual streams anytime, even down to the last stream.
- **Right-edge quick controls:** Play/Pause, hide picture (keep sound), **Break zone** (watch clearly while pausing card & Pomodoro timers, with live break/rest countdown), **Position** scrub bar for regular videos, **Backdrop** strength slider, **Study card** translucency slider, **Volume** slider, **Stream quality** (`Auto`, `360`, `480`, `720`, `1080`, `1440`, `4K`), built-in **Search / Browse** pop-up (`🔍`), link paste/clipboard offer, and recent links with per-item delete. Auto-minimizes when clicking back into the study area and never steals keyboard focus from Space or `1`–`4`.
- **Hold P to peek:** Hold **P** any time during review or deck overview to see the full video clearly; release to return to your card.
- **Audio ducking:** Automatically drops backdrop volume to 1/4 while card audio, `{{tts}}`, or [Read Aloud](https://ankiweb.net/shared/info/1227298382) is speaking.
- **Live streams & webcams:** Supports YouTube, YouTube Live, Twitch, Kick, direct `.mp4`/`.webm`/`.m3u8`/`.mpd` links, and other `yt-dlp`-supported streams (no live chat). Regular YouTube/WebM videos stream directly; live streams and H.264/HLS feeds convert in memory via Anki's bundled `mpv` using hardware decode (`--hwdec=auto-safe`) and adaptive `30–60 fps` (configurable via `multi_fps` in add-on config), stopping immediately when paused or closed.

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
