// Backdrop Video: draws the video in a layer under the page, keeps the card readable on top of it,
// and adds a hideable quick-controls panel on the right edge.
window.backdrop = (() => {
  const root = document.documentElement;
  let cfg = null, layer = null, media = null, time = 0, duration = 0, seeking = false, isLiveStream = false, timer = null, panel = null, ytSynced = false;
  const ducking = new Set();  // what is talking now: card audio, Read Aloud
  const send = (msg) => { if (typeof pycmd === "function") pycmd(msg); };
  const set = (changes) => send("backdrop:set:" + JSON.stringify(changes));

  function start(next) {
    // The link, not src, decides: src carries the resume time, which changes while it plays.
    const changed = !cfg || !next.on || !cfg.on || cfg.url !== next.url || cfg.src !== next.src && (cfg.live || next.live);
    cfg = next;
    root.classList.toggle("bd-plate", !!cfg.plate);
    root.classList.toggle("bd-lite", !!cfg.lite);
    root.classList.toggle("bd-nopic", cfg.picture === false);
    root.classList.toggle("bd-panel-open", !!cfg.panel);
    if (changed) stop();
    buildPanel();
    if (cfg.on) {
      root.classList.add("bd-on");
      root.style.setProperty("--bd-canvas", night() ? "#2c2c2c" : "#f5f5f5");
      style(cfg.strength);
      if (changed) build();
      else setVolume(cfg.volume);
      readPlate();
      watchCards();
    }
    update();
  }

  // Older Anki versions lack the --canvas colour variable, so the theme is read from the body class.
  const night = () => document.body.classList.contains("nightMode") || root.classList.contains("night-mode") || root.dataset.rfTheme === "dark";

  function build() {
    ytSynced = false;
    layer = document.createElement("div");
    layer.id = "bd-layer";
    layer.setAttribute("aria-hidden", "true");  // keep screen readers on the card
    if (cfg.kind === "video") {
      media = document.createElement("video");
      Object.assign(media, { autoplay: !cfg.paused, loop: !cfg.live, playsInline: true });
      if (!(cfg.live && cfg.paused)) media.src = cfg.src;  // a paused live stream isn't fetched (or converted) at all
      media.addEventListener("loadedmetadata", () => {
        if (!cfg.live) media.currentTime = cfg.start || 0;
        if (!cfg.live && Number.isFinite(media.duration) && media.duration > 0) { duration = media.duration; updateSeek(); }
        setVolume(cfg.volume);
      });
      media.addEventListener("durationchange", () => {
        if (!cfg.live && Number.isFinite(media.duration) && media.duration > 0) { duration = media.duration; updateSeek(); }
      });
      media.addEventListener("timeupdate", () => {
        if (!cfg.live && !seeking) { time = media.currentTime; updateSeek(); }
      });
      // A live stream converted by the add-on (live.py) ends when the stream does or its address expires.
      media.addEventListener("error", () => send(cfg.live ? "backdrop:error:stream" : "backdrop:error:video"));
      if (cfg.live) media.addEventListener("ended", () => send("backdrop:error:stream"));
    } else {
      media = document.createElement("iframe");
      // Other websites can't navigate Anki's window away or open pop-ups; YouTube's player is left as-is.
      if (cfg.kind === "page") media.setAttribute("sandbox", "allow-scripts allow-same-origin");
      media.referrerPolicy = "strict-origin-when-cross-origin";
      media.src = cfg.kind === "youtube" ? cfg.src + "&cc_load_policy=0&cc_lang_pref=none" : cfg.src;
      media.allow = "autoplay; encrypted-media";
      media.tabIndex = -1;
      if (cfg.kind === "youtube") media.addEventListener("load", () => { ytSend({ event: "listening", id: "bd", channel: "widget" }); setVolume(cfg.volume); });
    }
    layer.appendChild(media);
    root.appendChild(layer);  // after <body>, so it sits above theme backgrounds but under all content
    timer = setInterval(report, 5000);
  }

  function stop() {
    // Unload explicitly: a detached <video> keeps its connection (and the live converter) until garbage collection.
    if (media && media.tagName === "VIDEO") { media.removeAttribute("src"); media.load(); }
    if (layer) layer.remove();
    clearInterval(timer);
    layer = media = null;
    time = 0;
    duration = 0;
    seeking = false;
    isLiveStream = false;
    ytSynced = false;
    root.classList.remove("bd-on", "bd-peek", "bd-stock-over");
  }

  // The card's or overview container's own background colour becomes the frosted plate, so text stays readable.
  function readPlate() {
    if (!cfg || !cfg.on) return;
    const isStockOver = !cfg.plate && !document.querySelector(".overview-center-container, .stats-container, #overview, #overview-wrapper");
    root.classList.toggle("bd-stock-over", isStockOver);
    root.classList.remove("bd-on");
    const box = !cfg.plate && document.querySelector(".stats-container, .white-box, .congrats-card");
    const rgb = (box && rgbOf(getComputedStyle(box).backgroundColor))
      || rgbOf(getComputedStyle(document.body).backgroundColor)
      || rgbOf(getComputedStyle(root).backgroundColor)
      || (night() ? "44, 44, 44" : "255, 255, 255");
    root.classList.add("bd-on");
    root.style.setProperty("--bd-plate-rgb", rgb);
    root.style.setProperty("--bd-plate-border", night() ? "rgba(255, 255, 255, 0.14)" : "rgba(0, 0, 0, 0.09)");
  }

  // "rgb(r, g, b)" -> "r, g, b"; null for transparent or unknown colours (color-mix needs Chromium 111+).
  function rgbOf(color) {
    const m = /^rgba?\(([^)]+)\)$/.exec(color || "");
    if (!m) return null;
    const [r, g, b, a = "1"] = m[1].split(",").map((s) => s.trim());
    if (parseFloat(a) === 0) return null;
    return `${r}, ${g}, ${b}`;
  }

  function style(strength) {
    const s = strength / 100;
    root.style.setProperty("--bd-opacity", (0.3 + 0.7 * s).toFixed(2));
    root.style.setProperty("--bd-blur", Math.round(36 * (1 - s)) + "px");
    const alpha = cfg && cfg.lite ? 0.90 - 0.14 * s : 0.86 - 0.24 * s;
    root.style.setProperty("--bd-plate-alpha", alpha.toFixed(2));
  }

  function setVolume(v) {
    if (!media) return;
    if (ducking.size) v = Math.round(v / 4);
    if (cfg.kind === "video") media.volume = v / 100;
    if (cfg.kind === "youtube") {
      ytSend({ event: "command", func: v ? "unMute" : "mute", args: [] });
      ytSend({ event: "command", func: "setVolume", args: [v] });
    }
  }

  function setPaused(paused) {
    cfg.paused = paused;
    // A paused live stream is unloaded, which stops its converter; resuming rejoins it where it is now.
    if (media && cfg.live) paused ? (media.removeAttribute("src"), media.load()) : (media.src = cfg.src);
    else if (media && cfg.kind === "video") paused ? media.pause() : media.play().catch(() => {});
    if (cfg.kind === "youtube") ytSend({ event: "command", func: paused ? "pauseVideo" : "playVideo", args: [] });
    set({ paused });
    update();
  }

  // Turned down while Anki plays card audio (see duck() in __init__.py) or Read Aloud speaks.
  function duck(on, who = "card") {
    const before = ducking.size;
    on ? ducking.add(who) : ducking.delete(who);
    if (cfg && !before !== !ducking.size) setVolume(cfg.volume);
  }
  window.addEventListener("read-aloud", (e) => duck(!!(e.detail && e.detail.speaking), "read-aloud"));

  function ytSend(msg) {
    if (media && media.contentWindow) media.contentWindow.postMessage(JSON.stringify(msg), "*");
  }

  window.addEventListener("message", (e) => {
    if (!media || e.source !== media.contentWindow) return;
    let msg;
    try { msg = JSON.parse(e.data); } catch (_) { return; }
    if (msg.event === "onError") return send("backdrop:error:" + msg.info);
    // Re-sync volume once YouTube's player JS is ready in case load fired before its message listener attached.
    if (!ytSynced && (msg.event === "onReady" || msg.event === "infoDelivery")) {
      ytSynced = true;
      setVolume(cfg.volume);
    }
    const info = msg.info || {};
    // Live streams come only in H.264, which Anki's browser can't play; YouTube reports it here, not as onError.
    if (info.videoData && info.videoData.errorCode === "html5.unsupportedlive" && !cfg.warned) {
      cfg.warned = true;
      send("backdrop:error:live");
    }
    if (info.videoData && info.videoData.title && info.videoData.title !== cfg.title) {
      cfg.title = info.videoData.title;
      send("backdrop:title:" + cfg.title);
      update();
    }
    if (info.videoData && info.videoData.isLive) { isLiveStream = true; duration = 0; }
    else if (!isLiveStream && typeof info.duration === "number" && Number.isFinite(info.duration) && info.duration > 0) duration = info.duration;
    if (typeof info.currentTime === "number" && !seeking) time = info.currentTime;
    if (typeof info.currentTime === "number" || typeof info.duration === "number") updateSeek();
    // Captions are a distraction behind a card; the player loads them when playback starts.
    if (info.playerState === 1) ytSend({ event: "command", func: "unloadModule", args: ["captions"] });
    if (info.playerState === 0 && !/[?&]list=/.test(cfg.src)) {  // ended: start again (playlists loop themselves)
      ytSend({ event: "command", func: "seekTo", args: [0, true] });
      ytSend({ event: "command", func: "playVideo", args: [] });
    }
  });

  function report() {
    if (cfg.live) return;
    if (cfg.kind === "video" && media) time = media.currentTime;
    if (time) send("backdrop:time:" + time);
  }

  // ---- Quick controls -------------------------------------------------------------------------

  const ICON = {
    film: '<path d="M4 5h16v14H4z M8 5v14 M16 5v14 M4 9h4 M4 15h4 M16 9h4 M16 15h4"/>',
    pause: '<path d="M8 5v14 M16 5v14"/>',
    play: '<path d="M7 5l12 7-12 7z"/>',
    eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    eyeOff: '<path d="M3 3l18 18 M10.6 5.1A10 10 0 0 1 12 5c6 0 10 7 10 7a17 17 0 0 1-3 3.7 M6.6 6.6C3.9 8.3 2 12 2 12s4 7 10 7a9.7 9.7 0 0 0 5.4-1.6"/>',
    power: '<path d="M12 3v9 M6.3 7.3a8 8 0 1 0 11.4 0"/>',
    close: '<path d="M9 6l6 6-6 6"/>',
  };
  const svg = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON[name]}</svg>`;

  function buildPanel() {
    if (panel) return;
    panel = document.createElement("div");
    panel.id = "bd-panel";
    panel.innerHTML = `
      <button class="bd-tab" data-act="toggle" aria-label="Show backdrop video controls" title="Backdrop video">${svg("film")}</button>
      <div class="bd-box" role="region" aria-label="Backdrop video">
        <div class="bd-head"><span>Backdrop video</span>
          <button class="bd-icon" data-act="toggle" aria-label="Hide controls" title="Hide">${svg("close")}</button></div>
        <div class="bd-now"></div>
        <div class="bd-buttons">
          <button class="bd-icon" data-act="pause"></button>
          <button class="bd-icon" data-act="picture"></button>
          <button class="bd-icon" data-act="power"></button>
        </div>
        <label class="bd-slider bd-seek" hidden><span class="bd-seek-head"><span>Position</span><span class="bd-seek-time">0:00 / 0:00</span></span><input type="range" min="0" max="100" value="0" data-key="seek"></label>
        <label class="bd-slider"><span>Backdrop</span><input type="range" min="0" max="100" data-key="strength"></label>
        <label class="bd-slider"><span>Volume</span><input type="range" min="0" max="100" data-key="volume"></label>
        <div class="bd-quality" title="Applies to live streams (Twitch, YouTube Live, etc.). Regular YouTube videos adjust automatically.">
          <span>Stream quality</span>
          <div class="bd-pills" role="group" aria-label="Stream quality">
            <button type="button" class="bd-pill" data-quality="auto">Auto</button>
            <button type="button" class="bd-pill" data-quality="360">360</button>
            <button type="button" class="bd-pill" data-quality="480">480</button>
            <button type="button" class="bd-pill" data-quality="720">720</button>
            <button type="button" class="bd-pill" data-quality="1080">1080</button>
          </div>
        </div>
        <button class="bd-offer" data-act="offer" hidden></button>
        <form class="bd-link"><input type="text" placeholder="Paste a link" aria-label="Video link" spellcheck="false"><button type="submit">Play</button></form>
        <div class="bd-recent"></div>
        <div class="bd-hint">Hold P to peek · Shift+P pauses</div>
      </div>`;
    root.appendChild(panel);

    // Buttons never take keyboard focus, so Space and the answer keys keep working on the card.
    panel.addEventListener("mousedown", (e) => { if (!e.target.closest("input")) e.preventDefault(); });
    panel.addEventListener("click", (e) => {
      const el = e.target.closest("[data-act], [data-url], [data-quality]");
      if (!el) return;
      if (el.dataset.url) return set({ url: el.dataset.url });
      if (el.dataset.quality) {
        cfg.quality = el.dataset.quality;
        set({ quality: cfg.quality });
        update();
        return;
      }
      const act = el.dataset.act;
      if (act === "toggle") {
        cfg.panel = !cfg.panel;
        root.classList.toggle("bd-panel-open", cfg.panel);
        set({ panel: cfg.panel });
        if (cfg.panel) send("backdrop:clip");
      }
      if (act === "pause") setPaused(!cfg.paused);
      if (act === "picture") {
        cfg.picture = cfg.picture === false;
        root.classList.toggle("bd-nopic", !cfg.picture);
        set({ picture: cfg.picture });
        update();
      }
      if (act === "power") set({ on: !cfg.on });
      if (act === "offer") set({ url: el.dataset.link });
    });
    for (const range of panel.querySelectorAll("input[type=range]")) {
      const key = range.dataset.key;
      range.addEventListener("input", () => {
        if (key === "seek") {
          seeking = true;
          panel.querySelector(".bd-seek-time").textContent = `${fmtTime(+range.value)} / ${fmtTime(duration)}`;
          return;
        }
        cfg[key] = +range.value;
        if (key === "strength") style(cfg.strength);
        else setVolume(cfg.volume);
      });
      range.addEventListener("change", () => {
        if (key === "seek") {
          seeking = false;
          seekTo(+range.value);
          range.blur();
          return;
        }
        set({ [key]: +range.value });
        range.blur();
      });
    }
    const form = panel.querySelector(".bd-link"), input = form.querySelector("input");
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      if (input.value.trim()) set({ url: input.value.trim() });
      input.value = "";
      input.blur();
    });
    input.addEventListener("keydown", (e) => { if (e.key === "Escape") input.blur(); });
    if (cfg.panel) send("backdrop:clip");
  }

  function button(act, icon, label, disabled) {
    const b = panel.querySelector(`[data-act=${act}]`);
    b.innerHTML = svg(icon);
    b.title = label;
    b.setAttribute("aria-label", label);
    b.disabled = !!disabled;
  }

  function update() {
    if (!panel) return;
    panel.classList.toggle("bd-dark", night());
    panel.querySelector(".bd-now").textContent = cfg.on ? (cfg.paused ? "Paused · " : cfg.live ? "Live · " : "") + (cfg.title || shortLink(cfg.url)) : "Off";
    button("pause", cfg.paused ? "play" : "pause", cfg.paused ? "Play" : "Pause", !cfg.on);
    button("picture", cfg.picture === false ? "eyeOff" : "eye", cfg.picture === false ? "Show picture" : "Hide picture (keep sound)", !cfg.on);
    button("power", "power", cfg.on ? "Turn off" : "Turn on", !cfg.on && !cfg.url);
    panel.querySelector(".bd-buttons [data-act=power]").classList.toggle("bd-active", !!cfg.on);
    for (const range of panel.querySelectorAll("input[type=range]")) {
      if (range.dataset.key !== "seek" && document.activeElement !== range) range.value = cfg[range.dataset.key];
    }
    updateSeek();
    const q = cfg.quality || "auto";
    for (const pill of panel.querySelectorAll(".bd-pill")) {
      const active = pill.dataset.quality === q;
      pill.classList.toggle("bd-active", active);
      pill.setAttribute("aria-pressed", active ? "true" : "false");
    }
    const recent = panel.querySelector(".bd-recent");
    recent.textContent = "";
    const others = (cfg.recent || []).filter((r) => r.url !== cfg.url).slice(0, 4);
    if (others.length) {
      const head = document.createElement("div");
      head.className = "bd-label";
      head.textContent = "Recent";
      recent.appendChild(head);
    }
    for (const r of others) {
      const b = document.createElement("button");
      b.className = "bd-item";
      b.dataset.url = r.url;
      b.textContent = r.title || shortLink(r.url);  // text, never HTML: titles come from the web
      b.title = r.title || r.url;
      recent.appendChild(b);
    }
  }

  const shortLink = (url) => url.replace(/^https?:\/\/(www\.)?/, "");

  function fmtTime(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    const pad = (n) => (n < 10 ? "0" + n : "" + n);
    return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
  }

  function updateSeek() {
    const row = panel && panel.querySelector(".bd-seek");
    if (!row) return;
    const canSeek = !!(cfg && cfg.on && !cfg.live && !isLiveStream && (cfg.kind === "youtube" || cfg.kind === "video") && duration > 0);
    row.hidden = !canSeek;
    if (!canSeek || seeking) return;
    const range = row.querySelector("input");
    const max = Math.max(1, Math.floor(duration));
    range.max = max;
    if (document.activeElement !== range) range.value = Math.min(Math.floor(time), max);
    row.querySelector(".bd-seek-time").textContent = `${fmtTime(time)} / ${fmtTime(duration)}`;
  }

  function seekTo(sec) {
    if (!cfg || cfg.live) return;
    sec = Math.max(0, Math.min(sec, duration || sec));
    time = sec;
    if (cfg.kind === "video" && media) media.currentTime = sec;
    if (cfg.kind === "youtube") ytSend({ event: "command", func: "seekTo", args: [sec, true] });
    send("backdrop:time:" + sec);
    updateSeek();
  }

  // Python answers "backdrop:clip" with a copied video link, if there is one.
  function offer(link) {
    const b = panel && panel.querySelector(".bd-offer");
    if (!b) return;
    b.hidden = !link || link === cfg.url;
    b.dataset.link = link || "";
    b.textContent = "Play copied link: " + shortLink(link || "");
  }

  // Hold P to see the video clearly, Shift+P pauses; both ignored while typing.
  const isEditable = (el) => el && el.closest && el.closest("input, textarea, select, [contenteditable]");
  const typing = (e) => isEditable(e.target) || isEditable(document.activeElement);
  document.addEventListener("keydown", (e) => {
    if (!cfg || !cfg.on || (e.key !== "p" && e.key !== "P") || e.ctrlKey || e.metaKey || e.altKey || typing(e)) return;
    if (e.shiftKey) { if (!e.repeat) setPaused(!cfg.paused); }
    else if (cfg.picture !== false) root.classList.add("bd-peek");
  });
  document.addEventListener("keyup", (e) => { if (e.key === "p" || e.key === "P") root.classList.remove("bd-peek"); });
  window.addEventListener("blur", () => root.classList.remove("bd-peek"));

  // Re-read the plate colour on card swaps or theme changes (not on every inner cloze/timer DOM mutation).
  let watching = false, platePending = false;
  function schedulePlate() {
    if (platePending) return;
    platePending = true;
    requestAnimationFrame(() => {
      platePending = false;
      readPlate();
      if (panel) panel.classList.toggle("bd-dark", night());
    });
  }
  function watchCards() {
    if (watching) return;
    watching = true;
    const qa = document.getElementById("qa");
    if (qa) new MutationObserver(schedulePlate).observe(qa, { childList: true });
    if (document.body) new MutationObserver(schedulePlate).observe(document.body, { attributes: true, attributeFilter: ["class"] });
    new MutationObserver(schedulePlate).observe(root, { attributes: true, attributeFilter: ["data-rf-theme"] });
  }

  return { start, style, setVolume, duck, offer, togglePanel: () => panel && panel.querySelector(".bd-tab").click() };
})();
