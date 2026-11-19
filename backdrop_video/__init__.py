"""Backdrop Video: play an online video softly behind your cards while you study."""

import json
import math
import os
import re
import sys
import time
from urllib.parse import parse_qs, quote_plus, urlparse

from aqt import gui_hooks, mw
from aqt.overview import Overview
from aqt.qt import (QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                    QSlider, Qt, QVBoxLayout)
from aqt.reviewer import Reviewer
from aqt.utils import askUser, tooltip

from . import live

PKG = mw.addonManager.addonFromModule(__name__)
mw.addonManager.setWebExports(__name__, r"web/.*")
VIDEO_FILE = re.compile(r"\.(mp4|webm|m4v|mov|ogv)(\?|$)", re.I)
LIVE_MEDIA = re.compile(r"\.(m3u8|mpd)(\?|$)", re.I)
STREAM_SITES = re.compile(r"(^|\.)(youtube\.com|twitch\.tv|kick\.com)$")  # worth offering the helper for
YT_ID = re.compile(r"(?:youtu\.be/|/embed/|/shorts/|/live/|[?&]v=)([\w-]{11})")

MAX_STREAMS = 4
DEFAULTS = {"url": "", "urls": [], "muted": [], "on": False, "strength": 30, "plate_opacity": 70, "volume": 50,
            "time": 0, "paused": False, "picture": True, "panel": False, "quality": "auto", "multi_fps": "auto",
            "title": "", "recent": []}
QUALITIES = [("Auto", "auto"), ("360p", "360"), ("480p", "480"), ("720p", "720"), ("1080p", "1080"), ("1440p", "1440"), ("4K", "2160")]
VALID_QUALITIES = {v for _, v in QUALITIES}
_dialog = None
_browse = None
_last_browse_url = "https://www.youtube.com"
_break_mode = False
_break_started = 0.0
_break_from_review = False
_break_timer = None
ADDON_DIR = os.path.dirname(__file__)
_looking = set()  # links whose live stream is being looked up
_declined = False  # said no to the helper download this session
_retried = {}  # link -> when its stream was last looked up again after it stopped
_not_live = set()  # links a quiet lookup found no stream for, so it isn't repeated every page
_direct_urls = set()  # direct video files (.mp4/.mov) that fell back to mpv -> WebM transcoding


