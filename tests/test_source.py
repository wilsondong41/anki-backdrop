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
# Break Zone stays on the card screen (no page reload, so videos keep playing): the answer buttons hide,
# Pomodoro Bar is paused and resumed directly, and the card's timer restarts when the break ends.
moves, calls = [], []
ns["mw"].state = "review"
ns["mw"].moveToState = lambda s: (moves.append(s), setattr(ns["mw"], "state", s))
bottom_web = types.SimpleNamespace(visible=True, setVisible=lambda v: setattr(bottom_web, "visible", v))
card = types.SimpleNamespace(start_timer=lambda: calls.append("timer"))
ns["mw"].reviewer = types.SimpleNamespace(bottom=types.SimpleNamespace(web=bottom_web), card=card)
ns["refresh"] = lambda *a, **k: None
fake_pb = types.ModuleType("239058710")
fake_pb.__file__ = "/addons21/239058710/__init__.py"
fake_pb._resting = True
fake_pb._completed = False
fake_pb._rest_duration_seconds = lambda: 300
fake_pb._rest_elapsed = lambda: 60
fake_pb._pause_for_review_exit = lambda: calls.append("pomo-pause")
fake_pb._on_state_did_change = lambda new, old: calls.append(f"pomo-{old}->{new}")
sys.modules["239058710"] = fake_pb
try:
    ns["on_js_message"]((False, None), "backdrop:break:1", None)
    binfo = ns["state"](False)["break_info"]
    assert moves == [] and binfo["active"] and binfo["rest_left"] == 240, (moves, binfo)
    assert bottom_web.visible is False and calls == ["pomo-pause"], calls
    ns["on_js_message"]((False, None), "backdrop:break:0", None)
    assert moves == [] and not ns["state"](False)["break_info"]["active"]
    assert bottom_web.visible is True and calls == ["pomo-pause", "timer", "pomo-overview->review"], calls
    # Anki's auto advance is paused for the break (it would answer the hidden card) and comes back after.
    rv = ns["mw"].reviewer
    rv.auto_advance_enabled = True
    rv._clear_auto_advance_timers = lambda: calls.append("aa-clear")
    rv.auto_advance_if_enabled = lambda: calls.append("aa-resume")
    calls.clear()
    ns["on_js_message"]((False, None), "backdrop:break:1", None)
    assert rv.auto_advance_enabled is False and "aa-clear" in calls, calls
    ns["on_js_message"]((False, None), "backdrop:break:0", None)
    assert rv.auto_advance_enabled is True and calls[-3:] == ["aa-resume", "timer", "pomo-overview->review"], calls
    rv.auto_advance_enabled = False
    # Leaving the screen during a break ends it and brings the buttons back.
    ns["on_js_message"]((False, None), "backdrop:break:1", None)
    ns["on_state_will_change"]("deckBrowser", "review")
    assert bottom_web.visible is True and not ns["_break_mode"]
finally:
    sys.modules.pop("239058710", None)
