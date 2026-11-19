"""Check link parsing without Anki: python3 tests/test_source.py"""
import os, sys, types

for name in ["aqt", "aqt.overview", "aqt.qt", "aqt.reviewer", "aqt.utils"]:
    sys.modules[name] = types.ModuleType(name)
aqt = sys.modules["aqt"]
aqt.gui_hooks = types.SimpleNamespace()
aqt.mw = types.SimpleNamespace(addonManager=types.SimpleNamespace(
    addonFromModule=lambda n: "backdrop_video", setWebExports=lambda *a: None, getConfig=lambda n: None))
sys.modules["aqt.overview"].Overview = sys.modules["aqt.reviewer"].Reviewer = object
sys.modules["aqt.utils"].tooltip = lambda *a, **k: None
sys.modules["aqt.utils"].askUser = lambda *a, **k: False
for n in "QCheckBox QComboBox QDialog QFormLayout QHBoxLayout QLabel QLineEdit QPushButton QSlider Qt QVBoxLayout".split():
    setattr(sys.modules["aqt.qt"], n, object)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backdrop_video"))
import live
sys.modules["backdrop_video"] = types.ModuleType("backdrop_video")
sys.modules["backdrop_video"].live = sys.modules["backdrop_video.live"] = live
code = open(os.path.join(os.path.dirname(__file__), "..", "backdrop_video", "__init__.py")).read()
ns = {"__name__": "backdrop_video", "__package__": "backdrop_video", "__file__": "x/__init__.py"}
exec(code.split("class BackdropDialog")[0], ns)
source = ns["source"]

for url, kind in {
    "https://www.youtube.com/watch?v=jfKfPfyJRdk": "youtube",
    "youtu.be/jfKfPfyJRdk?t=30": "youtube",
    "https://www.youtube.com/live/jfKfPfyJRdk": "youtube",
    "https://www.youtube.com/playlist?list=PL123abc": "youtube",
    "https://example.com/rain.mp4": "video",
    "https://vimeo.com/123": "page",
}.items():
    assert source(url, 0)["kind"] == kind, url
assert "list=PL123abc" in source("https://www.youtube.com/playlist?list=PL123abc", 0)["src"]
assert "playlist=" not in source("youtu.be/jfKfPfyJRdk", 0)["src"]  # loop param breaks live streams
assert "&start=42" in source("youtu.be/jfKfPfyJRdk", 42)["src"]
assert source("javascript:alert(1)", 0)["src"].startswith("https://")
assert "<\\/script>" in ns["start_js"]({"src": "</script>"})
assert "autoplay=0" in source("youtu.be/jfKfPfyJRdk", 0, paused=True)["src"]

# Panel messages come from the page, so bad values must be dropped, not saved.
store = {}
aqt.mw.addonManager.getConfig = lambda n: dict(store)
aqt.mw.addonManager.writeConfig = lambda n, c: store.update(c)
aqt.mw.state = "deckBrowser"
ns["apply"]({"strength": 250, "plate_opacity": -10, "true_transparency": True, "volume": "50", "panel": 1, "picture": False, "evil": True, "quality": "720"})
assert store["strength"] == 100 and store["plate_opacity"] == 0 and store["true_transparency"] is True and store["volume"] == 50 and store["panel"] is False and store["quality"] == "720" and "evil" not in store
ns["apply"]({"quality": "999", "volume": float("nan"), "true_transparency": "yes", "strength": 42})
assert store["quality"] == "720" and store["strength"] == 42 and store["volume"] == 50 and store["true_transparency"] is True
for i in range(7):
    ns["apply"]({"url": f"youtu.be/aaaaaaaaaa{i}"})
