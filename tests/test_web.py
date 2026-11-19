"""Real Chromium CSS/JS checks for Reviewer & Overview themes. Run with Anki Python offscreen:
   QT_QPA_PLATFORM=offscreen <anki-venv>/bin/python tests/test_web.py
"""
import json
import os
import sys

import PyQt6.QtWebEngineWidgets  # must load before QApplication
from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = open(os.path.join(ROOT, "backdrop_video", "web", "backdrop.css"), encoding="utf-8").read()
JS = open(os.path.join(ROOT, "backdrop_video", "web", "backdrop.js"), encoding="utf-8").read()

app = QApplication(sys.argv)
view = QWebEngineView()
view.resize(1280, 800)
view.show()
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
      const liveSeekHidden = !document.querySelector("#bd-panel .bd-seek");
      const activeBefore = document.querySelector("#bd-panel .bd-pill.bd-active").dataset.quality;
      document.querySelector("#bd-panel .bd-pill[data-quality='720']").click();
      const activeAfter = document.querySelector("#bd-panel .bd-pill.bd-active").dataset.quality;
      // Switch to a YouTube embed, simulate infoDelivery with duration & currentTime, click title to open full-width seek.
      backdrop.start({on:true, plate:true, lite:false, picture:true, panel:true,
        url:"https://youtu.be/M7lc1UVf-VE", kind:"youtube", src:"https://www.youtube.com/embed/M7lc1UVf-VE?enablejsapi=1",
        start:0, live:false, strength:50, volume:50, quality:"720", paused:true, title:"", recent:[]});
      const iframeWin = document.querySelector("#bd-layer iframe").contentWindow;
      window.dispatchEvent(new MessageEvent("message", {
        source: iframeWin,
        data: JSON.stringify({event: "infoDelivery", info: {duration: 600, currentTime: 125}})
      }));
      document.querySelector("#bd-panel .bd-slot-name").click();
      const vodSeekHidden = !document.querySelector("#bd-panel .bd-seek");
      const seekLabel = document.querySelector("#bd-panel .bd-seek-time").textContent;
      const seekInput = document.querySelector("#bd-panel .bd-seek input");
      seekInput.value = "300";
      seekInput.dispatchEvent(new Event("change", {bubbles: true}));
      const afterSeekLabel = document.querySelector("#bd-panel .bd-seek-time").textContent;
      // If YouTube reports isLive:true later, seek closes and hides even if duration was non-zero.
      window.dispatchEvent(new MessageEvent("message", {
        source: iframeWin,
        data: JSON.stringify({event: "infoDelivery", info: {videoData: {isLive: true}, duration: 14400, currentTime: 500}})
      }));
      const ytLiveSeekHidden = !document.querySelector("#bd-panel .bd-seek");
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
view2.show()
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
assert out_resize[0]["w"] == 500 and abs(out_resize[0]["h"] - 450) <= 4, out_resize[0]  # portrait window stacks 2 videos top/bottom evenly!

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

