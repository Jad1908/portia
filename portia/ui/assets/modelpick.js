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
// Every open starts on the server's picture (the picked provider, an empty
// search, legacy folded unless the pick is in it), which is the one worth
// starting from. **But a redraw while it is open keeps where you were**
// *(2026-09-23)*: a listing asked for from inside the menu redraws its body in
// place (`components.redraw_behind_picker`), and the new body would otherwise
// jump back to the picked provider under the press that asked for Ollama's
// list. So what the menu is looking at is remembered from the press that
// opened it, and put back on a body that arrives while it is open.
(() => {
  if (window.__portiaModelPick) return;
  window.__portiaModelPick = true;

  const SEARCH = "[data-modelpick-search]";
  const ROW = "[data-modelpick-row]";
  const TYPED = "[data-modelpick-typed]";

  // Where the open menu is looking, or null for *the server's picture*.
  let memory = null; // { view, query, folds: { kind: open } }
  const remember = () => (memory = memory || { view: null, query: "", folds: {} });

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

  const show = (menu, kind) => {
    menu.querySelectorAll("[data-modelpick-rail]").forEach((r) => {
      r.toggleAttribute("data-active", r.dataset.modelpickRail === kind);
    });
    menu.querySelectorAll("[data-modelpick-panel]").forEach((p) => {
      p.toggleAttribute("data-active", p.dataset.modelpickPanel === kind);
    });
  };

  // A body the server drew while the menu was open: the provider, the folds
  // and the search go back as they were, and so does the caret.
  const restore = (menu) => {
    if (memory === null) return;
    if (memory.view && menu.querySelector(`[data-modelpick-panel="${memory.view}"]`)) {
      show(menu, memory.view);
    }
    Object.entries(memory.folds).forEach(([kind, open]) => {
      menu.querySelector(`[data-modelpick-panel="${kind}"]`)?.toggleAttribute("data-legacy-open", open);
    });
    const box = menu.querySelector(SEARCH);
    if (box) {
      box.value = memory.query;
      box.focus();
      box.setSelectionRange(box.value.length, box.value.length);
    }
    filter(menu);
  };

  new MutationObserver((records) => {
    if (memory === null) return;
    for (const record of records) {
      for (const node of record.addedNodes) {
        if (!(node instanceof Element)) continue;
        const menu = node.matches("[data-modelpick]") ? node : node.querySelector("[data-modelpick]");
        if (menu) restore(menu);
      }
    }
  }).observe(document.body, { childList: true, subtree: true });

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;

    // Opening: put the caret in the search box once the menu has mounted.
    // Quasar mounts it a frame or two after the press, so this looks for it
    // for a few frames rather than guessing one.
    if (target.closest("[data-modelpick-trigger]")) {
      memory = null;
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
      remember().view = kind;
      show(menu, kind);
      filter(menu);
      menu.querySelector(SEARCH)?.focus();
      return;
    }

    const fold = target.closest("[data-modelpick-fold]");
    if (fold) {
      const panel = fold.closest("[data-modelpick-panel]");
      if (panel) {
        const open = panel.toggleAttribute("data-legacy-open");
        remember().folds[panel.dataset.modelpickPanel] = open;
      }
    }
  });

  document.addEventListener("input", (event) => {
    const target = event.target;
    if (target instanceof Element && target.matches(SEARCH)) {
      const menu = menuOf(target);
      if (menu) {
        remember().query = target.value;
        filter(menu);
      }
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
