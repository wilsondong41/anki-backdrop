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

# 5. Zero crop/zoom when clear, page iframes never overscan, peek resets zoom, and window resize fits 100vw x 100vh.
fit_res = run_html(
    f"<!doctype html><html><head><style>{CSS} * {{ transition: none !important; }}</style><script>{JS}</script></head>"
    "<body><div id='qa'>Card</div></body></html>",
    """(() => {
      backdrop.start({on:true, plate:true, lite:false, picture:true, panel:false,
        url:"https://example.com/v.webm", kind:"video", src:"", start:0,
        strength:100, volume:50, paused:true, title:"", recent:[]});
      const vRect = document.querySelector("#bd-layer video").getBoundingClientRect();
      const vCs = getComputedStyle(document.querySelector("#bd-layer video"));
      const objectFit = vCs.objectFit;
      const videoTransform = vCs.transform;
      backdrop.start({on:true, plate:true, lite:false, picture:true, panel:false,
        url:"https://example.com/cam", kind:"page", src:"about:blank", start:0,
        strength:30, volume:50, paused:false, title:"", recent:[]});
      const pRect = document.querySelector("#bd-layer iframe").getBoundingClientRect();
      const pageScale = document.documentElement.style.getPropertyValue("--bd-scale");
      return {
        vw: window.innerWidth, vh: window.innerHeight,
        vRect: {w: Math.round(vRect.width), h: Math.round(vRect.height)},
        objectFit,
        videoTransform,
        clearScale: pageScale,
        pRect: {w: Math.round(pRect.width), h: Math.round(pRect.height)},
      };
    })()""",
)
assert fit_res["vRect"] == {"w": 1280, "h": 800} and fit_res["objectFit"] == "contain", fit_res
assert fit_res["pRect"] == {"w": 1280, "h": 800} and fit_res["clearScale"] == "1.000", fit_res

view2 = QWebEngineView()
view2.resize(500, 900)
page2 = view2.page()
loop2 = QEventLoop()
page2.loadFinished.connect(lambda _ok: loop2.quit())
page2.setHtml(f"<!doctype html><html><head><style>{CSS}</style><script>{JS}</script></head><body><div id='qa'>Card</div></body></html>")
loop2.exec()
out_resize = []
page2.runJavaScript(
    """(() => {
      const s = (u) => ({url:u, kind:"video", src:"", start:0, live:false, title:""});
      backdrop.start({on:true, plate:true, lite:false, picture:true, panel:false,
        url:"u1", urls:["u1","u2"], muted:[], streams:[s("u1"), s("u2")], strength:100, volume:50, paused:true, title:"", recent:[]});
      const r1 = document.querySelector("#bd-layer .bd-slot").getBoundingClientRect();
      return {w: Math.round(r1.width), h: Math.round(r1.height)};
    })()""",
    lambda v: (out_resize.append(v), loop2.quit()),
)
loop2.exec()
assert out_resize[0] == {"w": 500, "h": 450}, out_resize[0]  # portrait window stacks 2 videos top/bottom evenly!