# A countdown break: pick 5/10/15/20/30 minutes (0 = count up); anything else is ignored.
ns["mw"].state = "overview"
ns["apply"]({"break_minutes": 10})
ns["apply"]({"break_minutes": 7})
ns["apply"]({"break_minutes": True})
assert ns["conf"]()["break_minutes"] == 10
ns["_quick"] = (0.0, None)
ns["on_js_message"]((False, None), "backdrop:break:1", None)
bi = ns["state"](False)["break_info"]
assert bi["minutes"] == 10 and bi["left"] == 600, bi
ns["on_js_message"]((False, None), "backdrop:break:0", None)
ns["apply"]({"break_minutes": 0})
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
# Regression: playing a video and closing it with X (remove_url) or turning off NEVER deletes saved folders.
ns["apply"]({"url": "https://example.com/play-then-close.webm"})
ns["apply"]({"remove_url": "https://example.com/play-then-close.webm"})
assert [f["name"] for f in ns["state"](True)["folders"]] == ["Chill"] and len(ns["state"](True)["folders"][0]["items"]) == 1
assert ns["rename_folder"]("Chill", "Study Beats") is True
assert ns["create_folder"]("Focus") is True
assert ns["move_folder_item"]("Study Beats", "Focus", "https://example.com/b.webm") is True
f_after = ns["state"](True)["folders"]
assert [f["name"] for f in f_after] == ["Study Beats", "Focus"] and f_after[0]["items"] == [] and len(f_after[1]["items"]) == 1, f_after
# Browse start URL avoids reopening an already-playing video and falls back to that site's home page.
ns["_last_browse_url"] = "https://www.twitch.tv/lofi_channel"
store.update(on=True, url="https://www.twitch.tv/lofi_channel", urls=["https://www.twitch.tv/lofi_channel"])
assert ns["_browse_start_url"]() == "https://www.twitch.tv/", ns["_browse_start_url"]()
store.update(on=False)
assert ns["_browse_start_url"]() == "https://www.twitch.tv/lofi_channel", ns["_browse_start_url"]()
# Per-slot timestamps persist independently across multi-view videos.
ns["apply"]({"play_urls": ["https://youtu.be/aaaaaaaaaa1", "https://youtu.be/aaaaaaaaaa2"]})
ns["on_js_message"]((False, None), 'backdrop:slot_time:{"url":"https://youtu.be/aaaaaaaaaa1","time":120}', None)
ns["on_js_message"]((False, None), 'backdrop:slot_time:{"url":"https://youtu.be/aaaaaaaaaa2","time":345}', None)
st_times = ns["state"](True)
assert "&start=120" in st_times["streams"][0]["src"] and "&start=345" in st_times["streams"][1]["src"], st_times["streams"]
# Live status: yt-dlp answers and errors -> green/red/none, cached for a few minutes.
import time as _t
assert live.classify({"is_live": True}) == "live"
assert live.classify({"live_status": "was_live"}) == ""  # a recording of a finished broadcast plays like a video
assert live.classify({"live_status": "is_upcoming"}) == "offline"
assert live.classify({"live_status": "not_live", "duration": 300}) == ""
assert live.classify(Exception("ERROR: [twitch:stream] lofi: The channel is not currently live")) == "offline"
assert live.classify(Exception("ERROR: Room is currently offline")) == "offline"
assert live.classify(Exception("HTTP Error 404: Not Found")) == ""
live._status["https://www.twitch.tv/lofi_channel"] = ("live", _t.time())
live._status["https://example.com/cam"] = ("offline", _t.time() - live.LIVE_TTL - 5)  # stale
assert live.cached_status("https://www.twitch.tv/lofi_channel") == "live"
assert live.cached_status("https://example.com/cam") is None
store.setdefault("recent", []).insert(0, {"url": "https://www.twitch.tv/lofi_channel", "title": "Lofi", "time": 0})
assert ns["state"](True)["live_status"].get("https://www.twitch.tv/lofi_channel") == "live", ns["state"](True)["live_status"]
# A converted normal video (not live) restarts at the saved time, so it can be skipped/scrubbed like YouTube.
vlink = "https://vimeo.com/123456"
live._links[vlink] = "sidv"
live._meta[vlink] = {"vod": True, "duration": 600}
spec = ns["_stream_spec"](vlink, 42, False)
assert spec["vod"] is True and spec["duration"] == 600 and spec["src"].endswith(".webm?start=42"), spec
assert "?start" not in ns["_stream_spec"](vlink, 0, False)["src"]
live._meta[vlink] = {"vod": False}
assert "vod" not in ns["_stream_spec"](vlink, 42, False)
# The card page reports where the keyboard is, for Hold-P inside websites.
ns["on_js_message"]((False, None), "backdrop:focus:frame", None)
assert ns["_web_focus"] == "frame"
ns["on_js_message"]((False, None), "backdrop:focus:bogus", None)
assert ns["_web_focus"] == ""
# Only web addresses reach mpv; only saved links get live lookups.
for bad in ("file:///etc/passwd", "--script=x.lua", "lavfi://color"):
    try:
        live._store_stream("https://x.example/", bad, None, {}, "auto")
        raise AssertionError(bad)
    except ValueError:
        pass
