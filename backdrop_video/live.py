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
_procs = set()  # running converters; mpv outlives Anki unless stopped


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


def _store_stream(link: str, video: str, audio, headers: dict, quality: str, max_fps: int = 0) -> None:
    sid = secrets.token_hex(4)
    with _lock:
        _streams[sid] = (video, audio, headers, quality, max_fps)
        _links[link] = sid
    address(link)  # start the server off the main thread


def adaptive_fps(stream_count: int = 1, video_help: str = "", multi_fps="auto") -> int:
    """Pick an FPS cap (>=30, or 0 for uncapped) using hardware cores, stream count, and optional multi_fps override."""
    if isinstance(multi_fps, (int, float)) and not isinstance(multi_fps, bool):
        val = int(multi_fps)
        return 0 if val <= 0 else max(30, min(120, val))
    if isinstance(multi_fps, str) and multi_fps.strip().isdigit():
        val = int(multi_fps.strip())
        return 0 if val <= 0 else max(30, min(120, val))
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


def effective_note(quality: str = "auto", stream_count: int = 1, multi_fps="auto") -> str:
    """Short unobtrusive status showing what resolution and FPS cap Auto/stream conversion uses."""
    vhelp = (_codec_help or ("", ""))[0]
    heavy = bool(vhelp) and not re.search(r"--ovc=libvpx\s", vhelp)
    fps = adaptive_fps(stream_count, vhelp, multi_fps=multi_fps)
    fps_txt = "source fps" if fps == 0 else ("up to 60 fps" if fps >= 60 else f"up to {fps} fps")
    q = str(quality) if str(quality) in QUALITY_HEIGHT else "auto"
    if q == "auto":
        res = "360p" if (heavy or stream_count >= 2) else "480p"
        return f"Auto: {res}, {fps_txt}"
    label = "4K" if q == "2160" else f"{q}p"
    return f"{label}, {fps_txt}" if stream_count >= 2 else f"{label}, source fps"


def register_direct(link: str, quality: str = "auto", stream_count: int = 1, cap_fps: bool = False, multi_fps="auto") -> str:
    """Register a direct stream/video URL (.m3u8, .mpd, H.264 .mp4) for mpv-to-WebM conversion without yt-dlp."""
    q = _ensure_mpv(quality)
    vhelp = (_codec_help or ("", ""))[0]
    fps_cap = adaptive_fps(stream_count, vhelp, multi_fps=multi_fps) if (cap_fps or q == "auto") else 0
    _store_stream(link, link, None, {}, q, max_fps=fps_cap)
    return ""


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
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "format": fmt, "logger": _Silent()}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(link, download=False)
    if isinstance(info, dict) and info.get("entries"):
        info = next((e for e in info["entries"] if e), info)
    parts = (info.get("requested_formats") if isinstance(info, dict) else None) or [info]
    video = parts[0].get("url") if parts and isinstance(parts[0], dict) else None
    if not video:
        raise RuntimeError("no-stream")
    audio = parts[1].get("url") if len(parts) > 1 and isinstance(parts[1], dict) else None
    headers = dict(parts[0].get("http_headers") or info.get("http_headers") or {})
    vhelp = (_codec_help or ("", ""))[0]
    fps_cap = adaptive_fps(stream_count, vhelp, multi_fps=multi_fps) if (cap_fps or q == "auto") else 0
    src_fps = parts[0].get("fps") or (info.get("fps") if isinstance(info, dict) else None)
    if fps_cap and (fps_cap >= 60 or (isinstance(src_fps, (int, float)) and 0 < src_fps <= fps_cap)):
        fps_cap = 0  # source already fits under the cap; pass native frames through without duplicating
    _store_stream(link, video, audio, headers, q, max_fps=fps_cap)
    if info.get("extractor_key", "").startswith("Twitch") and info.get("description"):
        return f"{info.get('uploader') or info['uploader_id']} · {info['description']}"  # Twitch's title is the channel
    # yt-dlp adds the lookup time to live titles ("... 2026-10-26 01:55").
    return re.sub(r"( \(live\))? \d{4}-\d\d-\d\d \d\d:\d\d$", "", info.get("title") or "")


class _Silent:
    """yt-dlp would print to stderr, which Anki replaces with an object yt-dlp can't use; errors still raise."""
    def debug(self, msg): pass
    info = warning = error = debug


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
    # Cap FPS adaptively (>=30) to limit encode CPU without duplicating frames; clamp above 1080p so lower-res feeds don't upscale.
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


def convert_cmd(video: str, audio, headers: dict = None, quality: str = "auto", max_fps: int = 0) -> list:
    cmd = mpv() + ["--no-config", "--really-quiet", "--no-terminal", "--hwdec=auto-safe", video, "--o=-", "--of=webm"] + codecs(quality, max_fps=max_fps)
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
        parts = self.path.split("?", 1)[0].strip("/").split("/")
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
        proc = subprocess.Popen(convert_cmd(video, audio, headers, quality, max_fps=max_fps), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
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
