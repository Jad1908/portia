// Tell Python how wide the window is.
//
// `DESIGN.md` → Width behaviour specifies three bands, and none of it could be
// done in CSS once the panes moved inside Quasar splitters: a splitter sets an
// inline pixel width on its panel, so restyling the pane inside it changes
// nothing about the space the panel reserves. The stylesheet had media queries
// that read correctly and did nothing at all — below 1024 the left pane went
// `position: absolute` to "overlay", and its splitter panel went on holding
// 260px of layout beside it.
//
// So the width comes to the server, and `app.py` decides which panes are showing
// and how much room the splitter may give away. Debounced, because a drag-resize
// fires this continuously and each band change redraws the window.
(() => {
  if (window.__portiaViewport) return;
  window.__portiaViewport = true;

  const DEBOUNCE_MS = 150;
  let timer = null;

  const report = () => {
    if (typeof emitEvent === "function") emitEvent("portia:viewport", window.innerWidth);
  };

  window.addEventListener("resize", () => {
    clearTimeout(timer);
    timer = setTimeout(report, DEBOUNCE_MS);
  });

  // The socket is not necessarily up when this parses, and a first report that
  // silently goes nowhere would leave the layout on its default band until the
  // window happened to be resized.
  setTimeout(report, DEBOUNCE_MS);
  window.addEventListener("load", () => setTimeout(report, DEBOUNCE_MS));
})();
