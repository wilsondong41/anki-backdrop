"""Settings window smoke test with real Qt. Run with an Anki Python, offscreen:
   QT_QPA_PLATFORM=offscreen <anki-venv>/bin/python tests/test_dialog.py .
"""
import sys, types
import PyQt6.QtWebEngineWidgets  # must load before QApplication
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
import aqt, aqt.qt
cfg = {"url": "", "on": False, "strength": 30, "volume": 50}
evals = []
class AM:
    addonFromModule = staticmethod(lambda n: "backdrop_video")
    setWebExports = staticmethod(lambda *a: None)
    getConfig = staticmethod(lambda n: dict(cfg))
    writeConfig = staticmethod(lambda n, c: cfg.update(c))
    setConfigAction = staticmethod(lambda *a: None)
from PyQt6.QtWidgets import QMainWindow, QMenu
win = QMainWindow()
mw = win  # the dialog's parent must be a real widget
mw.addonManager, mw.app, mw.state = AM, app, "review"
mw.form = types.SimpleNamespace(menuTools=QMenu())
mw.web = types.SimpleNamespace(eval=evals.append, setPlaybackRequiresGesture=lambda v: None)
aqt.mw = mw
sys.path.insert(0, sys.argv[1])
app.clipboard().setText("https://youtu.be/M7lc1UVf-VE")
import backdrop_video as b
b.mw = mw
b.tooltip = lambda *a, **k: None
b.open_dialog()
d = b._dialog
assert d.browse_btn is not None and d.folder_combo is not None and d.folder_list is not None
d.save_link.setText("https://youtu.be/M7lc1UVf-VE")
d._save_pasted_link()
assert any(f["name"] == "Saved" and f["items"][0]["url"] == "https://youtu.be/M7lc1UVf-VE" for f in cfg["folders"]), cfg
d.folder_list.setCurrentRow(0)
d.play_selected()
assert cfg["on"] is True and cfg["url"] == "https://youtu.be/M7lc1UVf-VE", cfg
d.strength.setValue(80); d.plate_opacity.setValue(45); d.true_transparency.setChecked(True); d.volume.setValue(10); d.quality.setCurrentIndex(d.quality.findData("720")); d.turn_off()
assert cfg["on"] is False and cfg["strength"] == 80 and cfg["plate_opacity"] == 45 and cfg["true_transparency"] is True and cfg["volume"] == 10 and cfg["quality"] == "720", cfg
assert any(f["name"] == "Saved" for f in cfg["folders"]), cfg  # turning off never deletes folders
assert d.helper_btn is not None and "yt-dlp" in d.helper_btn.text()
assert any("backdrop.start" in e for e in evals) and any('"strength": 80' in e for e in evals) and any('"true_transparency": true' in e for e in evals)
d.close(); app.processEvents()
assert b._dialog is None
b.open_browse()
assert b._browse is not None
br = b._browse
assert br._tabs.count() == 1 and not br._play_btn.isHidden() and br._add_btn.isHidden() and br._replace_btn.isHidden()
br._open_tab("https://www.twitch.tv/test_stream", switch=True)
assert br._tabs.count() == 2
assert br._save_current_to("Streams") is True
assert any(f["name"] == "Streams" and any(it["url"] == "https://www.twitch.tv/test_stream" for it in f["items"]) for f in cfg["folders"]), cfg
br._close_tab(1)
assert br._tabs.count() == 1
br._close_tab(0)
assert br._tabs.count() == 1  # closing the last tab opens a clean fresh tab
# When 1 stream is already playing, Browse switches to safe + Add default and demotes Replace Current.
cfg.update(on=True, url="https://www.twitch.tv/live1", urls=["https://www.twitch.tv/live1"])
br._sync_buttons()
assert br._play_btn.isHidden() and not br._add_btn.isHidden() and br._add_btn.isEnabled() and "(1/4)" in br._add_btn.text() and not br._replace_btn.isHidden()
# When 4/4 streams are playing, + Add disables cleanly.
cfg.update(on=True, urls=["https://a.example/1", "https://a.example/2", "https://a.example/3", "https://a.example/4"])
br._sync_buttons()
assert not br._add_btn.isEnabled() and "Full (4/4)" in br._add_btn.text()
b._browse.close(); app.processEvents()
assert b._browse is None
# Reopening Browse when last URL is already playing opens that site's homepage instead.
b._last_browse_url = "https://www.twitch.tv/live1"
cfg.update(on=True, url="https://www.twitch.tv/live1", urls=["https://www.twitch.tv/live1"])
b.open_browse()
assert b._browse._tabs.widget(0).url().toString() == "https://www.twitch.tv/"
b._browse.close(); app.processEvents()
assert b._browse is None
# Quick links: no Kick; your own links show with a "+" to add the current page. "Stay signed in" keeps logins in
# Anki's main folder (outside the add-on and profile folders that Loadout copies), and forgetting deletes them.
import os, tempfile, shutil as _sh
from PyQt6.QtWidgets import QPushButton as _QPB, QCheckBox as _QCB
base = tempfile.mkdtemp(prefix="bd-base-")
mw.pm = types.SimpleNamespace(base=base)
cfg.update(quick_links=[{"label": "My Site", "url": "https://example.com/"}], stay_signed_in=False, on=False)
b.open_browse()
texts = [w.text() for w in b._browse.findChildren(_QPB)]
assert "Kick" not in texts and "My Site" in texts and "+" in texts and "YouTube" in texts, texts
stay = [w for w in b._browse.findChildren(_QCB) if w.text() == "Stay signed in"][0]
assert not stay.isChecked() and b._browse._tabs.widget(0).page().profile().isOffTheRecord()
stay.setChecked(True)  # applies to new tabs (and the page you're on)
b._browse._open_tab("https://example.com/a", switch=True)
prof = b._browse._tabs.currentWidget().page().profile()
assert cfg["stay_signed_in"] is True and not prof.isOffTheRecord() and prof.persistentStoragePath().startswith(base), prof.persistentStoragePath()
assert os.path.isdir(os.path.join(base, "backdrop_browser"))
b._browse.close(); app.processEvents()
cfg.update(stay_signed_in=False)
b.forget_logins()
import time as _t
end = _t.time() + 5
while _t.time() < end: app.processEvents(); _t.sleep(0.05)
assert not os.path.exists(os.path.join(base, "backdrop_browser"))
os.makedirs(os.path.join(base, "backdrop_browser")); b.on_profile_did_open()  # leftovers go at the next start
assert not os.path.exists(os.path.join(base, "backdrop_browser"))
# Replace one stream of several: the page you're on takes that one's place, the others keep playing.
A, B, C = "https://example.com/a1", "https://example.com/b2", "https://example.com/c3"
cfg.update(on=True, url=A, urls=[A, B], muted=[B])
b.open_browse()
b._browse._open_tab(C, switch=True)
b._browse._tabs.currentWidget().setUrl(__import__("PyQt6.QtCore", fromlist=["QUrl"]).QUrl(C))
end = _t.time() + 3
while _t.time() < end and b._browse._tabs.currentWidget().url().toString() != C: app.processEvents(); _t.sleep(0.05)
_real_refresh, b.refresh = b.refresh, (lambda *a, **k: None)  # no live-stream helper in this test
b._browse._pick_replace(A)
b.refresh = _real_refresh
assert cfg["urls"] == [C, B] and cfg["muted"] == [B], (cfg["urls"], cfg["muted"])
b._browse.close(); app.processEvents()
cfg.update(stay_signed_in=False, on=False)
_sh.rmtree(base, ignore_errors=True)
print("dialog ok", aqt.qt.QT_VERSION_STR if hasattr(aqt.qt, "QT_VERSION_STR") else "")
