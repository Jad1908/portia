// Drag the workflow canvas to move around it.
//
// Panning only — nodes do not move. The graph's layout is the recorded sequence
// of decisions (portia/ui/graph.py), so dragging a card would either mean
// nothing or imply the order can be rearranged, and neither is true yet.
//
// Delegated from the document so it survives every pane refresh without being
// re-attached, and `preventDefault` on mousedown stops a drag from turning into
// a text selection across the whole report.
(() => {
  if (window.__portiaPan) return;
  window.__portiaPan = true;

  const IGNORE = ".step-card, .source-node, button, input, textarea, a, .q-expansion-item";
  let canvas = null;
  let startX = 0;
  let startY = 0;
  let startLeft = 0;
  let startTop = 0;

  document.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    const found = e.target.closest(".graph-canvas");
    if (!found || e.target.closest(IGNORE)) return;
    canvas = found;
    startX = e.clientX;
    startY = e.clientY;
    startLeft = found.scrollLeft;
    startTop = found.scrollTop;
    found.classList.add("is-panning");
    e.preventDefault();
  });

  document.addEventListener("mousemove", (e) => {
    if (!canvas) return;
    canvas.scrollLeft = startLeft - (e.clientX - startX);
    canvas.scrollTop = startTop - (e.clientY - startY);
  });

  const stop = () => {
    if (canvas) canvas.classList.remove("is-panning");
    canvas = null;
  };
  document.addEventListener("mouseup", stop);
  document.addEventListener("mouseleave", stop);
})();