# 6. Multi-video grids (2 even, 3 = 2 top + 1 full bottom, 4 = 2x2), slot reuse, per-slot mute, peek(true/false), plate opacity slider, and tap-to-close panel.
multi_res = run_html(
    f"<!doctype html><html><head><style>{CSS} * {{ transition: none !important; }}</style><script>{JS}</script></head>"
    "<body><div id='qa'>Card</div></body></html>",
    """(() => {
      const s = (u, kind="video") => ({url:u, kind, src:"", start:0, live:false, title:""});
      backdrop.start({on:true, plate:true, lite:false, picture:true, panel:true,
        url:"u1", urls:["u1"], muted:[], streams:[s("u1")], strength:100, volume:60, quality:"2160", paused:true, title:"", recent:[]});
      const firstNode = document.querySelector("#bd-layer video");
      backdrop.start({on:true, plate:true, lite:false, picture:true, panel:true,
        url:"u1", urls:["u1","u2"], muted:["u2"], streams:[s("u1"), s("u2")], strength:100, volume:60, quality:"2160", paused:true, title:"", recent:[]});
      const reused = document.querySelectorAll("#bd-layer video")[0] === firstNode;
      const g2 = Array.from(document.querySelectorAll("#bd-layer .bd-slot")).map((el) => {
        const r = el.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)];
      });
      const v1Muted = document.querySelectorAll("#bd-layer video")[0].muted;
      const v2Muted = document.querySelectorAll("#bd-layer video")[1].muted;
      backdrop.start({on:true, plate:true, lite:false, picture:true, panel:true,
        url:"u1", urls:["u1","u2","u3"], muted:[], streams:[s("u1"), s("u2"), s("u3")], strength:50, volume:60, quality:"2160", paused:true, title:"", recent:[]});
      const g3 = Array.from(document.querySelectorAll("#bd-layer .bd-slot")).map((el) => {
        const r = el.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)];
      });
      backdrop.start({on:true, plate:true, lite:false, picture:true, panel:true,
        url:"u1", urls:["u1","u2","u3","u4"], muted:[], streams:[s("u1"), s("u2"), s("u3"), s("u4")], strength:50, volume:60, quality:"2160", paused:true, title:"", recent:[]});
      const g4 = Array.from(document.querySelectorAll("#bd-layer .bd-slot")).map((el) => {
        const r = el.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)];
      });
      const slotRows = document.querySelectorAll("#bd-panel .bd-slot-row").length;
      const has4kPill = !!document.querySelector("#bd-panel .bd-pill[data-quality='2160'].bd-active");
      backdrop.peek(true);
      const peekOn = document.documentElement.classList.contains("bd-peek");
      backdrop.peek(false);
      const peekOff = !document.documentElement.classList.contains("bd-peek");
      const cardSlider = document.querySelector("#bd-panel input[data-key='plate_opacity']");
      cardSlider.value = "25";
      cardSlider.dispatchEvent(new Event("input", {bubbles: true}));
      const plateAlpha25 = document.documentElement.style.getPropertyValue("--bd-plate-alpha");
      const noglassAt50 = document.documentElement.classList.contains("bd-noglass");
      cardSlider.value = "98";
      cardSlider.dispatchEvent(new Event("input", {bubbles: true}));
      const noglassAt98 = document.documentElement.classList.contains("bd-noglass");
      backdrop.closePanel();
      const panelClosed = !document.documentElement.classList.contains("bd-panel-open");
      document.querySelector("#bd-panel .bd-tab").click();
      const panelReopened = document.documentElement.classList.contains("bd-panel-open");
      document.getElementById("qa").dispatchEvent(new PointerEvent("pointerdown", {bubbles: true}));
      const panelAutoMinimized = !document.documentElement.classList.contains("bd-panel-open");
      backdrop.start({on:true, plate:false, lite:false, picture:true, panel:false,
        url:"u1", urls:["u1"], muted:[], streams:[s("u1")], strength:30, volume:60, quality:"auto",
        auto_note:"Auto: 480p, up to 60 fps", break_info:{active:true, rest_left:125, rest_done:false}, paused:true, title:"",
        recent:[{url:"u1", title:"One"}, {url:"u_old", title:"Old"}]});
      const singleSlotRows = document.querySelectorAll("#bd-panel .bd-slot-row").length;
      const singleHasRemove = !!document.querySelector("#bd-panel [data-act='remove-slot']");
      const hasRecentDelete = !!document.querySelector("#bd-panel [data-act='forget-recent']");
      const breakClass = document.documentElement.classList.contains("bd-break");
      const breakBodyOp = getComputedStyle(document.body).opacity;
      const breakText = document.querySelector("#bd-break-pill .bd-break-text").textContent;
      const hintText = document.querySelector("#bd-panel .bd-hint").textContent;
      const hasBrowseBtn = !!document.querySelector("#bd-panel [data-act='browse']");
      return {reused, g2, v1Muted, v2Muted, g3, g4, slotRows, has4kPill, peekOn, peekOff, plateAlpha25, noglassAt50, noglassAt98, panelClosed, panelReopened, panelAutoMinimized, singleSlotRows, singleHasRemove, hasRecentDelete, breakClass, breakBodyOp, breakText, hintText, hasBrowseBtn};
    })()""",
)
assert multi_res["reused"] is True, multi_res
assert multi_res["g2"] == [[640, 800], [640, 800]], multi_res
assert multi_res["v1Muted"] is False and multi_res["v2Muted"] is True, multi_res
assert multi_res["g3"] == [[640, 400], [640, 400], [1280, 400]], multi_res
assert multi_res["g4"] == [[640, 400], [640, 400], [640, 400], [640, 400]], multi_res
assert multi_res["slotRows"] == 4 and multi_res["has4kPill"] is True, multi_res
assert multi_res["peekOn"] and multi_res["peekOff"] and multi_res["plateAlpha25"] == "0.25", multi_res
assert multi_res["noglassAt50"] is True and multi_res["noglassAt98"] is True, multi_res
assert multi_res["panelClosed"] and multi_res["panelReopened"] and multi_res["panelAutoMinimized"], multi_res
assert multi_res["singleSlotRows"] == 1 and multi_res["singleHasRemove"] and multi_res["hasRecentDelete"], multi_res
assert multi_res["breakClass"] and multi_res["breakBodyOp"] == "0" and multi_res["breakText"] == "Break · Rest 2:05", multi_res
assert "Auto: 480p, up to 60 fps" in multi_res["hintText"] and multi_res["hasBrowseBtn"], multi_res

print("web ok")
