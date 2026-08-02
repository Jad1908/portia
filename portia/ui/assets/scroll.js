// Where a pane is scrolled to, kept across the pane being rebuilt.
//
// NiceGUI *replaces* a refreshable's elements rather than patching them, so the
// scroll container you were reading is not the one on screen after a click — and
// a brand new element starts at the top. Every selection therefore threw the
// left panel and the run report back to row one, which on a long file list is
// the difference between clicking through a project and losing your place in it.
//
// **The offset is client state, exactly like the canvas's pan and zoom**
// (`canvas.js`, and `CLAUDE.md` → the canvas view). The server never learns it,
// never persists it and is not asked for it on a render: it states a
// `data-scroll-key` in the DOM and this puts the position back on whatever
// element now carries that key. A key per artifact rather than one per pane, so
// switching from one saved run to another starts at the top of the new one
// instead of at the offset you left the last one at.
//
// Declarative for the same reason the focus mark is: a `run_javascript` fired
// during a render races the DOM patch and lands on the element about to be
// discarded.
(() => {
  if (window.__portiaScroll) return;
  window.__portiaScroll = true;

  // key -> the offset that key was last left at. Not persisted: it describes
  // this window's reading position, which does not outlive the window.
  const remembered = new Map();

  const keyOf = (el) => el && el.dataset && el.dataset.scrollKey;

  // `scroll` does not bubble, so this listens in the capture phase — one
  // listener for every keyed container, including ones that do not exist yet.
  document.addEventListener(
    "scroll",
    (e) => {
      const key = keyOf(e.target);
      if (key) remembered.set(key, e.target.scrollTop);
    },
    true,
  );

  const put = (el, want) => {
    el.scrollTop = want;
    // The container can arrive before the rows that give it something to scroll,
    // in which case the assignment above clamps to 0. Aim once more after layout
    // rather than guessing at the order NiceGUI patches things in.
    requestAnimationFrame(() => {
      if (el.isConnected && el.scrollTop < want) el.scrollTop = want;
    });
  };

  const restore = () => {
    document.querySelectorAll("[data-scroll-key]").forEach((el) => {
      const want = remembered.get(keyOf(el));
      if (!want) return;
      // A fresh element is one this has never marked. The second case is the
      // same bug arriving by a different route: the element survived but its
      // rows were replaced, which collapses its scrollHeight and clamps the
      // offset to 0 — and a 0 the human actually scrolled to was recorded above,
      // so `want` would be 0 too and nothing happens.
      if (el.dataset.portiaScroll !== "1") {
        el.dataset.portiaScroll = "1";
        put(el, want);
      } else if (el.scrollTop === 0) {
        put(el, want);
      }
    });
  };

  new MutationObserver(restore).observe(document.documentElement, {
    childList: true,
    subtree: true,
  });
  restore();
})();
