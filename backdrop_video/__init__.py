"""Backdrop Video: play an online video softly behind your cards while you study."""

import json
import math
import os
import re
import sys
import time
from urllib.parse import parse_qs, urlparse

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
STREAM_SITES = re.compile(r"(^|\.)(youtube\.com|twitch\.tv|kick\.com)$")  # worth offering the helper for
YT_ID = re.compile(r"(?:youtu\.be/|/embed/|/shorts/|/live/|[?&]v=)([\w-]{11})")

DEFAULTS = {"url": "", "on": False, "strength": 30, "volume": 50, "time": 0,
            "paused": False, "picture": True, "panel": False, "quality": "auto", "title": "", "recent": []}
QUALITIES = [("Auto", "auto"), ("360p", "360"), ("480p", "480"), ("720p", "720"), ("1080p", "1080")]
VALID_QUALITIES = {v for _, v in QUALITIES}
_dialog = None
ADDON_DIR = os.path.dirname(__file__)
_looking = set()  # links whose live stream is being looked up
_declined = False  # said no to the helper download this session
_retried = {}  # link -> when its stream was last looked up again after it stopped
_not_live = set()  # links a quiet lookup found no stream for, so it isn't repeated every page


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


def state(plate: bool) -> dict:
    c = conf()
    s = {k: c[k] for k in ("url", "strength", "volume", "paused", "picture", "panel", "quality", "title", "recent")}
    s.update(on=bool(c["on"] and c["url"]), plate=plate, lite=software_rendering())
    if s["on"]:
        s.update(source(c["url"], int(position), c["paused"]))
        if live.ready(c["url"]):
            s.update(kind="video", src=live.address(c["url"]), start=0, live=True)
        elif s["kind"] == "page" and STREAM_SITES.search(urlparse(s["src"]).hostname or ""):
            s["src"] = "about:blank"  # these sites refuse to be framed; the stream replaces this once found
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
        # YouTube codes 101/150/152/153 mean the owner blocks playback outside youtube.com; others mean removed, private or a bad link.
        code = message.split(":")[2]
        if code in ("101", "150", "152", "153"):
            tooltip("This video can't play inside Anki: its owner only allows it on YouTube. Try another link.", period=6000)
        elif code == "live":
            url = conf()["url"]
            if url:
                go_live(url)
        elif code == "stream":
            # The stream ended or its address expired (YouTube's last about 6 hours): look it up once more.
            url = conf()["url"]
            if url:
                live.forget(url)
                # Once per 10 minutes, so a long session survives several expiries but an ended stream doesn't loop.
                if time.time() - _retried.get(url, 0) > 600:
                    _retried[url] = time.time()
                    go_live(url)
                else:
                    tooltip("The live stream stopped. It may have ended.", period=6000)
        elif code == "video":
            # Anki's browser has no H.264, the format most .mp4 files use.
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
        set_title(message.split(":", 2)[2][:300])
        return True, None
    if message == "backdrop:clip":
        mw.web.eval("window.backdrop && backdrop.offer(%s)" % json.dumps(clipboard_link()))
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


def go_live(url: str, quiet: bool = False) -> None:
    """Live streams need yt-dlp (asked for once) to find the stream; the backdrop switches to it when found."""
    global _declined
    if url in _looking or live.ready(url):
        return
    if sys.version_info < (3, 10):  # yt-dlp needs Anki 23.10+; say so before offering a download that can't work
        if not quiet:
            tooltip("Live streams need Anki 23.10 or newer.", period=6000)
        return
    if not os.path.exists(live.helper_path(ADDON_DIR)):
        if _declined or not askUser(
                "This looks like a live stream. Anki can't play those directly, but it can with a free "
                "helper called yt-dlp (about 3 MB, from github.com/yt-dlp).\n\nDownload it now?",
                title="Backdrop Video"):
            _declined = True
            if not quiet:
                tooltip("Without the helper, try a long regular video instead, like a 10-hour lofi video.", period=8000)
            return
    _looking.add(url)
    quality = conf()["quality"]
    if not quiet:
        tooltip("Starting the live stream…")

    def task():
        if not os.path.exists(live.helper_path(ADDON_DIR)):
            live.download_helper(ADDON_DIR)
        try:
            return live.find(ADDON_DIR, url, quality)
        except Exception as e:
            # Sites change often and yt-dlp keeps up, so a day-old helper is updated once and retried.
            if str(e) in ("old-python", "no-mpv", "no-encoder") or time.time() - os.path.getmtime(live.helper_path(ADDON_DIR)) < 86400:
                raise
            live.download_helper(ADDON_DIR)
            return live.find(ADDON_DIR, url, quality)

    def done(fut):
        _looking.discard(url)
        try:
            title = fut.result()
        except Exception as e:
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
        if conf()["url"] == url:
            if title:
                set_title(title)
            refresh()

    mw.taskman.run_in_background(task, done)


