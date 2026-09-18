// Dragging a figure into a folder, resolved on the client.
//
// `docs/VISUALIZATION.md` §6. The gallery's arrangement is the user's work, and
// a drag is how a person moves a thing into a folder — a *move to…* menu is the
// same operation with two extra decisions in it.
//
// **It has to be resolved here, and that is the rule `pick.js` documents.**
// NiceGUI replaces elements rather than patching them, so a drag that reported
// its start to the server would find, by the time it ended, that the row it
// began on had been rebuilt and the row under the cursor was a different
// element. Nothing reaches Python until the whole gesture is known: which
// figure, which folder, one event.
//
// The DOM says what is draggable and what accepts a drop, in portia's own
// vocabulary — `data-figure` is a repo-relative path, `data-folder` is a path
// relative to `figures/`, and the empty string is the gallery's own top level.
// The server never learns that HTML5 has a drag protocol.

window.portiaGallery = (function () {
  const FIGURE = "data-figure";
  const FOLDER = "data-folder";

  let dragging = null;

  function figureRow(node) {
    return node && node.closest ? node.closest(`[${FIGURE}]`) : null;
  }

  function folderRow(node) {
    return node && node.closest ? node.closest(`[${FOLDER}]`) : null;
  }

  function clearMarks() {
    document
      .querySelectorAll(".gallery-drop-into")
      .forEach((el) => el.classList.remove("gallery-drop-into"));
  }

  document.addEventListener("dragstart", (event) => {
    const row = figureRow(event.target);
    if (!row) return;
    dragging = row.getAttribute(FIGURE);
    // `move` rather than `copy`: the cursor is the only thing telling you which
    // one this is, and a copy cursor on a move is a small lie.
    event.dataTransfer.effectAllowed = "move";
    // Firefox will not start a drag without payload set.
    event.dataTransfer.setData("text/plain", dragging);
    row.classList.add("gallery-dragging");
  });

  document.addEventListener("dragend", () => {
    dragging = null;
    clearMarks();
    document
      .querySelectorAll(".gallery-dragging")
      .forEach((el) => el.classList.remove("gallery-dragging"));
  });

  document.addEventListener("dragover", (event) => {
    if (dragging === null) return;
    const target = folderRow(event.target);
    if (!target) return;
    // Without this the browser refuses the drop, silently, and the gesture just
    // does nothing — which reads as a broken feature rather than a refusal.
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    if (!target.classList.contains("gallery-drop-into")) {
      clearMarks();
      target.classList.add("gallery-drop-into");
    }
  });

  document.addEventListener("dragleave", (event) => {
    const target = folderRow(event.target);
    if (target) target.classList.remove("gallery-drop-into");
  });

  document.addEventListener("drop", (event) => {
    if (dragging === null) return;
    const target = folderRow(event.target);
    clearMarks();
    if (!target) return;
    event.preventDefault();
    const folder = target.getAttribute(FOLDER) || "";
    const figure = dragging;
    dragging = null;
    // One event, with the gesture already decided. The server has no drag state
    // to keep in step and no half-finished move to clean up if the pane
    // refreshes underneath this.
    if (window.emitEvent) window.emitEvent("portia:figure-move", { figure, folder });
  });

  return { };
})();
