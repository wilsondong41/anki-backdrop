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
b.open_dialog()
d = b._dialog
assert d.link.text() == "https://youtu.be/M7lc1UVf-VE", d.link.text()
d.play(); d.strength.setValue(80); d.volume.setValue(10); d.quality.setCurrentIndex(d.quality.findData("720")); d.turn_off()
assert cfg["on"] is False and cfg["strength"] == 80 and cfg["volume"] == 10 and cfg["quality"] == "720", cfg
assert any("backdrop.start" in e for e in evals) and any('"strength": 80' in e for e in evals)
d.close(); app.processEvents()
assert b._dialog is None
print("dialog ok", aqt.qt.QT_VERSION_STR if hasattr(aqt.qt, "QT_VERSION_STR") else "")
