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
//
// Two more things are stated the same way, and both used to be `run_javascript`
// calls made mid-render — which is to say both were races:
//
//   * `data-scroll-stick="bottom"` — a region that grows at the bottom. It
//     follows the newest row *only while you are already at the bottom*, so
//     scrolling up to read the evidence a question is about keeps your place.
//     The transcript pinned itself with `scrollTop = 1e9` on every event, and
//     when that landed before the new rows existed it clamped to 0 — the chat
//     jumping to the top exactly when a question arrived or was answered.
//     Such a region is unanchored (`portia.css`): a move this did not make is
//     read as a person's, and the browser's scroll anchoring moving it a few
//     rows short of the foot was read as one scrolling up (2026-09-26).
//   * `data-scroll-to` — a token naming an element to bring into view, acted on
//     **once per token**, the same shape as the canvas's focus mark. A repeated
//     render is then harmless rather than something the server has to get right.
(() => {
  if (window.__portiaScroll) return;
  window.__portiaScroll = true;

  // Within this many pixels of the foot counts as *at the bottom*. Not zero:
  // fractional layout heights and a mid-scroll frame both land a pixel or two
  // short, and a region that stops following because of a rounding error looks
  // exactly like one that is broken.
  const FOOT = 24;

  // key -> {top, foot} — the offset that key was last left at, and whether it
  // was left at the foot. Not persisted: it describes this window's reading
  // position, which does not outlive the window.
  const remembered = new Map();

  const keyOf = (el) => el && el.dataset && el.dataset.scrollKey;
  const sticks = (el) => el.dataset.scrollStick === "bottom";
  const atFoot = (el) => el.scrollHeight - el.clientHeight - el.scrollTop <= FOOT;

  // `scroll` does not bubble, so this listens in the capture phase — one
  // listener for every keyed container, including ones that do not exist yet.
  document.addEventListener(
    "scroll",
    (e) => {
      const key = keyOf(e.target);
      if (!key) return;
      // **A jump to 0 in a frame that also changed the DOM is a clamp, not a
      // person** (2026-09-18). A container that survives while its rows are
      // replaced is thrown to the top by the browser, and that fires this event
      // *before* the frame's `restore()` runs: scroll events are dispatched
      // ahead of animation-frame callbacks. Recording it overwrote the offset
      // `keep` was about to put back, so its second branch could never fire.
      // Found on the add-data tree, where a tick redraws 400 rows inside a
      // region that stays.
      const was = remembered.get(key);
      if (queued && e.target.scrollTop === 0 && was && was.top > 0) return;
      // **Our own move to the foot is not a person leaving it** (2026-09-26).
      // The event is dispatched a frame after `aim` moved the region and reads
      // the geometry of *that* frame: rows that mounted in between put the foot
      // further down, and this recorded *scrolled up*. On a page reloaded
      // mid-chat the rows grew from 4,831 to 7,973 px between the two, and the
      // chat never followed again.
      if (aimed.get(e.target) === e.target.scrollTop) return;
      aimed.delete(e.target);
      remembered.set(key, { top: e.target.scrollTop, foot: atFoot(e.target) });
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

  // el -> the scrollTop `aim` left it at, which is how the listener above knows
  // the event is ours.
  const aimed = new WeakMap();

  const aim = (el) => {
    el.scrollTop = el.scrollHeight;
    aimed.set(el, el.scrollTop);
  };

  // Aimed again after layout, like `put`, but at the foot as it is *then*:
  // the rows that arrive in between are the reason for the second aim, so
  // aiming at the height measured before them stopped short.
  const toFoot = (el) => {
    aim(el);
    requestAnimationFrame(() => {
      const was = remembered.get(keyOf(el));
      if (el.isConnected && !(was && !was.foot)) aim(el);
    });
  };

  const keep = (el) => {
    const was = remembered.get(keyOf(el));
    if (!was || !was.top) return;
    // A fresh element is one this has never marked. The second case is the
    // same bug arriving by a different route: the element survived but its
    // rows were replaced, which collapses its scrollHeight and clamps the
    // offset to 0 — and a 0 the human actually scrolled to was recorded above,
    // so `want` would be 0 too and nothing happens.
    if (el.dataset.portiaScroll !== "1") {
      el.dataset.portiaScroll = "1";
      put(el, was.top);
    } else if (el.scrollTop === 0) {
      put(el, was.top);
    }
  };

  // A sticking region follows its own growth, so placing it once when it is
  // rebuilt is not enough: rows stream in afterwards. Its height is what says
  // something arrived — cheaper and steadier than watching the rows themselves.
  const heights = new WeakMap();

  const stick = (el) => {
    const was = remembered.get(keyOf(el));
    // Never seen means a chat you have just opened, and its newest message is
    // the one to be looking at. Scroll up and this stops following, because at
    // that point the newest row is not what you are reading.
    if (was && !was.foot) {
      keep(el);
      return;
    }
    if (el.dataset.portiaScroll !== "1" || heights.get(el) !== el.scrollHeight) {
      el.dataset.portiaScroll = "1";
      heights.set(el, el.scrollHeight);
      toFoot(el);
    }
  };

  // --- bringing one element into view ---------------------------------------

  // token -> already acted on. One move per request however many times the pane
  // redraws it: the server bumps the token when you actually ask to go
  // somewhere, and a rebuild carrying the same token is the same request
  // arriving again, not a new one.
  let lastTarget = null;

  const scrollTo = () => {
    const target = document.querySelector("[data-scroll-to]");
    if (!target) return;
    const token = target.getAttribute("data-scroll-to");
    if (!token || token === lastTarget) return;
    lastTarget = token;
    // `nearest` rather than `center`: the block is usually already on screen,
    // and a scroll that moves when it did not have to reads as the panel
    // twitching under the click.
    requestAnimationFrame(() => {
      if (target.isConnected) target.scrollIntoView({ block: "nearest" });
    });
  };

  const restore = () => {
    document.querySelectorAll("[data-scroll-key]").forEach((el) => {
      if (sticks(el)) stick(el);
      else keep(el);
    });
    scrollTo();
  };

  // **One `restore()` per frame, not one per mutation.** The observer watches
  // the whole document — it has to, because a keyed container is replaced
  // rather than patched, so there is no stable element to observe instead — and
  // NiceGUI patches a pane as many separate mutations. Each `restore()` reads
  // `scrollHeight` and `clientHeight` off every keyed container, and reading
  // either forces the browser to lay out the page it has just been told to
  // change. That is a synchronous layout per mutation, on the pane that redraws
  // on every event of a live exchange.
  //
  // Coalescing into one animation frame costs nothing correctness-wise: a frame
  // is when the page is painted, so putting the position back once per frame
  // puts it back exactly as often as anyone can see it. `stick` still notices
  // rows streaming in, because it compares `scrollHeight` against the last
  // value it saw rather than against the last mutation.
  let queued = false;
  const schedule = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      restore();
    });
  };

  new MutationObserver(schedule).observe(document.documentElement, {
    childList: true,
    subtree: true,
  });
  restore();
})();