assert "https://www.twitch.tv/lofi_channel" in ns["_saved_links"](store)
assert "https://evil.example/" not in ns["_saved_links"](store)
# Frame-rate picker: saved choice, older numbers, the converter cap and the status line.
assert ns["_fps_choice"]({"multi_fps": "auto"}) == "auto" and ns["_fps_choice"]({"multi_fps": 24}) == "24"
assert ns["_fps_choice"]({"multi_fps": 60}) == "0" and ns["_fps_choice"]({"multi_fps": 50}) == "45" and ns["_fps_choice"]({"multi_fps": "x"}) == "auto"
assert live.adaptive_fps(1, "", multi_fps="15") == 15 and live.adaptive_fps(1, "", multi_fps="0") == 0
assert live._vf_scale(720, 24) == "--vf=lavfi=[fps=fps=24,scale=-2:'min(ih,720)']", live._vf_scale(720, 24)
assert live.effective_note("720", 1, multi_fps=24) == "720p, up to 24 fps", live.effective_note("720", 1, multi_fps=24)
assert live.effective_note("720", 1, multi_fps=0) == "720p, uncapped fps"
assert live.effective_note("720", 1) == "720p"
store["multi_fps"] = 30
assert ns["state"](True)["fps"] == "30"
store["multi_fps"] = "auto"
# Audit fixes: direct files are keyed by the saved link; status line uses the converter's frame rate; quick config.
live._ensure_mpv = lambda q="auto": "auto"
live.address = lambda link: "http://127.0.0.1:1/t/x.webm"
# A video whose length yt-dlp doesn't give (some album sites, plain .mp4) asks mpv, so the position slider shows.
ran = []
real_run = live.subprocess.run
live.subprocess.run = lambda args, **k: ran.append(args) or types.SimpleNamespace(stdout="BDDUR=598.541\n")
assert live.probe_duration("https://v.example.com/a.mp4", {"Referer": "https://x/", "User-Agent": "UA", "Cookie": "a=1"}) == 598.541
assert "--referrer=https://x/" in ran[0] and "--user-agent=UA" in ran[0] and "--http-header-fields=Cookie: a=1" in ran[0]
assert ran[0][-2:] == ["--", "https://v.example.com/a.mp4"]
live.register_direct("example.com/clip.mp4", video="https://example.com/clip.mp4")
assert live.vod("example.com/clip.mp4") == {"vod": True, "duration": 598.541}
live.subprocess.run = lambda args, **k: types.SimpleNamespace(stdout="")
assert live.probe_duration("https://v.example.com/a.mp4") == 0.0
live.subprocess.run = real_run
# Albums: every video is kept, the panel steps through them (wrapping), and a new lookup keeps the place.
items = [("https://v.example.com/%d.mp4" % i, None, {}, 30, 60.0 + i) for i in range(3)]
live._album["alb"] = {"items": items, "q": "auto", "fps_cap": 0}
live._album_pos["alb"] = 0
assert live.album("alb") == {"index": 0, "count": 3}
assert live.album_step("alb", -1) is True and live.album("alb")["index"] == 2
assert live._streams[live._links["alb"]][0] == "https://v.example.com/2.mp4" and live.vod("alb")["duration"] == 62.0
assert live.album_step("alb", 1) and live.album("alb")["index"] == 0
live.forget("alb")
assert live.album("alb") == {} and live.album_step("alb", 1) is False and live._album_pos["alb"] == 0
# find() keeps each album video once (some pages list a video twice).
class _FakeYDL:
    def __init__(self, opts): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def extract_info(self, link, download=False):
        return {"title": "Alb", "entries": [{"url": "file:///etc/passwd", "title": "x"}] + [{"url": "https://v.example.com/" + n, "duration": 9, "title": "Alb (%d)" % i} for i, n in enumerate(["a.mp4", "a.mp4", "b.mp4", "b.mp4?t=9", "c.mp4"])]}
if sys.version_info >= (3, 10):  # find() needs the Python Anki ships
    sys.modules["yt_dlp"] = types.SimpleNamespace(YoutubeDL=_FakeYDL)
    live._album_pos.pop("https://alb.example/a", None)
    assert live.find(".", "https://alb.example/a") == "Alb"
    assert live.album("https://alb.example/a") == {"index": 0, "count": 3}, live.album("https://alb.example/a")
    assert [it[0][-5:] for it in live._album["https://alb.example/a"]["items"]] == ["a.mp4", "b.mp4", "c.mp4"]
    live.forget("https://alb.example/a")
    del sys.modules["yt_dlp"]
