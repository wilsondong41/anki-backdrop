"""Real Chromium CSS/JS checks for Reviewer & Overview themes. Run with Anki Python offscreen:
   QT_QPA_PLATFORM=offscreen <anki-venv>/bin/python tests/test_web.py
"""
import json
import os
import sys

import PyQt6.QtWebEngineWidgets  # must load before QApplication
from PyQt6.QtCore import QEventLoop
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = open(os.path.join(ROOT, "backdrop_video", "web", "backdrop.css"), encoding="utf-8").read()
JS = open(os.path.join(ROOT, "backdrop_video", "web", "backdrop.js"), encoding="utf-8").read()

app = QApplication(sys.argv)
view = QWebEngineView()
view.resize(1280, 800)
page = view.page()


def run_html(html: str, js_check: str):
    loop = QEventLoop()
    page.loadFinished.connect(lambda _ok: loop.quit())
    page.setHtml(html)
    loop.exec()
    page.loadFinished.disconnect()
    out = []
    page.runJavaScript(js_check, lambda v: (out.append(v), loop.quit()))
    loop.exec()
    return out[0]


# 1. Reviewer with AnKing night-mode card + Onigiri body::before wallpaper + wide viewport check.
reviewer_html = f"""<!doctype html>
<html class="night-mode">
<head>
<style>
body.card.nightMode {{ background-color: #272828 !important; color: #fff; margin: 20px; }}
body::before {{ content: ''; position: fixed; inset: 0; background: red; z-index: 0; }}
.timer {{ margin: 12em auto auto auto; }}
</style>
<style>{CSS}</style>
<script>{JS}</script>
</head>
<body class="card nightMode" style="width:1280px">
<div id="qa">Short card<div class="timer"></div><div id="pop" style="position:fixed;left:10px;top:10px;">P</div></div>
</body>
</html>"""

res = run_html(
    reviewer_html,
    """(() => {
      backdrop.start({on:true, plate:true, lite:false, picture:true, panel:false,
        url:"https://example.com/v.webm", kind:"video", src:"", start:0,
        strength:100, volume:50, paused:true, title:"", recent:[]});
      const qa = document.getElementById("qa");
      const qaRect = qa.getBoundingClientRect();
      const popRect = document.getElementById("pop").getBoundingClientRect();
      const before = getComputedStyle(qa, "::before");
      const bodyBefore = getComputedStyle(document.body, "::before");
      const bodyBg = getComputedStyle(document.body).backgroundColor;
      // Check fixed plate width & steady min-height across short/long cards and AnKing 10s "!" timer lines.
      const shortWidth = qaRect.width;
      const shortHeight = qaRect.height;
      document.querySelector("#qa .timer").innerHTML = "<span style='color:#CC5B5B'>!<br/>!<br/>!<br/>!<br/>!<br/>!</span>";
      const afterTimerHeight = qa.getBoundingClientRect().height;
      qa.firstChild.textContent = "A ".repeat(200);
      const longWidth = qa.getBoundingClientRect().width;
      backdrop.start({on:true, plate:true, lite:false, picture:true, panel:false,
        url:"https://example.com/v.webm", kind:"video", src:"", start:0,
        strength:100, volume:15, paused:true, title:"", recent:[]});
      const vidVol = document.querySelector("#bd-layer video").volume;
      return {
        bodyBg,
        bodyBeforeContent: bodyBefore.content,
        qaWidth: shortWidth,
        shortHeight,
        afterTimerHeight,
        longWidth,
        beforeBg: before.backgroundColor,
        beforeFilter: before.backdropFilter || before.webkitBackdropFilter || "",
        popLeft: popRect.left,
        vidVol,
      };
    })()""",
)
assert res["bodyBg"] == "rgba(0, 0, 0, 0)", res
assert res["bodyBeforeContent"] == "none", res
assert res["qaWidth"] == 760 and res["longWidth"] == 760, res  # consistent fixed card width, not adaptive
assert res["shortHeight"] >= 300 and res["afterTimerHeight"] == res["shortHeight"], res  # steady height & no 10s jump
assert "39, 40, 40" in res["beforeBg"] and "0.62" in res["beforeBg"], res
assert "blur(" in res["beforeFilter"], res
assert res["popLeft"] == 10, res  # position:fixed inside #qa stays viewport-relative
assert abs(res["vidVol"] - 0.15) < 0.01, res

# 2. Stock Anki Overview gets bd-stock-over plate; Onigiri/Synapse Overview makes wrappers transparent & boxes translucent.
stock_over = run_html(
    f"<!doctype html><html><head><style>{CSS}</style><script>{JS}</script></head>"
    "<body><center><table><tr><td>Deck</td></tr></table></center></body></html>",
    """(() => {
      backdrop.start({on:true, plate:false, lite:false, picture:true, panel:false,
        url:"https://example.com/v.webm", kind:"video", src:"", start:0,
        strength:100, volume:50, paused:true, title:"", recent:[]});
      return document.documentElement.classList.contains("bd-stock-over");
    })()""",
)
assert stock_over is True

synapse_over = run_html(
    f"""<!doctype html><html><head>
    <style>#overview-wrapper {{ background: #111 !important; }} .white-box {{ background: rgb(30, 30, 35); }}</style>
    <style>{CSS} * {{ transition: none !important; }}</style><script>{JS}</script></head>
    <body><div id="overview-wrapper"><div class="white-box">Stats</div></div></body></html>""",
    """(() => {
      backdrop.start({on:true, plate:false, lite:false, picture:true, panel:false,
        url:"https://example.com/v.webm", kind:"video", src:"", start:0,
        strength:100, volume:50, paused:true, title:"", recent:[]});
      return {
        stock: document.documentElement.classList.contains("bd-stock-over"),
        wrapBg: getComputedStyle(document.getElementById("overview-wrapper")).backgroundColor,
        boxBg: getComputedStyle(document.querySelector(".white-box")).backgroundColor,
      };
    })()""",
)
assert synapse_over["stock"] is False, synapse_over
assert synapse_over["wrapBg"] == "rgba(0, 0, 0, 0)", synapse_over
assert "0.62" in synapse_over["boxBg"], synapse_over