# 7. True transparency toggle (reduces glass blur, border, and shadow), Recents X removal, and numeric FPS display.
extra_res = run_html(
    f"<!doctype html><html class='night-mode'><head><style>{CSS} * {{ transition: none !important; }}</style><script>{JS}</script></head>"
    "<body class='nightMode'><div id='qa'>Card</div></body></html>",
    """(() => {
      const cmds = [];
      window.pycmd = (m) => cmds.push(m);
      const s = (u, fps=0) => ({url:u, kind:"video", src:"", start:0, live:true, title:"Stream", fps});
      // Frosted mode (true_transparency: false) at plate_opacity=0 keeps blur & border.
      backdrop.start({on:true, plate:true, lite:false, picture:true, panel:true,
        url:"u1", urls:["u1"], muted:[], streams:[s("u1", 60)], strength:100, plate_opacity:0,
        true_transparency:false, volume:50, quality:"2160", auto_note:"4K", paused:true, title:"One",
        recent:[{url:"u1", title:"One"}, {url:"u_old", title:"Old"}]});
      const qa = document.getElementById("qa");
      const hint60 = document.querySelector("#bd-panel .bd-hint").textContent;
      const frostFilter = getComputedStyle(qa, "::before").backdropFilter || getComputedStyle(qa, "::before").webkitBackdropFilter;
      const frostShadow = getComputedStyle(qa).boxShadow;
      const frostNoGlass = document.documentElement.classList.contains("bd-noglass");
      // Toggle True transparency ON via sidebar checkbox at plate_opacity=0 -> blur, border & shadow vanish.
      const ttBox = document.querySelector("#bd-panel input[data-key='true_transparency']");
      ttBox.click();
      const tt0Filter = getComputedStyle(qa, "::before").backdropFilter || getComputedStyle(qa, "::before").webkitBackdropFilter;
      const tt0Shadow = getComputedStyle(qa).boxShadow;
      const tt0Border = document.documentElement.style.getPropertyValue("--bd-plate-border");
      const tt0NoGlass = document.documentElement.classList.contains("bd-noglass");
      // Slide plate_opacity to 50 with True transparency ON -> partial blur (7px) & partial border/shadow.
      const cardSlider = document.querySelector("#bd-panel input[data-key='plate_opacity']");
      cardSlider.value = "50";
      cardSlider.dispatchEvent(new Event("input", {bubbles: true}));
      const tt50GlassVar = document.documentElement.style.getPropertyValue("--bd-glass-filter");
      const tt50NoGlass = document.documentElement.classList.contains("bd-noglass");
      // Click Recent X -> immediately removed from DOM and sends forget_recent.
      const recentCountBefore = document.querySelectorAll("#bd-panel .bd-recent-row").length;
      document.querySelector("#bd-panel [data-act='forget-recent']").click();
      const recentCountAfter = document.querySelectorAll("#bd-panel .bd-recent-row").length;
      // When off, all recent items (including last url) are shown and deletable.
      backdrop.start({on:false, plate:true, lite:false, picture:true, panel:true,
        url:"u1", urls:[], muted:[], streams:[], strength:100, plate_opacity:0,
        true_transparency:true, volume:50, quality:"auto", auto_note:"Auto: 480p, up to 60 fps", paused:true, title:"One",
        recent:[{url:"u1", title:"One"}, {url:"u2", title:"Two"}]});
      const offRecentCount = document.querySelectorAll("#bd-panel .bd-recent-row").length;
      return {cmds, hint60, frostFilter, frostShadow, frostNoGlass, tt0Filter, tt0Shadow, tt0Border, tt0NoGlass, tt50GlassVar, tt50NoGlass, recentCountBefore, recentCountAfter, offRecentCount};
    })()""",
)
assert "4K, 60 fps" in extra_res["hint60"], extra_res
assert "blur(14px)" in extra_res["frostFilter"] and extra_res["frostShadow"] != "none" and not extra_res["frostNoGlass"], extra_res
assert extra_res["tt0Filter"] == "none" and extra_res["tt0Shadow"] == "none" and "0.000" in extra_res["tt0Border"] and extra_res["tt0NoGlass"], extra_res
assert "blur(7px)" in extra_res["tt50GlassVar"] and not extra_res["tt50NoGlass"], extra_res
assert any(c == 'backdrop:set:{"true_transparency":true}' for c in extra_res["cmds"]), extra_res
assert extra_res["recentCountBefore"] == 1 and extra_res["recentCountAfter"] == 0, extra_res
assert any(c == 'backdrop:set:{"forget_recent":"u_old"}' for c in extra_res["cmds"]), extra_res
assert extra_res["offRecentCount"] == 2, extra_res

