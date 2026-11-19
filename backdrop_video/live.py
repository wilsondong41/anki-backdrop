"""Live streams: YouTube and Twitch send live video only as H.264, which Anki's browser can't play.
yt-dlp finds the stream, Anki's own mpv converts it to WebM, and a local server hands it to the backdrop."""

import atexit
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipimport
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HELPER_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"  # official pure-Python build
_codec_help = None  # cached (video_help, audio_help) from mpv
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # no console window flashing on Windows
QUALITY_HEIGHT = {"360": 360, "480": 480, "720": 720, "1080": 1080, "1440": 1440, "2160": 2160}
BITRATE_VPX = {360: "600k", 480: "900k", 720: "1800k", 1080: "3200k", 1440: "5500k", 2160: "8500k"}
BITRATE_AV1 = {360: "600k", 480: "900k", 720: "1500k", 1080: "2500k", 1440: "4200k", 2160: "6500k"}

_lock = threading.Lock()
_dl_lock = threading.Lock()
_server = None
_token = secrets.token_hex(8)  # other local web pages can't guess the stream address
_links = {}  # page link -> stream id; a fresh id per lookup, so a retry reloads the player
_streams = {}  # stream id -> (video url, audio url or None, http headers dict)
_fps = {}  # page link -> detected/effective integer fps
_procs = set()  # running converters; mpv outlives Anki unless stopped
_album = {}  # page link -> {"items": [(video, audio, headers, fps, length)], "q", "fps_cap"} for albums
_album_pos = {}  # page link -> which album video plays (kept when the link is looked up again)
_meta = {}  # page link -> {"vod": bool, "duration": float}; vod = a normal video (can seek), not a live stream
LIVE_TTL, OTHER_TTL = 180, 86400  # seconds a live/offline answer, or a "not a stream" answer, is kept
_status = {}  # page link -> (status, checked at); status "live", "offline" or "" (not a stream / unknown)
OFFLINE_ERROR = re.compile(
    r"offline|not (currently )?live|no longer live|isn't live|is not live|has ended|stream (has )?ended|"
    r"will begin|starts in|scheduled|is private|hidden|away|password|ticket|not online", re.I)


@atexit.register
def stop_all() -> None:
    with _lock:
        procs = list(_procs)
    for proc in procs:
        try:
            proc.kill()
        except OSError:
            pass


def helper_path(folder: str) -> str:
    return os.path.join(folder, "user_files", "yt-dlp.zip")


def download_helper(folder: str, if_missing: bool = False) -> None:
    """Fetch yt-dlp once into the add-on's user_files (kept across add-on updates)."""
    path = helper_path(folder)
    with _dl_lock:
        if if_missing and os.path.exists(path):
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with urllib.request.urlopen(HELPER_URL, timeout=60) as r, open(path + ".part", "wb") as f:
            shutil.copyfileobj(r, f)
        os.replace(path + ".part", path)
        # Clear all cached yt_dlp submodules and zipimport cache so a mid-session update reloads cleanly.
        for mod in [k for k in sys.modules if k == "yt_dlp" or k.startswith("yt_dlp.")]:
            sys.modules.pop(mod, None)
        zipimport._zip_directory_cache.pop(path, None)


def _ensure_mpv(quality: str = "auto") -> str:
    """Check mpv availability and WebM encoder support; return normalized quality."""
    if not shutil.which(mpv()[0]):
        raise RuntimeError("no-mpv")  # Linux builds use the system's mpv
    if not codecs(quality):
        raise RuntimeError("no-encoder")
    return str(quality) if str(quality) in QUALITY_HEIGHT else "auto"


VIDEO_FILE_PATH = re.compile(r"\.(mp4|m4v|webm|mov|mkv|ogv)$", re.I)
WEB_LINK = re.compile(r"^https?://", re.I)


