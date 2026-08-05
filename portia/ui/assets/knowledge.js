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
  // Kind decides colour. Nothing here scales with a number, orders by one, or
  // makes a well-connected node bigger — DESIGN.md's rule survives the move to
  // a third-party renderer, for everything the renderer lets us decide.
  const NODE_COLOUR = {
    Source: "#5b8def",
    Model: "#7b61ff",
    Column: "#8a94a6",
    Group: "#3aa66d",
  };
  const EDGE_DASH = { OVERLAPS: [6, 4] };

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
      color: NODE_COLOUR[n.kind] || NODE_COLOUR.Column,
      shape: n.kind === "Column" ? "dot" : "box",
      size: 10,
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
    container.__network = new vis.Network(
      container,
      { nodes: new vis.DataSet(nodes), edges: new vis.DataSet(edges) },
      {
        physics: { stabilization: { iterations: 200 } },
        interaction: { hover: true, tooltipDelay: 120 },
        nodes: { font: { size: 12 } },
        edges: { font: { size: 10, align: "middle" }, smooth: { type: "continuous" } },
      },
    );
  }

  return { draw };
})();
