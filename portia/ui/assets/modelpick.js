// The open model picker: which provider's list is showing, what the search
// box holds, whether the legacy models are unfolded, and which row the arrow
// keys are on (`components.model_menu`, `DESIGN.md` → `model-picker`).
//
// **All of it is where the picker is looking, so all of it is the client's.**
// The rule the canvas's pan and zoom follow, for the same two reasons: a round
// trip per keystroke would make the box being typed into the laggiest thing on
// screen, and a server redraw of the menu would rebuild that box under the
// caret. The server draws every offered provider's list once, marks the
// picked one `data-active`, and hears nothing until a row is pressed.
//
// Nothing here survives the menu closing, on purpose: Quasar mounts a menu's
// content each time it opens, so every open starts on the server's picture
// (the picked provider, an empty search, legacy folded unless the pick is in
// it), which is the one worth starting from.
(() => {
  if (window.__portiaModelPick) return;
  window.__portiaModelPick = true;

  const SEARCH = "[data-modelpick-search]";
  const ROW = "[data-modelpick-row]";
  const TYPED = "[data-modelpick-typed]";

  const menuOf = (el) => el.closest("[data-modelpick]");
  const activePanel = (menu) => menu.querySelector("[data-modelpick-panel][data-active]");

  // Rows a press can reach right now: the active provider's, not filtered out,
  // not folded away, then the typed row when it is offered. `offsetParent` is
  // null for anything `display: none`, which is how the fold hides its rows.
  const reachable = (menu) => {
    const panel = activePanel(menu);
    const rows = panel ? [...panel.querySelectorAll(ROW)] : [];
    const typed = menu.querySelector(TYPED);
    return [...rows, typed].filter((r) => r && r.offsetParent !== null);
  };

  const setCursor = (menu, row) => {
    menu.querySelectorAll("[data-cursor]").forEach((r) => r.removeAttribute("data-cursor"));
    if (row) {
      row.setAttribute("data-cursor", "");
      row.scrollIntoView({ block: "nearest" });
    }
  };

  // The search matches a model's name and its label, across current and
  // legacy alike: a search is for finding one, and a fold it cannot see into
  // would hide the answer. A name nothing matches exactly is offered as typed,
  // because `--model` takes names no list holds.
  const filter = (menu) => {
    const input = menu.querySelector(SEARCH);
    const typedName = (input?.value || "").trim();
    const query = typedName.toLowerCase();
    const panel = activePanel(menu);
    menu.toggleAttribute("data-searching", query !== "");
    let exact = false;
    menu.querySelectorAll(ROW).forEach((row) => {
      const hit = query === "" || (row.dataset.search || "").includes(query);
      row.toggleAttribute("data-hide", !hit);
      if (hit && panel && panel.contains(row) && (row.dataset.name || "").toLowerCase() === query) {
        exact = true;
      }
    });
    const typed = menu.querySelector(TYPED);
    if (typed && panel) {
      typed.toggleAttribute("data-hide", query === "" || exact);
      typed.dataset.kind = panel.dataset.modelpickPanel || "";
      typed.dataset.name = typedName;
      const name = typed.querySelector(".modelpick-row-name");
      if (name) name.textContent = typedName;
    }
    setCursor(menu, query === "" ? null : reachable(menu)[0]);
  };

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;

    // Opening: put the caret in the search box once the menu has mounted.
    // Quasar mounts it a frame or two after the press, so this looks for it
    // for a few frames rather than guessing one.
    if (target.closest("[data-modelpick-trigger]")) {
      let tries = 0;
      const focus = () => {
        const box = document.querySelector(`.q-menu ${SEARCH}`);
        if (box) box.focus();
        else if (tries++ < 20) requestAnimationFrame(focus);
      };
      requestAnimationFrame(focus);
      return;
    }

    const rail = target.closest("[data-modelpick-rail]");
    if (rail) {
      const menu = menuOf(rail);
      if (!menu) return;
      const kind = rail.dataset.modelpickRail;
      menu.querySelectorAll("[data-modelpick-rail]").forEach((r) => {
        r.toggleAttribute("data-active", r === rail);
      });
      menu.querySelectorAll("[data-modelpick-panel]").forEach((p) => {
        p.toggleAttribute("data-active", p.dataset.modelpickPanel === kind);
      });
      filter(menu);
      menu.querySelector(SEARCH)?.focus();
      return;
    }

    const fold = target.closest("[data-modelpick-fold]");
    if (fold) {
      fold.closest("[data-modelpick-panel]")?.toggleAttribute("data-legacy-open");
    }
  });

  document.addEventListener("input", (event) => {
    const target = event.target;
    if (target instanceof Element && target.matches(SEARCH)) {
      const menu = menuOf(target);
      if (menu) filter(menu);
    }
  });

  document.addEventListener("keydown", (event) => {
    const target = event.target;
    if (!(target instanceof Element) || !target.matches(SEARCH)) return;
    const menu = menuOf(target);
    if (!menu) return;
    const rows = reachable(menu);
    const at = rows.findIndex((r) => r.hasAttribute("data-cursor"));
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!rows.length) return;
      const step = event.key === "ArrowDown" ? 1 : -1;
      const next = at === -1 ? (step === 1 ? 0 : rows.length - 1) : (at + step + rows.length) % rows.length;
      setCursor(menu, rows[next]);
    } else if (event.key === "Enter") {
      const row = at === -1 ? rows[0] : rows[at];
      if (row) {
        event.preventDefault();
        row.click();
      }
    }
  });
})();