def _store_stream(link: str, video: str, audio, headers: dict, quality: str, max_fps: int = 0, fps=None) -> None:
    # Only web addresses go to mpv: a site can't make it read a local file or pass it an option.
    if not isinstance(video, str) or not WEB_LINK.match(video) or (audio and (not isinstance(audio, str) or not WEB_LINK.match(audio))):
        raise ValueError("not a web video address")
    sid = secrets.token_hex(4)
    with _lock:
        _streams[sid] = (video, audio, headers, quality, max_fps)
        _links[link] = sid
        if isinstance(fps, (int, float)) and fps > 0:
            _fps[link] = int(round(fps))
        else:
            _fps.pop(link, None)
    address(link)  # start the server off the main thread


def set_fps(link: str, fps) -> None:
    if not link or not isinstance(fps, (int, float)) or fps <= 0:
        return
    with _lock:
        _fps[link] = int(round(fps))


def stream_fps(link: str):
    with _lock:
        return _fps.get(link)


def active_fps(urls: list) -> list:
    with _lock:
        return [_fps[u] for u in (urls or []) if u in _fps and _fps[u]]


def _format_fps_list(fps_list) -> str:
    vals = sorted({int(round(f)) for f in (fps_list or []) if isinstance(f, (int, float)) and f > 0})
    if not vals:
        return ""
    if len(vals) == 1:
        return f"{vals[0]} fps"
    return f"{vals[0]}–{vals[-1]} fps"


def adaptive_fps(stream_count: int = 1, video_help: str = "", multi_fps="auto") -> int:
    """Pick an FPS cap (15-120 when chosen, 30-60 on Auto, or 0 for uncapped) using hardware cores, stream count, and optional multi_fps override."""
    if isinstance(multi_fps, (int, float)) and not isinstance(multi_fps, bool):
        val = int(multi_fps)
        return 0 if val <= 0 else max(15, min(120, val))
    if isinstance(multi_fps, str) and multi_fps.strip().isdigit():
        val = int(multi_fps.strip())
        return 0 if val <= 0 else max(15, min(120, val))
    cores = os.cpu_count() or 4
    heavy = bool(video_help) and not re.search(r"--ovc=libvpx\s", video_help)
    if stream_count >= 3:
        cap = 30
    elif stream_count == 2:
        cap = 30 if (heavy or cores < 10) else 45
    elif heavy:
        cap = 45 if cores >= 12 else 30
    elif cores >= 8:
        cap = 60
    elif cores >= 6:
        cap = 48
    else:
        cap = 30
    return max(30, min(60, cap))


def effective_note(quality: str = "auto", stream_count: int = 1, multi_fps="auto", fps_list=None) -> str:
    """Short unobtrusive status showing what resolution and FPS Auto/stream conversion uses."""
    vhelp = (_codec_help or ("", ""))[0]
    heavy = bool(vhelp) and not re.search(r"--ovc=libvpx\s", vhelp)
    fps = adaptive_fps(stream_count, vhelp, multi_fps=multi_fps)
    measured = _format_fps_list(fps_list)
    q = str(quality) if str(quality) in QUALITY_HEIGHT else "auto"
    if q == "auto":
        res = "360p" if (heavy or stream_count >= 2) else "480p"
        fps_txt = measured or ("uncapped fps" if fps == 0 else ("up to 60 fps" if fps >= 60 else f"up to {fps} fps"))
        return f"Auto: {res}, {fps_txt}"
    label = "4K" if q == "2160" else f"{q}p"
    if measured:
        return f"{label}, {measured}"
    chosen = str(multi_fps).strip().lower() != "auto"  # the user picked a frame rate
    if chosen:
        return f"{label}, {'uncapped fps' if fps == 0 or fps >= 60 else f'up to {fps} fps'}"
    if stream_count >= 2 and fps > 0:
        return f"{label}, {'up to 60 fps' if fps >= 60 else f'up to {fps} fps'}"
    return label