assert len(store["recent"]) == 5 and store["recent"][0]["url"] == "youtu.be/aaaaaaaaaa6" and store["on"]
ns["apply"]({"paused": True})
ns["apply"]({"url": "youtu.be/aaaaaaaaaa3"})  # picking a recent video moves it to the top and plays it
assert store["recent"][0]["url"] == "youtu.be/aaaaaaaaaa3" and len(store["recent"]) == 5 and not store["paused"]
store.update(url="youtu.be/old", title="Old", recent=[])  # a link saved before the recent list existed
ns["apply"]({"url": "youtu.be/aaaaaaaaaa4"})
assert store["recent"] == [{"url": "youtu.be/aaaaaaaaaa4", "title": ""}, {"url": "youtu.be/old", "title": "Old"}], store["recent"]
ns["apply"]({"url": "youtu.be/aaaaaaaaaa3"})
# A found live stream replaces the YouTube embed with the local converted stream.
live._links["youtu.be/aaaaaaaaaa3"] = "id"
live.address = lambda link: "http://127.0.0.1:1/t/0.webm"
st = ns["state"](True)
assert st["kind"] == "video" and st["live"] and st["src"].endswith(".webm"), st
assert live.convert_cmd("v", "a")[-1] == "--audio-file=a"
hdr_cmd = live.convert_cmd("v", None, {"User-Agent": "UA", "Referer": "https://r.example/", "Cookie": "a=1", "Bad": "x\ny"})
assert "--user-agent=UA" in hdr_cmd and "--referrer=https://r.example/" in hdr_cmd
assert "--http-header-fields-append=Cookie: a=1" in hdr_cmd and not any("Bad" in c for c in hdr_cmd)
# Mac mpv has VP8 + Opus; Windows mpv (anki_audio 0.2.3) only AV1 + FFmpeg's own Vorbis.
mac = live.pick_codecs("  --ovc=libvpx           libvpx VP8\n  --ovc=libvpx-vp9  libvpx VP9\n", "  --oac=libopus  libopus\n")
assert "--ovc=libvpx" in mac and "--oac=libopus" in mac, mac
win = live.pick_codecs("  --ovc=libaom-av1  libaom AV1\n  --ovc=libx264 x\n", "  --oac=vorbis  Vorbis\n")
assert "--ovc=libaom-av1" in win and "--oacopts=strict=-2" in win, win
assert "--vf=scale=-2:720" in live.pick_codecs("  --ovc=libvpx           x\n", "", "720")
assert "--vf=scale=-2:720" in live.pick_codecs("  --ovc=libaom-av1  x\n", "", "720")
assert "min(ih,2160)" in " ".join(live.pick_codecs("  --ovc=libvpx           x\n", "", "2160"))
assert "fps=30" in " ".join(live.pick_codecs("  --ovc=libvpx           x\n", "", "480", max_fps=30))
assert 30 <= live.adaptive_fps(1, "--ovc=libvpx ") <= 60 and live.adaptive_fps(3, "--ovc=libvpx ") == 30
assert live.adaptive_fps(3, "--ovc=libvpx ", multi_fps=60) == 60 and live.adaptive_fps(3, "--ovc=libvpx ", multi_fps=0) == 0
assert "Auto:" in live.effective_note("auto", 1) and "uncapped fps" in live.effective_note("auto", 3, multi_fps=0)
assert live.effective_note("2160", 1) == "4K" and live.effective_note("2160", 1, fps_list=[60]) == "4K, 60 fps"
assert live.effective_note("1080", 2, fps_list=[30, 60]) == "1080p, 30–60 fps"
assert "source fps" not in live.effective_note("2160", 1) and "source fps" not in live.effective_note("auto", 3, multi_fps=0)
ns["on_js_message"]((False, None), 'backdrop:fps:{"url":"youtu.be/aaaaaaaaaa3","fps":60}', None)
assert live.stream_fps("youtu.be/aaaaaaaaaa3") == 60
live.forget("youtu.be/aaaaaaaaaa3")
assert live.stream_fps("youtu.be/aaaaaaaaaa3") is None
assert "--hwdec=auto-safe" in live.convert_cmd("v", None)
assert "threads=4" in " ".join(live.pick_codecs("  --ovc=libvpx           x\n", "", "1440"))
assert "--ovc=libvpx" not in live.pick_codecs("  --ovc=libvpx-vp9  x\n", "") and live.pick_codecs("--ovc=libx264", "") == []
# Channel /live pages and Twitch are worth a stream lookup; ordinary pages are not.
calls = []
orig_go_live = ns["go_live"]
ns["go_live"] = lambda url, quiet=False: calls.append((url, quiet))
os_path_exists = ns["os"].path.exists
ns["os"].path.exists = lambda p: False  # helper not downloaded yet
store.pop("urls", None)  # simulate legacy config with only "url"
store["url"] = "https://www.twitch.tv/somechannel"
assert ns["state"](True)["src"] == "about:blank"  # Twitch refuses framing; blank until the stream is found
ns["try_live"](ns["state"](True))
store["url"] = "https://example.com/"
ns["try_live"](ns["state"](True))
store["url"] = "https://example.com/live.m3u8"
assert ns["state"](True)["src"] == "about:blank"  # raw .m3u8 waits for mpv conversion instead of downloading in iframe
ns["try_live"](ns["state"](True))
ns["os"].path.exists = os_path_exists
assert len(calls) == 2 and calls[0][1] is False and calls[1][0] == "https://example.com/live.m3u8", calls
# Explicit Play/URL paste asks for yt-dlp even on general pages.
store["url"] = "https://vimeo.com/123"
ns["try_live"]({"on": True, "kind": "page", "src": "https://vimeo.com/123"}, prompt_if_missing=True)
assert len(calls) == 3 and calls[2] == ("https://vimeo.com/123", True), calls
# register_direct wires .m3u8/.mp4 directly through mpv without yt-dlp.
live.codecs = lambda q="auto": ["--ovc=libvpx"]
live.shutil.which = lambda c: "/usr/bin/mpv"
assert live.register_direct("https://example.com/live.m3u8", "720") == ""
assert live.ready("https://example.com/live.m3u8") and live.address("https://example.com/live.m3u8").endswith(".webm")
live.forget("https://example.com/live.m3u8")
# A page a quiet lookup found no stream on isn't looked up again on every page.
store["url"] = "https://example.com/"
ns["_not_live"].add(store["url"])
ns["try_live"]({"on": True, "kind": "page", "src": "https://example.com/"})
assert len(calls) == 3, calls
store["chat"] = True  # a key an older version saved
assert "chat" not in ns["conf"]()
# Copied http(s) links are offered in the panel; bare non-video paths and plain text are not.
clip = []
ns["mw"].app = types.SimpleNamespace(clipboard=lambda: types.SimpleNamespace(text=lambda: clip[-1]))
clip.append("https://www.twitch.tv/somechannel")
assert ns["clipboard_link"]() == clip[-1]
clip.append("https://example.com/page")
assert ns["clipboard_link"]() == clip[-1]
clip.append("example.com/page")
assert ns["clipboard_link"]() == ""
clip.append("just plain text")
assert ns["clipboard_link"]() == ""
# Whitespace-only URL doesn't trigger refresh or save.
calls_ref = []
orig_refresh = ns["refresh"]
ns["refresh"] = lambda: calls_ref.append(1)
ns["apply"]({"url": "   "})
assert calls_ref == []
ns["refresh"] = orig_refresh
# Position saves on leaving review/overview and after >=30s advance.
ns["on_js_message"]((False, None), "backdrop:time:45", None)
assert store.get("time") == 45
ns["on_js_message"]((False, None), "backdrop:time:50", None)
assert store.get("time") == 45  # <30s change waits
ns["on_state_will_change"]("deckBrowser", "review")
assert store.get("time") == 50
# download_helper clears yt_dlp.* submodules cleanly.
sys.modules["yt_dlp"] = types.ModuleType("yt_dlp")
sys.modules["yt_dlp.utils"] = types.ModuleType("yt_dlp.utils")
import tempfile, io, zipfile
with tempfile.TemporaryDirectory() as td:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("yt_dlp/__init__.py", "")
    orig_urlopen = live.urllib.request.urlopen
    live.urllib.request.urlopen = lambda *a, **k: io.BytesIO(buf.getvalue())
    try:
        live.download_helper(td)
    finally:
        live.urllib.request.urlopen = orig_urlopen