def conf() -> dict:
    # Defaults fill keys missing from an older saved config; keys it no longer uses are dropped.
    saved = mw.addonManager.getConfig(__name__) or {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


position = float(conf()["time"])  # seconds into the current video, so it resumes after a reload or restart
_saved_position = position


def save(**changes) -> None:
    c = conf()
    c.update(changes)
    mw.addonManager.writeConfig(__name__, c)


def source(url: str, start: int, paused: bool = False) -> dict:
    """What to play for a link: a YouTube embed, a plain video file, or the page itself."""
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url  # also defuses javascript: and file: links
    query = parse_qs(urlparse(url).query)
    playlist = query.get("list", [""])[0]
    video = YT_ID.search(url)
    if video or playlist:
        vid = video.group(1) if video else "videoseries"
        params = f"autoplay={0 if paused else 1}&controls=0&disablekb=1&rel=0&iv_load_policy=3&playsinline=1&enablejsapi=1"
        # YouTube's loop=1&playlist=ID breaks live streams, so single videos are looped by backdrop.js instead.
        if playlist:
            params += f"&loop=1&list={playlist}"
        if start and video:
            params += f"&start={start}"
        return {"kind": "youtube", "src": f"https://www.youtube.com/embed/{vid}?{params}"}
    if VIDEO_FILE.search(urlparse(url).path):
        return {"kind": "video", "src": url, "start": start}
    # shortcut: other sites are framed as-is; many refuse framing (blank backdrop), add per-site embeds if users ask
    return {"kind": "page", "src": url}


def software_rendering() -> bool:
    # Blur roughly doubles CPU use without a GPU (measured 50% -> 100% of a core), so it is skipped there.
    try:
        from aqt.profiles import VideoDriver
        return mw.pm.video_driver() == VideoDriver.Software
    except Exception:
        return False


def active_urls(c: dict = None) -> list:
    c = c or conf()
    raw = [u.strip() for u in (c.get("urls") or []) if isinstance(u, str) and u.strip()]
    out, seen = [], set()
    for u in raw:
        if u not in seen:
            seen.add(u)
            out.append(u)
            if len(out) >= MAX_STREAMS:
                break
    if not out and c.get("url"):
        out = [c["url"]]
    return out


def _recent_title(c: dict, url: str) -> str:
    if url == c.get("url") and c.get("title"):
        return c["title"]
    for r in c.get("recent") or []:
        if r.get("url") == url:
            return r.get("title", "")
    return ""


def _remember_recent(c: dict, url: str, title: str = "") -> list:
    current = [{"url": c["url"], "title": c["title"]}] if c.get("url") else []  # may predate the recent list
    known = [r for r in c.get("recent", []) + current if r.get("url") == url]
    title = title or (known[0].get("title", "") if known else "")
    rest, seen = [], {url}
    for r in current + c.get("recent", []):
        if r.get("url") not in seen:
            seen.add(r["url"])
            rest.append(r)
    return ([{"url": url, "title": title}] + rest)[:5]


def _stream_spec(url: str, start: int, paused: bool, title: str = "") -> dict:
    spec = {"url": url, "title": title, **source(url, start, paused)}
    if live.ready(url):
        spec.update(kind="video", src=live.address(url), start=0, live=True)
    elif spec["kind"] == "page" and (
        STREAM_SITES.search(urlparse(spec["src"]).hostname or "") or LIVE_MEDIA.search(urlparse(spec["src"]).path)
    ):
        spec["src"] = "about:blank"  # framed sites or raw manifests won't play directly; live.py replaces this once ready
    return spec


def _pomodoro_state() -> dict:
    """Read-only check for Pomodoro Bar (239058710) or any generic Pomodoro/break add-on."""
    for mod in list(sys.modules.values()):
        f = getattr(mod, "__file__", "") or ""
        if "addons21" not in f or mod is sys.modules.get(__name__):
            continue
        # Pomodoro Bar (239058710)
        if hasattr(mod, "_resting") and hasattr(mod, "_rest_elapsed") and hasattr(mod, "_rest_duration_seconds"):
            if getattr(mod, "_resting", False):
                left = max(0, int(mod._rest_duration_seconds() - mod._rest_elapsed()))
                return {"rest_left": left, "rest_done": left <= 0}
            if getattr(mod, "_completed", False):
                return {"rest_done": True}
        # Generic duck-typed Pomodoro/timer add-ons
        resting = getattr(mod, "is_resting", None)
        if callable(resting):
            resting = resting()
        if resting is None:
            resting = getattr(mod, "resting", getattr(mod, "on_break", False))
        if resting:
            for attr in ("rest_remaining", "break_remaining", "remaining_seconds", "seconds_left"):
                val = getattr(mod, attr, None)
                val = val() if callable(val) else val
                if isinstance(val, (int, float)) and math.isfinite(val):
                    left = max(0, int(val))
                    return {"rest_left": left, "rest_done": left <= 0}
            return {}
    return {}


def _break_info() -> dict:
    if not _break_mode:
        return {"active": False}
    info = {"active": True, "elapsed": max(0, int(time.monotonic() - _break_started))}
    try:
        info.update(_pomodoro_state())
    except Exception:
        pass
    return info


def _stop_break_timer() -> None:
    global _break_timer
    if _break_timer is not None:
        try:
            _break_timer.stop()
            _break_timer.deleteLater()
        except Exception:
            pass
        _break_timer = None


def enter_break() -> None:
    global _break_mode, _break_started, _break_from_review, _break_timer
    if not conf()["on"] and active_urls():
        save(on=True, paused=False)
    _break_from_review = mw.state == "review"
    _break_mode = True
    _break_started = time.monotonic()
    _stop_break_timer()
    try:
        from aqt.qt import QTimer
        _break_timer = QTimer(mw)
        _break_timer.setInterval(1000)
        _break_timer.timeout.connect(
            lambda: getattr(mw, "web", None) and mw.web.eval(
                "window.backdrop && backdrop.updateBreak(%s)" % json.dumps(_break_info())
            )
        )
        _break_timer.start()
    except Exception:
        pass
    if mw.state == "review" and hasattr(mw, "moveToState"):
        mw.moveToState("overview")
    else:
        refresh()


def exit_break() -> None:
    global _break_mode, _break_from_review
    was_review = _break_from_review
    _break_mode = False
    _break_from_review = False
    _stop_break_timer()
    if was_review and mw.state == "overview" and hasattr(mw, "moveToState"):
        mw.moveToState("review")
    else:
        refresh()


def state(plate: bool) -> dict:
    c = conf()
    urls = active_urls(c)
    s = {k: c[k] for k in ("url", "strength", "plate_opacity", "volume", "paused", "picture", "panel", "quality", "title", "recent")}
    s["urls"] = urls
    s["muted"] = [u for u in (c.get("muted") or []) if isinstance(u, str) and u in urls]
    s["break_info"] = _break_info()
    s["auto_note"] = live.effective_note(c["quality"], max(1, len(urls)), multi_fps=c.get("multi_fps", "auto"))
    s.update(on=bool(c["on"] and urls), plate=plate, lite=software_rendering(), streams=[])
    if s["on"]:
        streams = [
            _stream_spec(u, int(position) if i == 0 and len(urls) == 1 else 0, c["paused"], _recent_title(c, u))
            for i, u in enumerate(urls)
        ]
        s["streams"] = streams
        s.update(kind=streams[0]["kind"], src=streams[0]["src"], start=streams[0].get("start", 0))
        if streams[0].get("live"):
            s["live"] = True
    return s


def start_js(s: dict) -> str:
    # "</" escaped so a link can't close the <script> tag it is written into.
    return "window.backdrop && backdrop.start(%s)" % json.dumps(s).replace("</", "<\\/")


def allow_autoplay() -> None:
    # The reviewer turns autoplay off per card for decks without audio autoplay, which would stop the video starting.
    # shortcut: this also lets <audio autoplay> in card HTML play on such decks; rare, revisit if reported
    if conf()["on"] and hasattr(mw.web, "setPlaybackRequiresGesture"):  # missing before Anki 2.1.50
        mw.web.setPlaybackRequiresGesture(False)


def on_set_content(web_content, context) -> None:
    if not isinstance(context, (Reviewer, Overview)):
        return
    web_content.css.append(f"/_addons/{PKG}/web/backdrop.css")
    web_content.js.append(f"/_addons/{PKG}/web/backdrop.js")
    s = state(isinstance(context, Reviewer))
    web_content.body += f"<script>{start_js(s)}</script>"
    allow_autoplay()
    try_live(s)


def on_js_message(handled, message, context):
    global position
    if message.startswith("backdrop:error:"):
        # Format: backdrop:error:<code> or backdrop:error:<code>:<slotUrl>
        parts = message.split(":", 3)
        code = parts[2]
        slot_url = parts[3] if len(parts) > 3 and parts[3] else conf()["url"]
        if code in ("101", "150", "152", "153"):
            tooltip("This video can't play inside Anki: its owner only allows it on YouTube. Try another link.", period=6000)
        elif code == "live":
            if slot_url:
                go_live(slot_url)
        elif code == "stream":
            # The stream ended or its address expired (YouTube's last about 6 hours): look it up once more.
            if slot_url:
                live.forget(slot_url)
                # Once per 10 minutes, so a long session survives several expiries but an ended stream doesn't loop.
                if time.time() - _retried.get(slot_url, 0) > 600:
                    _retried[slot_url] = time.time()
                    go_live(slot_url)
                else:
                    tooltip("The live stream stopped. It may have ended.", period=6000)
        elif code == "video":
            # Anki's browser lacks H.264, which most .mp4 files use; fall back once through mpv -> WebM before giving up.
            if slot_url and not live.ready(slot_url) and slot_url not in _not_live:
                _direct_urls.add(slot_url)
                go_live(slot_url, quiet=True, direct=True)
            else:
                tooltip("Anki can't play this video file's format. Try a YouTube link or a .webm file.", period=6000)
        else:
            tooltip("This video couldn't be played. It may be private, removed, or the link may be wrong.", period=6000)
        return True, None
    if message.startswith("backdrop:set:"):
        try:
            apply(json.loads(message.split(":", 2)[2]))
        except (ValueError, TypeError, AttributeError):
            pass
        return True, None
    if message.startswith("backdrop:title:"):
        raw_t = message.split(":", 2)[2]
        try:
            payload = json.loads(raw_t)
            if isinstance(payload, dict) and "title" in payload:
                set_title(str(payload["title"])[:300], payload.get("url"))
                return True, None
        except ValueError:
            pass
        set_title(raw_t[:300])
        return True, None
    if message == "backdrop:clip":
        mw.web.eval("window.backdrop && backdrop.offer(%s)" % json.dumps(clipboard_link()))
        return True, None
    if message == "backdrop:browse":
        open_browse()
        return True, None
    if message == "backdrop:break:1":
        enter_break()
        return True, None
    if message == "backdrop:break:0":
        exit_break()
        return True, None
    if not message.startswith("backdrop:time:"):
        return handled
    try:
        val = float(message.split(":", 2)[2])
        if math.isfinite(val):
            position = max(0.0, val)
            if abs(position - _saved_position) >= 30:
                save_position()
    except ValueError:
        pass
    return True, None


def go_live(url: str, quiet: bool = False, direct: bool = False) -> None:
    """Live streams and HLS/H.264 URLs convert via mpv (plus yt-dlp when extracting from a webpage)."""
    global _declined
    if url in _looking or live.ready(url):
        return
    parsed_src = source(url, 0)["src"]
    use_direct = direct or (url in _direct_urls) or bool(LIVE_MEDIA.search(urlparse(parsed_src).path))
    if not use_direct:
        if sys.version_info < (3, 10):  # yt-dlp needs Anki 23.10+; say so before offering a download that can't work
            if not quiet:
                tooltip("Live streams need Anki 23.10 or newer.", period=6000)
            return
        if not os.path.exists(live.helper_path(ADDON_DIR)):
            if _declined or not askUser(
                    "This looks like a live stream or web video. Anki needs a free helper called yt-dlp "
                    "(about 3 MB, from github.com/yt-dlp) to pull the video feed.\n\nDownload it now?",
                    title="Backdrop Video"):
                _declined = True
                if not quiet:
                    tooltip("Without the helper, try a YouTube, .webm, or .m3u8 stream link instead.", period=8000)
                return
    _looking.add(url)
    raw_quality = conf()["quality"]
    multi_fps = conf().get("multi_fps", "auto")
    stream_count = max(1, len(active_urls()))
    multi = stream_count >= 2
    quality = "360" if (raw_quality == "auto" and multi) else raw_quality
    cap_fps = raw_quality == "auto" or multi
    if not quiet:
        tooltip("Starting the live stream…")

    def task():
        if use_direct:
            return live.register_direct(
                parsed_src if direct else url, quality, stream_count=stream_count, cap_fps=cap_fps, multi_fps=multi_fps
            )
        live.download_helper(ADDON_DIR, if_missing=True)
        try:
            return live.find(ADDON_DIR, url, quality, stream_count=stream_count, cap_fps=cap_fps, multi_fps=multi_fps)
        except Exception as e:
            # Sites change often and yt-dlp keeps up, so a day-old helper is updated once and retried.
            if str(e) in ("old-python", "no-mpv", "no-encoder") or time.time() - os.path.getmtime(live.helper_path(ADDON_DIR)) < 86400:
                raise
            live.download_helper(ADDON_DIR)
            return live.find(ADDON_DIR, url, quality, stream_count=stream_count, cap_fps=cap_fps, multi_fps=multi_fps)

    def done(fut):
        _looking.discard(url)
        try:
            title = fut.result()
        except Exception as e:
            if direct:
                _not_live.add(url)
                tooltip("Anki can't play this video file's format. Try a YouTube link or a .webm file.", period=6000)
                return
            if quiet:
                _not_live.add(url)
                return
            if str(e) == "no-encoder":
                tooltip("This computer's mpv can't convert live streams for Anki.", period=6000)
            elif str(e) == "no-mpv":
                tooltip("Live streams need mpv. Install it from mpv.io, then try again.", period=6000)
            else:
                tooltip("Couldn't start this live stream. It may have ended, or the link may be wrong.", period=6000)
            return
        c = conf()
        if c["on"] and url in active_urls(c):
            if title:
                set_title(title, url)
            refresh()

    mw.taskman.run_in_background(task, done)


def set_title(title: str, url: str = None) -> None:
    c = conf()
    target = url or c["url"]
    changes = {"recent": [{**r, "title": title} if r.get("url") == target else r for r in c["recent"]]}
    if target == c["url"]:
        changes["title"] = title
    save(**changes)


def try_live(s: dict, prompt_if_missing: bool = False, only_url: str = None) -> None:
    """Convert .m3u8/.mpd directly or extract video/stream links from web pages with yt-dlp."""
    if not s.get("on"):
        return
    streams = s.get("streams") or ([{"url": conf()["url"], "kind": s.get("kind")}] if conf()["url"] else [])
    for slot in streams:
        url = slot.get("url")
        if not url or (only_url and url != only_url) or slot.get("kind") != "page" or url in _not_live:
            continue
        parsed = urlparse(source(url, 0)["src"])
        if LIVE_MEDIA.search(parsed.path):
            go_live(url, quiet=False)
            continue
        known = STREAM_SITES.search(parsed.hostname or "")
        if known or prompt_if_missing or os.path.exists(live.helper_path(ADDON_DIR)):
            go_live(url, quiet=not known)


def play_url(url: str) -> None:
    """Start a link as the single active backdrop, remembering it at the top of the recent list."""
    global position
    url = url.strip()
    _not_live.discard(url)
    live.forget(url)
    c = conf()
    for prev in active_urls(c):
        if prev != url:
            live.forget(prev)
    if url != c["url"]:
        position = 0.0
    recent = _remember_recent(c, url)
    title = recent[0]["title"]
    save(url=url, urls=[url], muted=[], on=True, paused=False, title=title, recent=recent)


def add_url(url: str) -> bool:
    """Add a link alongside currently playing backdrops (up to MAX_STREAMS)."""
    url = url.strip()
    if not url:
        return False
    c = conf()
    cur = active_urls(c) if c["on"] else []
    if not cur:
        play_url(url)
        return True
    if url in cur or len(cur) >= MAX_STREAMS:
        return False
    _not_live.discard(url)
    live.forget(url)
    new_urls = cur + [url]
    muted = [u for u in (c.get("muted") or []) if u != url and u in new_urls]
    recent = _remember_recent(c, url)
    save(url=new_urls[0], urls=new_urls, muted=muted, on=True, paused=False, recent=recent)
    return True


def remove_url(url: str) -> None:
    """Remove one active backdrop slot (or turn off & clear if it was the last one)."""
    global position
    c = conf()
    cur = active_urls(c)
    live.forget(url)
    new_urls = [u for u in cur if u != url]
    muted = [u for u in (c.get("muted") or []) if u in new_urls]
    if not new_urls:
        position = 0.0
        save(on=False, url="", urls=[], muted=[], title="")
        if _break_mode:
            exit_break()
        return
    primary = new_urls[0]
    if primary != c["url"]:
        position = 0.0
    save(url=primary, urls=new_urls, muted=muted, title=_recent_title(c, primary))


def set_quality(quality: str) -> None:
    if quality not in VALID_QUALITIES:
        return
    c = conf()
    if c["quality"] == quality:
        return
    save(quality=quality)
    if _dialog is not None:
        idx = _dialog.quality.findData(quality)
        if idx >= 0:
            _dialog.quality.blockSignals(True)
            _dialog.quality.setCurrentIndex(idx)
            _dialog.quality.blockSignals(False)
    if c["on"]:
        to_reload = [u for u in active_urls(c) if live.ready(u)]
        for u in to_reload:
            live.forget(u)
        refresh()
        for u in to_reload:
            go_live(u)


def apply(changes: dict) -> None:
    """Changes sent by the quick-controls panel; the page is untrusted, so each value is checked."""
    if not isinstance(changes, dict):
        return
    raw_url = changes.get("url")
    url = raw_url.strip() if isinstance(raw_url, str) and 0 < len(raw_url.strip()) <= 2000 else ""
    if url:
        play_url(url)
    raw_add = changes.get("add_url")
    added_url = raw_add.strip() if isinstance(raw_add, str) and 0 < len(raw_add.strip()) <= 2000 else ""
    if added_url:
        if not add_url(added_url):
            added_url = ""
    raw_rem = changes.get("remove_url")
    removed_url = raw_rem.strip() if isinstance(raw_rem, str) and raw_rem.strip() else ""
    if removed_url:
        remove_url(removed_url)
    raw_forget = changes.get("forget_recent")
    forgot_recent = raw_forget.strip() if isinstance(raw_forget, str) and raw_forget.strip() else ""
    if forgot_recent:
        c = conf()
        save(recent=[r for r in (c.get("recent") or []) if r.get("url") != forgot_recent])
    flags = {k: v for k, v in changes.items() if k in ("on", "paused", "picture", "panel") and isinstance(v, bool)}
    if flags.get("on"):
        flags["paused"] = False
    numbers = {k: max(0, min(100, int(v))) for k, v in changes.items()
               if k in ("strength", "plate_opacity", "volume") and isinstance(v, (int, float))
               and not isinstance(v, bool) and math.isfinite(v)}
    extra = {}
    if isinstance(changes.get("muted"), list):
        active = set(active_urls())
        extra["muted"] = [u for u in changes["muted"] if isinstance(u, str) and u in active][:MAX_STREAMS]
    save(**flags, **numbers, **extra)
    target_new = url or added_url
    if target_new or removed_url or forgot_recent or "on" in flags:
        refresh(ask_live=bool(target_new), only_url=target_new or None)
    quality = changes.get("quality")
    if isinstance(quality, str) and quality in VALID_QUALITIES:
        set_quality(quality)


def refresh(ask_live: bool = False, only_url: str = None) -> None:
    """Apply settings to the page on screen without reloading it."""
    if mw.state in ("review", "overview"):
        allow_autoplay()
        s = state(mw.state == "review")
        mw.web.eval(start_js(s))
        try_live(s, prompt_if_missing=ask_live, only_url=only_url)


def save_position() -> None:
    global _saved_position
    _saved_position = position
    save(time=int(position))


def on_state_will_change(new_state: str, old_state: str) -> None:
    global _break_mode, _break_from_review
    if old_state in ("review", "overview"):
        save_position()
        if getattr(mw, "web", None):
            mw.web.eval("window.backdrop && backdrop.peek(false)")
    if _break_mode and not (old_state == "review" and new_state == "overview"):
        _break_mode = False
        _break_from_review = False
        _stop_break_timer()


def on_profile_will_close() -> None:
    _stop_break_timer()
    save_position()
    live.stop_all()


def duck(on: bool) -> None:
    """Card audio (sound files, Anki's text-to-speech) is easier to hear with the backdrop turned down meanwhile."""
    if conf()["on"] and mw.state in ("review", "overview"):
        mw.web.eval("window.backdrop && backdrop.duck(%s)" % ("true" if on else "false"))


def clipboard_link() -> str:
    """Any copied http(s) link or bare video link, offered so the user doesn't have to paste it."""
    text = mw.app.clipboard().text().strip()
    if re.match(r"^https?://[\w.-]+\.\w{2,}(:\d+)?(/[^\s]*)?$", text, re.I):
        return text
    if not re.match(r"^[\w.-]+\.\w{2,}(:\d+)?/\S*$", text):
        return ""
    parsed = urlparse(source(text, 0)["src"])
    stream_site = STREAM_SITES.search(parsed.hostname or "")
    return text if source(text, 0)["kind"] != "page" or stream_site or LIVE_MEDIA.search(parsed.path) else ""


class BackdropDialog(QDialog):
    def __init__(self):
        super().__init__(mw)
        self.setWindowTitle("Backdrop Video")
        c = conf()
        pasted = clipboard_link()
        self.link = QLineEdit(pasted if pasted and pasted != c["url"] else c["url"])
        self.link.setPlaceholderText("Paste a YouTube or video link")
        self.link.setMinimumWidth(380)
        self.link.returnPressed.connect(self.play)

        self.strength = self.slider(c["strength"], lambda v: self.live(strength=v))
        self.strength.setAccessibleName("Backdrop strength")
        self.plate_opacity = self.slider(c["plate_opacity"], lambda v: self.live(plate_opacity=v))
        self.plate_opacity.setAccessibleName("Study card opacity")
        self.volume = self.slider(c["volume"], lambda v: self.live(volume=v))
        self.volume.setAccessibleName("Volume")
        self.quality = QComboBox()
        for label, value in QUALITIES:
            self.quality.addItem(label, value)
        self.quality.setCurrentIndex(max(0, self.quality.findData(c["quality"])))
        self.quality.setToolTip("Applies to live streams (Twitch, YouTube Live, etc.). Regular YouTube videos adjust automatically.")
        self.quality.currentIndexChanged.connect(self.set_quality)

        browse_btn = QPushButton("Browse…")
        browse_btn.setAutoDefault(False)
        browse_btn.setToolTip("Search or browse in Anki to pick a video or stream")
        browse_btn.clicked.connect(open_browse)
        play = QPushButton("Play")
        play.setAutoDefault(False)
        play.setDefault(False)
        play.clicked.connect(self.play)
        self.add_btn = QPushButton("Add (+)")
        self.add_btn.setAutoDefault(False)
        self.add_btn.setToolTip("Add alongside currently playing videos (up to 4)")
        self.add_btn.clicked.connect(self.add_stream)
        self.off = QPushButton("Turn Off")
        self.off.setAutoDefault(False)
        self.off.clicked.connect(self.turn_off)
        self.off.setEnabled(c["on"])
        buttons = QHBoxLayout()
        buttons.addWidget(browse_btn)
        buttons.addStretch()
        buttons.addWidget(self.off)
        buttons.addWidget(self.add_btn)
        buttons.addWidget(play)

        form = QFormLayout()
        form.addRow("Video link", self.link)
        form.addRow("Backdrop", self.labelled(self.strength, "Faint", "Clear"))
        form.addRow("Study card", self.labelled(self.plate_opacity, "Clear", "Solid"))
        form.addRow("Volume", self.volume)
        form.addRow("Stream quality", self.quality)
        hint = QLabel(
            f"Plays behind your cards and on the study screen ({live.effective_note(c['quality'], max(1, len(active_urls(c))), multi_fps=c.get('multi_fps', 'auto'))}). "
            "Edge tab has quick controls. Hold P to peek."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray")
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addLayout(buttons)

    def slider(self, value: int, on_change) -> QSlider:
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(0, 100)
        s.setValue(value)
        s.valueChanged.connect(on_change)
        return s

    def labelled(self, slider, left: str, right: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel(left))
        row.addWidget(slider)
        row.addWidget(QLabel(right))
        return row

    def live(self, **change) -> None:
        save(**change)
        refresh()

    def set_quality(self) -> None:
        set_quality(self.quality.currentData())

    def play(self) -> None:
        url = self.link.text().strip()
        if not url or len(url) > 2000:
            return
        play_url(url)
        self.off.setEnabled(True)
        refresh(ask_live=True)

    def add_stream(self) -> None:
        url = self.link.text().strip()
        if not url or len(url) > 2000:
            return
        if not add_url(url):
            tooltip(f"Already playing {MAX_STREAMS} videos (or this link is already active).", period=4000)
            return
        self.off.setEnabled(True)
        refresh(ask_live=True, only_url=url)

    def turn_off(self) -> None:
        save(on=False)
        self.off.setEnabled(False)
        refresh()


def open_dialog() -> None:
    global _dialog
    if _dialog is None:
        _dialog = BackdropDialog()
        _dialog.finished.connect(lambda _: globals().update(_dialog=None))
    _dialog.show()
    _dialog.raise_()
    _dialog.activateWindow()


def open_browse() -> None:
    """Built-in browser pop-up with quick site shortcuts, smart search, and Play / + Add buttons."""
    global _browse, _last_browse_url
    if _browse is not None:
        _browse.show()
        _browse.raise_()
        _browse.activateWindow()
        return
    try:
        from aqt.qt import QUrl
        from PyQt6.QtWebEngineWidgets import QWebEngineView
    except Exception:
        tooltip("Built-in browser isn't available in this Qt build.", period=4000)
        return

    dlg = QDialog(mw)
    dlg.setWindowTitle("Backdrop Video — Find a Video or Stream")
    dlg.resize(980, 680)
    view = QWebEngineView(dlg)

    sites_bar = QHBoxLayout()
    sites_bar.addWidget(QLabel("Quick jump:"))
    for label, site_url in (
        ("YouTube", "https://www.youtube.com"),
        ("Twitch", "https://www.twitch.tv"),
        ("Kick", "https://kick.com"),
        ("Google", "https://www.google.com"),
    ):
        btn = QPushButton(label)
        btn.setAutoDefault(False)
        btn.clicked.connect(lambda _c=False, u=site_url: view.setUrl(QUrl(u)))
        sites_bar.addWidget(btn)
    sites_bar.addStretch()

    bar = QHBoxLayout()
    back_b = QPushButton("←")
    fwd_b = QPushButton("→")
    rel_b = QPushButton("↻")
    for b in (back_b, fwd_b, rel_b):
        b.setFixedWidth(32)
        b.setAutoDefault(False)
    addr = QLineEdit()
    addr.setPlaceholderText("Search current site or paste a URL, then press Enter")
    go_b = QPushButton("Go")
    go_b.setAutoDefault(False)
    bar.addWidget(back_b)
    bar.addWidget(fwd_b)
    bar.addWidget(rel_b)
    bar.addWidget(addr, 1)
    bar.addWidget(go_b)

    def add_label() -> str:
        n = len(active_urls()) if conf()["on"] else 0
        return f"+ Add to Multi-View ({n}/{MAX_STREAMS})"

    add_b = QPushButton(add_label())
    add_b.setAutoDefault(False)
    play_b = QPushButton("Play This Page")
    play_b.setAutoDefault(False)
    play_b.setDefault(False)
    bottom_bar = QHBoxLayout()
    bottom_bar.addWidget(QLabel("Navigate to the video/stream page you want, then:"))
    bottom_bar.addStretch()
    bottom_bar.addWidget(add_b)
    bottom_bar.addWidget(play_b)

    lay = QVBoxLayout(dlg)
    lay.addLayout(sites_bar)
    lay.addLayout(bar)
    lay.addWidget(view, 1)
    lay.addLayout(bottom_bar)

    def go():
        txt = addr.text().strip()
        if not txt:
            return
        if txt.lower().startswith(("http://", "https://")):
            url = txt
        elif " " not in txt and "." in txt:
            url = "https://" + txt
        else:
            host = (view.url().host() or "").lower()
            q = quote_plus(txt)
            if "youtube.com" in host or "youtu.be" in host:
                url = f"https://www.youtube.com/results?search_query={q}"
            elif "twitch.tv" in host:
                url = f"https://www.twitch.tv/search?term={q}"
            elif "kick.com" in host:
                url = f"https://kick.com/search?query={q}"
            else:
                url = f"https://www.google.com/search?q={q}"
        view.setUrl(QUrl(url))

    def _silence_popup():
        view.page().runJavaScript("document.querySelectorAll('video,audio').forEach(m=>{try{m.pause();m.muted=true;}catch(e){}})")

    def pick(replace: bool):
        url = view.url().toString().strip()
        if not url or not url.lower().startswith(("http://", "https://")):
            return
        _silence_popup()
        if replace:
            play_url(url)
            refresh(ask_live=True)
            dlg.accept()
        elif add_url(url):
            add_b.setText(add_label())
            refresh(ask_live=True, only_url=url)
            tooltip(f"Added to backdrop ({len(active_urls())}/{MAX_STREAMS})", period=3000)
        else:
            tooltip(f"Already playing {MAX_STREAMS} videos (or this link is already active).", period=4000)

    def _on_url(u):
        global _last_browse_url
        s = u.toString()
        addr.setText(s)
        if s.startswith(("http://", "https://")):
            _last_browse_url = s

    back_b.clicked.connect(view.back)
    fwd_b.clicked.connect(view.forward)
    rel_b.clicked.connect(view.reload)
    addr.returnPressed.connect(go)
    go_b.clicked.connect(go)
    play_b.clicked.connect(lambda: pick(True))
    add_b.clicked.connect(lambda: pick(False))
    view.urlChanged.connect(_on_url)

    def cleanup(_res):
        global _browse
        _browse = None
        try:
            view.setUrl(QUrl("about:blank"))
            view.deleteLater()
        except Exception:
            pass

    dlg.finished.connect(cleanup)
    view.setUrl(QUrl(_last_browse_url or "https://www.youtube.com"))
    _browse = dlg
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


_peek_filter = None


def _install_peek_filter() -> None:
    """Catch Hold-P even when focus sits on Anki's bottom answer bar or main window instead of mw.web."""
    global _peek_filter
    if _peek_filter is not None or not hasattr(mw.app, "installEventFilter"):
        return
    try:
        from aqt.qt import QEvent, QObject, QPlainTextEdit, QTextEdit
    except Exception:
        return

    class _PeekFilter(QObject):
        def __init__(self):
            super().__init__(mw)
            self._peeking = False

        @staticmethod
        def _in_widget(obj, target) -> bool:
            cur = obj
            while cur is not None:
                if cur is target:
                    return True
                cur = cur.parent() if hasattr(cur, "parent") else None
            return False

        def eventFilter(self, obj, event):
            t = event.type()
            if t == QEvent.Type.MouseButtonPress and mw.state in ("review", "overview") and conf()["panel"]:
                bottom = getattr(mw, "bottomWeb", None)
                if bottom is not None and self._in_widget(obj, bottom):
                    apply({"panel": False})
                    mw.web.eval("window.backdrop && backdrop.closePanel()")
                return False
            if t == QEvent.Type.WindowDeactivate and self._peeking:
                self._peeking = False
                mw.web.eval("window.backdrop && backdrop.peek(false)")
                return False
            if t not in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                return False
            if event.key() != Qt.Key.Key_P or event.isAutoRepeat():
                return False
            if t == QEvent.Type.KeyRelease:
                if self._peeking:
                    self._peeking = False
                    mw.web.eval("window.backdrop && backdrop.peek(false)")
                return False
            if event.modifiers() != Qt.KeyboardModifier.NoModifier:
                return False
            if mw.state not in ("review", "overview") or not conf()["on"] or mw.app.activeWindow() is not mw:
                return False
            fw = mw.app.focusWidget()
            if self._in_widget(fw, mw.web) or isinstance(fw, (QLineEdit, QTextEdit, QPlainTextEdit, QComboBox)):
                return False
            self._peeking = True
            mw.web.eval("window.backdrop && backdrop.peek(true)")
            return False

    _peek_filter = _PeekFilter()
    mw.app.installEventFilter(_peek_filter)


_install_peek_filter()
action = mw.form.menuTools.addAction("Backdrop Video…")
action.setShortcut("Ctrl+Shift+B")
action.triggered.connect(open_dialog)
mw.addonManager.setConfigAction(__name__, open_dialog)
gui_hooks.webview_will_set_content.append(on_set_content)
gui_hooks.reviewer_did_show_question.append(lambda _card: allow_autoplay())
gui_hooks.webview_did_receive_js_message.append(on_js_message)
gui_hooks.state_will_change.append(on_state_will_change)
gui_hooks.profile_will_close.append(on_profile_will_close)
if hasattr(mw, "app") and hasattr(mw.app, "aboutToQuit"):
    mw.app.aboutToQuit.connect(live.stop_all)
gui_hooks.av_player_will_play.append(lambda _tag: duck(True))
gui_hooks.av_player_did_end_playing.append(lambda _player: duck(False))