def register_direct(link: str, quality: str = "auto", stream_count: int = 1, cap_fps: bool = False, multi_fps="auto",
                    video: str = None) -> str:
    """Register a direct stream/video URL (.m3u8, .mpd, H.264 .mp4) for mpv-to-WebM conversion without yt-dlp.
    link is the key the add-on looks it up by (as the user saved it); video is the full address mpv opens."""
    q = _ensure_mpv(quality)
    vhelp = (_codec_help or ("", ""))[0]
    fps_cap = adaptive_fps(stream_count, vhelp, multi_fps=multi_fps) if (cap_fps or q == "auto") else 0
    video = video or link
    _store_stream(link, video, None, {}, q, max_fps=fps_cap)
    # A plain video file can restart at any time; a .m3u8/.mpd manifest may be live, so it can't.
    path = video.split("?", 1)[0].lower()
    is_vod = not path.endswith((".m3u8", ".mpd"))
    with _lock:
        _meta[link] = {"vod": is_vod, "duration": 0.0}  # set with the stream, so a redraw meanwhile sees a normal video
    if is_vod:
        _set_duration(link, probe_duration(video))
    return ""


def _set_duration(link: str, duration) -> None:
    if isinstance(duration, (int, float)) and duration > 0:
        with _lock:
            if link in _meta:
                _meta[link]["duration"] = float(duration)


def probe_duration(url: str, headers: dict = None) -> float:
    """Length in seconds of a video file, asked from mpv; 0 if it can't tell. For sites whose
    yt-dlp answer has no length (some album sites, plain .mp4 links), so the position slider can show."""
    headers = headers or {}
    cmd, env = _mpv_cmd_env()
    args = cmd + ["--no-config", "--vo=null", "--ao=null", "--frames=1", "--quiet",
                  "--term-playing-msg=BDDUR=${=duration}"]
    if headers.get("User-Agent"):
        args.append(f"--user-agent={headers['User-Agent']}")
    if headers.get("Referer"):
        args.append(f"--referrer={headers['Referer']}")
    other = [f"{k}: {v}" for k, v in headers.items() if k not in ("User-Agent", "Referer")]
    if other:
        args.append("--http-header-fields=" + ",".join(h.replace(",", r"\,") for h in other))
    try:
        out = subprocess.run(args + ["--", url], capture_output=True, text=True, timeout=20, env=env,
                             creationflags=NO_WINDOW).stdout
        m = re.search(r"BDDUR=([\d.]+)", out)
        return float(m.group(1)) if m and float(m.group(1)) > 0 else 0.0
    except Exception:
        return 0.0


def find(folder: str, link: str, quality: str = "auto", stream_count: int = 1, cap_fps: bool = False, multi_fps="auto") -> str:
    """Look up the live stream behind a page link (slow: network). Returns its title."""
    if sys.version_info < (3, 10):
        raise RuntimeError("old-python")  # yt-dlp needs Python 3.10+, which Anki 23.10 and later ship
    q = _ensure_mpv(quality)
    if helper_path(folder) not in sys.path:
        sys.path.insert(0, helper_path(folder))  # the release file is a zip that Python can import from
    import yt_dlp

    h = QUALITY_HEIGHT.get(q, 480)
    fmt = f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best"
    # playlistend: a big playlist's every video is looked up before anything plays, so only the first 50.
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "playlistend": 50, "format": fmt, "logger": _Silent()}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(link, download=False)
    if not isinstance(info, dict):
        raise RuntimeError("no-stream")
    vhelp = (_codec_help or ("", ""))[0]
    fps_cap = adaptive_fps(stream_count, vhelp, multi_fps=multi_fps) if (cap_fps or q == "auto") else 0
    # An album holds several videos: keep them all so the panel can switch and a finished one moves on.
    # A page can list a video twice (some sites do, through yt-dlp's generic reader): keep each video once.
    seen, entries = set(), []
    for e in info.get("entries") or []:
        v = _entry_parts(e)[0] if isinstance(e, dict) else None
        if isinstance(v, str) and WEB_LINK.match(v) and _video_key(v) not in seen:  # skip one bad video, not the album
            seen.add(_video_key(v))
            entries.append(e)
    entries = entries or [info]
    items = [_entry_parts(e) for e in entries]
    if not items[0][0]:
        raise RuntimeError("no-stream")
    with _lock:
        idx = _album_pos.get(link, 0) % len(items) if len(items) > 1 else 0
        if len(items) > 1:
            _album[link] = {"items": items, "q": q, "fps_cap": fps_cap}
            _album_pos[link] = idx
        else:
            _album.pop(link, None)
    entry = entries[idx]
    is_live = bool(entry.get("is_live")) or entry.get("live_status") in ("is_live", "is_upcoming")
    _play_item(link, items[idx], q, fps_cap, is_live)
    with _lock:
        _status[link] = ("live" if is_live else "", time.time())
    if len(items) == 1 or not info.get("title"):
        info = entry  # an album keeps its own title; its videos are often "<album> (1)", "(2)"…
    if info.get("extractor_key", "").startswith("Twitch") and info.get("description"):
        return f"{info.get('uploader') or info['uploader_id']} · {info['description']}"  # Twitch's title is the channel
    # yt-dlp adds the lookup time to live titles ("... 2026-10-26 01:55").
    return re.sub(r"( \(live\))? \d{4}-\d\d-\d\d \d\d:\d\d$", "", info.get("title") or "")


