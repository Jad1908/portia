// Moving around the workflow canvas: drag to pan, the wheel or the buttons to
// zoom, and drag a card to put it somewhere else.
//
// **Cards move now** *(2026-09-07)*. This file used to say they never would,
// because the layout is derived from what the specs read and a card you can
// drag looks like an order you can rearrange. The argument failed on use: a
// reader arranging a canvas is not reordering anything — the arrows still
// leave the same card and arrive at the same card, `graph.py` still derives
// the grid, and the drag only adds an offset on top of it (`graph.moved`).
// What the reader owns is where the cards sit on the surface; the derived
// order is one Reset away.
//
// The drag is split across the two sides the way every gesture here is. This
// file moves the card and the arrows touching it for the length of the drag,
// so nothing reaches the server per pointer event; the drop emits one
// `portia:card-move` with the delta in layout pixels, and the next render
// draws the card there (`workflow.move_card`). Between the drop and that
// render the card stays where it was left, because NiceGUI does not touch the
// DOM until the patch arrives.
//
// **One gesture each.** Two fingers up and down zooms; click, hold and drag
// moves. Neither needs a modifier, and neither does the other one's job.
//
// **It moves a transform, not a scroll offset.** The first version scrolled the
// canvas, which meant a graph that fitted its pane had nothing to scroll and
// dragging it did precisely nothing — panning only ever worked on a graph big
// enough to overflow, which is the one case you least need it. Translating the
// content works at every size, and it is what lets the dot grid travel with the
// nodes: `--pan-x`, `--pan-y` and `--zoom` drive the content's `transform` and
// the canvas's `background-position` / `background-size` together, so the grid
// cannot drift out of step with what it sits behind.
//
// **The view lives in this module, not on the element and not on the server.**
// Two reasons, and the second one was a bug. A wheel gesture fires continuously,
// so a round trip per tick would make the only directly-manipulated surface the
// laggiest thing in the window. And NiceGUI *replaces* the canvas element on
// every pane refresh — so state kept in its inline style was silently thrown
// away every time you opened a model card, which is exactly the moment you most
// want to keep your place. A MutationObserver re-applies the view to whatever
// canvas is on screen now.
//
// Delegated from the document so it survives every pane refresh without being
// re-attached, and `preventDefault` on mousedown stops a drag from turning into
// a text selection across the whole report.
(() => {
  if (window.__portiaCanvas) return;
  window.__portiaCanvas = true;

  // Anything you can click is not a handle to drag the canvas by.
  const IGNORE =
    ".source-node, .model-card, .graph-hit, button, input, textarea, a, .q-expansion-item";
  // And a control inside a card is not a handle to drag the card by either.
  const CARD_IGNORE = "button, input, textarea, a";
  // How far the pointer travels before a press on a card is a drag rather
  // than a click. Four pixels: a click that wobbles stays a click, and a drag
  // that starts is already visibly a drag.
  const DRAG_SLOP = 4;
  // `graph.ARROW`, and the bezier's minimum bend — `Edge.path` and
  // `Edge.arrowhead` in `ui/graph.py`, spelled a second time so an arrow can
  // follow a card mid-drag. The drop re-renders from Python; this is for the
  // frames in between.
  const ARROW = 6;
  const MIN_BEND = 24;

  // Far enough out to see a twenty-model project whole, far enough in to read a
  // step card on a dense one. Past either the graph stops being more useful and
  // starts being less legible.
  const MIN_ZOOM = 0.35;
  const MAX_ZOOM = 2.5;
  // One press of + or −. A quarter each way, so three presses roughly halve or
  // double — a rate you can aim with rather than overshoot.
  const BUTTON_STEP = 1.25;
  // Wheel delta to zoom factor. Exponential, so the gesture feels the same at
  // every zoom level. One rate for every source of wheel events — a trackpad
  // swipe, a pinch, a mouse notch — because they differ in how *often* they fire
  // far more than in how much they report, and a rate per device is a thing that
  // goes wrong quietly on hardware nobody tested on.
  const ZOOM_RATE = 0.002;
  // Where a focused card lands: this far in from the canvas corner rather than
  // flush against it, so its incoming edges stay visible.
  const FOCUS_INSET = 48;
  // How long the canvas takes to travel to a focused card. Long enough to read as
  // movement — which is the whole point, since the alternative is arriving
  // somewhere with no idea where you came from — and short enough not to be
  // something you wait through. Kept in step with `portia.css`'s transition.
  const GLIDE_MS = 320;

  // Where the canvas is looking. The single source of truth for it.
  const view = { x: 0, y: 0, zoom: 1 };

  let dragging = null;
  let startX = 0;
  let startY = 0;
  let baseX = 0;
  let baseY = 0;

  // A press on a card. `card` is set on mousedown; `moving` once it has
  // travelled past `DRAG_SLOP` and the press stopped being a click.
  let card = null;
  let moving = false;
  let cardBase = { x: 0, y: 0 };
  let swallowClick = false;
  // Whether the card is currently over another one. **Two cards never stack**:
  // it may cross another, and a drop there puts it back where it started
  // (`graph.overlapping` is the server's word on it; this is the same
  // rectangle test so the card can say so *before* the drop).
  let blocked = false;

  const clamp = (z) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));

  const paint = (el) => {
    el.style.setProperty("--pan-x", `${view.x}px`);
    el.style.setProperty("--pan-y", `${view.y}px`);
    el.style.setProperty("--zoom", `${view.zoom}`);
    el.dataset.portiaView = "1";
  };

  const apply = () => {
    document.querySelectorAll(".graph-canvas").forEach(paint);
    // The percentage is a view of client state, so the client writes it. It is
    // the one label in this app Python does not own, and it is called out here
    // because that is otherwise a surprise to whoever goes looking for it.
    document.querySelectorAll(".zoom-level").forEach((out) => {
      out.textContent = `${Math.round(view.zoom * 100)}%`;
    });
  };

  const set = (x, y, zoom) => {
    view.x = x;
    view.y = y;
    view.zoom = zoom;
    apply();
  };

  // Move there rather than jump there.
  //
  // **Tweened here, not with a CSS transition**, and that is forced rather than
  // preferred: NiceGUI *replaces* the canvas element on every pane refresh, and
  // picking a spec refreshes the pane. A transition lives on an element, so the
  // element carrying it is destroyed mid-animation and the new one has no
  // previous value to travel from — it would animate from the property's initial
  // value, i.e. from the origin, every time. Tweening the view object instead
  // means the animation survives the rebuild, because `apply` paints whatever
  // canvas is on screen *now* (which is the same reason the observer exists).
  //
  // The per-frame cost is three `setProperty` calls — the same work a mousemove
  // does while dragging, which runs for as long as you hold the button. Both feed
  // a `transform` and a `background-position`, so the compositor does the rest;
  // nothing here touches layout and nothing reaches the server.
  let glide = null;

  const stopGlide = () => {
    if (glide !== null) cancelAnimationFrame(glide);
    glide = null;
  };

  // Ease-out cubic: the useful half of a navigation is the arrival.
  const ease = (t) => 1 - Math.pow(1 - t, 3);

  const glideTo = (x, y, zoom) => {
    stopGlide();
    if (prefersReducedMotion()) {
      set(x, y, zoom);
      return;
    }
    const from = { ...view };
    const t0 = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - t0) / GLIDE_MS);
      const k = ease(t);
      set(
        from.x + (x - from.x) * k,
        from.y + (y - from.y) * k,
        from.zoom + (zoom - from.zoom) * k,
      );
      glide = t < 1 ? requestAnimationFrame(step) : null;
    };
    glide = requestAnimationFrame(step);
  };

  const prefersReducedMotion = () =>
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // NiceGUI rebuilds the pane rather than patching it, so the canvas you are
  // looking at is rarely the one you last panned. Re-apply to whatever arrived,
  // and honour a focus request the same way — see `focusMarked`.
  // One pass per frame rather than per mutation (`scroll.js`): `focusMarked`
  // reads a node's offsets, which is a layout, and NiceGUI patches a pane as
  // many mutations.
  let queued = false;
  new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      if (document.querySelector(".graph-canvas:not([data-portia-view])")) apply();
      focusMarked();
    });
  }).observe(document.documentElement, { childList: true, subtree: true });

  // Zoom about a point, keeping whatever is under it exactly where it is.
  // Without this, zooming walks the thing you were looking at off the screen.
  const zoomAt = (factor, cx, cy) => {
    stopGlide();
    const to = clamp(view.zoom * factor);
    if (to === view.zoom) return;
    const ratio = to / view.zoom;
    set(cx - (cx - view.x) * ratio, cy - (cy - view.y) * ratio, to);
  };

  const canvasOf = (node) => node.closest(".graph-canvas");

  // --- the buttons, and the way back ----------------------------------------

  window.portiaZoomBy = (factor) => {
    const el = document.querySelector(".graph-canvas");
    if (!el) return;
    const box = el.getBoundingClientRect();
    zoomAt(factor, box.width / 2, box.height / 2);
  };

  window.portiaZoomIn = () => window.portiaZoomBy(BUTTON_STEP);
  window.portiaZoomOut = () => window.portiaZoomBy(1 / BUTTON_STEP);

  // You can pan a long way and zoom a long way from a small graph, so one action
  // undoes both. Double-clicking the canvas does the same thing.
  window.portiaRecenter = () => glideTo(0, 0, 1);

  // Bring a focused node into view. Picking a spec on the left focuses its card
  // here, so the left panel navigates the graph rather than replacing it.
  //
  // **Python marks the node and stamps a token; this does the arithmetic.** The
  // first version had the render call a `portiaFocus(x, y)` function, and it
  // never once worked: the JS raced the DOM patch and landed on the canvas that
  // was about to be thrown away, so the pan was silently unchanged. Stating it in
  // the DOM removes the ordering question — the mark and the node arrive
  // together, and the observer already watching for a new canvas handles it. The
  // token is what makes a repeated render harmless rather than a second jump.
  //
  // The node's coordinates are read off its own inline `left`/`top`, which are
  // layout pixels and know nothing about zoom, so the scaling happens here where
  // the zoom level lives. Two things are deliberately *not* scaled: the inset,
  // which is a margin on screen rather than a distance in the graph, and the
  // content's offset inside the canvas, which is padding the transform never
  // touches. Zoom is left alone — you asked to look at a different table, not to
  // change how close you are standing to it.
  let lastFocus = null;

  const focusMarked = () => {
    const node = document.querySelector(".graph-node--focus");
    if (!node) return;
    // One move per request, however many times the pane redraws it. The server
    // bumps the token when you actually ask to go somewhere; a rebuild carrying
    // the same token is the same request arriving again, not a new one.
    const token = node.getAttribute("data-focus-token");
    if (!token || token === lastFocus) return;
    // Captured *before* `lastFocus` is updated: whether this is the first focus
    // of the session is what decides jump-or-glide, and reading it afterwards
    // made the answer always "not the first".
    const first = lastFocus === null;
    lastFocus = token;
    const content = document.querySelector(".graph-content");
    const padX = content ? content.offsetLeft : 0;
    const padY = content ? content.offsetTop : 0;
    const x = parseFloat(node.style.left) || 0;
    const y = parseFloat(node.style.top) || 0;
    // **Glide on a repeat visit, jump on the first.** Landing on a card is a
    // navigation and worth animating; the very first paint of a canvas has
    // nowhere to travel *from*, and animating out of the default position reads
    // as the graph sliding into place for no reason.
    const go = first ? set : glideTo;
    go(FOCUS_INSET - padX - x * view.zoom, FOCUS_INSET - padY - y * view.zoom, view.zoom);
  };

  // --- dragging a card -------------------------------------------------------
  //
  // The card's `left`/`top` are layout pixels written by Python; the pointer
  // moves in screen pixels, so the delta is divided by the zoom. Reading the
  // card back by name on every move rather than holding the element: the pane
  // can refresh mid-drag (a running exchange syncing the artifact panes), and
  // the element you pressed is then a detached node while its replacement sits
  // where you left it.
  const cardEl = (name) =>
    document.querySelector(`.graph-canvas .graph-node[data-node="${CSS.escape(name)}"]`);

  const box = (el) => ({
    x: parseFloat(el.style.left) || 0,
    y: parseFloat(el.style.top) || 0,
    w: parseFloat(el.style.width) || el.offsetWidth,
    h: parseFloat(el.style.height) || el.offsetHeight,
  });

  // `Edge.path` and `Edge.arrowhead`, for the arrows touching a moving card.
  const edgePath = (a, b) => {
    const x1 = a.x + a.w;
    const y1 = a.y + Math.floor(a.h / 2);
    const x2 = b.x;
    const y2 = b.y + Math.floor(b.h / 2);
    const bend = Math.max(MIN_BEND, Math.floor((x2 - x1) / 2));
    const d = `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2 - ARROW} ${y2}`;
    const half = Math.floor(ARROW / 2) + 1;
    const head = `${x2},${y2} ${x2 - ARROW},${y2 - half} ${x2 - ARROW},${y2 + half}`;
    return { d, head };
  };

  const followEdges = (name) => {
    const svg = document.querySelector(".graph-canvas .graph-edges");
    if (!svg) return;
    svg.querySelectorAll("g[data-src], g[data-dst]").forEach((g) => {
      const src = g.dataset.src;
      const dst = g.dataset.dst;
      if (src !== name && dst !== name) return;
      const a = cardEl(src);
      const b = cardEl(dst);
      if (!a || !b) return;
      const { d, head } = edgePath(box(a), box(b));
      g.querySelectorAll("path").forEach((path) => path.setAttribute("d", d));
      const poly = g.querySelector("polygon");
      if (poly) poly.setAttribute("points", head);
    });
  };

  const overlapsAnother = (name) => {
    const me = cardEl(name);
    if (!me) return false;
    const a = box(me);
    return [...document.querySelectorAll(".graph-canvas .graph-node[data-node]")].some((el) => {
      if (el === me) return false;
      const b = box(el);
      return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
    });
  };

  document.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    const node = e.target.closest(".graph-canvas .graph-node[data-node]");
    // A swallow left over from a drop nothing clicked after (the button came
    // up outside the window) must not eat the next real click.
    swallowClick = false;
    if (!node || node.hasAttribute("data-ghost") || e.target.closest(CARD_IGNORE)) return;
    card = node.dataset.node;
    moving = false;
    startX = e.clientX;
    startY = e.clientY;
    const at = box(node);
    cardBase = { x: at.x, y: at.y };
    // No `preventDefault` yet: a press that turns out to be a click must keep
    // behaving like one. Selection is stopped once the drag is real, in CSS,
    // by the class the canvas takes.
  });

  document.addEventListener("mousemove", (e) => {
    if (card === null) return;
    const dx = e.clientX - startX;
    const dy = e.clientY - startY;
    if (!moving) {
      if (Math.abs(dx) < DRAG_SLOP && Math.abs(dy) < DRAG_SLOP) return;
      moving = true;
      stopGlide();
      const found = document.querySelector(".graph-canvas");
      if (found) found.classList.add("is-moving-card");
    }
    const node = cardEl(card);
    if (!node) return;
    node.classList.add("graph-node--moving");
    node.style.left = `${cardBase.x + dx / view.zoom}px`;
    node.style.top = `${cardBase.y + dy / view.zoom}px`;
    followEdges(card);
    blocked = overlapsAnother(card);
    node.classList.toggle("graph-node--blocked", blocked);
    e.preventDefault();
  });

  const dropCard = (e) => {
    if (card === null) return;
    const name = card;
    const was = moving;
    card = null;
    moving = false;
    document.querySelectorAll(".graph-canvas.is-moving-card").forEach((el) => {
      el.classList.remove("is-moving-card");
    });
    document.querySelectorAll(".graph-node--moving").forEach((el) => {
      el.classList.remove("graph-node--moving");
    });
    if (!was) return;
    // The click that follows a mouseup is about the card's head or a row in
    // it, and a drop is not a request to open anything.
    swallowClick = true;
    const node = cardEl(name);
    if (node) node.classList.remove("graph-node--blocked");
    if (blocked) {
      // Dropped on another card: back where it came from, arrows and all,
      // and the server never hears about it.
      blocked = false;
      if (node) {
        node.style.left = `${cardBase.x}px`;
        node.style.top = `${cardBase.y}px`;
        followEdges(name);
      }
      return;
    }
    const dx = Math.round((e.clientX - startX) / view.zoom);
    const dy = Math.round((e.clientY - startY) / view.zoom);
    if ((dx || dy) && typeof emitEvent === "function") {
      emitEvent("portia:card-move", { id: name, dx, dy });
    }
  };
  document.addEventListener("mouseup", dropCard);

  document.addEventListener(
    "click",
    (e) => {
      if (!swallowClick) return;
      swallowClick = false;
      e.stopPropagation();
      e.preventDefault();
    },
    true,
  );

  // --- dragging --------------------------------------------------------------

  document.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    const found = canvasOf(e.target);
    if (!found || e.target.closest(IGNORE)) return;
    stopGlide();  // the pointer wins over an animation it interrupted
    dragging = found;
    startX = e.clientX;
    startY = e.clientY;
    baseX = view.x;
    baseY = view.y;
    found.classList.add("is-panning");
    e.preventDefault();
  });

  document.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    set(baseX + (e.clientX - startX), baseY + (e.clientY - startY), view.zoom);
  });

  const stop = () => {
    if (dragging) dragging.classList.remove("is-panning");
    dragging = null;
  };
  document.addEventListener("mouseup", stop);
  document.addEventListener("mouseleave", stop);

  document.addEventListener("dblclick", (e) => {
    if (canvasOf(e.target) && !e.target.closest(IGNORE)) window.portiaRecenter();
  });

  // --- clicking an arrow ------------------------------------------------------
  //
  // An arrow says one table reads another; clicking it asks *which columns*. The
  // visible line is a 1px hairline and stays one — DESIGN.md's edges are quiet —
  // so the thing you actually hit is an invisible fat stroke drawn over it,
  // carrying `data-edge` (see `workflow._edges_svg`). Widening the line itself to
  // make it clickable would have made every arrow shout.
  //
  // Delegated like everything else here, so it survives a pane refresh, and it
  // only fires on a genuine click: `dragging` is still set while a pan is in
  // flight, and a pan that happens to end over an edge is not a request to open
  // it.
  document.addEventListener("click", (e) => {
    const hit = e.target.closest(".graph-hit");
    if (!hit || dragging) return;
    e.stopPropagation();
    if (typeof emitEvent === "function") emitEvent("portia:edge", hit.dataset.edge);
  });

  // --- the wheel -------------------------------------------------------------
  //
  // **The wheel zooms; it never moves the canvas.** Two fingers up and down is
  // the zoom gesture, and dragging is the only way to move around — so the two
  // things you can do to the canvas are one gesture each, rather than one gesture
  // that does different things depending on whether a modifier happened to be
  // held. A pinch arrives as a wheel event with `ctrlKey` on macOS and lands here
  // too, which makes it the same gesture rather than a second one to learn.
  //
  // An earlier version scrolled to pan and pinched to zoom, following the
  // whiteboard apps. On a graph you are mostly reading rather than arranging, the
  // thing you reach for constantly is scale, and having to remember a modifier
  // for it put the common action behind the rare one.
  //
  // `passive: false` so `preventDefault` holds — without it the page scrolls
  // behind the canvas, or the browser zooms the whole window.
  document.addEventListener(
    "wheel",
    (e) => {
      const found = canvasOf(e.target);
      if (!found) return;
      e.preventDefault();
      const box = found.getBoundingClientRect();
      zoomAt(Math.exp(-e.deltaY * ZOOM_RATE), e.clientX - box.left, e.clientY - box.top);
    },
    { passive: false },
  );
})();
