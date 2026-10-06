// A closed pane, dragged back open from the rail it left behind.
//
// Closing a pane is a drag (`app._splitter`): take its edge under the width it
// is readable at and it shuts, leaving a rail. Until 2026-10-06 the only way
// back was the arrow on the rail, so a gesture that closed by dragging could
// not be undone by dragging (the user: "I would appreciate to be able to drag
// to open them again, like a resizing"). This is the other half: press the
// rail, drag away from the window's edge, and a guide shows where the pane's
// edge will land. Past the pane's floor the guide is the accent and letting go
// opens the pane at that width; short of it the guide is dashed and letting go
// leaves the pane shut. The floor is the same number either way, so the
// threshold that closes a pane is the one that opens it.
//
// **The whole gesture is the client's**, `tabs.js`'s rule: the server hears one
// event at the end, `portia:pane-open` with the pane and the width, and the
// rail can be rebuilt under the pointer without the drag noticing, because
// nothing here holds an element past the press but the guide it drew itself.
//
// A press that does not move is the arrow's click, untouched. One that moves
// swallows the click that follows it, so a drag that ends on the arrow does not
// also press it.
(() => {
  if (window.__portiaRail) return;
  window.__portiaRail = true;

  // How far a press has to move before it is a drag rather than a click.
  const SLOP_PX = 4;

  const emit = (name, payload) => {
    if (typeof emitEvent === "function") emitEvent(name, payload);
  };

  let drag = null; // { pane, side, origin, floor, ceiling, guide, moved, width }

  function widthAt(x) {
    const raw = drag.side === "left" ? x - drag.origin : drag.origin - x;
    return Math.max(0, Math.min(drag.ceiling, Math.round(raw)));
  }

  function place(x) {
    const width = widthAt(x);
    drag.width = width;
    const edge = drag.side === "left" ? drag.origin + width : drag.origin - width;
    const left = Math.min(edge, drag.origin);
    drag.guide.style.left = left + "px";
    drag.guide.style.width = Math.abs(edge - drag.origin) + "px";
    drag.guide.classList.toggle("p-rail-guide--open", width >= drag.floor);
  }

  document.addEventListener(
    "pointerdown",
    (event) => {
      if (event.button !== 0) return;
      const rail = event.target.closest && event.target.closest("[data-rail]");
      if (!rail) return;
      const box = rail.getBoundingClientRect();
      const side = rail.dataset.railSide;
      drag = {
        pane: rail.dataset.rail,
        side,
        // The window edge the pane comes back from: the rail's outer side.
        origin: side === "left" ? box.left : box.right,
        floor: Number(rail.dataset.railFloor) || 0,
        ceiling: Number(rail.dataset.railCeiling) || Infinity,
        startX: event.clientX,
        guide: null,
        moved: false,
        width: 0,
        top: box.top,
        height: box.height,
      };
    },
    true,
  );

  document.addEventListener("pointermove", (event) => {
    if (!drag) return;
    if (!drag.moved) {
      if (Math.abs(event.clientX - drag.startX) < SLOP_PX) return;
      drag.moved = true;
      const guide = document.createElement("div");
      guide.className = "p-rail-guide p-rail-guide--" + drag.side;
      guide.style.top = drag.top + "px";
      guide.style.height = drag.height + "px";
      document.body.appendChild(guide);
      drag.guide = guide;
      document.body.classList.add("p-rail-dragging");
    }
    event.preventDefault();
    place(event.clientX);
  });

  function finish(commit) {
    if (!drag) return;
    const done = drag;
    drag = null;
    if (!done.moved) return;
    done.guide.remove();
    document.body.classList.remove("p-rail-dragging");
    // The click this press would otherwise end in, on the arrow or the rail.
    const swallow = (event) => {
      event.stopPropagation();
      event.preventDefault();
    };
    document.addEventListener("click", swallow, { capture: true, once: true });
    setTimeout(() => document.removeEventListener("click", swallow, true), 0);
    if (commit && done.width >= done.floor) {
      emit("portia:pane-open", { pane: done.pane, width: done.width });
    }
  }

  document.addEventListener("pointerup", () => finish(true));
  document.addEventListener("pointercancel", () => finish(false));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") finish(false);
  });
})();