assert "yt_dlp" not in sys.modules and "yt_dlp.utils" not in sys.modules
# Multi-stream up to 4: add_url, cap at 4, mute per slot, remove_url, and fallback to single.
store.clear()
ns["apply"]({"url": "https://example.com/1.webm"})
assert ns["apply"] or True
for u in ["https://example.com/2.webm", "https://example.com/3.webm", "https://example.com/4.webm", "https://example.com/5.webm"]:
    ns["apply"]({"add_url": u})
st4 = ns["state"](True)
assert len(st4["urls"]) == 4 and len(st4["streams"]) == 4 and st4["urls"][-1] == "https://example.com/4.webm", st4
ns["apply"]({"muted": ["https://example.com/2.webm", "https://not-active.webm"]})
assert ns["state"](True)["muted"] == ["https://example.com/2.webm"]
ns["apply"]({"remove_url": "https://example.com/1.webm"})
st3 = ns["state"](True)
assert st3["urls"] == ["https://example.com/2.webm", "https://example.com/3.webm", "https://example.com/4.webm"] and st3["url"] == "https://example.com/2.webm", st3
# Deleting from recent works (including current url when off), and removing the last active stream clears url/on cleanly.
ns["apply"]({"forget_recent": "https://example.com/1.webm"})
assert all(r["url"] != "https://example.com/1.webm" for r in ns["conf"]()["recent"])
for u in list(st3["urls"]):
    ns["apply"]({"remove_url": u})