assert live._video_key("https://V.example.com/a.mp4?token=1") == live._video_key("https://v.example.com/a.mp4?token=2")
assert live._video_key("https://v.example.com/watch?id=1") != live._video_key("https://v.example.com/watch?id=2")
assert live._video_key("https://v.example.com/AbC.mp4") != live._video_key("https://v.example.com/abc.mp4")  # file names keep case
# Album switches remember which video plays (a restart resumes it), and an ignored switch redraws the panel.
L = ns["live"]
L._album["https://alb.example/z"] = {"items": items, "q": "auto", "fps_cap": 0}
L._album_pos["https://alb.example/z"] = 0
ran_bg, redraws = [], []
ns["mw"].taskman = types.SimpleNamespace(run_in_background=lambda fn, done: ran_bg.append((fn, done)))
real_refresh, real_steps = ns["refresh"], L.album_step
ns["refresh"] = lambda *a, **k: redraws.append(1)
L.album_step = lambda link, step: L._album_pos.__setitem__(link, (L._album_pos[link] + step) % 3) or True
store.update(on=True, url="https://alb.example/z", urls=[], album_pos={"https://gone.example/": 4}, time=300)
ns["switch_album"]("https://alb.example/z", 1)
assert len(ran_bg) == 1
ns["switch_album"]("https://alb.example/z", 1)  # a second press while switching is ignored
assert len(ran_bg) == 1
fn, done = ran_bg[0]
done(types.SimpleNamespace(result=fn))
assert store["album_pos"] == {"https://alb.example/z": 1} and store["time"] == 0 and redraws, store
ns["switch_album"]("https://not-playing.example/", 1)
assert len(ran_bg) == 1 and len(redraws) == 2
ns["refresh"], L.album_step = real_refresh, real_steps
L._album.pop("https://alb.example/z")
assert live._entry_parts({"url": "https://a/b.mp4", "http_headers": {"Referer": "r"}, "duration": 5}) == ("https://a/b.mp4", None, {"Referer": "r"}, None, 5)
assert live.ready("example.com/clip.mp4") and live.vod("example.com/clip.mp4").get("vod") is True
assert live._streams[live._links["example.com/clip.mp4"]][0] == "https://example.com/clip.mp4"
live.forget("example.com/clip.mp4")
store["multi_fps"] = 50
assert ns["_fps_choice"](store) == "45" and "45 fps" in ns["state"](True)["auto_note"], ns["state"](True)["auto_note"]
store["multi_fps"] = "auto"
store["panel"] = True
assert ns["quick_conf"]()["panel"] is True
ns["save"](panel=False)
assert ns["quick_conf"]()["panel"] is False  # saving refreshes the cached copy at once
# Pausing one video: kept only for videos that are playing, cleared when a new set starts.
store.update(on=True, url="youtu.be/aaaaaaaaaa1", urls=["youtu.be/aaaaaaaaaa1", "youtu.be/aaaaaaaaaa2"], held=[], paused=False)
ns["apply"]({"held": ["youtu.be/aaaaaaaaaa2", "youtu.be/elsewhere", 5]})
assert store["held"] == ["youtu.be/aaaaaaaaaa2"], store["held"]
st = ns["state"](True)
assert st["held"] == ["youtu.be/aaaaaaaaaa2"] and "autoplay=0" in st["streams"][1]["src"] and "autoplay=1" in st["streams"][0]["src"]
ns["remove_url"]("youtu.be/aaaaaaaaaa1")
assert store["held"] == ["youtu.be/aaaaaaaaaa2"]
ns["play_url"]("youtu.be/aaaaaaaaaa3")
assert store["held"] == []
# Swap one stream for another in the same spot; the others (and their mute/pause) stay.
store.update(on=True, url="youtu.be/aaaaaaaaaa1", urls=["youtu.be/aaaaaaaaaa1", "youtu.be/aaaaaaaaaa2", "youtu.be/aaaaaaaaaa3"],
             muted=["youtu.be/aaaaaaaaaa3"], held=["youtu.be/aaaaaaaaaa2"], recent=[])