def _video_key(url: str) -> str:
    """Same video file = same key: for a plain file (.mp4, .webm…) the part after "?" is usually a
    per-copy access token, so it's left out; other addresses must match in full."""
    base, _, _query = url.partition("?")
    if not VIDEO_FILE_PATH.search(base):
        return url
    host, _, path = base.split("://", 1)[-1].partition("/")
    return host.lower() + "/" + path  # file names are case-sensitive (often random letters)


def _entry_parts(entry: dict) -> tuple:
    """(video, audio, headers, source fps, length) of one yt-dlp answer or album entry."""
    parts = entry.get("requested_formats") or [entry]
    first = parts[0] if isinstance(parts[0], dict) else {}
    video = first.get("url")
    audio = parts[1].get("url") if len(parts) > 1 and isinstance(parts[1], dict) else None
    headers = dict(first.get("http_headers") or entry.get("http_headers") or {})
    return video, audio, headers, first.get("fps") or entry.get("fps"), entry.get("duration")


def _play_item(link: str, item: tuple, q: str, fps_cap: int, is_live: bool = False) -> None:
    video, audio, headers, src_fps, duration = item
    if fps_cap and (fps_cap >= 60 or (isinstance(src_fps, (int, float)) and 0 < src_fps <= fps_cap)):
        fps_cap = 0  # source already fits under the cap; pass native frames through without duplicating
    known_fps = None
    if isinstance(src_fps, (int, float)) and src_fps > 0:
        known_fps = min(round(src_fps), fps_cap) if fps_cap else round(src_fps)
    with _lock:  # set with the stream, so a redraw meanwhile sees a normal video
        _meta[link] = {"vod": not is_live,
                       "duration": float(duration) if isinstance(duration, (int, float)) and duration > 0 else 0.0}
    _store_stream(link, video, audio, headers, q, max_fps=fps_cap, fps=known_fps)
    if not is_live and not _meta.get(link, {}).get("duration"):
        _set_duration(link, probe_duration(video, headers))  # some sites: yt-dlp gives no length


def album(link: str) -> dict:
    """{"index": 0-based, "count": n} when the link is an album of several videos; else {}."""
    with _lock:
        a = _album.get(link)
        return {"index": _album_pos.get(link, 0), "count": len(a["items"])} if a else {}


def album_step(link: str, step: int) -> bool:
    """Switch an album to the video `step` away (wrapping around); slow (asks mpv for the length)."""
    with _lock:
        a = _album.get(link)
        if not a:
            return False
        idx = (_album_pos.get(link, 0) + int(step)) % len(a["items"])
        _album_pos[link] = idx
        item = a["items"][idx]
    _play_item(link, item, a["q"], a["fps_cap"])
    return True


class _Silent:
    """yt-dlp would print to stderr, which Anki replaces with an object yt-dlp can't use; errors still raise."""
    def debug(self, msg): pass
    info = warning = error = debug


