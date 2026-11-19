// Backdrop Video: draws 1–4 videos/streams behind the page, keeps the card readable on top,
// and adds a hideable quick-controls panel on the right edge.
window.backdrop = (() => {
  const root = document.documentElement;
  let cfg = null, layer = null, slots = [], time = 0, duration = 0, seeking = false, timer = null, panel = null, breakPill = null;
  const ducking = new Set();  // what is talking now: card audio, Read Aloud
  const send = (msg) => { if (typeof pycmd === "function") pycmd(msg); };
  const set = (changes) => send("backdrop:set:" + JSON.stringify(changes));
  const isMuted = (url) => !!(cfg && Array.isArray(cfg.muted) && cfg.muted.includes(url));

  function normalizeStreams(next) {
    if (!next || !next.on) return [];
    if (Array.isArray(next.streams) && next.streams.length) return next.streams.slice(0, 4);
    if (!next.url) return [];
    return [{ url: next.url, kind: next.kind, src: next.src, start: next.start || 0, live: !!next.live, title: next.title || "" }];
  }

  function start(next) {
    const wasOn = !!(cfg && cfg.on);
    cfg = next;
    root.classList.toggle("bd-plate", !!cfg.plate);
    root.classList.toggle("bd-lite", !!cfg.lite);
    root.classList.toggle("bd-nopic", cfg.picture === false);
    root.classList.toggle("bd-panel-open", !!cfg.panel);
    buildPanel();
    const list = normalizeStreams(cfg);
    if (cfg.on && list.length) {
      root.classList.add("bd-on");
      root.style.setProperty("--bd-canvas", night() ? "#2c2c2c" : "#f5f5f5");
      syncSlots(list, !wasOn);
      style(cfg.strength);
      setVolume(cfg.volume);
      readPlate();
      watchCards();
    } else {
      stop();
    }
    updateBreak(cfg.break_info);
    update();
  }

  // Older Anki versions lack the --canvas colour variable, so the theme is read from the body class.
  const night = () => document.body.classList.contains("nightMode") || root.classList.contains("night-mode") || root.dataset.rfTheme === "dark";

  function sameSlot(slot, spec) {
    if (!slot || slot.url !== spec.url || slot.kind !== spec.kind) return false;
    return !(slot.live || spec.live) || slot.src === spec.src;
  }

  function unmountSlot(slot) {
    if (slot.media && slot.media.tagName === "VIDEO") {
      slot.media.removeAttribute("src");
      slot.media.load();
    }
    if (slot.el) slot.el.remove();
  }

  function mountSlot(spec) {
    const el = document.createElement("div");
    el.className = "bd-slot bd-kind-" + (spec.kind || "page");
    const slot = {
      url: spec.url, kind: spec.kind, src: spec.src, start: spec.start || 0,
      live: !!spec.live, title: spec.title || "", el, media: null,
      ytSynced: false, isLiveStream: false, duration: 0, time: spec.start || 0,
    };
    let media;
    if (spec.kind === "video") {
      media = document.createElement("video");
      Object.assign(media, { autoplay: !cfg.paused, loop: !spec.live, playsInline: true });
      if (!(spec.live && cfg.paused) && spec.src) media.src = spec.src;
      media.addEventListener("loadedmetadata", () => {
        if (!slot.live) {
          slot.time = slot.start || 0;
          media.currentTime = slot.time;
          if (slots[0] === slot) time = slot.time;
        }
        if (!slot.live && Number.isFinite(media.duration) && media.duration > 0) {
          slot.duration = media.duration;
          if (slots[0] === slot) { duration = slot.duration; updateSeek(); }
        }
        applySlotVolume(slot);
      });
      media.addEventListener("durationchange", () => {
        if (!slot.live && Number.isFinite(media.duration) && media.duration > 0) {
          slot.duration = media.duration;
          if (slots[0] === slot) { duration = slot.duration; updateSeek(); }
        }
      });
      media.addEventListener("timeupdate", () => {
        if (!slot.live && !seeking) {
          slot.time = media.currentTime;
          if (slots[0] === slot) { time = slot.time; updateSeek(); }
        }
      });
      media.addEventListener("error", () => send((slot.live ? "backdrop:error:stream:" : "backdrop:error:video:") + slot.url));
      if (slot.live) media.addEventListener("ended", () => send("backdrop:error:stream:" + slot.url));
    } else {
      media = document.createElement("iframe");
      // Other websites can't navigate Anki's window away or open pop-ups; YouTube's player is left as-is.
      if (spec.kind === "page") media.setAttribute("sandbox", "allow-scripts allow-same-origin");
      media.referrerPolicy = "strict-origin-when-cross-origin";
      media.src = spec.kind === "youtube" ? spec.src + "&cc_load_policy=0&cc_lang_pref=none" : spec.src;
      media.allow = "autoplay; encrypted-media";
      media.tabIndex = -1;
      if (spec.kind === "youtube") {
        media.addEventListener("load", () => {
          ytSend(slot, { event: "listening", id: "bd", channel: "widget" });
          applySlotVolume(slot);
        });
      }
    }
    slot.media = media;
    el.appendChild(media);
    return slot;
  }

  function syncSlots(list, forceRebuild) {
    if (!layer) {
      layer = document.createElement("div");
      layer.id = "bd-layer";
      layer.setAttribute("aria-hidden", "true");  // keep screen readers on the card
      root.appendChild(layer);  // after <body>, so it sits above theme backgrounds but under all content
      timer = setInterval(report, 5000);
    }
    const prev = forceRebuild ? [] : slots.slice();
    if (forceRebuild) {
      for (const s of slots) unmountSlot(s);
    }
    const nextSlots = [];
    const used = new Set();
    for (const spec of list) {
      const idx = prev.findIndex((s, i) => !used.has(i) && sameSlot(s, spec));
      if (idx >= 0) {
        used.add(idx);
        const existing = prev[idx];
        existing.title = spec.title || existing.title;
        nextSlots.push(existing);
      } else {
        nextSlots.push(mountSlot(spec));
      }
    }
    for (let i = 0; i < prev.length; i++) {
      if (!used.has(i)) unmountSlot(prev[i]);
    }
    // Keep existing iframe parents intact when order hasn't changed so iframes never reload unnecessarily.
    for (let i = 0; i < nextSlots.length; i++) {
      const el = nextSlots[i].el;
      if (layer.children[i] !== el) layer.insertBefore(el, layer.children[i] || null);
    }
    slots = nextSlots;
    layer.className = "bd-n" + Math.max(1, Math.min(4, slots.length));
    if (slots.length === 1) {
      duration = slots[0].duration || 0;
      time = slots[0].time || 0;
    } else {
      duration = 0;
      time = 0;
    }
  }

  function stop() {
    for (const s of slots) unmountSlot(s);
    slots = [];
    if (layer) layer.remove();
    clearInterval(timer);
    layer = timer = null;
    time = 0;
    duration = 0;
    seeking = false;
    root.classList.remove("bd-on", "bd-peek", "bd-stock-over", "bd-noglass", "bd-break");
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
    const blur = Math.round(36 * (1 - s));
    const scale = cfg && cfg.kind === "page" && slots.length <= 1 ? 1 : 1 + 0.08 * (1 - s);
    root.style.setProperty("--bd-opacity", (0.3 + 0.7 * s).toFixed(2));
    root.style.setProperty("--bd-filter", blur > 0 ? `blur(${blur}px)` : "none");
    root.style.setProperty("--bd-scale", scale.toFixed(3));
    const platePct = cfg && typeof cfg.plate_opacity === "number" ? Math.max(0, Math.min(100, cfg.plate_opacity)) : null;
    const alpha = platePct !== null
      ? platePct / 100
      : (cfg && cfg.lite ? 0.90 - 0.14 * s : 0.86 - 0.24 * s);
    root.style.setProperty("--bd-plate-alpha", alpha.toFixed(2));
    // Skip expensive second backdrop-filter blur pass when backdrop is already blurry or card is >=95% solid.
    root.classList.toggle("bd-noglass", blur >= 18 || (platePct !== null && platePct >= 95));
  }

  function applySlotVolume(slot) {
    if (!slot || !slot.media || !cfg) return;
    let v = isMuted(slot.url) ? 0 : cfg.volume;
    if (ducking.size) v = Math.round(v / 4);
    if (slot.kind === "video") {
      slot.media.muted = v === 0;
      slot.media.volume = v / 100;
    } else if (slot.kind === "youtube") {
      ytSend(slot, { event: "command", func: v ? "unMute" : "mute", args: [] });
      ytSend(slot, { event: "command", func: "setVolume", args: [v] });
    }
  }

  function setVolume(v) {
    if (cfg) cfg.volume = v;
    for (const s of slots) applySlotVolume(s);
  }

  function setPaused(paused) {
    cfg.paused = paused;
    for (const s of slots) {
      if (!s.media) continue;
      // A paused live stream is unloaded, which stops its converter; resuming rejoins it where it is now.
      if (s.live) paused ? (s.media.removeAttribute("src"), s.media.load()) : (s.media.src = s.src);
      else if (s.kind === "video") paused ? s.media.pause() : s.media.play().catch(() => {});
      else if (s.kind === "youtube") ytSend(s, { event: "command", func: paused ? "pauseVideo" : "playVideo", args: [] });
    }
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

  function ytSend(slot, msg) {
    if (slot && slot.media && slot.media.contentWindow) slot.media.contentWindow.postMessage(JSON.stringify(msg), "*");
  }

  window.addEventListener("message", (e) => {
    const slot = slots.find((s) => s.media && e.source === s.media.contentWindow);
    if (!slot) return;
    let msg;
    try { msg = JSON.parse(e.data); } catch (_) { return; }
    if (msg.event === "onError") return send("backdrop:error:" + msg.info + ":" + slot.url);
    // Re-sync volume once YouTube's player JS is ready in case load fired before its message listener attached.
    if (!slot.ytSynced && (msg.event === "onReady" || msg.event === "infoDelivery")) {
      slot.ytSynced = true;
      applySlotVolume(slot);
    }
    const info = msg.info || {};
    // Live streams come only in H.264, which Anki's browser can't play; YouTube reports it here, not as onError.
    if (info.videoData && info.videoData.errorCode === "html5.unsupportedlive" && !slot.warned) {
      slot.warned = true;
      send("backdrop:error:live:" + slot.url);
    }
    if (info.videoData && info.videoData.title && info.videoData.title !== slot.title) {
      slot.title = info.videoData.title;
      if (slots[0] === slot) cfg.title = slot.title;
      send("backdrop:title:" + JSON.stringify({ url: slot.url, title: slot.title }));
      update();
    }
    if (info.videoData && info.videoData.isLive) { slot.isLiveStream = true; slot.duration = 0; }
    else if (!slot.isLiveStream && typeof info.duration === "number" && Number.isFinite(info.duration) && info.duration > 0) {
      slot.duration = info.duration;
    }
    if (typeof info.currentTime === "number" && !seeking) slot.time = info.currentTime;
    if (slots[0] === slot && (typeof info.currentTime === "number" || typeof info.duration === "number")) {
      duration = slot.duration;
      time = slot.time;
      updateSeek();
    }
    // Captions are a distraction behind a card; the player loads them when playback starts.
    if (info.playerState === 1) {
      ytSend(slot, { event: "command", func: "unloadModule", args: ["captions"] });
      applySlotVolume(slot);
    }
    if (info.playerState === 0 && !/[?&]list=/.test(slot.src)) {  // ended: start again (playlists loop themselves)
      ytSend(slot, { event: "command", func: "seekTo", args: [0, true] });
      ytSend(slot, { event: "command", func: "playVideo", args: [] });
    }
  });

  function report() {
    if (!cfg || !cfg.on || cfg.paused || slots.length !== 1) return;
    const s = slots[0];
    if (s.live) return;
    if (s.kind === "video" && s.media) time = s.media.currentTime;
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
    coffee: '<path d="M17 8h1a4 4 0 0 1 0 8h-1 M3 8h14v9a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4V8z M6 2v3 M10 2v3 M14 2v3"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.35-4.35"/>',
    close: '<path d="M9 6l6 6-6 6"/>',
  };
  const svg = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON[name]}</svg>`;

  function buildPanel() {
    if (!breakPill) {
      breakPill = document.createElement("div");
      breakPill.id = "bd-break-pill";
      breakPill.innerHTML = `<span class="bd-break-text">Break</span><button type="button" data-act="back-study">Back to Study</button>`;
      breakPill.addEventListener("mousedown", (e) => e.preventDefault());
      breakPill.addEventListener("click", (e) => {
        if (e.target.closest("[data-act=back-study]")) send("backdrop:break:0");
      });
      root.appendChild(breakPill);
    }
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
          <button class="bd-icon" data-act="break"></button>
          <button class="bd-icon" data-act="power"></button>
        </div>
        <label class="bd-slider bd-seek" hidden><span class="bd-seek-head"><span>Position</span><span class="bd-seek-time">0:00 / 0:00</span></span><input type="range" min="0" max="100" value="0" data-key="seek"></label>
        <label class="bd-slider"><span>Backdrop</span><input type="range" min="0" max="100" data-key="strength"></label>
        <label class="bd-slider"><span>Study card</span><input type="range" min="0" max="100" data-key="plate_opacity"></label>
        <label class="bd-slider"><span>Volume</span><input type="range" min="0" max="100" data-key="volume"></label>
        <div class="bd-quality" title="Applies to live streams (Twitch, YouTube Live, etc.). Regular YouTube videos adjust automatically.">
          <span>Stream quality</span>
          <div class="bd-pills" role="group" aria-label="Stream quality">
            <button type="button" class="bd-pill" data-quality="auto">Auto</button>
            <button type="button" class="bd-pill" data-quality="360">360</button>
            <button type="button" class="bd-pill" data-quality="480">480</button>
            <button type="button" class="bd-pill" data-quality="720">720</button>
            <button type="button" class="bd-pill" data-quality="1080">1080</button>
            <button type="button" class="bd-pill" data-quality="1440">1440</button>
            <button type="button" class="bd-pill" data-quality="2160">4K</button>
          </div>
        </div>
        <button class="bd-offer" data-act="offer" hidden></button>
        <form class="bd-link">
          <input type="text" placeholder="Paste a link" aria-label="Video link" spellcheck="false">
          <button type="submit">Play</button>
          <button type="button" class="bd-add" data-act="add" title="Add alongside current video (up to 4)" hidden>+</button>
          <button type="button" class="bd-browse" data-act="browse" aria-label="Browse or search for a video" title="Search / Browse in Anki">${svg("search")}</button>
        </form>
        <div class="bd-slots"></div>
        <div class="bd-recent"></div>
        <div class="bd-hint">Hold P to peek</div>
      </div>`;
    root.appendChild(panel);

    const input = panel.querySelector(".bd-link input");
    // Buttons never take keyboard focus, so Space and the answer keys keep working on the card.
    panel.addEventListener("mousedown", (e) => { if (!e.target.closest("input")) e.preventDefault(); });
    panel.addEventListener("click", (e) => {
      const el = e.target.closest("[data-act], [data-url], [data-quality]");
      if (!el) return;
      const act = el.dataset.act;
      if (act === "mute-slot") {
        const u = el.dataset.slotUrl;
        const cur = new Set(cfg.muted || []);
        cur.has(u) ? cur.delete(u) : cur.add(u);
        cfg.muted = Array.from(cur);
        setVolume(cfg.volume);
        set({ muted: cfg.muted });
        update();
        return;
      }
      if (act === "remove-slot") return set({ remove_url: el.dataset.slotUrl });
      if (act === "forget-recent") return set({ forget_recent: el.dataset.recentUrl });
      if (act === "add") {
        const val = input.value.trim();
        if (val) {
          set({ add_url: val });
          input.value = "";
          input.blur();
        }
        return;
      }
      if (el.dataset.url) return set({ url: el.dataset.url });
      if (el.dataset.quality) {
        cfg.quality = el.dataset.quality;
        set({ quality: cfg.quality });
        update();
        return;
      }
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
      if (act === "break") {
        const nextBreak = !(cfg && cfg.break_info && cfg.break_info.active);
        if (nextBreak && cfg.panel) {
          closePanel();
          set({ panel: false });
        }
        send("backdrop:break:" + (nextBreak ? "1" : "0"));
      }
      if (act === "browse") send("backdrop:browse");
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
        if (key === "strength" || key === "plate_opacity") style(cfg.strength);
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
      range.addEventListener("pointerup", () => range.blur());
    }
    const form = panel.querySelector(".bd-link");
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
    const nowEl = panel.querySelector(".bd-now");
    if (!cfg.on) {
      nowEl.textContent = "Off";
    } else if (slots.length > 1) {
      nowEl.textContent = (cfg.paused ? "Paused · " : "") + slots.length + " streams playing";
    } else {
      const s0 = slots[0];
      nowEl.textContent = cfg.paused ? "Paused" : (s0 && s0.live ? "Live" : "Playing");
    }
    button("pause", cfg.paused ? "play" : "pause", cfg.paused ? "Play" : "Pause", !cfg.on);
    button("picture", cfg.picture === false ? "eyeOff" : "eye", cfg.picture === false ? "Show picture" : "Hide picture (keep sound)", !cfg.on);
    const onBreak = !!(cfg.break_info && cfg.break_info.active);
    button("break", "coffee", onBreak ? "Exit break zone" : "Break zone (pauses timers, watch clearly)", !onBreak && !cfg.on && !cfg.url);
    panel.querySelector(".bd-buttons [data-act=break]").classList.toggle("bd-active", onBreak);
    button("power", "power", cfg.on ? "Turn off" : "Turn on", !cfg.on && !cfg.url);
    panel.querySelector(".bd-buttons [data-act=power]").classList.toggle("bd-active", !!cfg.on);
    const addBtn = panel.querySelector("[data-act=add]");
    if (addBtn) addBtn.hidden = !(cfg.on && slots.length >= 1 && slots.length < 4);
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
    const hintEl = panel.querySelector(".bd-hint");
    if (hintEl) hintEl.textContent = cfg.auto_note ? `Hold P to peek · ${cfg.auto_note}` : "Hold P to peek";
    const slotsBox = panel.querySelector(".bd-slots");
    slotsBox.textContent = "";
    if (cfg.on && slots.length >= 1) {
      for (const s of slots) {
        const row = document.createElement("div");
        row.className = "bd-slot-row";
        const name = document.createElement("span");
        name.className = "bd-slot-name";
        name.textContent = s.title || shortLink(s.url);
        name.title = s.title || s.url;
        const muteBtn = document.createElement("button");
        muteBtn.type = "button";
        muteBtn.className = "bd-slot-btn" + (isMuted(s.url) ? " bd-muted" : "");
        muteBtn.dataset.act = "mute-slot";
        muteBtn.dataset.slotUrl = s.url;
        muteBtn.textContent = isMuted(s.url) ? "Muted" : "Mute";
        const rmBtn = document.createElement("button");
        rmBtn.type = "button";
        rmBtn.className = "bd-slot-btn";
        rmBtn.dataset.act = "remove-slot";
        rmBtn.dataset.slotUrl = s.url;
        rmBtn.title = "Remove stream";
        rmBtn.textContent = "×";
        row.append(name, muteBtn, rmBtn);
        slotsBox.appendChild(row);
      }
    }
    const activeSet = new Set(slots.map((s) => s.url));
    const recent = panel.querySelector(".bd-recent");
    recent.textContent = "";
    const others = (cfg.recent || []).filter((r) => !activeSet.has(r.url) && r.url !== cfg.url).slice(0, 4);
    if (others.length) {
      const head = document.createElement("div");
      head.className = "bd-label";
      head.textContent = "Recent";
      recent.appendChild(head);
    }
    for (const r of others) {
      const row = document.createElement("div");
      row.className = "bd-recent-row";
      const b = document.createElement("button");
      b.className = "bd-item";
      b.dataset.url = r.url;
      b.textContent = r.title || shortLink(r.url);  // text, never HTML: titles come from the web
      b.title = r.title || r.url;
      const del = document.createElement("button");
      del.type = "button";
      del.className = "bd-recent-del";
      del.dataset.act = "forget-recent";
      del.dataset.recentUrl = r.url;
      del.title = "Remove from recent";
      del.textContent = "×";
      row.append(b, del);
      recent.appendChild(row);
    }
  }

  const shortLink = (url) => (url || "").replace(/^https?:\/\/(www\.)?/, "");

  function fmtTime(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    const pad = (n) => (n < 10 ? "0" + n : "" + n);
    return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
  }

  function updateSeek() {
    const row = panel && panel.querySelector(".bd-seek");
    if (!row) return;
    const s0 = slots[0];
    const canSeek = !!(cfg && cfg.on && slots.length === 1 && s0 && !s0.live && !s0.isLiveStream
      && (s0.kind === "youtube" || s0.kind === "video") && duration > 0);
    row.hidden = !canSeek;
    if (!canSeek || seeking) return;
    const range = row.querySelector("input");
    const max = Math.max(1, Math.floor(duration));
    range.max = max;
    if (document.activeElement !== range) range.value = Math.min(Math.floor(time), max);
    row.querySelector(".bd-seek-time").textContent = `${fmtTime(time)} / ${fmtTime(duration)}`;
  }

  function seekTo(sec) {
    const s0 = slots[0];
    if (!cfg || !s0 || s0.live) return;
    sec = Math.max(0, Math.min(sec, duration || sec));
    time = s0.time = sec;
    if (s0.kind === "video" && s0.media) s0.media.currentTime = sec;
    if (s0.kind === "youtube") ytSend(s0, { event: "command", func: "seekTo", args: [sec, true] });
    send("backdrop:time:" + sec);
    updateSeek();
  }

  // Python answers "backdrop:clip" with a copied video link, if there is one.
  function offer(link) {
    const b = panel && panel.querySelector(".bd-offer");
    if (!b) return;
    const already = slots.some((s) => s.url === link);
    b.hidden = !link || link === cfg.url || already;
    b.dataset.link = link || "";
    b.textContent = "Play copied link: " + shortLink(link || "");
  }

  function peek(on) {
    if (!cfg || !cfg.on || cfg.picture === false) on = false;
    root.classList.toggle("bd-peek", !!on);
  }

  // Hold P to see the video clearly; capture phase prevents card scripts swallowing it, ignored while typing.
  const isEditable = (el) => el && el.closest && el.closest("input, textarea, select, [contenteditable]");
  const typing = (e) => isEditable(e.target) || isEditable(document.activeElement);
  const isP = (e) => e.code === "KeyP" || e.key === "p" || e.key === "P";
  document.addEventListener("keydown", (e) => {
    if (!isP(e) || e.shiftKey || e.ctrlKey || e.metaKey || e.altKey || typing(e)) return;
    peek(true);
  }, true);
  document.addEventListener("keyup", (e) => { if (isP(e)) peek(false); }, true);
  document.addEventListener("pointerdown", (e) => {
    if (!cfg || !cfg.panel || !panel || panel.contains(e.target)) return;
    cfg.panel = false;
    root.classList.remove("bd-panel-open");
    set({ panel: false });
  }, true);
  window.addEventListener("blur", () => peek(false));
  window.addEventListener("pagehide", stop);
  window.addEventListener("focus", () => { if (cfg && cfg.panel) send("backdrop:clip"); });

  function closePanel() {
    if (!cfg) return;
    cfg.panel = false;
    root.classList.remove("bd-panel-open");
  }

  function updateBreak(info) {
    if (cfg) cfg.break_info = info || null;
    const active = !!(info && info.active && cfg && cfg.on);
    root.classList.toggle("bd-break", active);
    if (!breakPill) return;
    const txt = breakPill.querySelector(".bd-break-text");
    if (!active) {
      breakPill.classList.remove("bd-rest-done");
      return;
    }
    const done = !!info.rest_done;
    breakPill.classList.toggle("bd-rest-done", done);
    if (done) {
      txt.textContent = "Rest complete!";
    } else if (typeof info.rest_left === "number") {
      txt.textContent = "Break · Rest " + fmtTime(info.rest_left);
    } else {
      txt.textContent = "Break · " + fmtTime(info.elapsed || 0);
    }
  }

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

  return { start, style, setVolume, duck, offer, peek, closePanel, updateBreak, togglePanel: () => panel && panel.querySelector(".bd-tab").click() };
})();
