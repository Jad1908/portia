// Drag the workflow canvas to move around it.
//
// Panning only — nodes do not move. The graph's layout is the recorded sequence
// of decisions (portia/ui/graph.py), so dragging a card would either mean
// nothing or imply the order can be rearranged, and neither is true yet.
//
// **It moves a transform, not a scroll offset.** The first version scrolled the
// canvas, which meant a graph that fitted its pane had nothing to scroll and
// dragging it did precisely nothing — panning only ever worked on a graph big
// enough to overflow, which is the one case you least need it. Translating the
// content works at every size, and it is what lets the dot grid travel with the
// nodes: the same two custom properties drive `transform` on the content and
// `background-position` on the canvas, so the grid cannot drift out of step.
//
// Delegated from the document so it survives every pane refresh without being
// re-attached, and `preventDefault` on mousedown stops a drag from turning into
// a text selection across the whole report.
(() => {
  if (window.__portiaPan) return;
  window.__portiaPan = true;

  // Anything you can click is not a handle to drag the canvas by.
  const IGNORE =
    ".step-card, .source-node, .model-card, button, input, textarea, a, .q-expansion-item";

  let canvas = null;
  let startX = 0;
  let startY = 0;
  let baseX = 0;
  let baseY = 0;

  // Read from the inline style, which is the only place this writes. The
  // stylesheet's `--pan-x: 0px` is the at-rest value and stays untouched, so a
  // pane refresh drops the inline style and the graph comes back centred.
  const readPan = (el) => [
    parseFloat(el.style.getPropertyValue("--pan-x")) || 0,
    parseFloat(el.style.getPropertyValue("--pan-y")) || 0,
  ];

  const writePan = (el, x, y) => {
    el.style.setProperty("--pan-x", `${x}px`);
    el.style.setProperty("--pan-y", `${y}px`);
  };

  // You can pan a long way from a small graph, so there has to be a way back.
  // Called by the Recenter button; double-clicking the canvas does the same.
  window.portiaRecenter = () => {
    document.querySelectorAll(".graph-canvas").forEach((el) => writePan(el, 0, 0));
  };

  // Bring a node into view. Picking a spec on the left focuses its card here, so
  // the left panel navigates the graph rather than replacing it — the canvas is
  // the one place both zoom levels are true at once and swapping it out on every
  // click would throw that away.
  window.portiaPanTo = (x, y) => {
    document.querySelectorAll(".graph-canvas").forEach((el) => writePan(el, x, y));
  };

  document.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    const found = e.target.closest(".graph-canvas");
    if (!found || e.target.closest(IGNORE)) return;
    canvas = found;
    startX = e.clientX;
    startY = e.clientY;
    [baseX, baseY] = readPan(found);
    found.classList.add("is-panning");
    e.preventDefault();
  });

  document.addEventListener("mousemove", (e) => {
    if (!canvas) return;
    writePan(canvas, baseX + (e.clientX - startX), baseY + (e.clientY - startY));
  });

  const stop = () => {
    if (canvas) canvas.classList.remove("is-panning");
    canvas = null;
  };
  document.addEventListener("mouseup", stop);
  document.addEventListener("mouseleave", stop);

  document.addEventListener("dblclick", (e) => {
    const found = e.target.closest(".graph-canvas");
    if (found && !e.target.closest(IGNORE)) writePan(found, 0, 0);
  });
})();
