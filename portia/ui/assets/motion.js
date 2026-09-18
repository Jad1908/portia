// Animating what *arrives*, and only what arrives.
//
// The server cannot ask for an entry animation, and DESIGN.md used to forbid
// them for exactly that reason: NiceGUI *replaces* a refreshable's elements
// rather than patching them, so from the server's side every streamed event is
// everything on the pane arriving at once — a CSS animation on ".new" rows
// would re-fire on every row on every event, which is a flicker, not an
// entrance.
//
// **New versus rebuilt is therefore the client's question**, the same finding
// as `pick.js` (a gesture cannot be reconstructed where the elements don't
// survive) and `scroll.js` (an offset must be keyed to the thing, not the
// element). The server states a key — `data-enter`, via `components.enters` /
// `enter_slot` — naming the *thing*: a tool call's SDK id, a figure's path, a
// table's name. This file remembers every key it has seen; an element whose key
// is new animates in once, and an element rebuilt under a key already seen
// stays exactly still.
//
// Keys are remembered for the life of the page and removal forgets nothing.
// That is deliberate twice over: a pane you close and reopen *restores* rather
// than arrives, and forgetting-on-removal would have to tell a real removal
// from the delete half of a rebuild — a race the one-batch render rule mostly
// prevents and this file should not depend on.
//
// Three rules keep it quiet:
//
//   * **A bulk render never animates.** Opening a replayed log, switching a
//     tab, the first paint of a project — dozens of unseen keys in one frame is
//     a pane arriving, not things happening, and a cascade over it is noise.
//     The keys are marked seen and nothing moves.
//   * **A small batch staggers.** The model asks for several tools in one
//     message; a few rows arriving 45ms apart read as a sequence, which is what
//     they are. The stagger is arrival order — document order — and nothing
//     else (kind, never rank: every entrance is the same entrance).
//   * **The animation is portia.css's `p-enter` and nothing here measures.**
//     This file decides *whether*, the stylesheet decides *what*, and
//     `prefers-reduced-motion` turns the what into nothing without this file
//     knowing.
(() => {
  if (window.__portiaMotion) return;
  window.__portiaMotion = true;

  // Kept in step with `p-enter` in portia.css — the timeout that removes the
  // class has to outlast the animation it is waiting for.
  const DURATION_MS = 220;
  const STAGGER_MS = 45;
  // More unseen keys than this in one frame is a pane arriving whole.
  const BULK = 12;
  // Opening the window is a restore, not an arrival, and the bulk rule alone
  // does not catch it: NiceGUI mounts the first render over the websocket in a
  // few small batches, each under BULK — measured at 8 + 4 on a small project —
  // so a reload played an entrance cascade over content that was there when the
  // window closed. For this long after load, keys are learned and nothing moves.
  const SETTLE_MS = 1500;
  const born = Date.now();

  const seen = new Set();

  const sweep = () => {
    const fresh = [];
    document.querySelectorAll("[data-enter]").forEach((el) => {
      const key = el.getAttribute("data-enter");
      if (!key || seen.has(key)) return;
      seen.add(key);
      fresh.push(el);
    });
    if (!fresh.length || fresh.length > BULK) return;
    if (Date.now() - born < SETTLE_MS) return;
    fresh.forEach((el, i) => {
      el.style.setProperty("--enter-delay", `${i * STAGGER_MS}ms`);
      el.classList.add("p-entering");
      // A timeout rather than `animationend`: with reduced motion there is no
      // animation and no end event, and the class would sit on the element
      // waiting for one.
      setTimeout(() => {
        el.classList.remove("p-entering");
        el.style.removeProperty("--enter-delay");
      }, DURATION_MS + i * STAGGER_MS + 80);
    });
  };

  // One sweep per frame, `scroll.js`'s shape for `scroll.js`'s reason: the
  // observer has to watch the whole document because a keyed element is
  // replaced rather than patched, and NiceGUI applies one patch as many
  // mutations.
  let queued = false;
  const schedule = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      sweep();
    });
  };

  new MutationObserver(schedule).observe(document.documentElement, {
    childList: true,
    subtree: true,
  });
  // Whatever is on screen when this loads was not an arrival.
  document.querySelectorAll("[data-enter]").forEach((el) => {
    const key = el.getAttribute("data-enter");
    if (key) seen.add(key);
  });
})();
