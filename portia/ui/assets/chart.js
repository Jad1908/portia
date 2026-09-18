// A chart, drawn by a library rather than by us — from a spec the agent wrote.
//
// `docs/VISUALIZATION.md` §5 and §2.9. Vega-Lite does the drawing, and since
// 2026-09-03 the **spec is the agent's own**: marks, layers, scales, palettes,
// axes, formats. This file's job changed with it. It used to compose a spec from
// portia's small vocabulary; now it *completes* one — the rows, the types the
// agent left out, the theme it did not ask about, and the size of the box it has
// to fit in — and never overrules what the agent actually wrote.
//
// **That is a real reversal of §5's swappable-library promise and it is
// deliberate.** The vocabulary the agent writes is Vega-Lite's, so replacing the
// renderer is no longer one file. What was bought is that widening the chart
// vocabulary is no longer a release of portia: two sessions in a row hit the
// edge of a list this repo maintained (four marks, then no palette and no
// layers), and the third would have too.
//
// What did not move is the rule under it. `agent/chartspec.check` refuses every
// key that computes — `aggregate`, `bin`, `transform`, `expr` and the rest —
// before a spec ever reaches here, because a number a chart grammar worked out
// came from no `SELECT`, is in no log, and `findings.review` cannot read it back.
//
// The payload rides in the DOM rather than arriving by `run_javascript` during a
// render. That is the rule `canvas.js` and `scroll.js` document: driving the
// client from inside a render races the DOM patch NiceGUI is applying.
//
// It is a hidden child element's **text**, not an attribute, and not a
// `<script type="application/json">` either. NiceGUI's prop parser splits on
// whitespace so a quoted prop value does not survive as an attribute — what
// arrived was the payload encoded twice, a JSON string holding indented JSON,
// which `JSON.parse` refused at the first newline. And `ui.html` refuses a
// script tag outright. Text content survives both, verbatim.