# 8. Saved folders: star save into new folder, folder pills, multi-select up to 4 across folders, unsave, and 2-step folder delete.
folder_res = run_html(
    f"<!doctype html><html><head><style>{CSS}</style><script>{JS}</script></head>"
    "<body><div id='qa'>Card</div></body></html>",
    """(() => {
      const cmds = [];
      window.pycmd = (m) => cmds.push(m);
      const s = (u, t) => ({url:u, kind:"video", src:"", start:0, live:false, title:t});
      backdrop.start({on:true, plate:true, lite:false, picture:true, panel:true,
        url:"https://e.test/1", urls:["https://e.test/1"], muted:[], streams:[s("https://e.test/1", "Vid 1")],
        strength:50, plate_opacity:70, true_transparency:false, volume:50, quality:"auto", paused:true, title:"Vid 1",
        recent:[{url:"https://e.test/1", title:"Vid 1"}],
        folders:[
          {name:"Study", items:[
            {url:"https://e.test/2", title:"Vid 2"},
            {url:"https://e.test/3", title:"Vid 3"},
            {url:"https://e.test/4", title:"Vid 4"},
            {url:"https://e.test/5", title:"Vid 5"},
            {url:"https://e.test/6", title:"Vid 6"},
          ]}
        ]});
      // Save active stream into a brand new folder "Lofi" via star button + inline form.
      document.querySelector("#bd-panel .bd-slot-row [data-act='open-save']").click();
      const saveInp = document.querySelector("#bd-panel .bd-save-form input");
      saveInp.value = "Lofi";
      document.querySelector("#bd-panel .bd-save-form").dispatchEvent(new Event("submit", {bubbles:true, cancelable:true}));
      const starSaved = document.querySelector("#bd-panel .bd-slot-row .bd-star").classList.contains("bd-saved");
      const pillTexts = Array.from(document.querySelectorAll("#bd-panel .bd-folder-pill")).map((p) => p.textContent);
      // Enter Select mode, select 1 from Lofi + 3 from Study, verify 5th item is disabled (cap 4), then Play selected.
      document.querySelector("#bd-panel [data-act='toggle-select']").click();
      document.querySelector("#bd-panel [data-act='toggle-select-item'][data-save-url='https://e.test/1']").click();
      document.querySelector("#bd-panel .bd-folder-pill[data-folder-name='Study']").click();
      document.querySelector("#bd-panel [data-act='toggle-select-item'][data-save-url='https://e.test/2']").click();
      document.querySelector("#bd-panel [data-act='toggle-select-item'][data-save-url='https://e.test/3']").click();
      document.querySelector("#bd-panel [data-act='toggle-select-item'][data-save-url='https://e.test/4']").click();
      const fifthDisabled = document.querySelector("#bd-panel [data-act='toggle-select-item'][data-save-url='https://e.test/5']").disabled;
      const playSelLabel = document.querySelector("#bd-panel [data-act='play-selected']").textContent;
      document.querySelector("#bd-panel [data-act='play-selected']").click();
      // Delete non-empty folder requires confirmation click.
      const del1 = document.querySelector("#bd-panel [data-act='delete-folder']");
      del1.click();
      const confirmTxt = document.querySelector("#bd-panel [data-act='delete-folder']").textContent;
      document.querySelector("#bd-panel [data-act='delete-folder']").click();
      const pillsAfterDel = Array.from(document.querySelectorAll("#bd-panel .bd-folder-pill")).map((p) => p.textContent);
      // Recent row has a + button to play alongside when another stream is playing.
      backdrop.start({on:true, plate:true, lite:false, picture:true, panel:true,
        url:"https://e.test/1", urls:["https://e.test/1"], muted:[], streams:[s("https://e.test/1", "Vid 1")],
        strength:50, plate_opacity:70, true_transparency:false, volume:50, quality:"auto", paused:true, title:"Vid 1",
        recent:[{url:"https://e.test/1", title:"Vid 1"}, {url:"https://e.test/rec2", title:"Recent 2"}],
        folders:[{name:"Lofi", items:[{url:"https://e.test/1", title:"Vid 1"}]}]});
      const recAddBtn = document.querySelector("#bd-panel .bd-recent [data-act='add-saved']");
      const hasRecAdd = !!recAddBtn;
      if (recAddBtn) recAddBtn.click();
      // Closing the last playing stream (on:false) preserves Saved folders intact.
      backdrop.start({on:false, plate:true, lite:false, picture:true, panel:true,
        url:"", urls:[], muted:[], streams:[], strength:50, plate_opacity:70, true_transparency:false,
        volume:50, quality:"auto", paused:false, title:"", recent:[{url:"https://e.test/1", title:"Vid 1"}],
        folders:[{name:"Lofi", items:[{url:"https://e.test/1", title:"Vid 1"}]}]});
      const pillsAfterClose = Array.from(document.querySelectorAll("#bd-panel .bd-folder-pill")).map((p) => p.textContent);
      return {cmds, starSaved, pillTexts, fifthDisabled, playSelLabel, confirmTxt, pillsAfterDel, hasRecAdd, pillsAfterClose};
    })()""",
)
assert folder_res["starSaved"] is True, folder_res
assert "Study (5)" in folder_res["pillTexts"] and "Lofi (1)" in folder_res["pillTexts"], folder_res
assert folder_res["fifthDisabled"] is True and folder_res["playSelLabel"] == "Play selected (4/4)", folder_res
assert any(c == 'backdrop:set:{"play_urls":["https://e.test/1","https://e.test/2","https://e.test/3","https://e.test/4"]}' for c in folder_res["cmds"]), folder_res
assert "Confirm delete" in folder_res["confirmTxt"] and folder_res["pillsAfterDel"] == ["Lofi (1)"], folder_res
assert any(c == 'backdrop:set:{"delete_folder":"Study"}' for c in folder_res["cmds"]), folder_res
assert folder_res["hasRecAdd"] is True and any(c == 'backdrop:set:{"add_url":"https://e.test/rec2"}' for c in folder_res["cmds"]), folder_res
assert folder_res["pillsAfterClose"] == ["Lofi (1)"], folder_res

