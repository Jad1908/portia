// The knowledge graph, drawn by a library rather than by us.
//
// `ui/graph.py` lays out the *project canvas* — a DAG of specs, positioned by
// dependency order and by nothing else, because DESIGN.md says position must
// communicate kind rather than rank. This is a different surface entirely
// (KNOWLEDGE_GRAPH.md §6.9): a graph explorer over what the data is to itself,
// where a force layout, expand-on-click and hairball management are the whole
// job. Reimplementing those inside a module whose job is dependency order would
// produce one canvas trying to be two things.
//
// So vis-network does the drawing — the same engine neovis.js wraps — and the
// data arrives from the server as portia's own vocabulary. Two consequences,
// both deliberate:
//
//   * the Neo4j password never reaches the browser. `ui/engine.py` runs the
//     query and hands over JSON, which is the same rule every other pane obeys:
//     nothing in ui/ talks to a database.
//   * swapping the library is this file and nothing else, because the JSON
//     says `kind` and `properties`, not vis-network's field names.
//
// **The JSON is in the DOM, and this file watches for it** (2026-10-08). The
// server used to push it with a `run_javascript` after every render of the
// middle pane, which during indexing or a chat is every catalog write, and each
// push destroyed the network and ran the force layout again. Now
// `workflow._knowledge_inspector` writes the data beside the canvas and this
// draws whatever canvas has not been drawn — `chart.js`'s shape, for
// `chart.js`'s reason: a render that drives the client races the DOM patch
// NiceGUI is in the middle of applying.

