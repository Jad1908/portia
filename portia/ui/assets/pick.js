// Telling a click on a spec apart from a double click, before either reaches
// the server.
//
// **Why this is not the browser's job, and not Python's.**
//
// It cannot be the browser's: clicking a row refreshes the left pane, and
// NiceGUI *replaces* elements rather than patching them, so the two presses of a
// double click land on two different DOM nodes and no `dblclick` is ever
// dispatched. Measured 2026-08-16.
//
// It cannot be Python's either, which is the sharper finding. Acting on the
// first click re-renders the pane **between** the two presses, and the rows move
// under a stationary cursor — two clicks 140ms apart at one screen position were
// measured hitting `stg_orders.yaml` and then `staging`, its neighbour. So the
// second press is not merely late, it can be a click on a *different spec*. No
// amount of server-side timing fixes that, because by the time the second event
// arrives it is already about the wrong row.
//
// So the gesture is resolved here, where it happens, and **nothing reaches the
// server until it is resolved.** The DOM cannot move between the two presses
// because nothing has asked it to yet.
//
// The cost is one short delay on a single click. That is a real cost and it is
// the right trade: the alternative on offer was a double click that needed three
// or four attempts, and a single click that sometimes selected the row above.
(() => {
  if (window.__portiaPick) return;
  window.__portiaPick = true;

  // Long enough for a comfortable double click, short enough that a single one
  // still feels like a press rather than a request. Browsers use ~500ms for
  // `dblclick`; that is too long to sit on every single click, and people
  // double-click faster than the maximum they are allowed.
  const DOUBLE_MS = 260;

  // The row identity is a **spec path** or an `opens` token, not an element: the
  // element is exactly the thing that does not survive.
  let pending = null; // { id, event, timer }

  // Two kinds of row use this, and the difference is only which event carries
  // the answer. A spec row selects and reveals on the canvas; a gallery row
  // opens a tab, as a preview on one press and for keeps on two
  // (`docs/VISUALIZATION.md` §3.8). The gesture is identical and so is the
  // reason it cannot be reconstructed on the server.
  const KINDS = [
    { attribute: "data-spec", event: "portia:spec", field: "spec" },
    { attribute: "data-opens", event: "portia:opens", field: "opens" },
  ];

  const send = (row, reveal) => {
    if (typeof emitEvent !== "function") return;
    emitEvent(row.event, { [row.field]: row.id, reveal });
  };

  const settle = () => {
    if (pending === null) return;
    clearTimeout(pending.timer);
    pending = null;
  };

  const rowAt = (target) => {
    for (const kind of KINDS) {
      const element = target.closest(`[${kind.attribute}]`);
      if (element) {
        return { id: element.getAttribute(kind.attribute), event: kind.event, field: kind.field };
      }
    }
    return null;
  };

  document.addEventListener(
    "click",
    (e) => {
      const row = rowAt(e.target);
      if (!row) {
        // A click anywhere else abandons a half-finished gesture rather than
        // leaving it to pair with the next row click a minute later.
        settle();
        return;
      }

      // The second press of a double click on the same row: resolve now and let
      // the queued single go, or the server would be told twice.
      if (pending !== null && pending.id === row.id && pending.event === row.event) {
        settle();
        send(row, true);
        return;
      }

      // A first press — or a press on a different row while one was pending, in
      // which case that one was a single click after all and is sent right away
      // so it is not lost.
      if (pending !== null) {
        const waiting = pending;
        settle();
        send(waiting, false);
      }
      pending = {
        id: row.id,
        event: row.event,
        field: row.field,
        timer: setTimeout(() => {
          const waiting = pending;
          pending = null;
          send(waiting, false);
        }, DOUBLE_MS),
      };
    },
    // Capture, so this runs before anything else on the row can act on it — and
    // so it still runs when the row is inside a container that stops propagation.
    true,
  );
})();