assert not ns["conf"]()["on"] and ns["conf"]()["url"] == "" and ns["conf"]()["urls"] == []
ns["apply"]({"url": "https://example.com/off-recent.webm"})
ns["apply"]({"on": False})
ns["apply"]({"forget_recent": "https://example.com/off-recent.webm"})
assert ns["conf"]()["url"] == "" and all(r["url"] != "https://example.com/off-recent.webm" for r in ns["conf"]()["recent"])
# Break Zone moves review -> overview cleanly and restores on exit, exposing Pomodoro Bar or generic Pomodoro rest status.
moves = []
ns["mw"].state = "review"
ns["mw"].moveToState = lambda s: (moves.append(s), setattr(ns["mw"], "state", s))
ns["refresh"] = lambda *a, **k: None
fake_pb = types.ModuleType("pb_fake")
fake_pb.__file__ = "/addons21/239058710/__init__.py"
fake_pb._resting = True
fake_pb._completed = False
fake_pb._rest_duration_seconds = lambda: 300
fake_pb._rest_elapsed = lambda: 60
sys.modules["pb_fake"] = fake_pb
try:
    ns["on_js_message"]((False, None), "backdrop:break:1", None)
    binfo = ns["state"](False)["break_info"]
    assert moves == ["overview"] and binfo["active"] and binfo["rest_left"] == 240, (moves, binfo)
    ns["on_js_message"]((False, None), "backdrop:break:0", None)
    assert moves == ["overview", "review"] and not ns["state"](False)["break_info"]["active"], moves
finally:
    sys.modules.pop("pb_fake", None)
generic_pb = types.ModuleType("pb_generic")
generic_pb.__file__ = "/addons21/999999/__init__.py"
generic_pb.is_resting = lambda: True
generic_pb.rest_remaining = lambda: 90
sys.modules["pb_generic"] = generic_pb
try:
    ns["on_js_message"]((False, None), "backdrop:break:1", None)
    assert ns["state"](False)["break_info"]["rest_left"] == 90
    ns["on_js_message"]((False, None), "backdrop:break:0", None)
finally:
    sys.modules.pop("pb_generic", None)
# Incompatible live stream prompt: declining once still asks on a different URL unless "Don't show this again" is saved.
ns["go_live"] = orig_go_live
asked = []
orig_ver = sys.version_info
sys.version_info = (3, 12, 0)
ns["os"].path.exists = lambda p: False
ns["_ask_download_helper"] = lambda: (asked.append(1) or False, False)
try:
    ns["go_live"]("https://www.twitch.tv/stream1")
    ns["go_live"]("https://www.twitch.tv/stream1")  # same URL suppressed this session until replayed
    ns["go_live"]("https://www.twitch.tv/stream2")  # different stream still asks
    assert len(asked) == 2, asked
    ns["_ask_download_helper"] = lambda: (asked.append(2) or False, True)  # check "Don't show this again"
    ns["go_live"]("https://www.twitch.tv/stream3")
    ns["go_live"]("https://www.twitch.tv/stream4")  # now globally suppressed
    assert len(asked) == 3 and ns["conf"]()["suppress_ytdlp_prompt"] is True, asked
finally:
    sys.version_info = orig_ver
    ns["os"].path.exists = os_path_exists
# Saved folders: create, save (case-insensitive dedupe), title sync, play_urls (cap at 4), unsave, delete, and Browse start URL.
store.clear()
ns["apply"]({"create_folder": "Lofi"})
ns["apply"]({"save_item": {"folder": "lofi", "url": "https://example.com/a.webm", "title": "Track A"}})
ns["apply"]({"save_item": {"folder": "Lofi", "url": "https://example.com/a.webm", "title": "Dup"}})  # no duplicate
ns["apply"]({"save_item": {"folder": "Chill", "url": "https://example.com/b.webm", "title": ""}})
flds = ns["state"](True)["folders"]
assert len(flds) == 2 and flds[0]["name"] == "Lofi" and len(flds[0]["items"]) == 1 and flds[0]["items"][0]["title"] == "Track A", flds
ns["set_title"]("Track B", "https://example.com/b.webm")
assert ns["state"](True)["folders"][1]["items"][0]["title"] == "Track B"
ns["apply"]({"play_urls": ["https://example.com/a.webm", "https://example.com/b.webm", "https://example.com/c.webm", "https://example.com/d.webm", "https://example.com/e.webm"]})
st_multi = ns["state"](True)
assert st_multi["on"] and len(st_multi["urls"]) == 4 and st_multi["urls"][0] == "https://example.com/a.webm", st_multi
ns["apply"]({"unsave_item": {"folder": "Lofi", "url": "https://example.com/a.webm"}})
assert ns["state"](True)["folders"][0]["items"] == []
ns["apply"]({"delete_folder": "Lofi"})
assert [f["name"] for f in ns["state"](True)["folders"]] == ["Chill"]
# Browse start URL avoids reopening an already-playing video and falls back to that site's home page.
ns["_last_browse_url"] = "https://www.twitch.tv/lofi_channel"
store.update(on=True, url="https://www.twitch.tv/lofi_channel", urls=["https://www.twitch.tv/lofi_channel"])
assert ns["_browse_start_url"]() == "https://www.twitch.tv/", ns["_browse_start_url"]()
store.update(on=False)
assert ns["_browse_start_url"]() == "https://www.twitch.tv/lofi_channel", ns["_browse_start_url"]()
print("ok")