def set_title(title: str) -> None:
    c = conf()
    save(title=title, recent=[{**r, "title": title} if r.get("url") == c["url"] else r for r in c["recent"]])


def try_live(s: dict) -> None:
    """Other links (Twitch, a channel's /live page) may be live streams; if not, the page stays framed."""
    url = conf()["url"]
    if not (s["on"] and s["kind"] == "page") or url in _not_live:
        return
    known = STREAM_SITES.search(urlparse(source(url, 0)["src"]).hostname or "")
    if known or os.path.exists(live.helper_path(ADDON_DIR)):
        go_live(url, quiet=not known)


def play_url(url: str) -> None:
    """Start a link, remembering it at the top of the recent list."""
    global position
    c = conf()
    if url != c["url"]:
        position = 0.0
    current = [{"url": c["url"], "title": c["title"]}] if c["url"] else []  # may predate the recent list
    known = [r for r in c["recent"] + current if r.get("url") == url]
    title = known[0].get("title", "") if known else ""
    rest, seen = [], {url}
    for r in current + c["recent"]:
        if r.get("url") not in seen:
            seen.add(r.get("url"))
            rest.append(r)
    save(url=url, on=True, paused=False, title=title, recent=([{"url": url, "title": title}] + rest)[:5])


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
    if c["on"] and live.ready(c["url"]):
        live.forget(c["url"])
        refresh()
        go_live(c["url"])


def apply(changes: dict) -> None:
    """Changes sent by the quick-controls panel; the page is untrusted, so each value is checked."""
    if not isinstance(changes, dict):
        return
    raw_url = changes.get("url")
    url = raw_url.strip() if isinstance(raw_url, str) and 0 < len(raw_url.strip()) <= 2000 else ""
    if url:
        play_url(url)
    flags = {k: v for k, v in changes.items() if k in ("on", "paused", "picture", "panel") and isinstance(v, bool)}
    if flags.get("on"):
        flags["paused"] = False
    numbers = {k: max(0, min(100, int(v))) for k, v in changes.items()
               if k in ("strength", "volume") and isinstance(v, (int, float))
               and not isinstance(v, bool) and math.isfinite(v)}
    save(**flags, **numbers)
    if url or "on" in flags:
        refresh()
    quality = changes.get("quality")
    if isinstance(quality, str) and quality in VALID_QUALITIES:
        set_quality(quality)


def refresh() -> None:
    """Apply settings to the page on screen without reloading it."""
    if mw.state in ("review", "overview"):
        allow_autoplay()
        s = state(mw.state == "review")
        mw.web.eval(start_js(s))
        try_live(s)


def save_position() -> None:
    global _saved_position
    _saved_position = position
    save(time=int(position))


def on_state_will_change(_new_state: str, old_state: str) -> None:
    if old_state in ("review", "overview"):
        save_position()


def on_profile_will_close() -> None:
    save_position()
    live.stop_all()


def duck(on: bool) -> None:
    """Card audio (sound files, Anki's text-to-speech) is easier to hear with the backdrop turned down meanwhile."""
    if conf()["on"] and mw.state in ("review", "overview"):
        mw.web.eval("window.backdrop && backdrop.duck(%s)" % ("true" if on else "false"))


def clipboard_link() -> str:
    """A copied YouTube or video-file link, offered so the user doesn't have to paste it."""
    text = mw.app.clipboard().text().strip()
    if not re.match(r"(https?://)?[\w.-]+\.\w{2,}/\S*$", text):
        return ""
    stream_site = STREAM_SITES.search(urlparse(source(text, 0)["src"]).hostname or "")
    return text if source(text, 0)["kind"] != "page" or stream_site else ""


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
        self.volume = self.slider(c["volume"], lambda v: self.live(volume=v))
        self.volume.setAccessibleName("Volume")
        self.quality = QComboBox()
        for label, value in QUALITIES:
            self.quality.addItem(label, value)
        self.quality.setCurrentIndex(max(0, self.quality.findData(c["quality"])))
        self.quality.setToolTip("Applies to live streams (Twitch, YouTube Live, etc.). Regular YouTube videos adjust automatically.")
        self.quality.currentIndexChanged.connect(self.set_quality)

        play = QPushButton("Play")
        play.setDefault(True)
        play.clicked.connect(self.play)
        self.off = QPushButton("Turn Off")
        self.off.clicked.connect(self.turn_off)
        self.off.setEnabled(c["on"])
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.off)
        buttons.addWidget(play)

        form = QFormLayout()
        form.addRow("Video link", self.link)
        form.addRow("Backdrop", self.labelled(self.strength, "Faint", "Clear"))
        form.addRow("Volume", self.volume)
        form.addRow("Stream quality", self.quality)
        hint = QLabel("Plays behind your cards and on the deck's study screen. While studying, the tab on the right edge "
                       "has quick controls (pause, hide picture, recent videos). Hold P to peek.")
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
        if not url:
            return
        play_url(url)
        self.off.setEnabled(True)
        refresh()

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
