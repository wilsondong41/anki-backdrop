# Backdrop Video — Advanced Config

Most controls live in the study sidebar and **Tools → Backdrop Video…**.

- **`multi_fps`**: the **Frame rate** picker (sidebar → Stream Quality, or Tools → Backdrop Video…) for live streams and other converted videos. Regular YouTube videos aren't affected.
  - `"auto"` *(default)*: 30–60 fps depending on your computer and how many streams play at once.
  - `15`, `24`, `30` or `45`: at most that many frames per second. Lower uses less CPU and battery; the backdrop is blurred, so 24 or 30 usually looks the same.
  - `0` (**Source**): the stream's own frame rate, no cap.
- **`suppress_ytdlp_prompt`**: Set to `true` (or check **Don't show this again** on the prompt) to stop asking for the `yt-dlp` helper download on incompatible streams. You can still download `yt-dlp` anytime from **Tools → Backdrop Video…**.