# 3. Software rendering (lite:true) disables glass blur, plain P triggers peek, Shift+P does not pause/peek.
lite_res = run_html(
    f"<!doctype html><html><head><style>body {{ background: #fff; }}</style><style>{CSS} * {{ transition: none !important; }}</style><script>{JS}</script></head>"
    "<body><div id='qa'>Light card</div></body></html>",
    """(() => {
      const cmds = [];
      window.pycmd = (m) => cmds.push(m);
      backdrop.start({on:true, plate:true, lite:true, picture:true, panel:false,
        url:"https://example.com/v.webm", kind:"video", src:"", start:0,
        strength:100, volume:50, paused:true, title:"", recent:[]});
      const before = getComputedStyle(document.getElementById("qa"), "::before");
      const filter = before.backdropFilter || before.webkitBackdropFilter || "none";
      const bg = before.backgroundColor;
      document.dispatchEvent(new KeyboardEvent("keydown", {key: "P", shiftKey: true, bubbles: true}));
      const shiftPeek = document.documentElement.classList.contains("bd-peek");
      document.dispatchEvent(new KeyboardEvent("keydown", {key: "p", bubbles: true}));
      const peeking = document.documentElement.classList.contains("bd-peek");
      const peekOp = getComputedStyle(document.getElementById("qa")).opacity;
      document.dispatchEvent(new KeyboardEvent("keyup", {key: "p", bubbles: true}));
      return {filter, bg, shiftPeek, cmds, peeking, peekOp, stillPeeking: document.documentElement.classList.contains("bd-peek")};
    })()""",
)
assert lite_res["filter"] == "none", lite_res
assert "255, 255, 255" in lite_res["bg"], lite_res
assert not lite_res["shiftPeek"] and lite_res["cmds"] == [], lite_res
assert lite_res["peeking"] and not lite_res["stillPeeking"] and abs(float(lite_res["peekOp"]) - 0.12) < 0.02, lite_res

# 4. Sidebar quality pills + seek bar visibility (hidden on live streams, shown for seekable videos).
panel_res = run_html(
    f"<!doctype html><html><head><style>{CSS}</style><script>{JS}</script></head>"
    "<body><div id='qa'>Card</div></body></html>",
    """(() => {
      const cmds = [];
      window.pycmd = (m) => cmds.push(m);
      backdrop.start({on:true, plate:true, lite:false, picture:true, panel:true,
        url:"https://www.twitch.tv/live", kind:"video", src:"", start:0, live:true,
        strength:50, volume:50, quality:"480", paused:true, title:"Live", recent:[]});
      const liveSeekHidden = document.querySelector("#bd-panel .bd-seek").hidden;
      const activeBefore = document.querySelector("#bd-panel .bd-pill.bd-active").dataset.quality;
      document.querySelector("#bd-panel .bd-pill[data-quality='720']").click();
      const activeAfter = document.querySelector("#bd-panel .bd-pill.bd-active").dataset.quality;
      // Switch to a YouTube embed, simulate infoDelivery with duration & currentTime, then test seek.
      backdrop.start({on:true, plate:true, lite:false, picture:true, panel:true,
        url:"https://youtu.be/M7lc1UVf-VE", kind:"youtube", src:"https://www.youtube.com/embed/M7lc1UVf-VE?enablejsapi=1",
        start:0, live:false, strength:50, volume:50, quality:"720", paused:true, title:"", recent:[]});
      const iframeWin = document.querySelector("#bd-layer iframe").contentWindow;
      window.dispatchEvent(new MessageEvent("message", {
        source: iframeWin,
        data: JSON.stringify({event: "infoDelivery", info: {duration: 600, currentTime: 125}})
      }));
      const vodSeekHidden = document.querySelector("#bd-panel .bd-seek").hidden;
      const seekLabel = document.querySelector("#bd-panel .bd-seek-time").textContent;
      const seekInput = document.querySelector("#bd-panel .bd-seek input");
      seekInput.value = "300";
      seekInput.dispatchEvent(new Event("change", {bubbles: true}));
      const afterSeekLabel = document.querySelector("#bd-panel .bd-seek-time").textContent;
      // If YouTube reports isLive:true later, seek hides even if duration was non-zero.
      window.dispatchEvent(new MessageEvent("message", {
        source: iframeWin,
        data: JSON.stringify({event: "infoDelivery", info: {videoData: {isLive: true}, duration: 14400, currentTime: 500}})
      }));
      const ytLiveSeekHidden = document.querySelector("#bd-panel .bd-seek").hidden;
      return {cmds, liveSeekHidden, activeBefore, activeAfter, vodSeekHidden, seekLabel, afterSeekLabel, ytLiveSeekHidden};
    })()""",
)
assert panel_res["liveSeekHidden"] is True, panel_res
assert panel_res["activeBefore"] == "480" and panel_res["activeAfter"] == "720", panel_res
assert any(c == 'backdrop:set:{"quality":"720"}' for c in panel_res["cmds"]), panel_res
assert panel_res["vodSeekHidden"] is False and panel_res["seekLabel"] == "2:05 / 10:00", panel_res
assert panel_res["afterSeekLabel"] == "5:00 / 10:00", panel_res
assert any(c == "backdrop:time:300" for c in panel_res["cmds"]), panel_res
assert panel_res["ytLiveSeekHidden"] is True, panel_res

print("web ok")
