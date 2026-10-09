// *Interrupt query* on a running data call's card, and the menu it opens
// (`ui/interrupt.py`, `docs/CONVERSATION.md` §16).
//
// **The press is resolved here, by the card's id, not by an element.** Every
// event the copilot streams redraws the running card, so the control under the
// pointer is often not the one that was drawn when the pointer set off; a
// handler bound to it is lost with it (`pick.js`, the tree rows' finding). The
// control says which card it belongs to (`data-interrupt`), and the press goes
// to one page-level event with that id. Caught in the capture phase and
// stopped there, so the card's own disclosure does not open or shut as well.
//
// **The menu floats over the transcript and follows the control by id.** The
// server shows it and names the card it is about (`data-anchor`); this places
// it under whichever control carries that id now, every few frames while it is
// open, so a redraw of the card underneath does not leave it hanging over the
// place the old control was. A click anywhere else or Escape shuts it, which
// the server reads as the press undone.
(() => {
  if (window.__portiaInterrupt) return;
  window.__portiaInterrupt = true;

  // How often an open menu is placed again. An interval and not an animation
  // frame: a tab that is not painting never fires a frame (`DESIGN.md` →
  // `dialog`), and a menu that is not placed is a menu in the wrong corner.
  const PLACE_MS = 50;
  // The gap between the control and the menu, and the margin it keeps from the
  // window's edges.
  const GAP = 4;
  const MARGIN = 8;

  let placing = null;

  const send = (event, call) => {
    if (typeof emitEvent === "function") emitEvent(event, { call });
  };

  const openMenu = () => document.querySelector(".interrupt-menu[data-anchor]");

  const controlFor = (call) => {
    for (const element of document.querySelectorAll("[data-interrupt]")) {
      if (element.getAttribute("data-interrupt") === call) return element;
    }
    return null;
  };

  const place = () => {
    const menu = openMenu();
    if (!menu) {
      // Shut: hidden again, so the next open is placed before it is seen.
      for (const shut of document.querySelectorAll(".interrupt-menu")) shut.style.visibility = "";
      clearInterval(placing);
      placing = null;
      return;
    }
    const control = controlFor(menu.getAttribute("data-anchor"));
    if (!control) {
      // The card's call has answered and its control is gone; the server
      // shuts the menu on its next tick.
      menu.style.visibility = "hidden";
      return;
    }
    const at = control.getBoundingClientRect();
    const width = menu.offsetWidth;
    const height = menu.offsetHeight;
    let left = Math.min(at.right - width, window.innerWidth - width - MARGIN);
    left = Math.max(left, MARGIN);
    let top = at.bottom + GAP;
    if (top + height > window.innerHeight - MARGIN) top = Math.max(at.top - GAP - height, MARGIN);
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
    menu.style.visibility = "visible";
  };

  const follow = () => {
    place();
    if (placing === null) placing = setInterval(place, PLACE_MS);
  };

  // The server shows the menu by naming its card, and shuts it by naming none;
  // follow it while it is named, and hide it the moment it is not.
  new MutationObserver(() => {
    if (openMenu()) follow();
    else place();
  }).observe(document.body, { attributes: true, subtree: true, attributeFilter: ["data-anchor"] });

  document.addEventListener(
    "click",
    (e) => {
      const control = e.target.closest("[data-interrupt]");
      if (control) {
        e.preventDefault();
        e.stopPropagation();
        send("portia:interrupt", control.getAttribute("data-interrupt"));
        return;
      }
      const menu = openMenu();
      if (menu && !menu.contains(e.target)) send("portia:interrupt-close", menu.getAttribute("data-anchor"));
    },
    true,
  );

  document.addEventListener(
    "keydown",
    (e) => {
      if (e.key !== "Escape") return;
      const menu = openMenu();
      if (menu) send("portia:interrupt-close", menu.getAttribute("data-anchor"));
    },
    true,
  );
})();