ns["apply"]({"replace": {"old": "youtu.be/aaaaaaaaaa2", "new": "youtu.be/bbbbbbbbbb1"}})
assert store["urls"] == ["youtu.be/aaaaaaaaaa1", "youtu.be/bbbbbbbbbb1", "youtu.be/aaaaaaaaaa3"], store["urls"]
assert store["muted"] == ["youtu.be/aaaaaaaaaa3"] and store["held"] == [] and store["recent"][0]["url"] == "youtu.be/bbbbbbbbbb1"
ns["apply"]({"replace": {"old": "youtu.be/aaaaaaaaaa1", "new": "youtu.be/aaaaaaaaaa3"}})  # already playing: no change
assert store["urls"][0] == "youtu.be/aaaaaaaaaa1"
# Each stream's own volume: only playing links, clamped, remembered per link.
ns["apply"]({"volumes": {"youtu.be/aaaaaaaaaa1": 140, "youtu.be/nope": 20, "youtu.be/aaaaaaaaaa3": "x"}})
assert store["volumes"] == {"youtu.be/aaaaaaaaaa1": 100}, store["volumes"]
ns["apply"]({"volumes": {"youtu.be/aaaaaaaaaa3": 35}})
assert ns["state"](True)["volumes"] == {"youtu.be/aaaaaaaaaa1": 100, "youtu.be/aaaaaaaaaa3": 35}
# Drag to reorder (in a break): only the same set of playing links, in a new order; the screen redraws in that order.
_refreshes = []
_real_refresh, ns["refresh"] = ns["refresh"], lambda *a, **k: _refreshes.append(1)
ns["apply"]({"order": ["youtu.be/aaaaaaaaaa3", "youtu.be/aaaaaaaaaa1", "youtu.be/bbbbbbbbbb1"]})
assert store["urls"] == ["youtu.be/aaaaaaaaaa3", "youtu.be/aaaaaaaaaa1", "youtu.be/bbbbbbbbbb1"] and store["url"] == "youtu.be/aaaaaaaaaa3"
assert _refreshes, "a new order must redraw the screen"
ns["refresh"] = _real_refresh
ns["apply"]({"order": ["youtu.be/evil", "youtu.be/aaaaaaaaaa1", "youtu.be/bbbbbbbbbb1"]})
assert store["urls"][0] == "youtu.be/aaaaaaaaaa3"
ns["apply"]({"order": [1, "youtu.be/aaaaaaaaaa1", ["x"]]})  # junk from the page is ignored, not a crash
assert store["urls"][0] == "youtu.be/aaaaaaaaaa3"
store["volumes"] = ["not", "a", "dict"]  # hand-edited config
assert ns["state"](True)["volumes"] == {}
ns["apply"]({"volumes": {"youtu.be/aaaaaaaaaa3": 20}})
assert store["volumes"] == {"youtu.be/aaaaaaaaaa3": 20}, store["volumes"]
# Rename a saved link: the folder, Recent and Now Playing all show the new name.
store.update(folders=[{"name": "Cams", "items": [{"url": "youtu.be/aaaaaaaaaa3", "title": "old"}]}],
             recent=[{"url": "youtu.be/aaaaaaaaaa3", "title": "old"}])
ns["apply"]({"rename_item": {"folder": "cams", "url": "youtu.be/aaaaaaaaaa3", "title": "  My   stream "}})
assert store["folders"][0]["items"][0]["title"] == "My stream" and store["recent"][0]["title"] == "My stream" and store["title"] == "My stream"
assert ns["rename_saved"]("Cams", "youtu.be/missing", "x") is False and ns["rename_saved"]("Cams", "youtu.be/aaaaaaaaaa3", "  ") is False
# Your own quick links: only web addresses with a name, at most 6.
store["quick_links"] = [{"label": "Mine", "url": "https://example.com/x"}, {"label": "Bad", "url": "javascript:alert(1)"},
                        {"label": "", "url": "https://example.com/y"}] + [{"label": f"L{i}", "url": f"https://e{i}.com"} for i in range(9)]
q = ns["quick_links"]()
assert q[0] == {"label": "Mine", "url": "https://example.com/x"} and len(q) == 6 and all(x["url"].startswith("https://") for x in q)
print("ok")