# 9. Full-stack Python <-> Chromium round-trip audit (real backdrop_video.on_js_message -> refresh -> backdrop.start).
import types as _types
import aqt as _aqt
from PyQt6.QtWidgets import QMainWindow as _QMainWindow, QMenu as _QMenu

_cfg = {}
_win = _QMainWindow()
_mw = _win
class _AM:
    addonFromModule = staticmethod(lambda n: "backdrop_video")
    setWebExports = staticmethod(lambda *a: None)
    getConfig = staticmethod(lambda n: dict(_cfg))
    writeConfig = staticmethod(lambda n, c: (_cfg.clear(), _cfg.update(c)))
    setConfigAction = staticmethod(lambda *a: None)
_mw.addonManager, _mw.app, _mw.state = _AM, app, "review"
_mw.form = _types.SimpleNamespace(menuTools=_QMenu())
_eval_queue = []
_mw.web = _types.SimpleNamespace(eval=lambda js: _eval_queue.append(js), setPlaybackRequiresGesture=lambda v: None)
_aqt.mw = _mw
sys.path.insert(0, ROOT)
import backdrop_video as _bv
_bv.mw = _mw
_bv.tooltip = lambda *a, **k: None
_bv.try_live = lambda *a, **k: None
_bv.go_live = lambda *a, **k: None

def _rt_exec(js: str):
    out = []
    loop = QEventLoop()
    page.runJavaScript(js, lambda v: (out.append(v), loop.quit()))
    loop.exec()
    return out[0] if out else None

def _rt_flush():
    cmds = _rt_exec("(() => { const c = window._rtCmds.splice(0); return c; })()") or []
    for m in cmds:
        _bv.on_js_message((False, None), m, None)
    while _eval_queue:
        js = _eval_queue.pop(0)
        _rt_exec(js)

run_html(
    f"<!doctype html><html class='night-mode'><head><style>{CSS} * {{ transition: none !important; }}</style><script>{JS}</script></head>"
    "<body class='nightMode'><div id='qa'>Card</div></body></html>",
    "(() => { window._rtCmds = []; window.pycmd = (m) => window._rtCmds.push(m); return true; })()",
)
_rt_exec(_bv.start_js(_bv.state(True)))