def vod(link: str) -> dict:
    """{"vod": True, "duration": seconds or 0} for a converted normal video (it can restart at a time); else {}."""
    with _lock:
        m = _meta.get(link)
    return dict(m) if m and m.get("vod") else {}


def classify(info_or_error) -> str:
    """'live', 'offline' or '' (not a stream, or can't tell) from yt-dlp's answer or its error."""
    if isinstance(info_or_error, BaseException):
        return "offline" if OFFLINE_ERROR.search(str(info_or_error)) else ""
    info = info_or_error if isinstance(info_or_error, dict) else {}
    status = info.get("live_status")
    if info.get("is_live") or status == "is_live":
        return "live"
    if status == "is_upcoming":
        return "offline"  # scheduled, not started ("was_live"/"post_live" are recordings that play normally)
    return ""


def cached_status(link: str):
    """The last answer for a link if it's still fresh, else None."""
    with _lock:
        hit = _status.get(link)
    if not hit:
        return None
    value, at = hit
    return value if time.time() - at < (LIVE_TTL if value else OTHER_TTL) else None


def check_status(folder: str, link: str) -> str:
    """Is this link a live stream right now? One light page lookup (no video is fetched); the answer is cached."""
    hit = cached_status(link)
    if hit is not None:
        return hit
    if sys.version_info < (3, 10) or not os.path.exists(helper_path(folder)):
        return ""
    if helper_path(folder) not in sys.path:
        sys.path.insert(0, helper_path(folder))
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True,
            "logger": _Silent(), "socket_timeout": 15, "extract_flat": "in_playlist"}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            value = classify(ydl.extract_info(link, download=False))
    except Exception as e:  # noqa: BLE001  offline rooms and channels raise
        value = classify(e)
    with _lock:
        _status[link] = (value, time.time())
    return value


def address(link: str) -> str:
    global _server
    with _lock:
        if _server is None:
            _server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            _server.daemon_threads = True
            threading.Thread(target=_server.serve_forever, daemon=True).start()
        sid = _links[link]
        port = _server.server_port
    return f"http://127.0.0.1:{port}/{_token}/{sid}.webm"


def ready(link: str) -> bool:
    with _lock:
        return link in _links


def forget(link: str) -> None:
    with _lock:
        _streams.pop(_links.pop(link, None), None)
        _fps.pop(link, None)
        _meta.pop(link, None)
        _album.pop(link, None)


def _mpv_cmd_env() -> tuple:
    try:
        from aqt.sound import _packagedCmd  # Anki's own lookup for its bundled mpv + clean env
        cmd, env = _packagedCmd(["mpv"])
        return cmd, env
    except Exception:
        return ["mpv"], None


def mpv() -> list:
    return _mpv_cmd_env()[0]


def _vf_scale(h: int, max_fps: int = 0) -> str:
    # Cap FPS (15-45 when picked, else adaptive >=30) to limit encode CPU without duplicating frames; clamp above 1080p so lower-res feeds don't upscale.
    if max_fps and 0 < max_fps < 60:
        return f"--vf=lavfi=[fps=fps={max_fps},scale=-2:'min(ih,{h})']"
    return f"--vf=lavfi=[scale=-2:'min(ih,{h})']" if h > 1080 else f"--vf=scale=-2:{h}"