window.portiaKnowledge = (function () {
  // Kind decides the look, and only from `portia.css`'s tokens, read at draw
  // time the way `chart.js` reads them. Nothing here scales with a number,
  // orders by one, or makes a well-connected node bigger — DESIGN.md's rule
  // survives the move to a third-party renderer, for everything the renderer
  // lets us decide. And nothing here is saturated: that is reserved for status,
  // and a node's kind is not a state. The four kinds borrow the project
  // canvas's vocabulary, so a table looks like a table on both surfaces:
  //
  //   * Source — a file that arrived: `source-node`, the quietest box.
  //   * Model  — a table portia built: `model-card`, one notch closer.
  //   * Group  — a name someone gave some sources: a pill, in prose type,
  //              because it is a label a person wrote, not a table.
  //   * Column — a dot, with its name beside it in mono.
  //
  // The accent appears in one place: what you selected, which is where you
  // are and not a claim about the node (DESIGN.md, `model-card-selected`).
  const KINDS = ["Source", "Model", "Group", "Column"];

  // The picture's own kinds, which the graph never holds (`query.MORE`,
  // `query.FINDING`). A `More` node is the columns of one table the picture
  // left out, and pressing it opens that table's whole list in the window. A
  // `FINDING` line joins two tables one finding is about.
  const MORE = "More";
  const FINDING = "FINDING";

  // The dash and the arrowhead say which kind an edge is. An overlap and a
  // finding are each about two things at once and neither points, so neither
  // has an arrow, and they are told apart by their dash.
  const EDGE_DASH = { OVERLAPS: [6, 4], [FINDING]: [1, 4] };
  const UNDIRECTED = ["OVERLAPS", FINDING];

  // The event a press on a `More` node sends, at page level (`ui/app.py`).
  const MORE_EVENT = "portia:more-columns";

  // **The layout floats** *(2026-10-08)*. vis-network's own physics, with the
  // options the graph shipped with: the picture settles for this many steps
  // before it is shown, then goes on simulating until nothing moves, and a node
  // dragged pulls its neighbours along. Earlier that day the layout ran once
  // and then physics went off for good, because six hundred nodes never rested
  // (about 390px every five seconds, with nothing happening). The user wanted
  // the floating back and put the drift down to the number of columns, which
  // `query.choose_columns` now holds to a few hundred. Measured on a project of
  // 6,164 columns: its picture of 280 nodes comes to rest on its own in about
  // eight seconds. One of 700, most of them a model's columns each linked to
  // the column it came from, was still moving 250px a second after thirty.
  const SETTLE_STEPS = 200;
  // The same project lays out the same way the first time, so a picture drawn
  // twice from nothing is one picture rather than two.
  const SEED = 7;

  // The tokens live on `body` — `body.body--dark` holds the dark block — so
  // they are read there; `:root` alone would answer in light mode every time.
  function token(name) {
    return getComputedStyle(document.body).getPropertyValue(name).trim();
  }

  function palette() {
    const t = (name) => token(`--${name}`);
    return {
      surface: t("surface"),
      canvas: t("canvas"),
      card: t("surface-card"),
      elevated: t("surface-elevated"),
      hairline: t("hairline"),
      strong: t("hairline-strong"),
      ink: t("ink"),
      body: t("body"),
      mute: t("mute"),
      stone: t("stone"),
      accent: t("accent-primary"),
      mono: t("font-mono"),
      sans: t("font-sans"),
    };
  }

  // One style per kind, as vis-network groups. `selected` is the border a node
  // takes when clicked: the accent, at the same width, and never a new fill.
  function box(p, { fill, edge, text, font, radius, size = 12, dashes = false }) {
    return {
      shape: "box",
      borderWidth: 1,
      borderWidthSelected: 1,
      margin: { top: 6, right: 10, bottom: 6, left: 10 },
      shapeProperties: { borderRadius: radius, borderDashes: dashes },
      color: {
        background: fill,
        border: edge,
        highlight: { background: fill, border: p.accent },
        hover: { background: fill, border: p.mute },
      },
      font: { face: font, size, color: text },
    };
  }

  function groups(p) {
    return {
      Source: box(p, { fill: p.canvas, edge: p.hairline, text: p.mute, font: p.mono, radius: 6 }),
      Model: box(p, { fill: p.card, edge: p.strong, text: p.ink, font: p.mono, radius: 8 }),
      Group: box(p, { fill: p.elevated, edge: p.strong, text: p.body, font: p.sans, radius: 12 }),
      Column: {
        shape: "dot",
        size: 5,
        borderWidth: 1,
        borderWidthSelected: 1,
        color: {
          background: p.stone,
          border: p.strong,
          highlight: { background: p.stone, border: p.accent },
          hover: { background: p.stone, border: p.mute },
        },
        font: { face: p.mono, size: 11, color: p.mute },
      },
      // A count, so mono, at a column's size and ink; dashed, because it
      // stands in for columns that are not on the picture — the ghost card's
      // *not quite here* (DESIGN.md), and not a colour, so it cannot read as
      // a severity.
      [MORE]: box(p, {
        fill: p.surface,
        edge: p.strong,
        text: p.mute,
        font: p.mono,
        radius: 12,
        size: 11,
        dashes: [3, 3],
      }),
    };
  }

  // Every edge is a hairline in the same ink; what kind it is, the dash and the
  // arrowhead already say. A label is drawn over a halo of the canvas's own
  // surface — vis-network's default halo is white, which in dark mode is a
  // white smear around every number.
  function styles(p) {
    return {
      groups: groups(p),
      edges: {
        width: 1,
        selectionWidth: 0.5,
        hoverWidth: 0.5,
        color: { color: p.strong, highlight: p.accent, hover: p.mute, inherit: false },
        arrows: { to: { scaleFactor: 0.4 } },
        font: {
          face: p.mono,
          size: 10,
          color: p.mute,
          strokeWidth: 4,
          strokeColor: p.surface,
          align: "middle",
        },
      },
    };
  }

  function label(node) {
    return node.kind === "Column" ? node.label.split("::").pop() : node.label;
  }

  // Properties that are portia's bookkeeping or are already on the node's own
  // label, so repeating them in a tooltip is noise.
  const HIDDEN = ["_build", "name", "key", "table", "path"];

  // A tooltip is a glance, not a document. Long values — a prose summary, an
  // `asked_because` sentence — are wrapped rather than run off the right edge of
  // the window, which is what an unwrapped one does.
  const WRAP_AT = 64;

  function wrap(text) {
    const words = String(text).split(/\s+/);
    const lines = [];
    let line = "";
    for (const word of words) {
      if (line && (line + " " + word).length > WRAP_AT) {
        lines.push(line);
        line = word;
      } else {
        line = line ? line + " " + word : word;
      }
    }
    if (line) lines.push(line);
    return lines.join("\n    ");
  }

  function tooltip(item) {
    const lines = [item.kind + (item.label ? `  ${item.label}` : "")];
    for (const [key, value] of Object.entries(item.properties || {})) {
      if (HIDDEN.includes(key) || value === null || value === "") continue;
      lines.push(`${key}: ${wrap(value)}`);
    }
    return lines.join("\n");
  }

  // A `More` node's hover is its name as a control: what pressing it does, in
  // the words the source inspector's own button uses.
  function moreTip(node) {
    const n = Number(node.properties.n_columns || 0).toLocaleString("en-US");
    return `Show all ${n} columns`;
  }

  // A finding's hover: the agent's three sentences, whether a table moved since,
  // and each query with its result as `findings.result_lines` laid it out —
  // copied from the log, so these are the numbers the finding rests on and not
  // a summary of them.
  function findingTip(edge) {
    const p = edge.properties || {};
    const lines = [edge.kind];
    for (const key of ["question", "answer", "so", "stale"]) {
      if (p[key]) lines.push(`${key}: ${wrap(p[key])}`);
    }
    for (const query of p.queries || []) {
      lines.push(`query: ${wrap(query.question || "")}`);
      for (const line of query.result || []) lines.push(`    ${wrap(line)}`);
    }
    if (p.file) lines.push(`file: ${p.file}`);
    return lines.join("\n");
  }

  // **Where every node sits is the client's**, like the pan and the zoom
  // (DESIGN.md → Window: they never round-trip). A refresh of the middle pane
  // replaces the canvas, and the new one starts every node it has seen before
  // where the old one had floated it to, by its id, so a refresh does not throw
  // the picture into a new shape; the physics carries on from there. Only a
  // node it has never seen is placed. Kept per view, because Tables and Columns
  // are two pictures of one graph.
  const PLACED = {};
  const LOOKING = {};

  // Where the nodes are now, and the view while the canvas is on the page: a
  // canvas taken off it has no width, and its centre would read as its corner.
  function remember(view, network) {
    Object.assign(PLACED[view], network.getPositions());
    if (!network.body.container.isConnected) return;
    LOOKING[view] = { position: network.getViewPosition(), scale: network.getScale() };
  }

  // A node the reader has not seen yet starts beside one they have — a new
  // column beside its table — so the layout it settles into is a local one.
  // The golden angle fans several around one neighbour rather than stacking
  // them on one point.
  function seed(edges, placed) {
    const at = {};
    let n = 0;
    for (const e of edges) {
      for (const [near, far] of [
        [e.from, e.to],
        [e.to, e.from],
      ]) {
        if (placed[near] || at[near] || !placed[far]) continue;
        const angle = n++ * 2.39996;
        at[near] = { x: placed[far].x + 60 * Math.cos(angle), y: placed[far].y + 60 * Math.sin(angle) };
      }
    }
    return at;
  }

  // Every network drawn and still on the page. NiceGUI replaces the canvas
  // rather than patching it, so the one it replaced is destroyed here rather
  // than left holding its canvas and its listeners.
  const LIVE = new Set();

  function sweep() {
    for (const network of LIVE) {
      if (network.body.container.isConnected) continue;
      // Where it had floated to, for the canvas that replaced it.
      remember(network.__view, network);
      network.destroy();
      LIVE.delete(network);
    }
  }

  function draw(host) {
    const holder = host.querySelector(".p-knowledge-data");
    const container = host.querySelector(".p-knowledge");
    const raw = holder && holder.textContent;
    if (!container || !raw || typeof vis === "undefined" || container.__drawn === raw) return;
    let data;
    try {
      data = JSON.parse(raw);
    } catch (err) {
      return;
    }
    container.__drawn = raw;
    const view = container.dataset.view || "";
    const placed = PLACED[view] || (PLACED[view] = {});
    const looking = LOOKING[view];
    const byId = {};
    for (const n of data.nodes) byId[n.id] = n;
    const fresh = data.nodes.some((n) => !placed[n.id]);
    const seeded = seed(data.edges, placed);

    const nodes = new vis.DataSet(
      data.nodes.map((n) => {
        const node = {
          id: n.id,
          label: label(n),
          title: n.kind === MORE ? moreTip(n) : tooltip(n),
          group: KINDS.includes(n.kind) || n.kind === MORE ? n.kind : "Column",
        };
        const at = placed[n.id] || seeded[n.id];
        if (at) Object.assign(node, { x: at.x, y: at.y });
        // What the reader has seen holds still while what is new finds a place,
        // and floats with the rest once it has.
        if (placed[n.id] && fresh) node.fixed = true;
        return node;
      }),
    );
    const between = {};
    const edges = new vis.DataSet(
      data.edges.map((e) => {
        const edge = {
          from: e.from,
          to: e.to,
          title: e.kind === FINDING ? findingTip(e) : tooltip(e),
          label: e.kind === "OVERLAPS" ? String(e.properties.n_measured_pairs || "") : "",
          dashes: EDGE_DASH[e.kind] || false,
          arrows: UNDIRECTED.includes(e.kind) ? "" : "to",
        };
        if (e.kind === FINDING) {
          // Two findings about one pair of tables are two lines, bowed apart so
          // each can be hovered. The bow is the order they came in and says
          // nothing else.
          const pair = [e.from, e.to].sort().join("|");
          between[pair] = (between[pair] || 0) + 1;
          edge.smooth = { type: "curvedCW", roundness: 0.15 * between[pair] };
        }
        return edge;
      }),
    );

    const look = styles(palette());
    const network = new vis.Network(
      container,
      { nodes, edges },
      {
        layout: { randomSeed: SEED },
        // Settling runs before anything is drawn, so it runs only when there
        // is a node to place. A picture of nodes all seen before is drawn at
        // once where they were and floats on from there, rather than waiting
        // on a layout it has already found.
        physics: {
          stabilization: fresh ? { iterations: SETTLE_STEPS, fit: !looking } : false,
        },
        interaction: { hover: true, tooltipDelay: 120 },
        groups: look.groups,
        edges: { ...look.edges, smooth: { type: "continuous" } },
      },
    );
    container.__network = network;
    network.__view = view;
    LIVE.add(network);
    if (looking) network.moveTo({ position: looking.position, scale: looking.scale });

    if (fresh) {
      network.once("stabilizationIterationsDone", () => {
        nodes.update(data.nodes.map((n) => ({ id: n.id, fixed: false })));
        remember(view, network);
      });
    }
    // Come to rest, after the first settling or after a drag; a node let go
    // of, or the view after a pan; and a zoom.
    network.on("stabilized", () => remember(view, network));
    network.on("dragEnd", () => remember(view, network));
    network.on("zoom", () => remember(view, network));

    network.on("click", (params) => {
      const node = params.nodes.length === 1 ? byId[params.nodes[0]] : null;
      if (!node || node.kind !== MORE || typeof emitEvent !== "function") return;
      emitEvent(MORE_EVENT, { kind: node.properties.table_kind, name: node.properties.table });
    });
    // The one node here that is a control says so under the pointer.
    network.on("hoverNode", (params) => {
      const node = byId[params.node];
      container.style.cursor = node && node.kind === MORE ? "pointer" : "";
    });
    network.on("blurNode", () => {
      container.style.cursor = "";
    });
  }

  function drawAll() {
    sweep();
    document.querySelectorAll(".p-knowledge-host").forEach(draw);
  }

  // One `drawAll()` per frame, not one per mutation — `chart.js`'s rule. The
  // check per canvas is a string comparison, and a canvas already drawn from
  // the same data is left alone.
  let queued = false;
  const observer = new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      drawAll();
    });
  });

  // A theme switch restyles every graph on screen in place: a canvas already
  // painted does not follow a CSS variable, and redrawing from scratch would
  // run the force layout again and move every node the reader was looking at.
  // Quasar flips `body--dark` for the settings panel and for the system
  // preference alike, so watching the class catches both.
  let dark = null;
  function restyle() {
    const now = document.body.classList.contains("body--dark");
    if (now === dark) return;
    dark = now;
    const look = styles(palette());
    document.querySelectorAll(".p-knowledge").forEach((el) => {
      if (el.__network) el.__network.setOptions(look);
    });
  }
  document.addEventListener("DOMContentLoaded", () => {
    dark = document.body.classList.contains("body--dark");
    new MutationObserver(restyle).observe(document.body, {
      attributes: true,
      attributeFilter: ["class"],
    });
    drawAll();
    observer.observe(document.body, { childList: true, subtree: true });
  });

  return { draw, drawAll };
})();