window.portiaChart = (function () {
  // Colour names a series — an identity, not an order. DESIGN.md's rule that
  // colour communicates kind and never rank is not a constraint on a categorical
  // palette (VISION.md settled this: orange for `ESP` asserts nothing), so what
  // it forbids here is an encoding that implies a verdict. Nothing in this file
  // sorts by a measure, scales a mark by one, or picks a colour from a value.
  //
  // It is the **default** and nothing more: an agent that writes a scale range
  // or a mark colour has said something specific, and a default that overruled
  // it would be this file having an opinion about someone's brand.
  const SERIES = [
    "#4f8ff7",
    "#2fae8f",
    "#c17ae0",
    "#e0913a",
    "#5fc2d6",
    "#d4718a",
    "#8a9ad6",
    "#7bb35e",
  ];

  // Marks portia styles when the agent named one as a bare string. A spec that
  // writes `{"type": "bar", "color": "#FF9900"}` is left exactly as it is —
  // these are the manners of an unstyled chart, not a house style imposed on a
  // styled one.
  const MARK_STYLE = {
    arc: { innerRadius: 0, stroke: "#fff", strokeWidth: 1 },
    area: { line: true, opacity: 0.6 },
    bar: { cornerRadiusEnd: 2 },
    boxplot: { extent: "min-max" },
    circle: { opacity: 0.75 },
    line: { point: false, strokeWidth: 2 },
    point: { filled: true, size: 45, opacity: 0.75 },
    rule: { strokeWidth: 1.5 },
    square: { opacity: 0.75 },
    text: { fontSize: 11 },
    tick: { thickness: 2 },
    trail: {},
  };

  // Keys whose value is another view, or a list of them. Walked so that a layer
  // or a facet gets its types filled and its marks styled like a plain chart.
  const VIEWS = ["layer", "hconcat", "vconcat", "concat", "spec", "facet", "repeat"];

  function css(name, fallback) {
    const value = getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim();
    return value || fallback;
  }

  // The look comes from `portia.css`'s tokens, read at draw time rather than
  // copied here, so a chart follows the light/dark switch with everything else.
  // Two files that each hold half a palette is how the app ends up with two.
  function theme() {
    return {
      background: "transparent",
      font: "Inter, system-ui, sans-serif",
      axis: {
        labelColor: css("--mute", "#6b7079"),
        titleColor: css("--body", "#3a3f47"),
        domainColor: css("--hairline", "#e3e6ec"),
        tickColor: css("--hairline", "#e3e6ec"),
        gridColor: css("--hairline-soft", "rgba(0,0,0,0.06)"),
        labelFontSize: 11,
        titleFontSize: 11,
        titleFontWeight: 500,
        titlePadding: 8,
      },
      legend: {
        labelColor: css("--body", "#3a3f47"),
        titleColor: css("--mute", "#6b7079"),
        labelFontSize: 11,
        titleFontSize: 11,
      },
      view: { stroke: null },
      range: { category: SERIES },
    };
  }

  // Which Vega-Lite type a column gets when the agent did not say. **Read off
  // the values that arrived**, never guessed from the column's name: a column
  // called `YEAR` holding strings is a category, and one called `CODE` holding
  // integers is still a number. The server sent JSON, so a date is a string and
  // has to be recognised as one.
  const ISO_DATE = /^\d{4}-\d{2}-\d{2}([T ]|$)/;

  function channelType(rows, column) {
    const values = [];
    for (const row of rows) {
      const value = row[column];
      if (value !== null && value !== undefined) values.push(value);
      if (values.length >= 50) break;
    }
    if (!values.length) return "nominal";
    if (values.every((v) => typeof v === "number")) return "quantitative";
    if (values.every((v) => typeof v === "string" && ISO_DATE.test(v))) {
      return "temporal";
    }
    return "nominal";
  }

  // Fill in what the agent left out, and touch nothing it wrote. Recursive
  // because a layered spec is a spec of specs, and a field two layers down needs
  // its type as much as one at the top.
  function complete(node, rows) {
    if (Array.isArray(node)) {
      node.forEach((child) => complete(child, rows));
      return;
    }
    if (!node || typeof node !== "object") return;

    if (typeof node.mark === "string") {
      const style = MARK_STYLE[node.mark];
      node.mark = style ? Object.assign({ type: node.mark }, style) : node.mark;
    }
    if (node.encoding && typeof node.encoding === "object") {
      for (const channel of Object.keys(node.encoding)) {
        fillType(node.encoding[channel], rows);
      }
    }
    for (const key of VIEWS) {
      if (node[key]) complete(node[key], rows);
    }
  }

  function fillType(channel, rows) {
    if (Array.isArray(channel)) {
      channel.forEach((c) => fillType(c, rows));
      return;
    }
    if (!channel || typeof channel !== "object") return;
    if (channel.field && !channel.type) {
      channel.type = channelType(rows, channel.field);
    }
  }

  // Whether anything in the spec lays panels out for itself. A faceted or
  // concatenated chart given a container width lays every panel out at the full
  // width and clips all but the first, so the fit is conditional rather than
  // global — Vega-Lite's own default is right in that one case and wrong in
  // every other.
  function sizesItself(node) {
    if (!node || typeof node !== "object") return false;
    if (node.facet || node.repeat || node.hconcat || node.vconcat || node.concat) {
      return true;
    }
    if (node.encoding && (node.encoding.column || node.encoding.row)) return true;
    return (node.layer || []).some(sizesItself);
  }

  // Every column the query returned, as a tooltip — unless the spec says
  // otherwise anywhere in it. The rows are already in the browser and hovering a
  // mark to ask "what is this one" is the first thing anyone does; sending them
  // and then hiding them would be an odd kind of thrift. An agent that wrote its
  // own tooltip has decided, and this does not argue.
  function hasTooltip(node) {
    if (!node || typeof node !== "object") return false;
    if (node.encoding && node.encoding.tooltip) return true;
    if (node.tooltip) return true;
    return VIEWS.some((key) => {
      const child = node[key];
      if (Array.isArray(child)) return child.some(hasTooltip);
      return hasTooltip(child);
    });
  }

  function spec(payload) {
    const rows = payload.rows;
    // A copy: the payload in the DOM is what the server sent and a redraw reads
    // it again. Completing it in place would compound on every theme change.
    const vega = JSON.parse(JSON.stringify(payload.vega || {}));
    complete(vega, rows);
    if (!hasTooltip(vega) && !vega.layer && !sizesItself(vega)) {
      vega.encoding = vega.encoding || {};
      vega.encoding.tooltip = (payload.columns || []).map((c) => ({
        field: c,
        type: channelType(rows, c),
      }));
    }
    vega.$schema = "https://vega.github.io/schema/vega-lite/v5.json";
    vega.data = { values: rows };
    // The agent's config wins where it said something; the theme fills the rest.
    vega.config = Object.assign(theme(), vega.config || {});
    if (!sizesItself(vega)) {
      if (vega.width === undefined) vega.width = "container";
      if (vega.height === undefined) vega.height = "container";
      if (vega.autosize === undefined) {
        vega.autosize = { type: "fit", contains: "padding" };
      }
    }
    return vega;
  }

  function draw(element) {
    const holder = element.querySelector(".chart-data");
    const raw = holder && holder.textContent;
    if (!raw) return;
    // Keyed by the payload **and the box it is drawn in**, so a pane refresh
    // that rebuilds an identical chart does not repaint it, one that changed
    // does, and a chart whose pane has been dragged wider redraws at the width
    // it now has. That last clause is the resize fix: `width: container` is
    // measured once, at embed time, and nothing about a splitter drag tells Vega
    // to measure again.
    const box = Math.round(element.clientWidth) + "x" + Math.round(element.clientHeight);
    const stamp = raw.length + ":" + raw.slice(0, 64) + ":" + box;
    if (element.dataset.portiaDrawn === stamp) return;
    let payload;
    try {
      payload = JSON.parse(raw);
    } catch (err) {
      return;
    }
    element.dataset.portiaDrawn = stamp;
    // **Vega mounts into a child, not into the figure.** `vegaEmbed` clears the
    // container it is given, which would take the element holding the data with
    // it — and then a redraw would find nothing to draw. One mount div, reused,
    // keeps the payload where the next redraw can still read it.
    let mount = element.querySelector(".chart-mount");
    const fresh = !mount;
    if (fresh) {
      mount = document.createElement("div");
      mount.className = "chart-mount";
      element.appendChild(mount);
    }
    // **A fresh element opens on the last picture, not on a blank.** A pane
    // refresh replaces the figure and the painted canvas dies with it, while
    // the embed below is async — so a rebuilt chart blanked and repainted
    // however identical it was, which is most of what made a refresh read as a
    // flash. The snapshot of the previous render stands in until the new one
    // lands; on an unchanged chart the swap is invisible.
    const snapshot = payload.key && SNAPSHOTS.get(payload.key);
    if (fresh && snapshot) {
      const ghost = document.createElement("img");
      ghost.className = "chart-ghost";
      ghost.src = snapshot;
      element.appendChild(ghost);
    }
    const settle = () => {
      const ghost = element.querySelector(".chart-ghost");
      if (ghost) ghost.remove();
    };
    // `vegaEmbed` is async and the element may be replaced under it — NiceGUI
    // *replaces* elements rather than patching them, which is the trap this file
    // shares with `canvas.js`. Checking that it is still in the document is
    // cheaper than trying to cancel the embed.
    window
      .vegaEmbed(mount, spec(payload), { actions: false, renderer: "canvas" })
      .then(() => {
        settle();
        // Captured on the first paint of this element only. A resize drag
        // redraws once per frame, and `toDataURL` is not a per-frame cost —
        // the snapshot refreshes anyway when the drag ends, because the
        // server's refresh replaces the element and the next draw is fresh.
        if (fresh && payload.key && mount.isConnected) {
          const canvas = mount.querySelector("canvas");
          if (canvas) {
            try {
              SNAPSHOTS.set(payload.key, canvas.toDataURL());
            } catch (err) {
              /* a tainted or zero-size canvas has no snapshot to offer */
            }
          }
        }
      })
      .catch((err) => {
        settle();
        if (!mount.isConnected) return;
        // A spec Vega-Lite refuses is the agent's spec, so it says so where the
        // agent's other refusals are read — on the chart, in words, rather than
        // as an empty box the user has to guess about.
        const message = String((err && err.message) || err);
        mount.textContent = "";
        const note = document.createElement("div");
        note.className = "chart-broken";
        note.textContent = message;
        mount.appendChild(note);
        // **And it goes back** (`docs/VISUALIZATION.md` §11). The rule this file
        // lives under is that *where you are looking* is the client's state and
        // must not round-trip; a render that failed is not where you are
        // looking, it is a fact about work portia did, and the agent holds a
        // receipt saying this chart was drawn. Failure only — a chart that draws
        // says nothing, which keeps the common path free.
        if (typeof emitEvent === "function" && payload && payload.key) {
          emitEvent("portia:chart-failed", { tab: payload.key, message: message });
        }
      });
    watch(element);
  }

  // The last successfully painted canvas per chart key, as a data URL. What a
  // fresh element shows while its own render is in flight (`draw`). Keyed like
  // everything client-side is — by the thing, never the element, because the
  // element is exactly what does not survive a refresh.
  const SNAPSHOTS = new Map();

  // Redraw when the box changes. **This is what makes a pane draggable** — the
  // three panes and the tab split are all `ui.splitter`s, and a splitter drag
  // never reaches the server, so nothing re-renders and a canvas painted at the
  // old width keeps it and overlaps its neighbour.
  //
  // One observer per figure, kept on the element, because a pane refresh
  // replaces the element and the old observer goes with it. `requestAnimationFrame`
  // rather than a timer: a drag fires this continuously and the browser already
  // owns the "once per frame" question.
  const OBSERVED = new WeakSet();

  function watch(element) {
    if (OBSERVED.has(element) || !window.ResizeObserver) return;
    OBSERVED.add(element);
    let queued = false;
    const observer = new ResizeObserver(() => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => {
        queued = false;
        if (element.isConnected) draw(element);
      });
    });
    observer.observe(element);
  }

  function drawAll() {
    document.querySelectorAll(".chart-figure").forEach(draw);
    document.querySelectorAll(".chart-grip").forEach(grip);
  }

  // The handle under a chart. **The height is the client's while the drag is
  // happening** and the server's the moment it ends — a round trip per pixel
  // would make the one directly-manipulated thing in this pane the laggiest, and
  // a height that never reached the server would be lost on the next refresh
  // (NiceGUI replaces elements rather than patching them).
  //
  // Pointer events rather than mouse ones, and pointer capture: a drag that
  // leaves the element still belongs to the element that started it, which is
  // what makes dragging past the window edge behave.
  const GRIPPED = new WeakSet();

  function grip(handle) {
    if (GRIPPED.has(handle)) return;
    GRIPPED.add(handle);
    const figure = handle.parentElement.querySelector(".chart-figure");
    if (!figure) return;
    let from = 0;
    let start = 0;
    let dragging = false;

    // **A flag and window listeners, not pointer capture.** Capture would tie
    // the drag to an element NiceGUI may replace under it, and a failed capture
    // fails silently — the handle would look draggable and do nothing. The
    // window hears every move, so leaving the element mid-drag is fine, which is
    // most of what dragging a divider *is*.
    const move = (event) => {
      if (!dragging) return;
      const height = Math.max(MIN_HEIGHT, Math.round(start + event.clientY - from));
      figure.style.height = height + "px";
      figure.style.flex = "0 0 auto";
      event.preventDefault();
    };

    const finish = () => {
      if (!dragging) return;
      dragging = false;
      handle.classList.remove("chart-grip--dragging");
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", finish);
      if (typeof emitEvent === "function") {
        emitEvent("portia:chart-height", {
          group: handle.getAttribute("data-grip") || "left",
          height: Math.round(figure.getBoundingClientRect().height),
        });
      }
    };

    handle.addEventListener("pointerdown", (event) => {
      from = event.clientY;
      start = figure.getBoundingClientRect().height;
      dragging = true;
      handle.classList.add("chart-grip--dragging");
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", finish);
      window.addEventListener("pointercancel", finish);
      event.preventDefault();
    });
  }

  // The shortest a chart may be dragged. `charts.MIN_CHART_HEIGHT` holds the
  // same number for the server's side of the same clamp — below it the axis
  // labels are taller than the plot.
  const MIN_HEIGHT = 140;

  // The server does not tell us when a chart lands: `workflow.pane` refreshes by
  // replacing its elements, and a `run_javascript` fired during that render is
  // the race described above. So we watch the DOM instead, which is the same
  // shape `pick.js` uses to survive a pane rebuild.
  // **One `drawAll()` per frame, not one per mutation** — `scroll.js`'s rule,
  // for `scroll.js`'s reason. `draw` reads each figure's `clientWidth` and
  // `clientHeight` to stamp the size it was painted at, and reading either
  // forces a layout of the page the mutation just changed. The observer watches
  // the whole document, and NiceGUI patches a pane as many mutations, so a
  // streamed transcript event was a synchronous layout per mutation *per chart
  // on screen*. Coalesced, it is one per painted frame, which is as often as
  // a redraw could be seen.
  let queued = false;
  const observer = new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      drawAll();
    });
  });
  document.addEventListener("DOMContentLoaded", () => {
    drawAll();
    observer.observe(document.body, { childList: true, subtree: true });
  });
  // A theme change repaints every chart, because the palette is read at draw
  // time and a canvas already painted does not follow a CSS variable.
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    document
      .querySelectorAll(".chart-figure")
      .forEach((el) => delete el.dataset.portiaDrawn);
    drawAll();
  });

  return { draw, drawAll };
})();