def pick_codecs(video_help: str, audio_help: str, quality: str = "auto", max_fps: int = 0) -> list:
    """WebM formats Anki's browser plays that this mpv can write. Anki's Mac mpv has VP8; its Windows mpv only AV1.
    In auto mode, the heavier AV1 encoder defaults to 360p and VP8 to 480p; explicit choices override both."""
    q = str(quality) if str(quality) in QUALITY_HEIGHT else "auto"
    if re.search(r"--ovc=libvpx\s", video_help):
        h = QUALITY_HEIGHT.get(q, 480)
        threads = ",threads=4" if h >= 1080 else ""
        out = [_vf_scale(h, max_fps), "--ovc=libvpx", f"--ovcopts=deadline=realtime,cpu-used=8{threads},b={BITRATE_VPX[h]}"]
    elif "--ovc=libaom-av1" in video_help:  # measured ~40% of one core at 480p on an M2, so auto uses 360p
        h = QUALITY_HEIGHT.get(q, 360)
        out = [_vf_scale(h, max_fps), "--ovc=libaom-av1", f"--ovcopts=usage=realtime,cpu-used=8,row-mt=1,lag-in-frames=0,b={BITRATE_AV1[h]}"]
    elif "--ovc=libsvtav1" in video_help:
        h = QUALITY_HEIGHT.get(q, 360)
        out = [_vf_scale(h, max_fps), "--ovc=libsvtav1", f"--ovcopts=preset=12,b={BITRATE_AV1[h]}"]
    else:
        return []
    # FFmpeg's own Vorbis encoder is marked experimental (strict=-2) and only takes stereo.
    vorbis = ["--oac=vorbis", "--oacopts=strict=-2", "--audio-channels=stereo"]
    return out + (["--oac=libopus"] if "--oac=libopus" in audio_help else vorbis)


def codecs(quality: str = "auto", max_fps: int = 0) -> list:
    global _codec_help
    if _codec_help is None:
        cmd, env = _mpv_cmd_env()

        def help_for(opt):
            try:
                return subprocess.run(cmd + ["--no-config", opt], capture_output=True, text=True, timeout=15,
                                      env=env, creationflags=NO_WINDOW).stdout
            except (OSError, subprocess.SubprocessError):
                return ""
        _codec_help = (help_for("--ovc=help"), help_for("--oac=help"))
    return pick_codecs(_codec_help[0], _codec_help[1], quality, max_fps=max_fps)


def convert_cmd(video: str, audio, headers: dict = None, quality: str = "auto", max_fps: int = 0, start: float = 0) -> list:
    cmd = mpv() + ["--no-config", "--really-quiet", "--no-terminal", "--hwdec=auto-safe", video, "--o=-", "--of=webm"] + codecs(quality, max_fps=max_fps)
    if start and start > 0:
        cmd.append(f"--start=+{float(start):.1f}")  # a normal video restarted at a time (seeking); live streams ignore it
    for k, v in (headers or {}).items():
        if not isinstance(k, str) or not isinstance(v, str) or "\n" in v or "\r" in v:
            continue
        lk = k.lower()
        if lk == "user-agent":
            cmd.append(f"--user-agent={v}")
        elif lk == "referer":
            cmd.append(f"--referrer={v}")
        elif lk in ("cookie", "origin", "accept-language"):
            cmd.append(f"--http-header-fields-append={k}: {v}")
    if audio:
        cmd.append(f"--audio-file={audio}")
    return cmd


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path, _, query = self.path.partition("?")
        parts = path.strip("/").split("/")
        start = 0.0
        for pair in query.split("&"):
            key, _, value = pair.partition("=")
            if key == "start":
                try:
                    start = max(0.0, min(float(value), 864000.0))
                except ValueError:
                    start = 0.0
        with _lock:
            stream = _streams.get(parts[1][:-5]) if len(parts) == 2 and parts[0] == _token else None
        if not stream or not parts[1].endswith(".webm"):
            return self.send_error(404)
        video, audio = stream[:2]
        headers = stream[2] if len(stream) > 2 else None
        quality = stream[3] if len(stream) > 3 else "auto"
        raw_fps = stream[4] if len(stream) > 4 else 0
        max_fps = (30 if raw_fps is True else (0 if raw_fps is False else int(raw_fps)))
        _, env = _mpv_cmd_env()
        proc = subprocess.Popen(convert_cmd(video, audio, headers, quality, max_fps=max_fps, start=start), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, env=env, creationflags=NO_WINDOW)
        with _lock:
            _procs.add(proc)
        self.send_response(200)
        self.send_header("Content-Type", "video/webm")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        read = getattr(proc.stdout, "read1", proc.stdout.read)
        try:
            while chunk := read(64 * 1024):
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # the page moved on or reloaded
        finally:
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.SubprocessError:
                pass
            with _lock:
                _procs.discard(proc)

    def log_message(self, *args):
        pass
