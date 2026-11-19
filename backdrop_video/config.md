# Backdrop Video — Advanced Config

Most controls live in the study sidebar and **Tools → Backdrop Video…**. The option below is kept here for higher-FPS multi-stream setups:

- **`multi_fps`**: Controls the frame-rate cap when playing **2–4 converted live streams / `.m3u8` / `.mp4` feeds** at once (or in **Auto** quality mode).
  - `"auto"` *(default)*: scales safely between `30` and `60` fps based on CPU cores and active stream count.
  - `45` or `60`: forces up to `45` or `60` fps even with 2+ concurrent streams (uses more CPU).
  - `0`: disables the FPS cap (uses native source frame rate).
- **`suppress_ytdlp_prompt`**: Set to `true` (or check **Don't show this again** on the prompt) to stop asking for the `yt-dlp` helper download on incompatible streams. You can still download `yt-dlp` anytime from **Tools → Backdrop Video…**.