# Step A: Open panel, create empty folder "Favorites", play a video, save it into "Watch Later", then close video with X.
_rt_exec("document.querySelector('#bd-panel .bd-tab').click();")
_rt_flush()
_rt_exec("document.querySelector('#bd-panel [data-act=toggle-new-folder]').click();")
_rt_exec("(() => { const f = document.querySelector('#bd-panel .bd-new-folder-form'); f.querySelector('input').value = 'Favorites'; f.dispatchEvent(new Event('submit', {bubbles:true, cancelable:true})); })()")
_rt_flush()
_rt_exec("(() => { const f = document.querySelector('#bd-panel .bd-link'); f.querySelector('input').value = 'https://e.test/v1.webm'; f.dispatchEvent(new Event('submit', {bubbles:true, cancelable:true})); })()")
_rt_flush()
_rt_exec("document.querySelector('#bd-panel .bd-slot-row [data-act=open-save]').click();")
_rt_exec("(() => { const f = document.querySelector('#bd-panel .bd-save-form'); f.querySelector('input').value = 'Watch Later'; f.dispatchEvent(new Event('submit', {bubbles:true, cancelable:true})); })()")
_rt_flush()
# Now click X on the currently playing video (the exact user-reported scenario!).
_rt_exec("document.querySelector('#bd-panel [data-act=remove-slot]').click();")
_rt_flush()
rt_after_close = _rt_exec("Array.from(document.querySelectorAll('#bd-panel .bd-folder-pill')).map(p => p.textContent)")
assert rt_after_close == ["Favorites (0)", "Watch Later (1)"], (rt_after_close, _cfg.get("folders"))
assert [f["name"] for f in _cfg["folders"]] == ["Favorites", "Watch Later"], _cfg["folders"]

# Step B: Play a 2nd video, then click + on Recent v1 to play alongside -> 2 slots playing, folders intact.
_rt_exec("(() => { const f = document.querySelector('#bd-panel .bd-link'); f.querySelector('input').value = 'https://e.test/v2.webm'; f.dispatchEvent(new Event('submit', {bubbles:true, cancelable:true})); })()")
_rt_flush()
_rt_exec("document.querySelector('#bd-panel .bd-recent [data-act=add-saved]').click();")
_rt_flush()
assert len(_cfg["urls"]) == 2 and _rt_exec("document.querySelectorAll('#bd-layer .bd-slot').length") == 2, _cfg
assert [f["name"] for f in _cfg["folders"]] == ["Favorites", "Watch Later"], _cfg["folders"]

# Step C: Close both playing videos one by one -> folders STILL intact, then delete Recent item with X -> gone from config & DOM.
_rt_exec("document.querySelector('#bd-panel [data-act=remove-slot]').click();")
_rt_flush()
_rt_exec("document.querySelector('#bd-panel [data-act=remove-slot]').click();")
_rt_flush()
assert _cfg["on"] is False and [f["name"] for f in _cfg["folders"]] == ["Favorites", "Watch Later"], _cfg
_rt_exec("document.querySelector('#bd-panel [data-act=forget-recent]').click();")
_rt_flush()
assert len(_cfg["recent"]) == 1 and _rt_exec("document.querySelectorAll('#bd-panel .bd-recent .bd-recent-row').length") == 1, _cfg

# Step D: Toggle True transparency ON and slide Study card to 0 -> persisted in Python & blur/shadow gone.
_rt_exec("document.querySelector('#bd-panel [data-act=switch-tab][data-tab=controls]').click();")
_rt_exec("document.querySelector('#bd-panel input[data-key=true_transparency]').click();")
_rt_exec("(() => { const r = document.querySelector('#bd-panel input[data-key=plate_opacity]'); r.value='0'; r.dispatchEvent(new Event('input',{bubbles:true})); r.dispatchEvent(new Event('change',{bubbles:true})); })()")
_rt_flush()
assert _cfg["true_transparency"] is True and _cfg["plate_opacity"] == 0, _cfg
assert _rt_exec("document.querySelector('#bd-panel [data-val=plate_opacity]').textContent") == "0%"
assert _rt_exec("document.querySelector('#bd-panel .bd-pane[data-pane=controls]').hidden") is False
assert _rt_exec("document.querySelector('#bd-panel .bd-pane[data-pane=library]').hidden") is True
_rt_exec("document.querySelector('#bd-panel [data-act=switch-tab][data-tab=library]').click();")
assert _rt_exec("document.querySelector('#bd-panel .bd-pane[data-pane=library]').hidden") is False
assert _rt_exec("document.querySelector('#bd-panel .bd-buttons [data-act=break] .bd-act-lbl').textContent") == "Break"

