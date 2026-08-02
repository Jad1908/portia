// Moving around the workflow canvas: drag to pan, the wheel or the buttons to zoom.
//
// Panning and zooming only — nodes do not move. The graph's layout is the
// recorded sequence of decisions (portia/ui/graph.py), so dragging a card would
// either mean nothing or imply the order can be rearranged, and neither is true.
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
    ".step-card, .source-node, .model-card, button, input, textarea, a, .q-expansion-item";

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

  // Where the canvas is looking. The single source of truth for it.
  const view = { x: 0, y: 0, zoom: 1 };

  let dragging = null;
  let startX = 0;
  let startY = 0;
  let baseX = 0;
  let baseY = 0;

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

  // NiceGUI rebuilds the pane rather than patching it, so the canvas you are
  // looking at is rarely the one you last panned. Re-apply to whatever arrived,
  // and honour a focus request the same way — see `focusMarked`.
  new MutationObserver(() => {
    if (document.querySelector(".graph-canvas:not([data-portia-view])")) apply();
    focusMarked();
  }).observe(document.documentElement, { childList: true, subtree: true });

  // Zoom about a point, keeping whatever is under it exactly where it is.
  // Without this, zooming walks the thing you were looking at off the screen.
  const zoomAt = (factor, cx, cy) => {
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
  window.portiaRecenter = () => set(0, 0, 1);

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
    lastFocus = token;
    const content = document.querySelector(".graph-content");
    const padX = content ? content.offsetLeft : 0;
    const padY = content ? content.offsetTop : 0;
    const x = parseFloat(node.style.left) || 0;
    const y = parseFloat(node.style.top) || 0;
    set(FOCUS_INSET - padX - x * view.zoom, FOCUS_INSET - padY - y * view.zoom, view.zoom);
  };

  // --- dragging --------------------------------------------------------------

  document.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    const found = canvasOf(e.target);
    if (!found || e.target.closest(IGNORE)) return;
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
