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
  const EDGE_DASH = { OVERLAPS: [6, 4] };

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
  function box(p, { fill, edge, text, font, radius }) {
    return {
      shape: "box",
      borderWidth: 1,
      borderWidthSelected: 1,
      margin: { top: 6, right: 10, bottom: 6, left: 10 },
      shapeProperties: { borderRadius: radius },
      color: {
        background: fill,
        border: edge,
        highlight: { background: fill, border: p.accent },
        hover: { background: fill, border: p.mute },
      },
      font: { face: font, size: 12, color: text },
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

  function draw(elementId, data) {
    const container = document.getElementById(elementId);
    if (!container || typeof vis === "undefined") return;

    const nodes = data.nodes.map((n) => ({
      id: n.id,
      label: label(n),
      title: tooltip(n),
      group: KINDS.includes(n.kind) ? n.kind : "Column",
    }));
    const edges = data.edges.map((e) => ({
      from: e.from,
      to: e.to,
      title: tooltip(e),
      label: e.kind === "OVERLAPS" ? String(e.properties.n_measured_pairs || "") : "",
      dashes: EDGE_DASH[e.kind] || false,
      arrows: e.kind === "OVERLAPS" ? "" : "to",
    }));

    if (container.__network) container.__network.destroy();
    const look = styles(palette());
    container.__network = new vis.Network(
      container,
      { nodes: new vis.DataSet(nodes), edges: new vis.DataSet(edges) },
      {
        physics: { stabilization: { iterations: 200 } },
        interaction: { hover: true, tooltipDelay: 120 },
        groups: look.groups,
        edges: { ...look.edges, smooth: { type: "continuous" } },
      },
    );
  }

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
  });

  return { draw };
})();
