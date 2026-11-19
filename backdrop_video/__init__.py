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
from aqt.qt import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
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
MAX_FOLDERS = 30
MAX_FOLDER_ITEMS = 50
DEFAULTS = {"url": "", "urls": [], "muted": [], "on": False, "strength": 30, "plate_opacity": 70,
            "true_transparency": False, "volume": 50, "time": 0, "paused": False, "picture": True,
            "panel": False, "quality": "auto", "multi_fps": "auto", "suppress_ytdlp_prompt": False,
            "title": "", "recent": [], "folders": []}
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
_declined_urls = set()  # links declined this session (without "Don't show again"), cleared on explicit replay
_retried = {}  # link -> when its stream was last looked up again after it stopped
_not_live = set()  # links a quiet lookup found no stream for, so it isn't repeated every page
_direct_urls = set()  # direct video files (.mp4/.mov) that fell back to mpv -> WebM transcoding


def conf() -> dict:
    # Defaults fill keys missing from an older saved config; keys it no longer uses are dropped.
    saved = mw.addonManager.getConfig(__name__) or {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


position = float(conf()["time"])  # seconds into the current video, so it resumes after a reload or restart
_saved_position = position
_positions = {}  # url -> float seconds into each active video slot


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


def _sanitize_folders(raw) -> list:
    if not isinstance(raw, list):
        return []
    out, seen_names = [], set()
    for f in raw:
        if not isinstance(f, dict):
            continue
        name = (f.get("name") or "").strip()[:40] if isinstance(f.get("name"), str) else ""
        if not name or name.lower() in seen_names:
            continue
        seen_names.add(name.lower())
        items, seen_urls = [], set()
        for it in (f.get("items") or []):
            if not isinstance(it, dict):
                continue
            u = it.get("url", "").strip() if isinstance(it.get("url"), str) else ""
            if not u or len(u) > 2000 or u in seen_urls:
                continue
            seen_urls.add(u)
            t = it.get("title", "").strip()[:300] if isinstance(it.get("title"), str) else ""
            items.append({"url": u, "title": t})
            if len(items) >= MAX_FOLDER_ITEMS:
                break
        out.append({"name": name, "items": items})
        if len(out) >= MAX_FOLDERS:
            break
    return out


def _recent_title(c: dict, url: str) -> str:
    if url == c.get("url") and c.get("title"):
        return c["title"]
    for r in c.get("recent") or []:
        if r.get("url") == url and r.get("title"):
            return r.get("title", "")
    for f in _sanitize_folders(c.get("folders")):
        for it in f["items"]:
            if it["url"] == url and it["title"]:
                return it["title"]
    return ""


def _remember_recent(c: dict, url: str, title: str = "") -> list:
    current = [{"url": c["url"], "title": c["title"]}] if c.get("url") else []  # may predate the recent list
    known = [r for r in c.get("recent", []) + current if r.get("url") == url]
    title = title or (known[0].get("title", "") if known else "") or _recent_title(c, url)
    rest, seen = [], {url}
    for r in current + c.get("recent", []):
        if r.get("url") not in seen:
            seen.add(r["url"])
            rest.append(r)
    return ([{"url": url, "title": title}] + rest)[:5]


def create_folder(name: str) -> bool:
    name = (name or "").strip()[:40] if isinstance(name, str) else ""
    if not name:
        return False
    c = conf()
    folders = _sanitize_folders(c.get("folders"))
    if any(f["name"].lower() == name.lower() for f in folders) or len(folders) >= MAX_FOLDERS:
        return False
    folders.append({"name": name, "items": []})
    save(folders=folders)
    return True


def delete_folder(name: str) -> bool:
    name = (name or "").strip() if isinstance(name, str) else ""
    if not name:
        return False
    c = conf()
    folders = _sanitize_folders(c.get("folders"))
    kept = [f for f in folders if f["name"].lower() != name.lower()]
    if len(kept) == len(folders):
        return False
    save(folders=kept)
    return True


def rename_folder(old_name: str, new_name: str) -> bool:
    old_name = (old_name or "").strip() if isinstance(old_name, str) else ""
    new_name = (new_name or "").strip()[:40] if isinstance(new_name, str) else ""
    if not old_name or not new_name:
        return False
    c = conf()
    folders = _sanitize_folders(c.get("folders"))
    target = next((f for f in folders if f["name"].lower() == old_name.lower()), None)
    if target is None:
        return False
    if any(f is not target and f["name"].lower() == new_name.lower() for f in folders):
        return False
    target["name"] = new_name
    save(folders=folders)
    return True


def move_folder_item(src_folder: str, dst_folder: str, url: str) -> bool:
    src_folder = (src_folder or "").strip() if isinstance(src_folder, str) else ""
    dst_folder = (dst_folder or "").strip()[:40] if isinstance(dst_folder, str) else ""
    url = (url or "").strip() if isinstance(url, str) else ""
    if not src_folder or not dst_folder or not url or src_folder.lower() == dst_folder.lower():
        return False
    c = conf()
    folders = _sanitize_folders(c.get("folders"))
    src = next((f for f in folders if f["name"].lower() == src_folder.lower()), None)
    if src is None:
        return False
    item = next((it for it in src["items"] if it["url"] == url), None)
    if item is None:
        return False
    dst = next((f for f in folders if f["name"].lower() == dst_folder.lower()), None)
    if dst is None:
        if len(folders) >= MAX_FOLDERS:
            return False
        dst = {"name": dst_folder, "items": []}
        folders.append(dst)
    if not any(it["url"] == url for it in dst["items"]):
        if len(dst["items"]) >= MAX_FOLDER_ITEMS:
            return False
        dst["items"].insert(0, item)
    src["items"] = [it for it in src["items"] if it["url"] != url]
    save(folders=folders)
    return True


def save_to_folder(folder_name: str, url: str, title: str = "") -> bool:
    folder_name = (folder_name or "").strip()[:40] if isinstance(folder_name, str) else ""
    url = (url or "").strip() if isinstance(url, str) else ""
    if not folder_name or not url or len(url) > 2000:
        return False
    c = conf()
    folders = _sanitize_folders(c.get("folders"))
    title = (title.strip()[:300] if isinstance(title, str) else "") or _recent_title(c, url)
    target = next((f for f in folders if f["name"].lower() == folder_name.lower()), None)
    if target is None:
        if len(folders) >= MAX_FOLDERS:
            return False
        target = {"name": folder_name, "items": []}
        folders.append(target)
    existing = next((it for it in target["items"] if it["url"] == url), None)
    if existing is not None:
        if title and not existing["title"]:
            existing["title"] = title
    else:
        if len(target["items"]) >= MAX_FOLDER_ITEMS:
            return False
        target["items"].insert(0, {"url": url, "title": title})
    save(folders=folders)
    return True


def remove_from_folder(folder_name: str, url: str) -> bool:
    folder_name = (folder_name or "").strip() if isinstance(folder_name, str) else ""
    url = (url or "").strip() if isinstance(url, str) else ""
    if not folder_name or not url:
        return False
    c = conf()
    folders = _sanitize_folders(c.get("folders"))
    changed = False
    for f in folders:
        if f["name"].lower() == folder_name.lower():
            before = len(f["items"])
            f["items"] = [it for it in f["items"] if it["url"] != url]
            changed = len(f["items"]) != before
            break
    if changed:
        save(folders=folders)
    return changed


def _norm_url(url: str) -> str:
    return (url or "").strip().rstrip("/").lower()


def _browse_start_url(c: dict = None) -> str:
    """Avoid reopening a video that is already playing in the backdrop; open its site home page instead."""
    c = c or conf()
    last = (_last_browse_url or "https://www.youtube.com").strip()
    if not last.lower().startswith(("http://", "https://")):
        return "https://www.youtube.com"
    playing = {_norm_url(u) for u in active_urls(c)} if c.get("on") else set()
    if _norm_url(last) in playing:
        parsed = urlparse(last)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}/"
        return "https://www.youtube.com"
    return last