# Step E: Multi-video per-slot persistent seek & skip (-10s / +10s / scrub bar / close back to star, mute, X).
_rt_exec("document.querySelector('#bd-panel [data-act=switch-tab][data-tab=controls]').click();")
_bv.apply({"play_urls": ["https://youtu.be/aaaaaaaaaa1", "https://youtu.be/aaaaaaaaaa2"]})
_rt_flush()
slot_seek_res = _rt_exec("""(() => {
  // Simulate YouTube duration & currentTime for both slots.
  const iframes = document.querySelectorAll("#bd-layer iframe");
  window.dispatchEvent(new MessageEvent("message", {
    source: iframes[0].contentWindow,
    data: JSON.stringify({event: "infoDelivery", info: {duration: 600, currentTime: 90}})
  }));
  window.dispatchEvent(new MessageEvent("message", {
    source: iframes[1].contentWindow,
    data: JSON.stringify({event: "infoDelivery", info: {duration: 400, currentTime: 100}})
  }));
  const row2 = document.querySelector("#bd-panel .bd-slot-row[data-slot-url='https://youtu.be/aaaaaaaaaa2']");
  const chipBefore = row2.querySelector(".bd-slot-chip").textContent;
  // Click the title area (outside star/mute/X) on video 2 to open persistent seek/skip controls.
  row2.querySelector(".bd-slot-name").click();
  const row2Open = document.querySelector("#bd-panel .bd-slot-row[data-slot-url='https://youtu.be/aaaaaaaaaa2']");
  const isOpen = row2Open.classList.contains("bd-seek-open");
  const hasStarWhileOpen = !!row2Open.querySelector("[data-act='open-save']");
  // Click +10s twice, then -10s once (100 -> 110 -> 120 -> 110).
  row2Open.querySelector("[data-act='skip-slot'][data-delta='10']").click();
  row2Open.querySelector("[data-act='skip-slot'][data-delta='10']").click();
  row2Open.querySelector("[data-act='skip-slot'][data-delta='-10']").click();
  const timeAfterSkips = row2Open.querySelector(".bd-slot-time").textContent;
  // Drag slot 2's scrub slider to 250s.
  const range2 = row2Open.querySelector(".bd-slot-range");
  range2.value = "250";
  range2.dispatchEvent(new Event("input", {bubbles: true}));
  range2.dispatchEvent(new Event("change", {bubbles: true}));
  const timeAfterScrub = row2Open.querySelector(".bd-slot-time").textContent;
  return {chipBefore, isOpen, hasStarWhileOpen, timeAfterSkips, timeAfterScrub};
})()""")
_rt_flush()
assert slot_seek_res["chipBefore"] == "1:40", slot_seek_res
assert slot_seek_res["isOpen"] is True and slot_seek_res["hasStarWhileOpen"] is False, slot_seek_res
assert slot_seek_res["timeAfterSkips"] == "1:50 / 6:40", slot_seek_res
assert slot_seek_res["timeAfterScrub"] == "4:10 / 6:40", slot_seek_res
assert _bv._positions.get("https://youtu.be/aaaaaaaaaa2") == 250.0, _bv._positions
# Verify seek mode stays open persistently across refresh() and closes back to star/mute/X when clicking the return button.
_bv.refresh()
_rt_flush()
still_open = _rt_exec("document.querySelector(\"#bd-panel .bd-slot-row[data-slot-url='https://youtu.be/aaaaaaaaaa2']\").classList.contains('bd-seek-open')")
assert still_open is True
restored_star = _rt_exec("""(() => {
  const row2 = document.querySelector("#bd-panel .bd-slot-row[data-slot-url='https://youtu.be/aaaaaaaaaa2']");
  row2.querySelector(".bd-seek-close").click();
  const row2Closed = document.querySelector("#bd-panel .bd-slot-row[data-slot-url='https://youtu.be/aaaaaaaaaa2']");
  return !row2Closed.classList.contains("bd-seek-open") && !!row2Closed.querySelector("[data-act='open-save']");
})()""")
assert restored_star is True


