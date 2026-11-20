"""During a break on the card screen, Anki's card keys (real Qt shortcuts, like Space and 1) must not act on the
hidden card, Esc ends the break, and Ctrl/Cmd shortcuts keep working. Run with an Anki Python, offscreen:
   QT_QPA_PLATFORM=offscreen <anki-venv>/bin/python tests/test_breakkeys.py .
"""
import sys, types
import PyQt6.QtWebEngineWidgets  # must load before QApplication
from PyQt6.QtWidgets import QApplication, QMainWindow, QMenu, QPushButton
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
app = QApplication(sys.argv)
import aqt, aqt.qt
cfg = {"url": "", "on": True}
class AM:
    addonFromModule = staticmethod(lambda n: "backdrop_video")
    setWebExports = staticmethod(lambda *a: None)
    getConfig = staticmethod(lambda n: dict(cfg))
    writeConfig = staticmethod(lambda n, c: cfg.update(c))
    setConfigAction = staticmethod(lambda *a: None)
win = QMainWindow()
mw = win
mw.addonManager, mw.app, mw.state = AM, app, "review"
mw.form = types.SimpleNamespace(menuTools=QMenu())
mw.web = types.SimpleNamespace(eval=lambda js: None, setPlaybackRequiresGesture=lambda v: None)
aqt.mw = mw
sys.path.insert(0, sys.argv[1])
import backdrop_video as b
b.mw = mw
hits = []
for seq in ("Space", "1", "Ctrl+M"):
    QShortcut(QKeySequence(seq), win, activated=lambda s=seq: hits.append(s))
target = QPushButton("card area", win)
win.setCentralWidget(target)
win.show(); win.activateWindow(); target.setFocus()
for _ in range(20): app.processEvents()
assert app.activeWindow() is win, app.activeWindow()
b._peek_filter = None
b._install_peek_filter()
ended = []
b.exit_break = lambda: ended.append(1)

QTest.keyClick(target, Qt.Key.Key_Space); QTest.keyClick(target, Qt.Key.Key_1)
assert hits == ["Space", "1"], hits  # control: outside a break the shortcuts work

hits.clear()
b._break_mode = True
QTest.keyClick(target, Qt.Key.Key_Space); QTest.keyClick(target, Qt.Key.Key_1)
assert hits == [], hits  # the hidden card can't be answered
QTest.keyClick(target, Qt.Key.Key_M, Qt.KeyboardModifier.ControlModifier)
assert hits == ["Ctrl+M"], hits  # modified shortcuts still work
QTest.keyClick(target, Qt.Key.Key_Escape)
assert ended == [1], ended
b._break_mode = False
print("break keys ok")