def _stream_spec(url: str, start: int, paused: bool, title: str = "") -> dict:
    spec = {"url": url, "title": title, **source(url, start, paused)}
    if live.ready(url):
        spec.update(kind="video", src=live.address(url), start=0, live=True)
    elif spec["kind"] == "page" and (
        STREAM_SITES.search(urlparse(spec["src"]).hostname or "") or LIVE_MEDIA.search(urlparse(spec["src"]).path)
    ):
        spec["src"] = "about:blank"  # framed sites or raw manifests won't play directly; live.py replaces this once ready
    fps = live.stream_fps(url)
    if fps:
        spec["fps"] = fps
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
    s = {k: c[k] for k in ("url", "strength", "plate_opacity", "true_transparency", "volume", "paused", "picture", "panel", "quality", "title", "recent")}
    s["folders"] = _sanitize_folders(c.get("folders"))
    s["urls"] = urls
    s["muted"] = [u for u in (c.get("muted") or []) if isinstance(u, str) and u in urls]
    s["break_info"] = _break_info()
    s["auto_note"] = live.effective_note(
        c["quality"], max(1, len(urls)), multi_fps=c.get("multi_fps", "auto"),
        fps_list=live.active_fps(urls) if c["on"] else None,
    )
    s.update(on=bool(c["on"] and urls), plate=plate, lite=software_rendering(), streams=[])
    if s["on"]:
        streams = [
            _stream_spec(
                u,
                int(_positions.get(u, position if i == 0 else 0)),
                c["paused"],
                _recent_title(c, u),
            )
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
    if message.startswith("backdrop:fps:"):
        try:
            payload = json.loads(message.split(":", 2)[2])
            if isinstance(payload, dict) and isinstance(payload.get("url"), str):
                fps_val = payload.get("fps")
                if isinstance(fps_val, (int, float)) and not isinstance(fps_val, bool) and math.isfinite(fps_val) and 0 < fps_val <= 240:
                    live.set_fps(payload["url"], fps_val)
        except (ValueError, TypeError):
            pass
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
    if message.startswith("backdrop:slot_time:"):
        try:
            payload = json.loads(message.split(":", 2)[2])
            if isinstance(payload, dict) and isinstance(payload.get("url"), str):
                u = payload["url"].strip()
                val = float(payload.get("time", 0))
                if u and math.isfinite(val):
                    _positions[u] = max(0.0, val)
                    if u == conf().get("url"):
                        position = _positions[u]
                        if abs(position - _saved_position) >= 30:
                            save_position()
        except (ValueError, TypeError):
            pass
        return True, None
    if not message.startswith("backdrop:time:"):
        return handled
    try:
        val = float(message.split(":", 2)[2])
        if math.isfinite(val):
            position = max(0.0, val)
            cur_u = conf().get("url")
            if cur_u:
                _positions[cur_u] = position
            if abs(position - _saved_position) >= 30:
                save_position()
    except ValueError:
        pass
    return True, None


def _ask_download_helper() -> tuple:
    """Prompt to download yt-dlp with a 'Don't show again' checkbox. Returns (accepted, dont_show_again)."""
    msg = (
        "Anki's built-in browser can't play this live stream or web video directly without a free helper "
        "called yt-dlp (~3 MB, from github.com/yt-dlp).\n\n"
        "Download yt-dlp now? (You can also download it anytime in Tools → Backdrop Video…)"
    )
    try:
        from aqt.qt import QCheckBox, QMessageBox
        box = QMessageBox(mw)
        box.setWindowTitle("Backdrop Video")
        box.setText(msg)
        cb = QCheckBox("Don't show this again")
        box.setCheckBox(cb)
        dl_btn = box.addButton("Download yt-dlp", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Not Now", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(dl_btn)
        box.exec()
        return box.clickedButton() is dl_btn, cb.isChecked()
    except Exception:
        return bool(askUser(msg, title="Backdrop Video")), False


def go_live(url: str, quiet: bool = False, direct: bool = False) -> None:
    """Live streams and HLS/H.264 URLs convert via mpv (plus yt-dlp when extracting from a webpage)."""
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
            if conf().get("suppress_ytdlp_prompt") or url in _declined_urls:
                return
            accepted, dont_show = _ask_download_helper()
            if dont_show:
                save(suppress_ytdlp_prompt=True)
            if not accepted:
                _declined_urls.add(url)
                if not quiet:
                    tooltip("You can download yt-dlp anytime in Tools → Backdrop Video…", period=7000)
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
    folders = _sanitize_folders(c.get("folders"))
    folder_changed = False
    for f in folders:
        for it in f["items"]:
            if it["url"] == target and it["title"] != title:
                it["title"] = title
                folder_changed = True
    if folder_changed:
        changes["folders"] = folders
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
    _declined_urls.discard(url)
    live.forget(url)
    c = conf()
    for prev in active_urls(c):
        if prev != url:
            live.forget(prev)
            _positions.pop(prev, None)
    if url != c["url"]:
        position = 0.0
        _positions.pop(url, None)
    recent = _remember_recent(c, url)
    title = recent[0]["title"]
    save(url=url, urls=[url], muted=[], on=True, paused=False, title=title, recent=recent)


def play_urls(urls: list) -> bool:
    """Start 1–4 links together in multi-view, remembering them in recent."""
    global position
    cleaned, seen = [], set()
    for raw in (urls or []):
        if isinstance(raw, str):
            u = raw.strip()
            if u and len(u) <= 2000 and u not in seen:
                seen.add(u)
                cleaned.append(u)
                if len(cleaned) >= MAX_STREAMS:
                    break
    if not cleaned:
        return False
    if len(cleaned) == 1:
        play_url(cleaned[0])
        return True
    c = conf()
    keep = set(cleaned)
    for prev in active_urls(c):
        if prev not in keep:
            live.forget(prev)
            _positions.pop(prev, None)
    recent = list(c.get("recent") or [])
    for u in reversed(cleaned):
        _not_live.discard(u)
        _declined_urls.discard(u)
        recent = _remember_recent({**c, "recent": recent}, u)
    position = float(_positions.get(cleaned[0], 0.0))
    primary = cleaned[0]
    save(url=primary, urls=cleaned, muted=[], on=True, paused=False, title=_recent_title({**c, "recent": recent}, primary), recent=recent)
    return True


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
    _declined_urls.discard(url)
    live.forget(url)
    _positions.pop(url, None)
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
    _positions.pop(url, None)
    new_urls = [u for u in cur if u != url]
    muted = [u for u in (c.get("muted") or []) if u in new_urls]
    if not new_urls:
        position = 0.0
        _positions.clear()
        save(on=False, url="", urls=[], muted=[], title="")
        if _break_mode:
            exit_break()
        return
    primary = new_urls[0]
    if primary != c["url"]:
        position = float(_positions.get(primary, 0.0))
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
    played_multi = False
    raw_multi = changes.get("play_urls")
    if isinstance(raw_multi, list):
        played_multi = play_urls(raw_multi)
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
        rec = [r for r in (c.get("recent") or []) if (r.get("url") or "").strip() != forgot_recent]
        forget_changes = {"recent": rec}
        if not c.get("on") and (c.get("url") or "").strip() == forgot_recent:
            forget_changes.update(url="", urls=[], title="")
        save(**forget_changes)
    folder_changed = False
    if isinstance(changes.get("create_folder"), str):
        folder_changed = create_folder(changes["create_folder"]) or folder_changed
    if isinstance(changes.get("delete_folder"), str):
        folder_changed = delete_folder(changes["delete_folder"]) or folder_changed
    save_item = changes.get("save_item")
    if isinstance(save_item, dict):
        folder_changed = save_to_folder(
            save_item.get("folder", ""), save_item.get("url", ""), save_item.get("title", "")
        ) or folder_changed
    unsave_item = changes.get("unsave_item")
    if isinstance(unsave_item, dict):
        folder_changed = remove_from_folder(
            unsave_item.get("folder", ""), unsave_item.get("url", "")
        ) or folder_changed
    flags = {k: v for k, v in changes.items()
             if k in ("on", "paused", "picture", "panel", "true_transparency") and isinstance(v, bool)}
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
    if _dialog is not None and "true_transparency" in flags:
        _dialog.true_transparency.blockSignals(True)
        _dialog.true_transparency.setChecked(flags["true_transparency"])
        _dialog.true_transparency.blockSignals(False)
    target_new = url or added_url
    if target_new or played_multi or removed_url or forgot_recent or folder_changed or "on" in flags:
        refresh(ask_live=bool(target_new or played_multi), only_url=None if played_multi else (target_new or None))
    quality = changes.get("quality")
    if isinstance(quality, str) and quality in VALID_QUALITIES:
        set_quality(quality)


def refresh(ask_live: bool = False, only_url: str = None) -> None:
    """Apply settings to the page on screen without reloading it."""
    if _browse is not None and hasattr(_browse, "_sync_buttons"):
        try:
            _browse._sync_buttons()
        except Exception:
            pass
    if _dialog is not None and hasattr(_dialog, "sync_from_config"):
        try:
            _dialog.sync_from_config()
        except Exception:
            pass
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
        from aqt.qt import QAbstractItemView, QGroupBox, QListWidget
        self.setWindowTitle("Backdrop Video")
        self.resize(780, 440)
        self._syncing = False
        c = conf()

        # ---- Left: Browse & Saved Folder Organizer ----
        lib_box = QGroupBox("Saved Folders && Browse")
        lib_lay = QVBoxLayout(lib_box)

        self.browse_btn = QPushButton("Browse / Pre-find Videos…")
        self.browse_btn.setAutoDefault(False)
        self.browse_btn.setToolTip("Open the tabbed mini-browser to find, add, or save videos into folders")
        self.browse_btn.setStyleSheet(
            "QPushButton { background: #3b82f6; color: #ffffff; font-weight: 600; padding: 6px 12px; border-radius: 6px; }"
        )
        self.browse_btn.clicked.connect(open_browse)
        lib_lay.addWidget(self.browse_btn)

        folder_bar = QHBoxLayout()
        self.folder_combo = QComboBox()
        self.folder_combo.currentIndexChanged.connect(self._render_folder_items)
        self.new_folder_btn = QPushButton("+ New")
        self.rename_folder_btn = QPushButton("Rename")
        self.del_folder_btn = QPushButton("Delete")
        for b in (self.new_folder_btn, self.rename_folder_btn, self.del_folder_btn):
            b.setAutoDefault(False)
        self.new_folder_btn.clicked.connect(self._create_folder_prompt)
        self.rename_folder_btn.clicked.connect(self._rename_folder_prompt)
        self.del_folder_btn.clicked.connect(self._delete_current_folder)
        folder_bar.addWidget(self.folder_combo, 1)
        folder_bar.addWidget(self.new_folder_btn)
        folder_bar.addWidget(self.rename_folder_btn)
        folder_bar.addWidget(self.del_folder_btn)
        lib_lay.addLayout(folder_bar)

        self.folder_list = QListWidget()
        self.folder_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.folder_list.itemSelectionChanged.connect(self._sync_folder_buttons)
        self.folder_list.itemDoubleClicked.connect(lambda _it: self.play_selected())
        lib_lay.addWidget(self.folder_list, 1)

        paste_bar = QHBoxLayout()
        self.save_link = QLineEdit()
        self.save_link.setPlaceholderText("Paste a link to save into this folder…")
        self.save_link.returnPressed.connect(self._save_pasted_link)
        self.save_link_btn = QPushButton("Save")
        self.save_link_btn.setAutoDefault(False)
        self.save_link_btn.clicked.connect(self._save_pasted_link)
        paste_bar.addWidget(self.save_link, 1)
        paste_bar.addWidget(self.save_link_btn)
        lib_lay.addLayout(paste_bar)

        item_bar = QHBoxLayout()
        self.play_sel_btn = QPushButton("Play Selected")
        self.add_sel_btn = QPushButton("+ Add")
        self.move_btn = QPushButton("Move to…")
        self.rem_btn = QPushButton("Remove")
        for b in (self.play_sel_btn, self.add_sel_btn, self.move_btn, self.rem_btn):
            b.setAutoDefault(False)
        self.play_sel_btn.setToolTip("Play up to 4 selected saved videos together in multi-view")
        self.add_sel_btn.setToolTip("Add selected saved video(s) alongside what is currently playing")
        self.play_sel_btn.clicked.connect(self.play_selected)
        self.add_sel_btn.clicked.connect(self.add_selected)
        self.move_btn.clicked.connect(self._move_selected_menu)
        self.rem_btn.clicked.connect(self.remove_selected)
        item_bar.addWidget(self.play_sel_btn)
        item_bar.addWidget(self.add_sel_btn)
        item_bar.addStretch()
        item_bar.addWidget(self.move_btn)
        item_bar.addWidget(self.rem_btn)
        lib_lay.addLayout(item_bar)

        # ---- Right: Display, Quality & Live Stream Helper ----
        set_box = QGroupBox("Display && Live Streams")
        set_lay = QVBoxLayout(set_box)

        self.strength = self.slider(c["strength"], lambda v: self.live(strength=v))
        self.strength.setAccessibleName("Backdrop strength")
        self.plate_opacity = self.slider(c["plate_opacity"], lambda v: self.live(plate_opacity=v))
        self.plate_opacity.setAccessibleName("Study card opacity")
        self.true_transparency = QCheckBox("True transparency")
        self.true_transparency.setChecked(bool(c.get("true_transparency")))
        self.true_transparency.setToolTip("Also fade study card blur and border as you slide Study card down.")
        self.true_transparency.toggled.connect(lambda v: self.live(true_transparency=bool(v)))
        self.volume = self.slider(c["volume"], lambda v: self.live(volume=v))
        self.volume.setAccessibleName("Volume")
        self.quality = QComboBox()
        for label, value in QUALITIES:
            self.quality.addItem(label, value)
        self.quality.setCurrentIndex(max(0, self.quality.findData(c["quality"])))
        self.quality.setToolTip("Applies to live streams (Twitch, YouTube Live, etc.). Regular YouTube videos adjust automatically.")
        self.quality.currentIndexChanged.connect(self.set_quality)

        has_helper = os.path.exists(live.helper_path(ADDON_DIR))
        self.helper_btn = QPushButton("yt-dlp Installed ✓ (Update)" if has_helper else "Download yt-dlp Helper (~3 MB)")
        self.helper_btn.setAutoDefault(False)
        self.helper_btn.setToolTip("Free helper needed for Twitch, Kick, YouTube Live, and many webcams")
        self.helper_btn.clicked.connect(self.download_helper)
        helper_note = QLabel("Needed for Twitch, Kick, YouTube Live, and webcams. Regular YouTube videos work without it.")
        helper_note.setWordWrap(True)
        helper_note.setStyleSheet("color: gray; font-size: 11px;")

        form = QFormLayout()
        form.addRow("Backdrop", self.labelled(self.strength, "Faint", "Clear"))
        form.addRow("Study card", self.labelled(self.plate_opacity, "Clear", "Solid"))
        form.addRow("", self.true_transparency)
        form.addRow("Volume", self.volume)
        form.addRow("Stream quality", self.quality)
        form.addRow("Live helper", self.helper_btn)
        form.addRow("", helper_note)
        set_lay.addLayout(form)

        self.status_hint = QLabel()
        self.status_hint.setWordWrap(True)
        self.status_hint.setStyleSheet("color: gray")
        set_lay.addWidget(self.status_hint)
        set_lay.addStretch()

        self.off = QPushButton("Turn Off Backdrop")
        self.off.setAutoDefault(False)
        self.off.clicked.connect(self.turn_off)
        off_row = QHBoxLayout()
        off_row.addStretch()
        off_row.addWidget(self.off)
        set_lay.addLayout(off_row)

        cols = QHBoxLayout(self)
        cols.addWidget(lib_box, 11)
        cols.addWidget(set_box, 10)
        self.sync_from_config()

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
        if self._syncing:
            return
        save(**change)
        refresh()

    def set_quality(self) -> None:
        if self._syncing:
            return
        set_quality(self.quality.currentData())

    def _current_folder_name(self) -> str:
        return self.folder_combo.currentData() or ""

    def _selected_urls(self) -> list:
        out = []
        for it in self.folder_list.selectedItems():
            u = it.data(Qt.ItemDataRole.UserRole)
            if u:
                out.append(u)
        return out

    def sync_from_config(self, select_folder: str = None) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            c = conf()
            for widget, val in (
                (self.strength, c["strength"]),
                (self.plate_opacity, c["plate_opacity"]),
                (self.volume, c["volume"]),
            ):
                if widget.value() != val:
                    widget.blockSignals(True)
                    widget.setValue(val)
                    widget.blockSignals(False)
            if self.true_transparency.isChecked() != bool(c.get("true_transparency")):
                self.true_transparency.blockSignals(True)
                self.true_transparency.setChecked(bool(c.get("true_transparency")))
                self.true_transparency.blockSignals(False)
            q_idx = self.quality.findData(c["quality"])
            if q_idx >= 0 and self.quality.currentIndex() != q_idx:
                self.quality.blockSignals(True)
                self.quality.setCurrentIndex(q_idx)
                self.quality.blockSignals(False)
            self.off.setEnabled(bool(c["on"]))
            urls = active_urls(c)
            note = live.effective_note(
                c["quality"], max(1, len(urls)), multi_fps=c.get("multi_fps", "auto"),
                fps_list=live.active_fps(urls) if c["on"] else None,
            )
            self.status_hint.setText(
                f"Active settings: {note}. Use the right-edge tab while studying for quick controls. Hold P to peek."
            )
            folders = _sanitize_folders(c.get("folders"))
            prev = select_folder if select_folder is not None else self._current_folder_name()
            self.folder_combo.blockSignals(True)
            self.folder_combo.clear()
            for f in folders:
                self.folder_combo.addItem(f"{f['name']} ({len(f['items'])})", f["name"])
            idx = self.folder_combo.findData(prev)
            if idx >= 0:
                self.folder_combo.setCurrentIndex(idx)
            self.folder_combo.blockSignals(False)
            self._render_folder_items()
        finally:
            self._syncing = False

    def _render_folder_items(self) -> None:
        from aqt.qt import QListWidgetItem
        self.folder_list.clear()
        fname = self._current_folder_name()
        folders = _sanitize_folders(conf().get("folders"))
        folder = next((f for f in folders if f["name"] == fname), None)
        if folder is None:
            placeholder = QListWidgetItem("No folders yet — click + New or ★ in the sidebar / Browse")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.folder_list.addItem(placeholder)
        elif not folder["items"]:
            placeholder = QListWidgetItem("This folder is empty — paste a link below or use Browse…")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.folder_list.addItem(placeholder)
        else:
            for it in folder["items"]:
                label = it["title"] or re.sub(r"^https?://(www\.)?", "", it["url"])
                row = QListWidgetItem(label)
                row.setData(Qt.ItemDataRole.UserRole, it["url"])
                row.setToolTip(f"{it['title']}\n{it['url']}" if it["title"] else it["url"])
                self.folder_list.addItem(row)
        self._sync_folder_buttons()

    def _sync_folder_buttons(self) -> None:
        has_folder = bool(self._current_folder_name())
        sel = self._selected_urls()
        folders = _sanitize_folders(conf().get("folders"))
        self.rename_folder_btn.setEnabled(has_folder)
        self.del_folder_btn.setEnabled(has_folder)
        self.save_link.setEnabled(True)
        self.play_sel_btn.setEnabled(bool(sel))
        self.play_sel_btn.setText(f"Play Selected ({min(len(sel), MAX_STREAMS)})" if len(sel) > 1 else "Play Selected")
        self.add_sel_btn.setEnabled(bool(sel))
        self.move_btn.setEnabled(bool(sel) and len(folders) >= 2)
        self.rem_btn.setEnabled(bool(sel))

    def _create_folder_prompt(self) -> None:
        from aqt.qt import QInputDialog
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
        if ok and name.strip():
            if create_folder(name.strip()):
                self.sync_from_config(select_folder=name.strip()[:40])
                refresh()

    def _rename_folder_prompt(self) -> None:
        from aqt.qt import QInputDialog
        old = self._current_folder_name()
        if not old:
            return
        name, ok = QInputDialog.getText(self, "Rename Folder", "New folder name:", text=old)
        if ok and name.strip() and rename_folder(old, name.strip()):
            self.sync_from_config(select_folder=name.strip()[:40])
            refresh()

    def _delete_current_folder(self) -> None:
        fname = self._current_folder_name()
        if not fname:
            return
        folders = _sanitize_folders(conf().get("folders"))
        target = next((f for f in folders if f["name"] == fname), None)
        if target and target["items"] and not askUser(f"Delete folder \"{fname}\" and its {len(target['items'])} saved link(s)?", title="Backdrop Video"):
            return
        if delete_folder(fname):
            self.sync_from_config()
            refresh()

    def _save_pasted_link(self) -> None:
        url = self.save_link.text().strip()
        if not url or len(url) > 2000:
            return
        fname = self._current_folder_name() or "Saved"
        if save_to_folder(fname, url):
            self.save_link.clear()
            self.sync_from_config(select_folder=fname)
            refresh()

    def play_selected(self) -> None:
        urls = self._selected_urls()[:MAX_STREAMS]
        if not urls:
            return
        if play_urls(urls):
            self.off.setEnabled(True)
            refresh(ask_live=True)

    def add_selected(self) -> None:
        urls = self._selected_urls()
        if not urls:
            return
        added = []
        for u in urls:
            if add_url(u):
                added.append(u)
        if added:
            self.off.setEnabled(True)
            refresh(ask_live=True, only_url=added[0] if len(added) == 1 else None)
            tooltip(f"Added {len(added)} stream(s) ({len(active_urls())}/{MAX_STREAMS})", period=3000)
        else:
            tooltip(f"Already playing {MAX_STREAMS} videos (or selected link is already active).", period=4000)

    def _move_selected_menu(self) -> None:
        from aqt.qt import QMenu
        src = self._current_folder_name()
        urls = self._selected_urls()
        if not src or not urls:
            return
        menu = QMenu(self.move_btn)
        for f in _sanitize_folders(conf().get("folders")):
            if f["name"].lower() != src.lower():
                act = menu.addAction(f["name"])
                act.triggered.connect(lambda _c=False, dst=f["name"]: self._move_urls(src, dst, urls))
        menu.exec(self.move_btn.mapToGlobal(self.move_btn.rect().bottomLeft()))

    def _move_urls(self, src: str, dst: str, urls: list) -> None:
        moved = any(move_folder_item(src, dst, u) for u in urls)
        if moved:
            self.sync_from_config(select_folder=src)
            refresh()

    def remove_selected(self) -> None:
        fname = self._current_folder_name()
        urls = self._selected_urls()
        if not fname or not urls:
            return
        if any(remove_from_folder(fname, u) for u in urls):
            self.sync_from_config(select_folder=fname)
            refresh()

    def download_helper(self) -> None:
        self.helper_btn.setEnabled(False)
        self.helper_btn.setText("Downloading yt-dlp…")

        def done(fut):
            try:
                fut.result()
            except Exception:
                if _dialog is self:
                    self.helper_btn.setEnabled(True)
                    self.helper_btn.setText("Download yt-dlp Helper (~3 MB)")
                tooltip("Couldn't download yt-dlp right now. Check your internet connection.", period=5000)
                return
            _not_live.clear()
            _declined_urls.clear()
            save(suppress_ytdlp_prompt=False)
            if _dialog is self:
                self.helper_btn.setEnabled(True)
                self.helper_btn.setText("yt-dlp Installed ✓ (Update)")
            tooltip("yt-dlp downloaded! Ready for live streams.", period=4000)
            refresh(ask_live=True)

        mw.taskman.run_in_background(lambda: live.download_helper(ADDON_DIR), done)

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
    """Built-in tabbed browser pop-up with smart search, Save to folder, and safe + Add / Replace buttons."""
    global _browse, _last_browse_url
    if _browse is not None:
        if hasattr(_browse, "_sync_buttons"):
            _browse._sync_buttons()
        _browse.show()
        _browse.raise_()
        _browse.activateWindow()
        return
    try:
        from aqt.qt import QInputDialog, QMenu, QTabWidget, QToolButton, QUrl
        from PyQt6.QtWebEngineCore import QWebEnginePage
        from PyQt6.QtWebEngineWidgets import QWebEngineView
    except Exception:
        tooltip("Built-in browser isn't available in this Qt build.", period=4000)
        return

    dlg = QDialog(mw)
    dlg.setWindowTitle("Backdrop Video — Find a Video or Stream")
    dlg.resize(980, 680)

    tabs = QTabWidget(dlg)
    tabs.setTabsClosable(True)
    tabs.setMovable(True)
    tabs.setDocumentMode(True)

    def current_view():
        w = tabs.currentWidget()
        return w if isinstance(w, QWebEngineView) else None

    def _silence_view(v) -> None:
        if v is None:
            return
        try:
            v.page().runJavaScript("document.querySelectorAll('video,audio').forEach(m=>{try{m.pause();m.muted=true;}catch(e){}})")
        except Exception:
            pass

    def _tab_label(title: str, url: str = "") -> str:
        t = (title or "").strip()
        if not t and url:
            t = urlparse(url).hostname or url
        t = t or "New Tab"
        return (t[:22] + "…") if len(t) > 23 else t

    class _BrowseView(QWebEngineView):
        def createWindow(self, win_type):
            if win_type == QWebEnginePage.WebWindowType.WebBrowserBackgroundTab:
                return open_tab(None, switch=False)
            if win_type == QWebEnginePage.WebWindowType.WebBrowserTab:
                return open_tab(None, switch=True)
            return None

    def open_tab(initial_url: str = None, switch: bool = True):
        v = _BrowseView(tabs)
        idx = tabs.addTab(v, _tab_label("", initial_url or ""))

        def on_url(u, view_ref=v):
            global _last_browse_url
            s = u.toString()
            if current_view() is view_ref:
                addr.setText(s)
            if s.startswith(("http://", "https://")):
                _last_browse_url = s
            ti = tabs.indexOf(view_ref)
            if ti >= 0 and not view_ref.title().strip():
                tabs.setTabText(ti, _tab_label("", s))
                tabs.setTabToolTip(ti, s)

        def on_title(t, view_ref=v):
            ti = tabs.indexOf(view_ref)
            if ti >= 0:
                u = view_ref.url().toString()
                tabs.setTabText(ti, _tab_label(t, u))
                tabs.setTabToolTip(ti, f"{t}\n{u}" if u else t)

        v.urlChanged.connect(on_url)
        v.titleChanged.connect(on_title)
        if switch:
            tabs.setCurrentIndex(idx)
        if initial_url:
            v.setUrl(QUrl(initial_url))
            if switch:
                addr.setText(initial_url)
        return v

    def _new_tab_url() -> str:
        v = current_view()
        if v is not None:
            s = v.url().toString().strip()
            if s.startswith(("http://", "https://")):
                p = urlparse(s)
                if p.scheme and p.netloc:
                    return f"{p.scheme}://{p.netloc}/"
        return _browse_start_url()

    def close_tab(idx: int) -> None:
        v = tabs.widget(idx)
        if v is not None:
            _silence_view(v)
            try:
                v.setUrl(QUrl("about:blank"))
                v.deleteLater()
            except Exception:
                pass
        tabs.removeTab(idx)
        if tabs.count() == 0:
            open_tab(_browse_start_url(), switch=True)

    new_tab_b = QToolButton(tabs)
    new_tab_b.setText("+")
    new_tab_b.setToolTip("Open a new tab")
    new_tab_b.clicked.connect(lambda: open_tab(_new_tab_url(), switch=True))
    tabs.setCornerWidget(new_tab_b, Qt.Corner.TopRightCorner)
    tabs.tabCloseRequested.connect(close_tab)

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
        btn.clicked.connect(lambda _c=False, u=site_url: current_view() and current_view().setUrl(QUrl(u)))
        sites_bar.addWidget(btn)
    sites_bar.addStretch()
    nt_top = QPushButton("+ New Tab")
    nt_top.setAutoDefault(False)
    nt_top.clicked.connect(lambda: open_tab(_new_tab_url(), switch=True))
    sites_bar.addWidget(nt_top)

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

    status_lbl = QLabel()
    replace_b = QPushButton("Replace Current")
    replace_b.setAutoDefault(False)
    replace_b.setToolTip("Stop current stream(s) and play only this page")
    save_b = QPushButton("★ Save…")
    save_b.setAutoDefault(False)
    save_b.setToolTip("Save this page to a folder without changing what is playing")
    add_b = QPushButton()
    add_b.setAutoDefault(False)
    add_b.setStyleSheet("QPushButton { background: #3b82f6; color: #ffffff; font-weight: 600; padding: 5px 12px; border-radius: 6px; }"
                        "QPushButton:disabled { background: #6b7280; color: #e5e7eb; }")
    play_b = QPushButton("Play This Page")
    play_b.setAutoDefault(False)
    play_b.setDefault(False)
    play_b.setStyleSheet("QPushButton { background: #3b82f6; color: #ffffff; font-weight: 600; padding: 5px 12px; border-radius: 6px; }")

    bottom_bar = QHBoxLayout()
    bottom_bar.addWidget(status_lbl)
    bottom_bar.addStretch()
    bottom_bar.addWidget(replace_b)
    bottom_bar.addWidget(save_b)
    bottom_bar.addWidget(add_b)
    bottom_bar.addWidget(play_b)

    def sync_buttons() -> None:
        c = conf()
        n = len(active_urls(c)) if c["on"] else 0
        if n == 0:
            status_lbl.setText("Nothing playing yet")
            replace_b.setVisible(False)
            add_b.setVisible(False)
            play_b.setVisible(True)
        else:
            status_lbl.setText(f"Playing {n}/{MAX_STREAMS} stream" + ("" if n == 1 else "s"))
            replace_b.setVisible(True)
            play_b.setVisible(False)
            add_b.setVisible(True)
            if n >= MAX_STREAMS:
                add_b.setEnabled(False)
                add_b.setText(f"Multi-View Full ({MAX_STREAMS}/{MAX_STREAMS})")
            else:
                add_b.setEnabled(True)
                add_b.setText(f"+ Add to Backdrop ({n}/{MAX_STREAMS})")

    lay = QVBoxLayout(dlg)
    lay.addLayout(sites_bar)
    lay.addLayout(bar)
    lay.addWidget(tabs, 1)
    lay.addLayout(bottom_bar)

    def go():
        v = current_view()
        txt = addr.text().strip()
        if not v or not txt:
            return
        if txt.lower().startswith(("http://", "https://")):
            url = txt
        elif " " not in txt and "." in txt:
            url = "https://" + txt
        else:
            host = (v.url().host() or "").lower()
            q = quote_plus(txt)
            if "youtube.com" in host or "youtu.be" in host:
                url = f"https://www.youtube.com/results?search_query={q}"
            elif "twitch.tv" in host:
                url = f"https://www.twitch.tv/search?term={q}"
            elif "kick.com" in host:
                url = f"https://kick.com/search?query={q}"
            else:
                url = f"https://www.google.com/search?q={q}"
        v.setUrl(QUrl(url))

    def _current_page_info() -> tuple:
        v = current_view()
        if v is None:
            return "", ""
        url = v.url().toString().strip()
        if not url or not url.lower().startswith(("http://", "https://")):
            return "", ""
        return url, (v.title() or "").strip()

    def save_current_to(folder_name: str) -> bool:
        url, title = _current_page_info()
        if not url:
            return False
        if save_to_folder(folder_name, url, title):
            refresh()
            tooltip(f"Saved to {folder_name}", period=3000)
            return True
        return False

    def open_save_menu() -> None:
        url, _ = _current_page_info()
        if not url:
            tooltip("Open a video or stream page first.", period=3000)
            return
        menu = QMenu(save_b)
        folders = _sanitize_folders(conf().get("folders"))
        for f in folders:
            has = any(it["url"] == url for it in f["items"])
            act = menu.addAction(f"✓ {f['name']}" if has else f["name"])
            act.triggered.connect(lambda _c=False, fn=f["name"]: save_current_to(fn))
        if folders:
            menu.addSeparator()
        new_act = menu.addAction("+ New folder…")

        def prompt_new():
            name, ok = QInputDialog.getText(dlg, "New Folder", "Folder name:")
            if ok and name.strip():
                save_current_to(name.strip())

        new_act.triggered.connect(prompt_new)
        menu.exec(save_b.mapToGlobal(save_b.rect().topLeft()))

    def pick(replace: bool):
        v = current_view()
        url, _ = _current_page_info()
        if not v or not url:
            return
        _silence_view(v)
        if replace:
            play_url(url)
            refresh(ask_live=True)
            sync_buttons()
            if tabs.count() <= 1:
                dlg.accept()
            else:
                tooltip("Now playing this page in backdrop.", period=3000)
        elif add_url(url):
            refresh(ask_live=True, only_url=url)
            sync_buttons()
            tooltip(f"Added to backdrop ({len(active_urls())}/{MAX_STREAMS})", period=3000)
        else:
            tooltip(f"Already playing {MAX_STREAMS} videos (or this link is already active).", period=4000)

    def on_tab_changed(_idx: int) -> None:
        v = current_view()
        addr.setText(v.url().toString() if v is not None else "")

    tabs.currentChanged.connect(on_tab_changed)
    back_b.clicked.connect(lambda: current_view() and current_view().back())
    fwd_b.clicked.connect(lambda: current_view() and current_view().forward())
    rel_b.clicked.connect(lambda: current_view() and current_view().reload())
    addr.returnPressed.connect(go)
    go_b.clicked.connect(go)
    play_b.clicked.connect(lambda: pick(True))
    replace_b.clicked.connect(lambda: pick(True))
    add_b.clicked.connect(lambda: pick(False))
    save_b.clicked.connect(open_save_menu)

    def cleanup(_res):
        global _browse, _last_browse_url
        v_cur = current_view()
        if v_cur is not None:
            s = v_cur.url().toString().strip()
            if s.startswith(("http://", "https://")):
                _last_browse_url = s
        _browse = None
        for i in range(tabs.count() - 1, -1, -1):
            w = tabs.widget(i)
            _silence_view(w)
            try:
                w.setUrl(QUrl("about:blank"))
                w.deleteLater()
            except Exception:
                pass

    dlg.finished.connect(cleanup)
    dlg._tabs = tabs
    dlg._open_tab = open_tab
    dlg._close_tab = close_tab
    dlg._sync_buttons = sync_buttons
    dlg._save_btn = save_b
    dlg._add_btn = add_b
    dlg._replace_btn = replace_b
    dlg._play_btn = play_b
    dlg._save_current_to = save_current_to
    open_tab(_browse_start_url(), switch=True)
    sync_buttons()
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