# 9. Live dots in the panel, Hold-P kept across a rebuild, seek on converted normal videos and on websites (bridge).
run_html(
    f"<!doctype html><html><head><style>{CSS}</style><script>{JS}</script></head><body><div id='qa'>Card</div></body></html>",
    "1",
)
_rt_exec("""window._sent = []; window.pycmd = (m) => window._sent.push(m);
  window._cfg = {on:true, plate:true, lite:false, picture:true, panel:true, url:"https://vimeo.com/1", urls:["https://vimeo.com/1","https://site.example/v"],
    muted:[], strength:100, volume:50, paused:false, title:"",
    streams:[{url:"https://vimeo.com/1", kind:"video", src:"http://127.0.0.1:9/t/a.webm?start=30", start:30, live:true, vod:true, duration:600, title:"Vimeo"},
             {url:"https://site.example/v", kind:"page", src:"about:blank", start:40, live:false, title:"Site"}],
    recent:[{url:"https://www.twitch.tv/a", title:"A"}, {url:"https://cams.example/b", title:"B"}, {url:"https://youtu.be/aaaaaaaaaa1", title:"C"}],
    live_status:{"https://www.twitch.tv/a":"live"}};
  backdrop.start(window._cfg); 1""")
_rt_exec("backdrop.liveStatus({'https://cams.example/b':'offline'}); 1")
dots = _rt_exec("""({live: document.querySelectorAll('.bd-live-dot.bd-is-live').length,
  off: document.querySelectorAll('.bd-live-dot.bd-is-off').length,
  asked: window._sent.filter((m) => m.startsWith('backdrop:livecheck:')).length})""")
assert dots == {"live": 1, "off": 1, "asked": 1}, dots
vod_row = _rt_exec("""!!document.querySelector("#bd-panel .bd-slot-row[data-slot-url='https://vimeo.com/1'] .bd-seekable")""")
assert vod_row is True, "converted normal video should offer time controls"
site_before = _rt_exec("""!!document.querySelector("#bd-panel .bd-slot-row[data-slot-url='https://site.example/v'] .bd-seekable")""")
assert site_before is False, "website without a bridge answer can't seek yet"
# The bridge script inside the website frame answers; the first answer resumes at the saved 40 s.
_rt_exec("""window._got = []; window._bf = document.querySelectorAll('#bd-layer iframe')[0];
  _bf.contentWindow.addEventListener('message', (e) => window._got.push(e.data)); 1""")
_rt_exec("""_bf.contentWindow.eval("parent.postMessage({bdBridge:'status', time:0, duration:300, live:false}, '*')"); 1""")
loop_w = QEventLoop(); QTimer.singleShot(300, loop_w.quit); loop_w.exec()
_rt_exec("""_bf.contentWindow.eval("parent.postMessage({bdBridge:'status', time:41, duration:300, live:false}, '*')"); 1""")
loop_w = QEventLoop(); QTimer.singleShot(300, loop_w.quit); loop_w.exec()
site = _rt_exec("""({seek: !!document.querySelector("#bd-panel .bd-slot-row[data-slot-url='https://site.example/v'] .bd-seekable"),
  resumed: window._got.some((m) => m && m.bdBridge === 'seek' && m.t === 40)})""")
assert site == {"seek": True, "resumed": True}, site
# Hold-P survives a rebuild (new card) while still held.
held = _rt_exec("backdrop.peek(true); backdrop.start(window._cfg); document.documentElement.classList.contains('bd-peek')")
assert held is True, held
assert _rt_exec("backdrop.peek(false); document.documentElement.classList.contains('bd-peek')") is False

# Frame-rate pills: one row, the saved choice is highlighted, a click sends it to Python.
fps_res = _rt_exec("""(() => {
  window._cfg.fps = "30"; backdrop.start(window._cfg); window._sent = [];
  const pills = [...document.querySelectorAll("#bd-panel .bd-pill[data-fps]")];
  const before = pills.filter((p) => p.classList.contains("bd-active")).map((p) => p.dataset.fps);
  const tops = new Set(pills.map((p) => Math.round(p.getBoundingClientRect().top)));
  pills.find((p) => p.dataset.fps === "24").click();
  const after = [...document.querySelectorAll("#bd-panel .bd-pill[data-fps].bd-active")].map((p) => p.dataset.fps);
  return {n: pills.length, before, after, rows: tops.size, sent: window._sent.filter((m) => m.startsWith("backdrop:set:"))};
})()""")
assert fps_res["n"] == 6 and fps_res["before"] == ["30"] and fps_res["after"] == ["24"], fps_res
assert fps_res["sent"] == ['backdrop:set:{"fps":"24"}'], fps_res

print("web ok")
