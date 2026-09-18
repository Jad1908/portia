// Dragging a tab: along its own strip, across to the other half, or into a half
// that does not exist yet. Resolved on the client, sent as one event.
//
// `docs/VISUALIZATION.md` §3.7. The middle pane is two editor groups, each with
// its own strip, and every arrangement gesture in it is a drag — because a drag
// is the one gesture that says *put this there*, and a button cannot: it has to
// guess the there.
//
// **It has to be resolved here, and that is the rule `pick.js` documents.**
// NiceGUI replaces elements rather than patching them, so a gesture that
// reported its start to the server would find, by the time it ended, that the
// strip had been rebuilt and the tab under the cursor was a different element.
// Nothing reaches Python until the whole gesture is known: which tab, which
// strip, which side of which neighbour.
//
// The DOM says what may be dragged and where it may land, in portia's own
// vocabulary — `data-tab` is a **position** on a strip, `data-tab-group` and
// `data-strip` name the half. A position rather than a name because a chart's
// name is a sentence the agent wrote, and quoting one into a NiceGUI prop puts a
// parser and an escaping rule in the seam that has already cost this module two
// bugs. The server never learns that HTML5 has a drag protocol.

window.portiaTabs = (function () {
  const TAB = "data-tab";
  const GROUP = "data-tab-group";
  const STRIP = "data-strip";
  const ZONE = ".tab-drop";
  // On the body rather than on the pane: the drop zone is drawn inside the
  // middle pane, and CSS cannot reach from a tab up out of the strip and back
  // down into a sibling. One class, set for the length of the gesture.
  const DRAGGING = "tab-dragging";
  const OVER = "tab-drop--over";
  const MARK = "pane-tab--drop";
  // The gesture's identity rides the DataTransfer, under a type of its own. A
  // module variable set at the press is state on portia's side of the drag,
  // and the browser owns the drag: if NiceGUI replaces the strip mid-gesture
  // — a chart landing, a chat event, anything that refreshes the middle pane
  // — the element the press happened on is gone, Chrome never fires `dragend`
  // for a source it cannot find, and a variable read at the drop describes a
  // press that may be a gesture old. The DataTransfer is read back at the drop
  // whatever happened to the DOM in between, and its *types* are readable on
  // every `dragover`, which is how the zone knows a tab is what is over it.
  const TYPE = "application/x-portia-tab";

  // Long enough for a comfortable double click, and the same number `pick.js`
  // uses — one idea of how fast a double click is, in a window where the same
  // finger does both gestures.
  const DOUBLE_MS = 260;

  let dragging = null; // { group, index }
  let pending = null; // { group, index, timer }

  const emit = (name, payload) => {
    if (typeof emitEvent === "function") emitEvent(name, payload);
  };

  function tab(node) {
    return node && node.closest ? node.closest(`[${TAB}]`) : null;
  }

  function strip(node) {
    return node && node.closest ? node.closest(`[${STRIP}]`) : null;
  }

  function zone(node) {
    return node && node.closest ? node.closest(ZONE) : null;
  }

  function clearMarks() {
    document.querySelectorAll(`.${OVER}, .${MARK}`).forEach((el) => {
      el.classList.remove(OVER);
      el.classList.remove(MARK);
    });
  }

  // Where in the target strip the tab would land: the index of the tab it would
  // sit **before**, or null for the end. Decided by which half of a tab the
  // cursor is over, which is how every editor does it and the only way the last
  // position is reachable at all.
  function landing(event) {
    const bar = strip(event.target);
    if (!bar) return null;
    const over = tab(event.target);
    if (!over) return { group: bar.getAttribute(STRIP), at: null, mark: null };
    const box = over.getBoundingClientRect();
    const after = event.clientX > box.left + box.width / 2;
    const index = Number(over.getAttribute(TAB));
    return {
      group: bar.getAttribute(STRIP),
      at: after ? index + 1 : index,
      mark: over,
    };
  }

  // Whether the drag in flight is a tab. The variable says so while it is
  // set; the DataTransfer says so even when it is not — a `dragend` that never
  // fired leaves the variable stale, and a drag that began somewhere else (a
  // figure, in `gallery.js`) must not be read as a tab because one did.
  function isTab(event) {
    const types = event.dataTransfer ? Array.from(event.dataTransfer.types || []) : [];
    return types.includes(TYPE) || dragging !== null;
  }

  // Where the drag began, at the drop: the variable if it survived, the
  // payload if it did not. `getData` is only readable at the drop in Chrome,
  // which is the one moment this is needed.
  function origin(event) {
    if (dragging !== null) return dragging;
    const raw = event.dataTransfer ? event.dataTransfer.getData(TYPE) : "";
    const match = /^(left|right):(\d+)$/.exec(raw || "");
    return match ? { group: match[1], index: Number(match[2]) } : null;
  }

  document.addEventListener("dragstart", (event) => {
    const row = tab(event.target);
    // Any other drag starting clears a tab gesture that never ended.
    dragging = null;
    if (!row) return;
    dragging = {
      group: row.getAttribute(GROUP),
      index: Number(row.getAttribute(TAB)),
    };
    // `move` rather than `copy`: the cursor is the only thing telling you which
    // one this is, and a copy cursor on a move is a small lie.
    event.dataTransfer.effectAllowed = "move";
    const payload = `${dragging.group}:${dragging.index}`;
    event.dataTransfer.setData(TYPE, payload);
    // Firefox will not start a drag without a plain payload set.
    event.dataTransfer.setData("text/plain", payload);
    place(row);
    document.body.classList.add(DRAGGING);
  });

  // The drop zone starts **under the strip**, never over it. It used to cover
  // the whole right half of the pane from the top, so a tab past the middle
  // of the strip vanished under the overlay the instant it was picked up, and
  // dropping anywhere near the strip's right half read as *open beside* when
  // the gesture was a reorder. That is where the fourth tab sits at the widths
  // this window is used at, which is why it was the fourth tab that "did not
  // detach". The strip keeps its own vocabulary (before, after, the end) and
  // the picture below it is where a split is asked for — VS Code's layout.
  // Measured at the press rather than fixed in CSS: the strip's height is the
  // font's and the padding's, and a number copied out of the stylesheet is a
  // number that goes stale.
  function place(row) {
    const zone = document.querySelector(ZONE);
    const bar = strip(row);
    const pane = zone && zone.parentElement;
    if (!zone || !bar || !pane) return;
    const top = bar.getBoundingClientRect().bottom - pane.getBoundingClientRect().top;
    zone.style.top = `${Math.max(0, Math.round(top))}px`;
  }

  document.addEventListener("dragend", () => {
    dragging = null;
    clearMarks();
    document.body.classList.remove(DRAGGING);
  });

  // The tab a group is showing is brought into view when the strip overflows.
  // A new chart takes focus (§3.4), and with five tabs in a 740px pane the tab
  // that just took it was drawn past the strip's right edge — active, and not
  // on screen. Declarative, on the class the server already sets, and acted on
  // once per element: NiceGUI replaces the strip on every refresh, so a fresh
  // element is a fresh request, and `nearest` moves nothing when the tab is
  // already visible.
  const reveal = () => {
    document.querySelectorAll(`[${STRIP}] .pane-tab--active`).forEach((el) => {
      if (el.dataset.portiaShown === "1") return;
      el.dataset.portiaShown = "1";
      el.scrollIntoView({ inline: "nearest", block: "nearest" });
    });
  };
  let queued = false;
  new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      reveal();
    });
  }).observe(document.documentElement, { childList: true, subtree: true });
  reveal();

  // `dragenter` is cancelled as well as `dragover`. The drag-and-drop model
  // picks its drop target when the pointer *enters* an element and only then
  // consults `dragover`; an entry nothing cancelled falls back to the body,
  // and a drop on the body is a drop on nothing. Chrome forgives the omission,
  // and this window does not get to choose the browser it is opened in.
  const accept = (event) => {
    if (!isTab(event)) return false;
    if (zone(event.target) || strip(event.target)) {
      // Without this the browser refuses the drop, silently, and the gesture
      // just does nothing — which reads as a broken feature rather than a
      // refusal.
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      return true;
    }
    return false;
  };

  document.addEventListener("dragenter", accept);

  document.addEventListener("dragover", (event) => {
    if (!accept(event)) return;
    const target = zone(event.target);
    if (target) {
      clearMarks();
      target.classList.add(OVER);
      return;
    }
    const spot = landing(event);
    if (!spot) return;
    clearMarks();
    if (spot.mark) spot.mark.classList.add(MARK);
  });

  document.addEventListener("dragleave", (event) => {
    const target = zone(event.target);
    if (target) target.classList.remove(OVER);
  });

  document.addEventListener("drop", (event) => {
    if (!isTab(event)) return;
    const from = origin(event);
    if (from === null) return;
    const onZone = zone(event.target);
    const spot = onZone ? { group: "right", at: null } : landing(event);
    clearMarks();
    document.body.classList.remove(DRAGGING);
    dragging = null;
    if (!spot) return;
    event.preventDefault();
    // One event, with the gesture already decided. The server has no drag state
    // to keep in step and no half-finished move to clean up if the pane
    // refreshes underneath this.
    emit("portia:tab-move", {
      from: from.group,
      index: from.index,
      to: spot.group,
      at: spot.at,
    });
  });

  // A double click on a tab keeps a preview — the editor gesture, and the same
  // one `pick.js` resolves on a row in the left pane. It is here rather than as
  // a `dblclick` handler for `pick.js`'s reason: the first press refreshes the
  // pane, so the two presses land on two elements and no `dblclick` is ever
  // dispatched. A single press needs no delay, because the server already got it
  // from the tab's own click handler; only the second one is ours to send.
  document.addEventListener("click", (event) => {
    const row = tab(event.target);
    if (!row) {
      pending = null;
      return;
    }
    const group = row.getAttribute(GROUP);
    const index = Number(row.getAttribute(TAB));
    if (pending && pending.group === group && pending.index === index) {
      clearTimeout(pending.timer);
      pending = null;
      emit("portia:tab-pin", { group, index });
      return;
    }
    if (pending) clearTimeout(pending.timer);
    pending = {
      group,
      index,
      timer: setTimeout(() => (pending = null), DOUBLE_MS),
    };
  });

  // Middle click closes, which is the gesture every editor and every browser
  // has. It reaches the server as a move to nowhere rather than growing an event
  // of its own: the tab's ✕ is already a close, so this dispatches to it.
  document.addEventListener("auxclick", (event) => {
    if (event.button !== 1) return;
    const row = tab(event.target);
    if (!row) return;
    const close = row.querySelector(".chart-tab-close");
    if (close) {
      event.preventDefault();
      close.click();
    }
  });

  return {};
})();
