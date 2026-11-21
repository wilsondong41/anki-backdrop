// Backdrop Video: draws 1–4 videos/streams behind the page, keeps the card readable on top,
// and adds a hideable quick-controls panel on the right edge.
window.backdrop = (() => {
  const root = document.documentElement;
  let cfg = null, layer = null, slots = [], time = 0, duration = 0, seeking = false, timer = null, fpsTimer = null, panel = null, breakPill = null;
  let openFolder = null, selectMode = false, savingItem = null, addingFolder = false, confirmDeleteFolder = null, activeTab = null;
  let renaming = null;  // {folder, url} of the saved link being renamed in the panel
  let replacing = null;  // {url, title}: 4 streams playing, picking which one this link replaces

  // "+" plays a link alongside; with 4 already playing, "⇄" asks which one to swap out.
  function addOrSwapButton(row, url, title) {
    if (!cfg.on || slots.some((s) => s.url === url)) return;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "bd-recent-del";
    b.dataset.saveUrl = url;
    b.dataset.saveTitle = title || "";
    if (slots.length < 4) {
      b.dataset.act = "add-saved";
      b.title = "Play alongside what's playing";
      b.textContent = "+";
    } else {
      b.dataset.act = "replace-pick";
      b.title = "Swap this in for one of the playing streams";
      b.setAttribute("aria-label", b.title);
      b.textContent = "⇄";
    }
    row.appendChild(b);
  }
  const selectedUrls = new Set();
  const seekOpenSlots = new Set();
  const ducking = new Set();  // what is talking now: card audio, Read Aloud
  const send = (msg) => { if (typeof pycmd === "function") pycmd(msg); };
  const set = (changes) => send("backdrop:set:" + JSON.stringify(changes));
  const isMuted = (url) => !!(cfg && Array.isArray(cfg.muted) && cfg.muted.includes(url));
  const isHeld = (url) => !!(cfg && Array.isArray(cfg.held) && cfg.held.includes(url));  // paused on its own

  function normalizeStreams(next) {
    if (!next || !next.on) return [];
    if (Array.isArray(next.streams) && next.streams.length) return next.streams.slice(0, 4);
    if (!next.url) return [];
    return [{ url: next.url, kind: next.kind, src: next.src, start: next.start || 0, live: !!next.live, title: next.title || "" }];
  }

  function start(next) {
    const wasOn = !!(cfg && cfg.on);
    const prevFolders = cfg && Array.isArray(cfg.folders) ? cfg.folders : [];
    cfg = next;
    if (!Array.isArray(cfg.folders)) cfg.folders = prevFolders;
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
    if (cfg.peek) peekHeld = true;
    if (peekHeld) peek(true);
    update();
  }

  // Older Anki versions lack the --canvas colour variable, so the theme is read from the body class.
  const night = () => document.body.classList.contains("nightMode") || root.classList.contains("night-mode") || root.dataset.rfTheme === "dark";

  function sameSlot(slot, spec) {
    if (!slot || slot.url !== spec.url || slot.kind !== spec.kind) return false;
    const base = (src) => (src || "").split("?")[0];
    return !(slot.live || spec.live) || base(slot.src) === base(spec.src);
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
      url: spec.url, kind: spec.kind, src: spec.src, start: spec.start || 0, held: isHeld(spec.url), still: null,
      live: !!spec.live, title: spec.title || "", fps: spec.fps || 0, measuredFps: 0, fpsSample: null,
      el, media: null, ytSynced: false, isLiveStream: false, duration: 0, time: spec.start || 0, seeking: false,
      // A converted normal video (not a live stream): it restarts at a time to seek, so its clock is offset + currentTime.
      vod: !!(spec.live && spec.vod), offset: spec.live && spec.vod ? Math.floor(spec.start || 0) : 0,
      baseSrc: (spec.src || "").split("?")[0], bridge: null,
      // Several videos behind one link: {index, count}; YouTube playlists fill it in from the player.
      album: spec.album && spec.album.count > 1 ? spec.album : null,
    };
    if (slot.vod && spec.duration > 0) slot.duration = spec.duration;
    let media;
    if (spec.kind === "video") {
      media = document.createElement("video");
      const still = cfg.paused || slot.held;
      Object.assign(media, { autoplay: !still, loop: !spec.live, playsInline: true });
      // Anki's own converter allows copying frames, so the last one can be kept across screens (see pagehide).
      if (spec.live) media.crossOrigin = "anonymous";
      // A paused converted stream loads just long enough to show its frame, then lets the converter stop.
      slot.priming = !!(spec.live && still);
      if (spec.src) media.src = spec.src;
      media.addEventListener("loadeddata", () => {
        if (!slot.priming) return;
        slot.priming = false;
        holdFrame(slot);
        unload(slot);
      });
      media.addEventListener("playing", () => {
        if (typeof media.getVideoPlaybackQuality === "function") {
          slot.fpsSample = { frames: media.getVideoPlaybackQuality().totalVideoFrames || 0, t: performance.now() };
        }
      });
      media.addEventListener("loadedmetadata", () => {
        if (!slot.live) {
          slot.time = slot.start || 0;
          media.currentTime = slot.time;
          if (slots[0] === slot) time = slot.time;
        }
        if (!slot.live && Number.isFinite(media.duration) && media.duration > 0) {
          slot.duration = media.duration;
          if (slots[0] === slot) duration = slot.duration;
          updateSeek();
        }
        applySlotVolume(slot);
      });
      media.addEventListener("durationchange", () => {
        if (!slot.live && Number.isFinite(media.duration) && media.duration > 0) {
          slot.duration = media.duration;
          if (slots[0] === slot) duration = slot.duration;
          updateSeek();
        }
      });
      media.addEventListener("timeupdate", () => {
        if (!media.getAttribute("src") || slot.priming) return;  // unloaded (paused) or only showing a frame: keep the time
        if ((!slot.live || slot.vod) && !slot.seeking && !(slots[0] === slot && seeking)) {
          slot.time = slot.offset + media.currentTime;
          if (slots[0] === slot) time = slot.time;
          updateSeek();
        }
      });
      media.addEventListener("error", () => send((slot.live ? "backdrop:error:stream:" : "backdrop:error:video:") + slot.url));
      // A finished normal video starts over (like loop); a live stream that ends is looked up again.
      // (A normal video whose connection dropped part-way picks up where it was.)
      if (slot.live) media.addEventListener("ended", () => {
        if (!slot.vod) return send("backdrop:error:stream:" + slot.url);
        const dropped = slot.duration && slot.time < slot.duration - 10;
        if (slot.album && !dropped) return albumStep(slot, 1);  // an album plays its next video (after the last: the first)
        seekSlotTo(slot, dropped ? slot.time : 0);
      });
    } else {
      media = document.createElement("iframe");
      // Other websites can't navigate Anki's window away or open pop-ups; YouTube's player is left as-is.
      if (spec.kind === "page") media.setAttribute("sandbox", "allow-scripts allow-same-origin");
      media.referrerPolicy = "strict-origin-when-cross-origin";
      media.src = spec.kind === "youtube" && /^https:/.test(spec.src) ? spec.src + "&cc_load_policy=0&cc_lang_pref=none" : spec.src;
      media.allow = "autoplay; encrypted-media";
      media.tabIndex = -1;
      if (spec.kind === "page") {
        media.addEventListener("load", () => {
          if (cfg.paused || slot.held) setTimeout(() => pauseSlot(slot), 1500);  // after the site starts its video
        });
      }
      if (spec.kind === "youtube") {
        media.addEventListener("load", () => {
          ytSend(slot, { event: "listening", id: "bd", channel: "widget" });
          applySlotVolume(slot);
        });
      }
    }
    slot.media = media;
    el.appendChild(media);
    if (pageFresh) restoreStill(slot);
    return slot;
  }

  function syncSlots(list, forceRebuild) {
    if (!layer) {
      layer = document.createElement("div");
      layer.id = "bd-layer";
      layer.setAttribute("aria-hidden", "true");  // keep screen readers on the card
      root.appendChild(layer);  // after <body>, so it sits above theme backgrounds but under all content
      timer = setInterval(report, 5000);
      fpsTimer = setInterval(sampleVideoFps, 2000);
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
        if (spec.fps) existing.fps = spec.fps;
        existing.held = isHeld(existing.url);
        // A converted video's length can arrive after it started (asked from mpv): the slider appears then.
        if (existing.vod && spec.duration > 0 && !(existing.duration > 0)) existing.duration = spec.duration;
        if (existing.kind !== "youtube") {
          existing.album = spec.album && spec.album.count > 1 ? spec.album : null;
          existing.albumBusy = false;  // a switch that failed leaves the same video: its picker works again
        }
        nextSlots.push(existing);
      } else {
        nextSlots.push(mountSlot(spec));
      }
    }
    for (let i = 0; i < prev.length; i++) {
      if (used.has(i)) continue;
      // The same link reloaded as a new video (album next/previous, new quality): keep its last frame meanwhile.
      const heir = nextSlots.find((n) => n.url === prev[i].url && !prev.includes(n));
      if (heir && heir.media && heir.media.tagName === "VIDEO") {
        const p = prev[i];
        holdFrame(heir, p.media && p.media.tagName === "VIDEO" && p.media.readyState >= 2 ? p.media : (p.still && !p.still.hidden ? p.still : null));
        releaseFrame(heir);
      }
      unmountSlot(prev[i]);
    }
    // Tiles are placed with CSS order, never moved in the page: moving a website or YouTube frame would reload it.
    for (let i = 0; i < nextSlots.length; i++) {
      const el = nextSlots[i].el;
      if (el.parentNode !== layer) layer.appendChild(el);
      el.style.order = String(i);
      el.dataset.pos = String(i + 1);
    }
    slots = nextSlots;
    pageFresh = false;
    const activeUrls = new Set(slots.map((s) => s.url));
    for (const u of Array.from(seekOpenSlots)) {
      if (!activeUrls.has(u)) seekOpenSlots.delete(u);
    }
    layer.className = "bd-n" + Math.max(1, Math.min(4, slots.length));
    if (slots.length >= 1) {
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
    seekOpenSlots.clear();
    if (layer) layer.remove();
    clearInterval(timer);
    clearInterval(fpsTimer);
    layer = timer = fpsTimer = null;
    time = 0;
    duration = 0;
    seeking = false;
    root.classList.remove("bd-on", "bd-peek", "bd-stock-over", "bd-noglass", "bd-true-trans", "bd-break");
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
    style(cfg.strength);
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
    const trueTrans = !!(cfg && cfg.true_transparency);
    root.classList.toggle("bd-true-trans", trueTrans);
    root.style.setProperty("--bd-plate-alpha", alpha.toFixed(2));
    const dark = night();
    if (trueTrans) {
      const glassBlur = Math.round(14 * alpha);
      const glassSat = (1 + 0.15 * alpha).toFixed(2);
      const borderAlpha = ((dark ? 0.14 : 0.09) * alpha).toFixed(3);
      const shadowAlpha = (0.16 * alpha).toFixed(3);
      root.style.setProperty("--bd-glass-filter", glassBlur > 0 ? `blur(${glassBlur}px) saturate(${glassSat})` : "none");
      root.style.setProperty("--bd-plate-border", dark ? `rgba(255, 255, 255, ${borderAlpha})` : `rgba(0, 0, 0, ${borderAlpha})`);
      root.style.setProperty("--bd-plate-shadow", alpha > 0.01 ? `0 8px 32px rgba(0, 0, 0, ${shadowAlpha})` : "none");
      root.classList.toggle("bd-noglass", blur >= 18 || glassBlur <= 0 || (platePct !== null && platePct >= 95));
    } else {
      root.style.setProperty("--bd-glass-filter", "blur(14px) saturate(1.15)");
      root.style.setProperty("--bd-plate-border", dark ? "rgba(255, 255, 255, 0.14)" : "rgba(0, 0, 0, 0.09)");
      root.style.setProperty("--bd-plate-shadow", "0 8px 32px rgba(0, 0, 0, 0.16)");
      // Skip expensive second backdrop-filter blur pass when backdrop is already blurry or card is >=95% solid.
      root.classList.toggle("bd-noglass", blur >= 18 || (platePct !== null && platePct >= 95));
    }
  }

  // Each stream's own level (0–100, default 100) scales the main Volume.
  const slotLevel = (url) => {
    const v = cfg && cfg.volumes && cfg.volumes[url];
    return typeof v === "number" && v >= 0 && v <= 100 ? v : 100;
  };

  function applySlotVolume(slot) {
    if (!slot || !slot.media || !cfg) return;
    let v = isMuted(slot.url) || zoomPaused.has(slot.url) ? 0 : Math.round(cfg.volume * slotLevel(slot.url) / 100);
    if (ducking.size) v = Math.round(v / 4);
    if (slot.kind === "video") {
      slot.media.muted = v === 0;
      slot.media.volume = v / 100;
    } else if (slot.kind === "youtube") {
      ytSend(slot, { event: "command", func: v ? "unMute" : "mute", args: [] });
      ytSend(slot, { event: "command", func: "setVolume", args: [v] });
    } else if (slot.kind === "page" && slot.media.contentWindow) {
      slot.media.contentWindow.postMessage({ bdBridge: "volume", v: v / 100 }, "*");  // a website's own video
    }
  }

  function setVolume(v) {
    if (cfg) cfg.volume = v;
    for (const s of slots) applySlotVolume(s);
  }

  function setPaused(paused) {
    cfg.paused = paused;
    const wasHeld = slots.filter((s) => s.held);
    if (!paused) {
      for (const s of slots) s.held = false;
      cfg.held = [];
    }
    for (const s of slots) {
      if (!s.media) continue;
      if (paused) { if (!wasHeld.includes(s)) pauseSlot(s); }
      else resumeSlot(s);
    }
    set(paused ? { paused } : { paused, held: [] });
    update();
  }

  // One video paused on its own (the others keep playing); it keeps showing its frame.
  function setHeld(s, held) {
    s.held = held;
    const cur = new Set(cfg.held || []);
    held ? cur.add(s.url) : cur.delete(s.url);
    cfg.held = Array.from(cur);
    if (!cfg.paused) held ? pauseSlot(s) : resumeSlot(s);
    set({ held: cfg.held });
    update();
  }

  // A video's own Pause/Play (its row in Now Playing, or its tile during a break). With one video, or when it's the
  // last one still playing, this is the main Pause, so the main button and the break bar always agree.
  const isStopped = (s) => !!(cfg.paused || s.held);
  function toggleOne(s) {
    const others = slots.filter((x) => x !== s);
    if (!isStopped(s)) {
      if (others.every(isStopped)) setPaused(true);  // the last one playing: same as the main Pause (Play resumes all)
      else setHeld(s, true);
      return;
    }
    if (!cfg.paused) return setHeld(s, false);
    // Everything was paused: play just this one, the rest stay paused.
    cfg.paused = false;
    for (const x of others) x.held = true;
    s.held = false;
    cfg.held = others.map((x) => x.url);
    resumeSlot(s);
    set({ paused: false, held: cfg.held });
    update();
  }

  function unload(s) {
    s.media.removeAttribute("src");
    s.media.load();
  }

  // A paused converted stream is unloaded, which stops its converter; its last frame stays on screen.
  function pauseSlot(s) {
    if (!s.media) return;
    if (s.live) { holdFrame(s); unload(s); }
    else if (s.kind === "video") s.media.pause();
    else if (s.kind === "youtube") ytSend(s, { event: "command", func: "pauseVideo", args: [] });
    else if (s.kind === "page" && s.media.contentWindow) s.media.contentWindow.postMessage({ bdBridge: "pause" }, "*");
  }

  function resumeSlot(s) {
    if (!s.media) return;
    if (s.live && s.vod) restartAt(s, s.time || 0);  // picks up where it was
    else if (s.live) { s.media.src = s.src; releaseFrame(s); }  // a live stream rejoins where it is now
    else if (s.kind === "video") s.media.play().catch(() => {});
    else if (s.kind === "youtube") ytSend(s, { event: "command", func: "playVideo", args: [] });
    else if (s.kind === "page" && s.media.contentWindow) s.media.contentWindow.postMessage({ bdBridge: "play" }, "*");
  }

  // A still copy of the video's current frame, shown while the video reloads (a skip, pause or resume)
  // so the backdrop never flashes blank. Drawing a video from the local converter is allowed; nothing is read back.
  function holdFrame(s, v = s.media) {
    if (!v || (v.tagName === "VIDEO" ? v.readyState < 2 || !v.videoWidth : v.tagName !== "CANVAS" || !v.width)) return;
    if (!s.still) {
      s.still = document.createElement("canvas");
      s.still.className = "bd-still";
      s.el.appendChild(s.still);
    }
    s.still.width = v.videoWidth || v.width;
    s.still.height = v.videoHeight || v.height;
    try {
      s.still.getContext("2d").drawImage(v, 0, 0);
      s.still.hidden = false;
    } catch (_) {
      s.still.hidden = true;
    }
  }

  // Hide the still copy once the video shows a new frame.
  function releaseFrame(s) {
    const v = s.media;
    if (!s.still || s.still.hidden || !v) return;
    const done = () => { if (!s.priming && s.still) s.still.hidden = true; };
    if (typeof v.requestVideoFrameCallback === "function") v.requestVideoFrameCallback(done);
    else v.addEventListener("playing", done, { once: true });
  }

  // Going between the deck overview and the cards loads a new page, which restarts every video (a converted stream
  // needs ~2 s). The last frames are kept for the next page, so the picture stays put instead of going black.
  const STILLS = "bd-stills";
  let pageFresh = true;  // only the page's first layout uses them (later reloads keep their own frame, see syncSlots)
  addEventListener("pagehide", () => {
    if (!cfg || !cfg.on || cfg.picture === false) return;
    const out = { t: Date.now(), s: {} };
    for (const s of slots) {
      const v = s.media && s.media.tagName === "VIDEO" && s.media.readyState >= 2 && s.media.videoWidth ? s.media
        : (s.still && !s.still.hidden ? s.still : null);
      if (!v) continue;
      const vw = v.videoWidth || v.width, vh = v.videoHeight || v.height;
      try {
        const c = document.createElement("canvas");
        c.width = Math.min(640, vw);
        c.height = Math.round(c.width * vh / vw);
        c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
        out.s[s.url] = c.toDataURL("image/jpeg", 0.7);
      } catch (_) {}  // a video from a site that doesn't allow copying its picture
    }
    try { sessionStorage.setItem(STILLS, JSON.stringify(out)); } catch (_) {}
  });

  function restoreStill(s) {
    let saved = null;
    try { saved = JSON.parse(sessionStorage.getItem(STILLS) || "null"); } catch (_) {}
    let data = saved && Date.now() - saved.t < 15 * 60000 && saved.s && saved.s[s.url];
    const yt = s.kind === "youtube" && /(?:youtu\.be\/|\/embed\/|\/shorts\/|\/live\/|[?&]v=)([\w-]{11})/.exec(s.url || "");
    if (!data && yt) data = "https://i.ytimg.com/vi/" + yt[1] + "/mqdefault.jpg";  // YouTube's own preview picture
    if (!data || (s.kind !== "video" && s.kind !== "youtube")) return;
    const img = new Image();
    img.onload = () => {
      if (!slots.includes(s) && !s.el.isConnected) return;
      if (s.kind === "video" && (s.media.readyState >= 2 || s.priming || (s.still && !s.still.hidden))) return;  // a fresher frame is there
      if (s.kind === "youtube" && s.revealed) return;  // the player already shows its picture
      if (!s.still) {
        s.still = document.createElement("canvas");
        s.still.className = "bd-still";
        s.el.appendChild(s.still);
      }
      s.still.width = img.naturalWidth;
      s.still.height = img.naturalHeight;
      try { s.still.getContext("2d").drawImage(img, 0, 0); s.still.hidden = false; } catch (_) { return; }
      if (s.kind === "video") releaseFrame(s);
      else if (!s.revealed) s.media.classList.add("bd-wait");  // YouTube's player is black while it loads
    };
    img.src = data;
    // Never stay hidden. (A link waiting for the converter has no player: its picture stays until the video comes.)
    if (s.kind === "youtube" && s.src !== "about:blank") setTimeout(() => revealYT(s), 6000);
  }

  // YouTube's player shows its picture: drop the stand-in.
  function revealYT(s) {
    if (s.revealed) return;
    s.revealed = true;
    s.media.classList.remove("bd-wait");
    if (s.still) setTimeout(() => { if (s.still) s.still.hidden = true; }, 250);
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

  // Status from the bridge script in another website's frame (or a frame inside it).
  const fromFrame = (w, target) => {
    for (let i = 0; w && i < 8; i++) {
      if (w === target) return true;
      if (w === w.parent) return false;
      w = w.parent;
    }
    return false;
  };
  window.addEventListener("message", (e) => {  // P pressed while a backdrop website had the keyboard
    const m = e.data;
    if (!m || typeof m !== "object" || m.bdBridge !== "peek") return;
    if (slots.some((x) => x.media && x.media.contentWindow && fromFrame(e.source, x.media.contentWindow))) peek(!!m.on);
  });
  window.addEventListener("message", (e) => {
    const m = e.data;
    if (!m || typeof m !== "object" || m.bdBridge !== "status") return;
    const s = slots.find((x) => x.kind === "page" && x.media && fromFrame(e.source, x.media.contentWindow));
    if (!s) return;
    const could = canSlotSeek(s);
    s.bridge = { at: Date.now(), live: !!m.live };
    const dur = Number(m.duration);
    let t = Number(m.time);
    if (!m.live && Number.isFinite(dur) && dur > 0) s.duration = dur;
    if (!s.resumed) {  // first answer: pick up where this website's video was left (sites ignore a start time in the link)
      s.resumed = true;
      if (!m.live && s.start >= 5 && s.start < s.duration - 5 && t < 3) {
        e.source.postMessage({ bdBridge: "seek", t: s.start }, "*");
        t = s.start;
      }
    }
    if (Number.isFinite(t) && !s.seeking && !(slots[0] === s && seeking)) {
      s.time = Math.max(0, t);
      if (slots[0] === s) { time = s.time; duration = s.duration; }
    }
    could === canSlotSeek(s) ? updateSeek() : update();
  });
  setInterval(() => {
    for (const s of slots) {
      if (s.kind === "page" && s.media && s.media.contentWindow) s.media.contentWindow.postMessage({ bdBridge: "watch" }, "*");
    }
  }, 1000);

  window.addEventListener("message", (e) => {
    const slot = slots.find((s) => s.media && e.source === s.media.contentWindow);
    if (!slot) return;
    let msg;
    try { msg = JSON.parse(e.data); } catch (_) { return; }
    if (msg.event === "onError") { revealYT(slot); return send("backdrop:error:" + msg.info + ":" + slot.url); }
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
    if (info.videoData && info.videoData.isLive) {
      const wasOpen = seekOpenSlots.delete(slot.url);
      const changedLive = !slot.isLiveStream;
      slot.isLiveStream = true;
      slot.duration = 0;
      if (wasOpen || changedLive) update();
    } else if (!slot.isLiveStream && typeof info.duration === "number" && Number.isFinite(info.duration) && info.duration > 0) {
      slot.duration = info.duration;
    }
    if (typeof info.currentTime === "number" && !slot.seeking && !(slots[0] === slot && seeking)) {
      slot.time = info.currentTime;
    }
    if (typeof info.currentTime === "number" || typeof info.duration === "number" || (info.videoData && info.videoData.isLive)) {
      if (slots[0] === slot) {
        duration = slot.duration;
        time = slot.time;
      }
      updateSeek();
    }
    if (Array.isArray(info.playlist) && typeof info.playlistIndex === "number") {
      const count = info.playlist.length;
      const was = slot.album;
      if (slot.firstIndex == null) slot.firstIndex = info.playlistIndex;  // the video the saved time belongs to
      slot.album = count > 1 ? { index: Math.max(0, info.playlistIndex), count } : null;
      if (!was !== !slot.album || (was && slot.album && (was.index !== slot.album.index || was.count !== slot.album.count))) {
        slot.albumBusy = false;
        if (seekOpenSlots.has(slot.url)) update(true); else updateSeek();
      }
    }
    // Captions are a distraction behind a card; the player loads them when playback starts.
    if (info.playerState === 1 || info.playerState === 2) revealYT(slot);
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
    if (!cfg || !cfg.on || cfg.paused || !slots.length) return;
    for (let i = 0; i < slots.length; i++) {
      const s = slots[i];
      if ((s.live && !s.vod) || s.isLiveStream || (s.kind === "page" && !canSlotSeek(s)) || s.held) continue;
      // A YouTube playlist reopens at its first video, so a later video's time isn't saved.
      if (s.kind === "youtube" && s.album && s.album.index !== s.firstIndex) continue;
      if (s.kind === "video" && s.media && s.media.getAttribute("src") && !s.priming) s.time = s.offset + s.media.currentTime;
      if (i === 0) time = s.time;
      if (s.time > 0) {
        if (i === 0 && slots.length === 1) send("backdrop:time:" + s.time);
        send("backdrop:slot_time:" + JSON.stringify({ url: s.url, time: s.time }));
      }
    }
  }

  function snapFps(raw) {
    if (!Number.isFinite(raw) || raw <= 0) return 0;
    const r = Math.round(raw);
    for (const std of [24, 25, 30, 48, 50, 60, 120]) {
      if (Math.abs(raw - std) <= 1.2) return std;
    }
    return r;
  }

  function sampleVideoFps() {
    let changed = false;
    const now = performance.now();
    for (const s of slots) {
      if (s.kind !== "video" || !s.media) continue;
      const v = s.media;
      if (v.paused || v.ended || v.readyState < 2 || typeof v.getVideoPlaybackQuality !== "function") {
        s.fpsSample = null;
        continue;
      }
      const frames = v.getVideoPlaybackQuality().totalVideoFrames || 0;
      if (!s.fpsSample || frames < s.fpsSample.frames) {
        s.fpsSample = { frames, t: now };
        continue;
      }
      const dt = (now - s.fpsSample.t) / 1000;
      const df = frames - s.fpsSample.frames;
      if (dt >= 1.5 && df > 0) {
        const measured = snapFps(df / dt);
        s.fpsSample = { frames, t: now };
        if (measured > 0 && measured !== s.measuredFps) {
          s.measuredFps = measured;
          changed = true;
          send("backdrop:fps:" + JSON.stringify({ url: s.url, fps: measured }));
        }
      }
    }
    if (changed) updateHint();
  }

  function updateHint() {
    const hintEl = panel && panel.querySelector(".bd-hint");
    const qBadge = panel && panel.querySelector(".bd-q-badge");
    if (!cfg) return;
    let note = cfg.auto_note || "";
    const vals = Array.from(new Set(
      slots.map((s) => s.measuredFps || s.fps).filter((f) => typeof f === "number" && f > 0)
    )).sort((a, b) => a - b);
    if (vals.length && note) {
      const fpsLabel = vals.length === 1 ? `${vals[0]} fps` : `${vals[0]}–${vals[vals.length - 1]} fps`;
      const base = note.replace(/,\s*(?:up to\s+)?(?:\d+(?:[–-]\d+)?|uncapped|source)\s*fps$/i, "");
      note = `${base}, ${fpsLabel}`;
    }
    if (qBadge) {
      const m = /(?:up to\s+)?\d+(?:[–-]\d+)?\s*fps|uncapped\s*fps/i.exec(note);
      qBadge.textContent = m ? m[0] : "";
    }
    if (hintEl) hintEl.textContent = note ? `Hold P to peek · ${note}` : "Hold P to peek";
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
    volOn: '<path d="M11 5L6 9H2v6h4l5 4V5z M15.5 8.5a5 5 0 0 1 0 7 M18.5 5.5a9 9 0 0 1 0 13"/>',
    volOff: '<path d="M11 5L6 9H2v6h4l5 4V5z M22 9l-6 6 M16 9l6 6"/>',
    close: '<path d="M9 6l6 6-6 6"/>',
  };
  const svg = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON[name]}</svg>`;

  function buildPanel() {
    if (!breakPill) {
      breakPill = document.createElement("div");
      breakPill.id = "bd-break-pill";
      // One tap sets a timer (it starts from that moment and is kept for the next break); "Off" just counts up.
      breakPill.innerHTML = `<span class="bd-break-text">Break</span>
        <span class="bd-break-len" role="group" aria-label="Break timer" title="Break timer: counts down from when you pick it, and next break uses it too">
          <span class="bd-break-len-lbl">Timer</span>${[0, 5, 10, 15, 20, 30].map((m) =>
            `<button type="button" class="bd-len" data-min="${m}">${m ? m : "Off"}</button>`).join("")}<span class="bd-break-len-lbl">min</span>
        </span>
        <button type="button" class="bd-pill-pause" data-act="pause-all"></button>
        <button type="button" class="bd-back" data-act="back-study">Back to Study</button>
        <span class="bd-break-bar" aria-hidden="true"><span></span></span>`;
      breakPill.addEventListener("mousedown", (e) => e.preventDefault());  // the card keeps the keyboard
      // The bar stays open while the mouse rests on it.
      breakPill.addEventListener("pointerenter", () => { overPill = true; wake(); });
      breakPill.addEventListener("pointerleave", () => { overPill = false; wake(); });
      breakPill.addEventListener("click", (e) => {
        if (e.target.closest("[data-act=back-study]")) return send("backdrop:break:0");
        if (e.target.closest("[data-act=pause-all]")) return cfg && cfg.on && setPaused(!cfg.paused);
        const len = e.target.closest(".bd-len");
        if (!len) return;
        const m = parseInt(len.dataset.min, 10) || 0;
        const info = Object.assign({}, (cfg && cfg.break_info) || { active: true, elapsed: 0 }, { minutes: m });
        if (m) info.left = m * 60; else delete info.left;
        updateBreak(info);  // show it right away; the next tick from Anki agrees
        set({ break_minutes: m });
      });
      root.appendChild(breakPill);
    }
    if (panel) return;
    panel = document.createElement("div");
    panel.id = "bd-panel";
    panel.innerHTML = `
      <button class="bd-tab" data-act="toggle" aria-label="Show backdrop video controls" title="Backdrop video">${svg("film")}</button>
      <div class="bd-box" role="region" aria-label="Backdrop video">
        <div class="bd-head">
          <div class="bd-head-title"><span>Backdrop Video</span><span class="bd-now"></span></div>
          <button class="bd-icon" data-act="toggle" aria-label="Hide controls" title="Hide">${svg("close")}</button>
        </div>
        <div class="bd-buttons">
          <button class="bd-icon" data-act="pause"></button>
          <button class="bd-icon" data-act="picture"></button>
          <button class="bd-icon" data-act="break"></button>
          <button class="bd-icon" data-act="power"></button>
        </div>
        <div class="bd-nav" role="tablist" aria-label="Backdrop sections">
          <button type="button" class="bd-nav-btn" role="tab" data-act="switch-tab" data-tab="controls">Controls</button>
          <button type="button" class="bd-nav-btn" role="tab" data-act="switch-tab" data-tab="library">Library</button>
        </div>
        <div class="bd-save-pop" hidden></div>
        <div class="bd-pane" data-pane="controls">
          <div class="bd-card bd-slots-card">
            <div class="bd-card-head">
              <span class="bd-label bd-slots-title">Now Playing</span>
              <button type="button" class="bd-sec-btn" data-act="browse" title="Find or add another video">+ Browse</button>
            </div>
            <div class="bd-slots"></div>
            <div class="bd-empty-play" hidden>
              <span>No video playing yet.</span>
              <button type="button" class="bd-primary-btn" data-act="browse">${svg("search")}<span>Browse Videos</span></button>
            </div>
          </div>
          <div class="bd-card">
            <div class="bd-card-head"><span class="bd-label">Display &amp; Sound</span></div>
            <label class="bd-slider"><span class="bd-slider-head"><span>Backdrop</span><span class="bd-val" data-val="strength"></span></span><input type="range" min="0" max="100" data-key="strength"></label>
            <label class="bd-slider"><span class="bd-slider-head"><span>Study card</span><span class="bd-val" data-val="plate_opacity"></span></span><input type="range" min="0" max="100" data-key="plate_opacity"></label>
            <label class="bd-check" title="Also fade card blur and border as you slide Study card down">
              <input type="checkbox" data-key="true_transparency"><span>True transparency</span>
            </label>
            <label class="bd-slider"><span class="bd-slider-head"><span>Volume</span><span class="bd-val" data-val="volume"></span></span><input type="range" min="0" max="100" data-key="volume"></label>
          </div>
          <div class="bd-card bd-quality" title="Applies to live streams (Twitch, YouTube Live, etc.). Regular YouTube videos adjust automatically.">
            <div class="bd-card-head"><span class="bd-label">Stream Quality</span><span class="bd-q-badge"></span></div>
            <div class="bd-pills" role="group" aria-label="Stream quality">
              <button type="button" class="bd-pill" data-quality="auto">Auto</button>
              <button type="button" class="bd-pill" data-quality="360">360</button>
              <button type="button" class="bd-pill" data-quality="480">480</button>
              <button type="button" class="bd-pill" data-quality="720">720</button>
              <button type="button" class="bd-pill" data-quality="1080">1080</button>
              <button type="button" class="bd-pill" data-quality="1440">1440</button>
              <button type="button" class="bd-pill" data-quality="2160">4K</button>
            </div>
            <div class="bd-sub-label" title="Lower uses less CPU and battery; the backdrop is blurred, so 24 or 30 usually looks the same.">Frame rate</div>
            <div class="bd-pills" role="group" aria-label="Frame rate">
              <button type="button" class="bd-pill" data-fps="auto" title="Picks 30–60 fps for you, based on your computer and how many videos are playing">Auto</button>
              <button type="button" class="bd-pill" data-fps="15" title="Lowest that still looks like smooth motion. Saves the most CPU and battery; the blur hides the difference">15</button>
              <button type="button" class="bd-pill" data-fps="24" title="Film rate. Most movies and many online videos are 24, so nothing is dropped">24</button>
              <button type="button" class="bd-pill" data-fps="30" title="The usual rate for web videos and most live streams (Twitch, cam sites)">30</button>
              <button type="button" class="bd-pill" data-fps="45" title="A middle step for 60 fps streams on slower computers">45</button>
              <button type="button" class="bd-pill" data-fps="0" title="The stream's own frame rate, no limit (60 fps streams stay 60). Uses the most CPU">Source</button>
            </div>
          </div>
        </div>
        <div class="bd-pane" data-pane="library">
          <div class="bd-card">
            <div class="bd-card-head"><span class="bd-label">Add a Video</span></div>
            <button type="button" class="bd-primary-btn bd-browse-full" data-act="browse" aria-label="Browse or search for a video" title="Search / Browse in Anki">${svg("search")}<span>Browse / Search Videos</span></button>
            <button class="bd-offer" data-act="offer" hidden></button>
            <form class="bd-link">
              <input type="text" placeholder="Paste a link…" aria-label="Video link" spellcheck="false">
              <button type="submit">Play</button>
              <button type="button" class="bd-add" data-act="add" title="Add alongside current video (up to 4)" hidden>+</button>
            </form>
          </div>
          <div class="bd-card bd-folders"></div>
          <div class="bd-card bd-recent"></div>
        </div>
        <div class="bd-hint">Hold P to peek</div>
      </div>`;
    root.appendChild(panel);

    const input = panel.querySelector(".bd-link input");
    // Buttons never take keyboard focus, so Space and the answer keys keep working on the card.
    panel.addEventListener("mousedown", (e) => { if (!e.target.closest("input[type=text], input[type=range]")) e.preventDefault(); });
    panel.addEventListener("click", (e) => {
      const el = e.target.closest("[data-act], [data-url], [data-quality], [data-fps]");
      if (!el) return;
      const act = el.dataset.act;
      if (act === "switch-tab") {
        activeTab = el.dataset.tab === "library" ? "library" : "controls";
        update(true);
        return;
      }
      if (act === "toggle-slot-seek") {
        const u = el.dataset.slotUrl;
        const s = slots.find((x) => x.url === u);
        if (!canSlotSeek(s)) return;
        seekOpenSlots.has(u) ? seekOpenSlots.delete(u) : seekOpenSlots.add(u);
        update(true);
        return;
      }
      if (act === "hold-slot") {
        const s = slots.find((x) => x.url === el.dataset.slotUrl);
        if (s) toggleOne(s);
        return;
      }
      if (act === "album-slot") {
        const s = slots.find((x) => x.url === el.dataset.slotUrl);
        if (s) albumStep(s, parseInt(el.dataset.delta || "0", 10));
        return;
      }
      if (act === "skip-slot") {
        const u = el.dataset.slotUrl;
        const s = slots.find((x) => x.url === u);
        const delta = parseFloat(el.dataset.delta || "0");
        if (s && Number.isFinite(delta)) seekSlotTo(s, (s.time || 0) + delta);
        return;
      }
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
      if (act === "open-save") {
        const u = el.dataset.saveUrl, t = el.dataset.saveTitle || "";
        savingItem = (savingItem && savingItem.url === u) ? null : { url: u, title: t };
        addingFolder = false;
        update(true);
        const inp = panel.querySelector(".bd-save-pop input");
        if (inp) inp.focus();
        return;
      }
      if (act === "close-save") {
        savingItem = null;
        update(true);
        return;
      }
      if (act === "save-to-folder") {
        const folder = el.dataset.folderName;
        if (savingItem && folder) saveToFolderLocal(folder, savingItem.url, savingItem.title);
        return;
      }
      if (act === "toggle-new-folder") {
        addingFolder = !addingFolder;
        savingItem = null;
        confirmDeleteFolder = null;
        update(true);
        const inp = panel.querySelector(".bd-new-folder-form input");
        if (inp) inp.focus();
        return;
      }
      if (act === "open-folder") {
        const name = el.dataset.folderName;
        openFolder = (openFolder === name) ? null : name;
        confirmDeleteFolder = null;
        update(true);
        return;
      }
      if (act === "toggle-select") {
        selectMode = !selectMode;
        confirmDeleteFolder = null;
        if (!selectMode) {
          selectedUrls.clear();
        } else if (!openFolder) {
          const first = (cfg.folders || []).find((f) => (f.items || []).length > 0) || (cfg.folders || [])[0];
          if (first) openFolder = first.name;
        }
        update(true);
        return;
      }
      if (act === "toggle-select-item") {
        const u = el.dataset.saveUrl;
        if (selectedUrls.has(u)) selectedUrls.delete(u);
        else if (selectedUrls.size < 4) selectedUrls.add(u);
        update(true);
        return;
      }
      if (act === "clear-selected") {
        selectedUrls.clear();
        update(true);
        return;
      }
      if (act === "play-selected") {
        if (selectedUrls.size > 0) {
          const list = Array.from(selectedUrls).slice(0, 4);
          selectMode = false;
          selectedUrls.clear();
          update(true);
          set({ play_urls: list });
        }
        return;
      }
      if (act === "add-saved") {
        if (el.dataset.saveUrl) set({ add_url: el.dataset.saveUrl });
        return;
      }
      if (act === "replace-pick") {
        replacing = { url: el.dataset.saveUrl, title: el.dataset.saveTitle || "" };
        activeTab = "controls";  // Now Playing is there
        update(true);
        return;
      }
      if (act === "replace-cancel") {
        replacing = null;
        update(true);
        return;
      }
      if (act === "replace-slot") {
        const pick = replacing;
        replacing = null;
        if (pick) set({ replace: { old: el.dataset.slotUrl, new: pick.url } });
        update(true);
        return;
      }
      if (act === "rename-item") {
        renaming = { folder: el.dataset.folderName, url: el.dataset.saveUrl };
        update(true);
        return;
      }
      if (act === "unsave-item") {
        const folder = el.dataset.folderName, u = el.dataset.saveUrl;
        const f = (cfg.folders || []).find((x) => x.name === folder);
        if (f) f.items = (f.items || []).filter((it) => it.url !== u);
        selectedUrls.delete(u);
        update(true);
        return set({ unsave_item: { folder, url: u } });
      }
      if (act === "delete-folder") {
        const folder = el.dataset.folderName;
        const f = (cfg.folders || []).find((x) => x.name === folder);
        const nonEmpty = f && (f.items || []).length > 0;
        if (nonEmpty && confirmDeleteFolder !== folder) {
          confirmDeleteFolder = folder;
          update(true);
          return;
        }
        confirmDeleteFolder = null;
        if (openFolder === folder) openFolder = null;
        cfg.folders = (cfg.folders || []).filter((x) => x.name !== folder);
        update(true);
        return set({ delete_folder: folder });
      }
      if (act === "forget-recent") {
        const u = el.dataset.recentUrl;
        cfg.recent = (cfg.recent || []).filter((r) => r.url !== u);
        if (!cfg.on && cfg.url === u) cfg.url = "";
        update();
        return set({ forget_recent: u });
      }
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
      if (el.dataset.fps) {
        cfg.fps = el.dataset.fps;
        set({ fps: cfg.fps });
        update();
        return;
      }
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
        if (cfg.panel) {
          send("backdrop:clip");
          askLive(true);  // dots right away (answers are cached in Python, so this is cheap)
        }
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
        cfg[key] = +range.value;
        const valEl = panel.querySelector(`[data-val="${key}"]`);
        if (valEl) valEl.textContent = cfg[key] + "%";
        if (key === "strength" || key === "plate_opacity") style(cfg.strength);
        else setVolume(cfg.volume);
      });
      range.addEventListener("change", () => {
        set({ [key]: +range.value });
        range.blur();
      });
      range.addEventListener("pointerup", () => range.blur());
    }
    const tt = panel.querySelector("input[data-key=true_transparency]");
    if (tt) {
      tt.addEventListener("change", () => {
        cfg.true_transparency = tt.checked;
        style(cfg.strength);
        set({ true_transparency: cfg.true_transparency });
        tt.blur();
      });
    }
    const form = panel.querySelector(".bd-link");
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      if (input.value.trim()) set({ url: input.value.trim() });
      input.value = "";
      input.blur();
    });
    input.addEventListener("keydown", (e) => { if (e.key === "Escape") input.blur(); });
    panel.addEventListener("submit", (e) => {
      if (e.target.classList.contains("bd-save-form")) {
        e.preventDefault();
        const inp = e.target.querySelector("input");
        const name = (inp && inp.value || "").trim().slice(0, 40);
        if (name && savingItem) saveToFolderLocal(name, savingItem.url, savingItem.title);
        if (inp) inp.blur();
      } else if (e.target.classList.contains("bd-new-folder-form")) {
        e.preventDefault();
        const inp = e.target.querySelector("input");
        const name = (inp && inp.value || "").trim().slice(0, 40);
        if (name) {
          cfg.folders = cfg.folders || [];
          const existing = cfg.folders.find((f) => f.name.toLowerCase() === name.toLowerCase());
          if (!existing) cfg.folders.push({ name, items: [] });
          openFolder = existing ? existing.name : name;
          addingFolder = false;
          update(true);
          set({ create_folder: name });
        }
        if (inp) inp.blur();
      }
    });
    panel.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && (e.target.closest(".bd-save-pop") || e.target.closest(".bd-new-folder-form"))) {
        e.target.blur();
        savingItem = null;
        addingFolder = false;
        update(true);
      }
    });
    if (cfg.panel) send("backdrop:clip");
  }

  function saveToFolderLocal(folderName, url, title) {
    cfg.folders = cfg.folders || [];
    let target = cfg.folders.find((f) => f.name.toLowerCase() === folderName.toLowerCase());
    if (!target) {
      target = { name: folderName, items: [] };
      cfg.folders.push(target);
    }
    if (!target.items.some((it) => it.url === url)) {
      target.items.unshift({ url, title: title || "" });
    }
    openFolder = target.name;
    savingItem = null;
    update(true);
    set({ save_item: { folder: target.name, url, title: title || "" } });
  }

  function isSavedUrl(url) {
    return (cfg && cfg.folders || []).some((f) => (f.items || []).some((it) => it.url === url));
  }

  function renderSavePop(force) {
    const pop = panel.querySelector(".bd-save-pop");
    if (!pop) return;
    if (!force && pop.contains(document.activeElement)) return;
    pop.textContent = "";
    if (!savingItem) {
      pop.hidden = true;
      return;
    }
    pop.hidden = false;
    const head = document.createElement("div");
    head.className = "bd-save-head";
    const lbl = document.createElement("span");
    lbl.textContent = "Save to folder";
    const close = document.createElement("button");
    close.type = "button";
    close.className = "bd-recent-del";
    close.dataset.act = "close-save";
    close.textContent = "×";
    close.title = "Cancel";
    head.append(lbl, close);
    pop.appendChild(head);
    const folders = cfg.folders || [];
    if (folders.length) {
      const pills = document.createElement("div");
      pills.className = "bd-folder-pills";
      for (const f of folders) {
        const has = (f.items || []).some((it) => it.url === savingItem.url);
        const p = document.createElement("button");
        p.type = "button";
        p.className = "bd-folder-pill" + (has ? " bd-active" : "");
        p.dataset.act = "save-to-folder";
        p.dataset.folderName = f.name;
        p.textContent = (has ? "✓ " : "") + f.name;
        pills.appendChild(p);
      }
      pop.appendChild(pills);
    }
    const f = document.createElement("form");
    f.className = "bd-save-form";
    const inp = document.createElement("input");
    inp.type = "text";
    inp.maxLength = 40;
    inp.placeholder = "New folder name…";
    inp.setAttribute("aria-label", "New folder name");
    const btn = document.createElement("button");
    btn.type = "submit";
    btn.textContent = "Save";
    f.append(inp, btn);
    pop.appendChild(f);
  }

  function renderFolders(activeSet, force) {
    const box = panel.querySelector(".bd-folders");
    if (!box) return;
    if (!force && box.contains(document.activeElement)) return;
    box.textContent = "";
    const folders = cfg.folders || [];
    const totalItems = folders.reduce((n, f) => n + (f.items || []).length, 0);
    if (totalItems === 0 && selectMode) {
      selectMode = false;
      selectedUrls.clear();
    }
    if (openFolder && !folders.some((f) => f.name === openFolder)) openFolder = null;

    const head = document.createElement("div");
    head.className = "bd-sec-head";
    const lbl = document.createElement("span");
    lbl.className = "bd-label";
    lbl.textContent = "Saved";
    const actions = document.createElement("div");
    actions.className = "bd-sec-actions";
    if (totalItems > 0) {
      const selBtn = document.createElement("button");
      selBtn.type = "button";
      selBtn.className = "bd-sec-btn" + (selectMode ? " bd-active" : "");
      selBtn.dataset.act = "toggle-select";
      selBtn.textContent = selectMode ? "Done" : "Select";
      selBtn.title = "Select up to 4 saved videos to play together";
      actions.appendChild(selBtn);
    }
    const newBtn = document.createElement("button");
    newBtn.type = "button";
    newBtn.className = "bd-sec-btn" + (addingFolder ? " bd-active" : "");
    newBtn.dataset.act = "toggle-new-folder";
    newBtn.textContent = "+";
    newBtn.title = "New folder";
    actions.appendChild(newBtn);
    head.append(lbl, actions);
    box.appendChild(head);

    if (addingFolder) {
      const nf = document.createElement("form");
      nf.className = "bd-new-folder-form";
      const inp = document.createElement("input");
      inp.type = "text";
      inp.maxLength = 40;
      inp.placeholder = "Folder name…";
      inp.setAttribute("aria-label", "Folder name");
      const btn = document.createElement("button");
      btn.type = "submit";
      btn.textContent = "Add";
      nf.append(inp, btn);
      box.appendChild(nf);
    }

    if (folders.length) {
      const pills = document.createElement("div");
      pills.className = "bd-folder-pills";
      for (const f of folders) {
        const p = document.createElement("button");
        p.type = "button";
        p.className = "bd-folder-pill" + (openFolder === f.name ? " bd-active" : "");
        p.dataset.act = "open-folder";
        p.dataset.folderName = f.name;
        const count = (f.items || []).length;
        p.textContent = `${f.name} (${count})`;
        const liveN = (f.items || []).filter((it) => cfg.live_status && cfg.live_status[it.url] === "live").length;
        if (liveN) {
          const d = document.createElement("span");
          d.className = "bd-live-dot bd-is-live";
          p.prepend(d);
          p.title = `${liveN} live now`;
        }
        pills.appendChild(p);
      }
      box.appendChild(pills);
    } else if (!addingFolder) {
      const emptyHint = document.createElement("div");
      emptyHint.className = "bd-folder-empty";
      emptyHint.textContent = "Click ★ on any video or + above to organize folders.";
      box.appendChild(emptyHint);
    }

    if (selectMode) {
      const bar = document.createElement("div");
      bar.className = "bd-select-bar";
      const playSel = document.createElement("button");
      playSel.type = "button";
      playSel.className = "bd-play-sel";
      playSel.dataset.act = "play-selected";
      playSel.disabled = selectedUrls.size === 0;
      playSel.textContent = `Play selected (${selectedUrls.size}/4)`;
      bar.appendChild(playSel);
      if (selectedUrls.size > 0) {
        const clr = document.createElement("button");
        clr.type = "button";
        clr.className = "bd-sec-btn";
        clr.dataset.act = "clear-selected";
        clr.textContent = "Clear";
        bar.appendChild(clr);
      }
      box.appendChild(bar);
    }

    const active = folders.find((f) => f.name === openFolder);
    if (!active) return;
    const body = document.createElement("div");
    body.className = "bd-folder-body";
    const items = active.items || [];
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "bd-folder-empty";
      empty.textContent = "No saved videos yet";
      body.appendChild(empty);
    } else {
      for (const it of items) {
        if (selectMode) {
          const picked = selectedUrls.has(it.url);
          const row = document.createElement("button");
          row.type = "button";
          row.className = "bd-item bd-selectable" + (picked ? " bd-selected" : "");
          row.dataset.act = "toggle-select-item";
          row.dataset.saveUrl = it.url;
          row.disabled = !picked && selectedUrls.size >= 4;
          row.title = it.title || it.url;
          row.textContent = (picked ? "☑ " : "☐ ") + (it.title || shortLink(it.url));
          body.appendChild(row);
        } else {
          const row = document.createElement("div");
          row.className = "bd-recent-row";
          const b = document.createElement("button");
          b.type = "button";
          b.className = "bd-item";
          b.dataset.url = it.url;
          b.textContent = it.title || shortLink(it.url);
          b.title = it.title || it.url;
          const fdot = liveDot(it.url);
          if (fdot) b.prepend(fdot);
          row.appendChild(b);
          addOrSwapButton(row, it.url, it.title);
          if (renaming && renaming.url === it.url && renaming.folder === active.name) {
            // Inline rename: Enter saves, Esc cancels.
            const inp = document.createElement("input");
            inp.type = "text";
            inp.className = "bd-rename-input";
            inp.value = it.title || "";
            inp.placeholder = shortLink(it.url);
            inp.maxLength = 120;
            inp.setAttribute("aria-label", "New name");
            inp.addEventListener("keydown", (e) => {
              e.stopPropagation();
              if (e.key === "Escape") { renaming = null; update(true); }
              if (e.key === "Enter") {
                const title = inp.value.trim();
                if (title) {
                  it.title = title;
                  set({ rename_item: { folder: active.name, url: it.url, title } });
                }
                renaming = null;
                update(true);
              }
            });
            inp.addEventListener("blur", () => { if (renaming) { renaming = null; setTimeout(() => update(true), 0); } });
            b.replaceWith(inp);
            setTimeout(() => { inp.focus(); inp.select(); }, 0);
          } else {
            const ren = document.createElement("button");
            ren.type = "button";
            ren.className = "bd-recent-del";
            ren.dataset.act = "rename-item";
            ren.dataset.folderName = active.name;
            ren.dataset.saveUrl = it.url;
            ren.title = "Rename";
            ren.setAttribute("aria-label", "Rename");
            ren.textContent = "✎";
            row.appendChild(ren);
          }
          const del = document.createElement("button");
          del.type = "button";
          del.className = "bd-recent-del";
          del.dataset.act = "unsave-item";
          del.dataset.folderName = active.name;
          del.dataset.saveUrl = it.url;
          del.title = "Remove from folder";
          del.textContent = "×";
          row.appendChild(del);
          body.appendChild(row);
        }
      }
    }
    const delF = document.createElement("button");
    delF.type = "button";
    delF.className = "bd-del-folder" + (confirmDeleteFolder === active.name ? " bd-danger" : "");
    delF.dataset.act = "delete-folder";
    delF.dataset.folderName = active.name;
    delF.textContent = confirmDeleteFolder === active.name ? `Confirm delete "${active.name}"?` : "Delete folder";
    body.appendChild(delF);
    box.appendChild(body);
  }

  function button(act, icon, shortText, label, disabled) {
    const b = panel.querySelector(`[data-act=${act}]`);
    b.innerHTML = `${svg(icon)}<span class="bd-act-lbl">${shortText}</span>`;
    b.title = label;
    b.setAttribute("aria-label", label);
    b.disabled = !!disabled;
  }

  // Pause/Play look for one video's button: paused (on its own or with everything) shows Play.
  function holdLook(b, s, base) {
    const stopped = isStopped(s);
    const tip = stopped ? (slots.length > 1 ? "Play this video" : "Play")
      : (slots.length > 1 ? "Pause just this video (the others keep playing)" : "Pause");
    b.className = base + (stopped ? " bd-held" : "");
    b.title = tip;
    b.setAttribute("aria-label", tip);
    b.innerHTML = svg(stopped ? "play" : "pause");
  }

  // The break bar's Pause/Play (every video at once: "Pause all" with 2+) and each break tile's own (just that one).
  function syncPause() {
    if (breakPill && cfg) {
      const b = breakPill.querySelector("[data-act=pause-all]");
      const p = !!cfg.paused, all = slots.length > 1 ? " all" : "";
      b.innerHTML = svg(p ? "play" : "pause") + `<span>${p ? "Play" : "Pause"}${all}</span>`;
      b.title = p ? `Play${all}` : (all ? "Pause all videos (each video's own button pauses just that one)" : "Pause");
      b.setAttribute("aria-label", b.title);
    }
    for (const s of slots) {
      const t = s.el && s.el.querySelector(":scope > .bd-tile-hold");
      if (t) holdLook(t, s, "bd-tile-hold");
    }
  }

  function update(force = false) {
    if (!panel) return;
    syncPause();
    if (cfg && cfg.panel) setTimeout(() => askLive(false), 0);  // after the lists below are drawn
    panel.classList.toggle("bd-dark", night());
    const nowEl = panel.querySelector(".bd-now");
    if (!cfg.on) {
      nowEl.textContent = "Off";
    } else if (slots.length > 1) {
      nowEl.textContent = (cfg.paused ? "Paused · " : "") + slots.length + " playing";
    } else {
      const s0 = slots[0];
      nowEl.textContent = cfg.paused ? "Paused" : (s0 && s0.live ? "Live" : "Playing");
    }
    nowEl.classList.toggle("bd-live", !!(cfg.on && !cfg.paused));
    button("pause", cfg.paused ? "play" : "pause", cfg.paused ? "Play" : "Pause", cfg.paused ? "Play" : "Pause", !cfg.on);
    button("picture", cfg.picture === false ? "eyeOff" : "eye", cfg.picture === false ? "Show" : "Hide", cfg.picture === false ? "Show picture" : "Hide picture (keep sound)", !cfg.on);
    const onBreak = !!(cfg.break_info && cfg.break_info.active);
    button("break", "coffee", "Break", onBreak ? "Exit break zone" : "Break zone (pauses timers, watch clearly)", !onBreak && !cfg.on && !cfg.url);
    panel.querySelector(".bd-buttons [data-act=break]").classList.toggle("bd-active", onBreak);
    button("power", "power", cfg.on ? "Off" : "On", cfg.on ? "Turn off" : "Turn on", !cfg.on && !cfg.url);
    panel.querySelector(".bd-buttons [data-act=power]").classList.toggle("bd-active", !!cfg.on);

    const tab = activeTab || (cfg.on ? "controls" : "library");
    for (const tb of panel.querySelectorAll(".bd-nav-btn")) {
      const isAct = tb.dataset.tab === tab;
      tb.classList.toggle("bd-active", isAct);
      tb.setAttribute("aria-selected", isAct ? "true" : "false");
    }
    for (const pane of panel.querySelectorAll(".bd-pane")) {
      pane.hidden = pane.dataset.pane !== tab;
    }

    const addBtn = panel.querySelector("[data-act=add]");
    if (addBtn) addBtn.hidden = !(cfg.on && slots.length >= 1 && slots.length < 4);
    for (const range of panel.querySelectorAll("input[type=range]")) {
      if (range.dataset.key !== "seek" && document.activeElement !== range) range.value = cfg[range.dataset.key];
      const valEl = panel.querySelector(`[data-val="${range.dataset.key}"]`);
      if (valEl && typeof cfg[range.dataset.key] === "number") valEl.textContent = cfg[range.dataset.key] + "%";
    }
    const tt = panel.querySelector("input[data-key=true_transparency]");
    if (tt) tt.checked = !!cfg.true_transparency;
    updateSeek();
    const q = cfg.quality || "auto";
    const f = cfg.fps || "auto";
    for (const pill of panel.querySelectorAll(".bd-pill")) {
      const active = pill.dataset.fps ? pill.dataset.fps === f : pill.dataset.quality === q;
      pill.classList.toggle("bd-active", active);
      pill.setAttribute("aria-pressed", active ? "true" : "false");
    }
    updateHint();
    const slotsTitle = panel.querySelector(".bd-slots-title");
    if (slotsTitle) slotsTitle.textContent = cfg.on && slots.length ? `Now Playing (${slots.length}/4)` : "Now Playing";
    const slotsAddBtn = panel.querySelector(".bd-slots-card .bd-sec-btn");
    if (slotsAddBtn) slotsAddBtn.hidden = !(cfg.on && slots.length < 4);
    const emptyPlay = panel.querySelector(".bd-empty-play");
    if (emptyPlay) emptyPlay.hidden = !!(cfg.on && slots.length >= 1);
    const slotsBox = panel.querySelector(".bd-slots");
    if (force || !slots.some((s) => s.seeking)) {
      slotsBox.textContent = "";
      if (replacing && !(cfg.on && slots.length >= 4)) replacing = null;  // a spot opened up meanwhile
      if (replacing) {
        const ask = document.createElement("div");
        ask.className = "bd-replace-ask";
        const txt = document.createElement("span");
        txt.textContent = "Replace which stream with “" + (replacing.title || shortLink(replacing.url)) + "”?";
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.className = "bd-slot-btn";
        cancel.dataset.act = "replace-cancel";
        cancel.textContent = "Cancel";
        ask.append(txt, cancel);
        slotsBox.appendChild(ask);
      }
      if (cfg.on && slots.length >= 1) {
        for (const s of slots) {
          const seekable = canSlotSeek(s);
          if (!seekable) seekOpenSlots.delete(s.url);
          const isSeekOpen = seekable && seekOpenSlots.has(s.url);
          const row = document.createElement("div");
          row.className = "bd-slot-row" + (isSeekOpen ? " bd-seek-open" : "");
          row.dataset.slotUrl = s.url;
          const top = document.createElement("div");
          top.className = "bd-slot-top";
          let name;
          if (seekable) {
            name = document.createElement("button");
            name.type = "button";
            name.className = "bd-slot-name bd-seekable";
            name.dataset.act = "toggle-slot-seek";
            name.dataset.slotUrl = s.url;
            name.title = isSeekOpen
              ? "Click to close time controls"
              : ((s.title || s.url) + (s.album ? ` — Video ${s.album.index + 1} of ${s.album.count}. Click to pick a video, skip or jump` : " — Click for timestamp & skip controls"));
            const txt = document.createElement("span");
            txt.className = "bd-slot-title-txt";
            txt.textContent = s.title || shortLink(s.url);
            name.appendChild(txt);
            if (!isSeekOpen) {
              const chip = document.createElement("span");
              chip.className = "bd-slot-chip";
              chip.textContent = (s.duration > 0 || s.time > 0) ? fmtTime(s.time) : "⏱";
              name.appendChild(chip);
            }
          } else {
            name = document.createElement("span");
            name.className = "bd-slot-name";
            name.textContent = s.title || shortLink(s.url);
            name.title = s.title || s.url;
          }
          top.appendChild(name);
          if (replacing) {
            // Picking which stream to swap out: one clear button per row, nothing else.
            const rb = document.createElement("button");
            rb.type = "button";
            rb.className = "bd-slot-btn bd-replace-btn";
            rb.dataset.act = "replace-slot";
            rb.dataset.slotUrl = s.url;
            rb.textContent = "Replace";
            rb.title = "Replace " + (s.title || shortLink(s.url));
            top.appendChild(rb);
            row.appendChild(top);
            slotsBox.appendChild(row);
            continue;
          }
          if (isSeekOpen) {
            const backBtn = document.createElement("button");
            backBtn.type = "button";
            backBtn.className = "bd-slot-btn bd-skip-btn";
            backBtn.dataset.act = "skip-slot";
            backBtn.dataset.slotUrl = s.url;
            backBtn.dataset.delta = "-10";
            backBtn.title = "Skip back 10 seconds";
            backBtn.textContent = "-10s";
            const fwdBtn = document.createElement("button");
            fwdBtn.type = "button";
            fwdBtn.className = "bd-slot-btn bd-skip-btn";
            fwdBtn.dataset.act = "skip-slot";
            fwdBtn.dataset.slotUrl = s.url;
            fwdBtn.dataset.delta = "10";
            fwdBtn.title = "Skip forward 10 seconds";
            fwdBtn.textContent = "+10s";
            const closeBtn = document.createElement("button");
            closeBtn.type = "button";
            closeBtn.className = "bd-slot-btn bd-seek-close";
            closeBtn.dataset.act = "toggle-slot-seek";
            closeBtn.dataset.slotUrl = s.url;
            closeBtn.title = "Back to star, mute, and remove";
            closeBtn.setAttribute("aria-label", "Close time controls");
            closeBtn.textContent = "↩";
            top.append(backBtn, fwdBtn, closeBtn);
            row.appendChild(top);
            const seekBar = document.createElement("div");
            seekBar.className = "bd-slot-seek bd-seek";
            const seekHead = document.createElement("div");
            seekHead.className = "bd-seek-head";
            const posLbl = document.createElement("span");
            posLbl.textContent = "Position";
            const timeLbl = document.createElement("span");
            timeLbl.className = "bd-slot-time bd-seek-time";
            timeLbl.textContent = s.duration > 0 ? `${fmtTime(s.time)} / ${fmtTime(s.duration)}` : fmtTime(s.time);
            seekHead.append(posLbl, timeLbl);
            const range = document.createElement("input");
            range.type = "range";
            range.className = "bd-slot-range";
            const max = Math.max(1, Math.floor(s.duration || 100));
            range.min = "0";
            range.max = String(max);
            range.value = String(Math.min(Math.floor(s.time || 0), max));
            range.dataset.slotUrl = s.url;
            range.setAttribute("aria-label", "Video position");
            range.addEventListener("input", () => {
              s.seeking = true;
              timeLbl.textContent = s.duration > 0 ? `${fmtTime(+range.value)} / ${fmtTime(s.duration)}` : fmtTime(+range.value);
            });
            range.addEventListener("change", () => {
              s.seeking = false;
              seekSlotTo(s, +range.value);
              range.blur();
            });
            range.addEventListener("pointerup", () => range.blur());
            // Length unknown (some converted videos): only the ±10 s buttons, no slider that would map to the wrong time.
            if (s.duration > 0) seekBar.append(seekHead, range);
            else seekBar.append(seekHead);
            if (s.album) {
              const pick = document.createElement("div");
              pick.className = "bd-album";
              const mk = (delta, text, tip) => {
                const b = document.createElement("button");
                b.type = "button";
                b.className = "bd-slot-btn bd-skip-btn bd-album-btn";
                b.dataset.act = "album-slot";
                b.dataset.slotUrl = s.url;
                b.dataset.delta = String(delta);
                b.title = tip;
                b.setAttribute("aria-label", tip);
                b.textContent = text;
                return b;
              };
              const lbl = document.createElement("span");
              lbl.className = "bd-album-lbl";
              lbl.textContent = s.albumBusy ? "Loading…" : `Video ${s.album.index + 1} of ${s.album.count}`;
              pick.append(mk(-1, "‹", "Previous video"), lbl, mk(1, "›", "Next video"));
              seekBar.appendChild(pick);
            }
            row.appendChild(seekBar);
          } else {
            const starBtn = document.createElement("button");
            starBtn.type = "button";
            starBtn.className = "bd-slot-btn bd-star" + (isSavedUrl(s.url) ? " bd-saved" : "");
            starBtn.dataset.act = "open-save";
            starBtn.dataset.saveUrl = s.url;
            starBtn.dataset.saveTitle = s.title || "";
            starBtn.title = "Save to folder";
            starBtn.textContent = "★";
            const muteBtn = document.createElement("button");
            muteBtn.type = "button";
            const muted = isMuted(s.url);
            muteBtn.className = "bd-slot-btn bd-slot-ico" + (muted ? " bd-muted" : "");
            muteBtn.dataset.act = "mute-slot";
            muteBtn.dataset.slotUrl = s.url;
            muteBtn.title = muted ? "Unmute stream" : "Mute stream";
            muteBtn.setAttribute("aria-label", muted ? "Unmute stream" : "Mute stream");
            muteBtn.innerHTML = svg(muted ? "volOff" : "volOn");
            const rmBtn = document.createElement("button");
            rmBtn.type = "button";
            rmBtn.className = "bd-slot-btn";
            rmBtn.dataset.act = "remove-slot";
            rmBtn.dataset.slotUrl = s.url;
            rmBtn.title = "Remove stream";
            rmBtn.textContent = "×";
            // Pause/Play this video (with 2+, the others keep playing).
            const holdBtn = document.createElement("button");
            holdBtn.type = "button";
            holdBtn.dataset.act = "hold-slot";
            holdBtn.dataset.slotUrl = s.url;
            holdLook(holdBtn, s, "bd-slot-btn bd-slot-ico");
            top.append(starBtn, holdBtn, muteBtn, rmBtn);
            row.appendChild(top);
            // 2+ streams: a slim volume slider for each (the main Volume sets them all).
            if (slots.length > 1) {
              const vol = document.createElement("input");
              vol.type = "range";
              vol.min = "0";
              vol.max = "100";
              vol.className = "bd-slot-vol";
              vol.value = String(slotLevel(s.url));
              vol.dataset.slotUrl = s.url;
              const label = "Volume for " + (s.title || shortLink(s.url));
              vol.title = label;
              vol.setAttribute("aria-label", label);
              vol.addEventListener("input", () => {
                cfg.volumes = Object.assign({}, cfg.volumes || {}, { [s.url]: +vol.value });
                applySlotVolume(s);
              });
              vol.addEventListener("change", () => { set({ volumes: { [s.url]: +vol.value } }); vol.blur(); });
              vol.addEventListener("pointerup", () => vol.blur());
              row.appendChild(vol);
            }
          }
          slotsBox.appendChild(row);
        }
      }
    }
    updateSeek();
    const activeSet = new Set(slots.map((s) => s.url));
    renderSavePop(force);
    renderFolders(activeSet, force);
    const recent = panel.querySelector(".bd-recent");
    recent.textContent = "";
    const others = (cfg.recent || []).filter((r) => !activeSet.has(r.url)).slice(0, 5);
    recent.hidden = others.length === 0;
    if (others.length) {
      const head = document.createElement("div");
      head.className = "bd-card-head";
      const lbl = document.createElement("span");
      lbl.className = "bd-label";
      lbl.textContent = "Recent";
      head.appendChild(lbl);
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
      const rdot = liveDot(r.url);
      if (rdot) b.prepend(rdot);
      row.appendChild(b);
      addOrSwapButton(row, r.url, r.title);
      const star = document.createElement("button");
      star.type = "button";
      star.className = "bd-recent-del bd-star" + (isSavedUrl(r.url) ? " bd-saved" : "");
      star.dataset.act = "open-save";
      star.dataset.saveUrl = r.url;
      star.dataset.saveTitle = r.title || "";
      star.title = "Save to folder";
      star.textContent = "★";
      const del = document.createElement("button");
      del.type = "button";
      del.className = "bd-recent-del";
      del.dataset.act = "forget-recent";
      del.dataset.recentUrl = r.url;
      del.title = "Remove from recent";
      del.textContent = "×";
      row.append(star, del);
      recent.appendChild(row);
    }
  }

  const shortLink = (url) => (url || "").replace(/^https?:\/\/(www\.)?/, "");

  // Green/red dot for saved streams that are live/offline now. Python looks them up (cached a few minutes)
  // only while the panel is open; links that aren't streams get no dot.
  function liveDot(url) {
    const st = cfg && cfg.live_status && cfg.live_status[url];
    if (st !== "live" && st !== "offline") return null;
    const d = document.createElement("span");
    d.className = "bd-live-dot " + (st === "live" ? "bd-is-live" : "bd-is-off");
    d.title = st === "live" ? "Live now" : "Offline";
    return d;
  }
  let liveAskedAt = 0;
  function askLive(forceNow) {
    if (!cfg || !cfg.panel || !panel) return;
    if (!forceNow && Date.now() - liveAskedAt < 60000) return;
    liveAskedAt = Date.now();
    const urls = [];
    for (const b of panel.querySelectorAll(".bd-recent .bd-item, .bd-folder-body .bd-item")) {
      if (b.dataset.url && !urls.includes(b.dataset.url)) urls.push(b.dataset.url);
    }
    // Then links in closed folders, so their buttons can show "live now" (Python skips ones it checked recently).
    for (const f of cfg.folders || []) {
      for (const it of f.items || []) if (it.url && !urls.includes(it.url)) urls.push(it.url);
    }
    if (urls.length) send("backdrop:livecheck:" + JSON.stringify(urls.slice(0, 60)));
  }
  setInterval(() => askLive(false), 60000);
  function liveStatus(map) {
    if (!cfg || !map || typeof map !== "object") return;
    cfg.live_status = Object.assign({}, cfg.live_status || {}, map);
    update();
  }

  function fmtTime(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    const pad = (n) => (n < 10 ? "0" + n : "" + n);
    return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
  }

  function canSlotSeek(s) {
    if (!s) return false;
    // Another website: its own video, reached through the bridge script (see BRIDGE_JS in __init__.py).
    if (s.kind === "page") return !!(s.bridge && !s.bridge.live && s.duration > 0 && Date.now() - s.bridge.at < 6000);
    if (s.live) return !!s.vod;
    return !s.isLiveStream && (s.kind === "youtube" || s.kind === "video");
  }

  // A converted normal video can't jump inside its stream, so the converter restarts at the new time.
  // Next (1) or previous (-1) video of an album or YouTube playlist.
  function albumStep(s, step) {
    if (!s || !s.album || s.albumBusy || (step !== 1 && step !== -1)) return;
    if (s.kind === "youtube") {
      ytSend(s, { event: "command", func: step > 0 ? "nextVideo" : "previousVideo", args: [] });
      return;
    }
    s.albumBusy = true;  // Python looks the new video up, then rebuilds this slot
    const lbl = panel && panel.querySelector(`.bd-slot-row[data-slot-url="${CSS.escape(s.url)}"] .bd-album-lbl`);
    if (lbl) lbl.textContent = "Loading…";
    send("backdrop:album:" + JSON.stringify({ url: s.url, step }));
  }

  function restartAt(s, sec) {
    holdFrame(s);  // keep the old frame until the new one arrives (no blank flash)
    s.offset = Math.max(0, Math.floor(sec));
    s.time = s.offset;
    const still = cfg.paused || s.held;
    s.media.autoplay = !still;
    s.priming = still;  // paused: show the new frame, then stop the converter
    s.media.src = s.baseSrc + (s.offset ? "?start=" + s.offset : "");
    if (!still) s.media.play().catch(() => {});
    releaseFrame(s);
  }

  function updateSeek() {
    if (!panel) return;
    const slotRows = panel.querySelectorAll(".bd-slot-row");
    for (const r of slotRows) {
      const s = slots.find((x) => x.url === r.dataset.slotUrl);
      if (!s) continue;
      const chip = r.querySelector(".bd-slot-chip");
      if (chip) chip.textContent = (s.duration > 0 || s.time > 0) ? fmtTime(s.time) : "⏱";
      const range = r.querySelector(".bd-slot-range");
      const timeLbl = r.querySelector(".bd-slot-time");
      if (!range && timeLbl && s.duration > 0 && r.classList.contains("bd-seek-open") && !s.seeking) {
        setTimeout(() => update(true), 0);  // the length just became known: draw the slider
        return;
      }
      if (timeLbl && !range && !s.seeking) timeLbl.textContent = fmtTime(s.time);
      if (range && timeLbl && !s.seeking) {
        const max = Math.max(1, Math.floor(s.duration || 100));
        range.max = String(max);
        if (document.activeElement !== range) range.value = String(Math.min(Math.floor(s.time || 0), max));
        timeLbl.textContent = s.duration > 0 ? `${fmtTime(s.time)} / ${fmtTime(s.duration)}` : fmtTime(s.time);
      }
    }
  }

  function seekSlotTo(s, sec) {
    if (!cfg || !canSlotSeek(s)) return;
    const dur = s.duration > 0 ? s.duration : 0;
    sec = Math.max(0, dur ? Math.min(sec, dur) : sec);
    s.time = sec;
    if (slots[0] === s) time = sec;
    if (s.kind === "video" && s.media) s.live ? restartAt(s, sec) : (s.media.currentTime = sec);
    if (s.kind === "youtube") ytSend(s, { event: "command", func: "seekTo", args: [sec, true] });
    if (s.kind === "page" && s.media && s.media.contentWindow) s.media.contentWindow.postMessage({ bdBridge: "seek", t: sec }, "*");
    if (slots[0] === s) send("backdrop:time:" + sec);
    send("backdrop:slot_time:" + JSON.stringify({ url: s.url, time: sec }));
    updateSeek();
  }

  function seekTo(sec) {
    seekSlotTo(slots[0], sec);
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

  let peekHeld = false;  // P is down; kept so a reload or a new card mid-peek stays clear
  function peek(on) {
    peekHeld = !!on;
    if (!cfg || !cfg.on || cfg.picture === false) on = false;
    root.classList.toggle("bd-peek", !!on);
  }

  // Python handles Hold-P even when another website's frame has the keyboard (its keys never reach this page),
  // so it needs to know where focus is: in a text box (P types), in a backdrop frame, or on the card.
  let focusKind = null;  // null: not reported yet, so a reloaded page always tells Python once
  function reportFocus() {
    let a = document.activeElement;
    if (a && a.tagName === "IFRAME" && a.closest("#bd-layer")) {
      // A backdrop website grabbed the keyboard (sites can't be clicked, so it focused itself): hand it back to the card,
      // so Space, the answer keys and P keep working. Python still catches P if this ever fails.
      try { a.blur(); window.focus(); } catch (_) {}
      a = document.activeElement;
    }
    const k = isEditable(a) ? "typing" : (a && a.tagName === "IFRAME" && a.closest("#bd-layer") ? "frame" : "");
    if (k !== focusKind) {
      focusKind = k;
      send("backdrop:focus:" + k);
    }
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
    closePanel();
    set({ panel: false });
  }, true);
  // Focus moving into a frame on this page also blurs the window; only a real switch away ends the peek.
  window.addEventListener("blur", () => setTimeout(() => {
    if (!document.hasFocus()) peek(false);
    reportFocus();
  }, 0));
  document.addEventListener("focusin", reportFocus, true);
  setTimeout(reportFocus, 0);
  document.addEventListener("focusout", () => setTimeout(reportFocus, 0), true);
  window.addEventListener("pagehide", stop);
  window.addEventListener("focus", () => { if (cfg && cfg.panel) send("backdrop:clip"); });

  function closePanel() {
    if (!cfg) return;
    cfg.panel = false;
    savingItem = null;
    addingFolder = false;
    confirmDeleteFolder = null;
    root.classList.remove("bd-panel-open");
  }

  // During a break with 2+ streams: click a stream to fill the screen with it (the others pause and go quiet),
  // click again to go back; drag one onto another to swap their places.
  let zoomed = null;  // url of the stream filling the screen
  const zoomPaused = new Set();  // streams the zoom paused (not ones you paused yourself)

  function breakTiles(active) {
    for (const u of Array.from(zoomPaused)) if (!slots.some((x) => x.url === u)) zoomPaused.delete(u);
    if (zoomed && !slots.some((x) => x.url === zoomed)) {
      zoomed = null;
      if (layer) layer.classList.remove("bd-has-zoom");
    }
    const on = active && slots.length > 1;
    if (!on && zoomed) setZoom(null);
    for (const s of slots) {
      let hit = s.el.querySelector(":scope > .bd-hit");
      let hold = s.el.querySelector(":scope > .bd-tile-hold");
      if (!active) { if (hold) hold.remove(); }
      else if (!hold) {
        hold = document.createElement("button");
        hold.type = "button";
        hold.className = "bd-tile-hold";
        // Its own button above the tile: pressing it never zooms or drags, and the card keeps the keyboard.
        hold.addEventListener("pointerdown", (e) => { e.stopPropagation(); e.preventDefault(); });
        hold.addEventListener("click", (e) => { e.stopPropagation(); toggleOne(s); });
        s.el.appendChild(hold);
      }
      if (!on) { if (hit) hit.remove(); continue; }
      if (!hit) {
        hit = document.createElement("div");
        hit.className = "bd-hit";
        // Mouse tracking, not the browser's drag-and-drop: Anki's card window refuses drops, so those never land.
        hit.addEventListener("pointerdown", (e) => tilePointerDown(e, s, hit));
        s.el.appendChild(hit);
      }
      hit.title = zoomed === s.url ? "Click to show all streams again" : "Click to fill the screen with this stream · drag onto another to swap places";
    }
    syncPause();
  }

  // Press a tile: a plain click fills the screen with it; moving it a little picks it up, and letting go over another
  // tile swaps the two at once (outlined in blue while you're over it).
  let tileDrag = null;  // {slot, x, y, moved, target}
  const tileAt = (x, y, except) => slots.find((t) => t !== except && t.el && t.el.style.display !== "none" && (() => {
    const r = t.el.getBoundingClientRect();
    return r.width > 0 && x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
  })());
  function markDrop(target) {
    for (const t of slots) {
      const h = t.el && t.el.querySelector(":scope > .bd-hit");
      if (h) h.classList.toggle("bd-drop", t === target);
    }
  }
  function endTileDrag() {
    if (!tileDrag) return;
    if (tileDrag.slot.el) tileDrag.slot.el.classList.remove("bd-lifted");
    if (layer) layer.classList.remove("bd-dragging");
    markDrop(null);
    tileDrag = null;
  }
  function tilePointerDown(e, s, hit) {
    if (e.button !== 0) return;
    e.preventDefault();
    try { hit.setPointerCapture(e.pointerId); } catch (_) {}
    tileDrag = { slot: s, x: e.clientX, y: e.clientY, moved: false, target: null };
    const move = (ev) => {
      if (!tileDrag) return;
      if (!tileDrag.moved && Math.hypot(ev.clientX - tileDrag.x, ev.clientY - tileDrag.y) < 6) return;
      if (zoomed) return;  // one stream fills the screen: nothing to swap with
      if (!tileDrag.moved) {
        tileDrag.moved = true;
        s.el.classList.add("bd-lifted");
        layer && layer.classList.add("bd-dragging");
      }
      tileDrag.target = tileAt(ev.clientX, ev.clientY, s) || null;
      markDrop(tileDrag.target);
    };
    const up = (ev) => {
      hit.removeEventListener("pointermove", move);
      hit.removeEventListener("pointerup", up);
      hit.removeEventListener("pointercancel", up);
      const d = tileDrag;
      endTileDrag();
      if (!d || ev.type === "pointercancel") return;
      if (!d.moved) { setZoom(zoomed === s.url ? null : s.url); return; }
      if (d.target) swapTiles(s.url, d.target.url);
    };
    hit.addEventListener("pointermove", move);
    hit.addEventListener("pointerup", up);
    hit.addEventListener("pointercancel", up);
  }
  function swapTiles(a, b) {
    const urls = slots.map((x) => x.url);
    const i = urls.indexOf(a), j = urls.indexOf(b);
    if (i < 0 || j < 0 || i === j) return;
    [urls[i], urls[j]] = [urls[j], urls[i]];
    // Move them on screen right away (no frame reloads: only the CSS position changes), then save the order.
    slots.sort((x, y) => urls.indexOf(x.url) - urls.indexOf(y.url));
    slots.forEach((x, k) => { x.el.style.order = String(k); x.el.dataset.pos = String(k + 1); });
    if (cfg) { cfg.urls = urls.slice(); cfg.url = urls[0]; }
    set({ order: urls });
  }

  function setZoom(url) {
    zoomed = url && slots.some((s) => s.url === url) ? url : null;
    layer && layer.classList.toggle("bd-has-zoom", !!zoomed);
    for (const s of slots) {
      const big = s.url === zoomed;
      s.el.classList.toggle("bd-zoom", big);
      if (zoomed && !big && !s.held && !cfg.paused && !zoomPaused.has(s.url)) {
        zoomPaused.add(s.url);
        pauseSlot(s);
      } else if ((!zoomed || big) && zoomPaused.has(s.url)) {
        zoomPaused.delete(s.url);
        if (!s.held && !cfg.paused) resumeSlot(s);
      }
      applySlotVolume(s);
    }
    breakTiles(!!(cfg && cfg.break_info && cfg.break_info.active && cfg.on));
  }

  // On a break, once the mouse rests for a moment the pause buttons hide and the bar shrinks to just its timer;
  // any mouse movement (over a website video too, via the bridge) brings them back.
  const IDLE_MS = 1500;
  let idleTimer = null, overPill = false, lastWake = 0;
  function wake() {
    if (!root.classList.contains("bd-break")) return;
    const now = Date.now();
    if (now - lastWake < 150 && !root.classList.contains("bd-idle")) return;  // mouse moves fire very often
    lastWake = now;
    root.classList.remove("bd-idle");
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      if (overPill) return;
      if (root.classList.contains("bd-break")) root.classList.add("bd-idle");
    }, IDLE_MS);
  }
  for (const ev of ["pointermove", "pointerdown", "wheel", "keydown"]) document.addEventListener(ev, wake, { capture: true, passive: true });
  window.addEventListener("message", (e) => {
    const m = e.data;
    if (m && typeof m === "object" && m.bdBridge === "activity" &&
        slots.some((x) => x.media && x.media.contentWindow && fromFrame(e.source, x.media.contentWindow))) wake();
  });

  function updateBreak(info) {
    if (cfg) cfg.break_info = info || null;
    const active = !!(info && info.active && cfg && cfg.on);
    const was = root.classList.contains("bd-break");
    root.classList.toggle("bd-break", active);
    if (active && !was) { lastWake = 0; overPill = false; wake(); }
    if (!active) { clearTimeout(idleTimer); root.classList.remove("bd-idle"); }
    breakTiles(active);
    if (!breakPill) return;
    const txt = breakPill.querySelector(".bd-break-text");
    if (!active) {
      breakPill.classList.remove("bd-rest-done");
      return;
    }
    const lens = breakPill.querySelector(".bd-break-len");
    const pomodoro = typeof info.rest_left === "number" || !!info.rest_done;
    lens.hidden = pomodoro;  // a Pomodoro add-on's rest timer is shown instead
    for (const b of lens.querySelectorAll(".bd-len")) b.classList.toggle("bd-on", +b.dataset.min === (info.minutes || 0));
    const over = !!info.rest_done || (!pomodoro && info.minutes > 0 && info.left === 0);
    breakPill.classList.toggle("bd-rest-done", over);
    const counting = !pomodoro && info.minutes > 0;
    breakPill.classList.toggle("bd-counting", counting);
    breakPill.querySelector(".bd-break-bar > span").style.width =
      counting ? Math.max(0, Math.min(100, (info.left / (info.minutes * 60)) * 100)).toFixed(1) + "%" : "0%";
    if (info.rest_done) {
      txt.textContent = "Rest complete!";
    } else if (typeof info.rest_left === "number") {
      txt.textContent = "Break · Rest " + fmtTime(info.rest_left);
    } else if (info.minutes > 0) {
      txt.textContent = over ? "Break's over" : fmtTime(info.left) + " left";
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

  return { start, style, setVolume, duck, offer, peek, liveStatus, closePanel, updateBreak, togglePanel: () => panel && panel.querySelector(".bd-tab").click() };
})();
