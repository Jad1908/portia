/* The page's three interactions: pick a log, filter kinds, open everything.
 *
 * No framework and no build step — the page has to be one file you can open
 * from anywhere, including a copy someone kept from a run three weeks ago.
 * Everything else the page does is a native <details>.
 */

(function () {
  "use strict";

  var picks = Array.prototype.slice.call(document.querySelectorAll(".pick"));
  var logs = Array.prototype.slice.call(document.querySelectorAll(".log"));

  function show(id) {
    logs.forEach(function (log) { log.classList.toggle("on", log.id === id); });
    picks.forEach(function (p) { p.classList.toggle("on", p.dataset.target === id); });
    window.scrollTo(0, 0);
  }

  picks.forEach(function (pick) {
    pick.addEventListener("click", function () { show(pick.dataset.target); });
  });

  /* The newest log, which is the one you just ran — unless the URL names one.
     A link into a page is how you point someone at the call you are talking
     about, and the call is inside a log that has to be showing first. */
  if (logs.length) { show(logFromHash() || logs[0].id); }

  function logFromHash() {
    var target = hashTarget();
    if (!target) { return null; }
    var log = target.closest(".log");
    return log ? log.id : null;
  }

  function hashTarget() {
    if (!window.location.hash || window.location.hash === "#") { return null; }
    try { return document.querySelector(window.location.hash); } catch (e) { return null; }
  }

  document.querySelectorAll("[data-filter]").forEach(function (box) {
    box.addEventListener("change", function () {
      /* The box says what to *show*, so checking it clears the hiding class —
         except "errors only", which is a filter you switch on. */
      var key = box.dataset.filter;
      var cls = key === "errors-only" ? "errors-only" : "hide-" + key;
      var on = key === "errors-only" ? box.checked : !box.checked;
      document.body.classList.toggle(cls, on);
    });
    /* Everything is visible until you say otherwise. */
    if (box.dataset.filter !== "errors-only") { box.checked = true; }
  });

  document.querySelectorAll("[data-act]").forEach(function (button) {
    button.addEventListener("click", function () {
      var open = button.dataset.act === "expand";
      var active = document.querySelector(".log.on") || document;
      active.querySelectorAll("details").forEach(function (d) { d.open = open; });
    });
  });

  /* A chip links to a call that may be inside a collapsed ancestor. */
  window.addEventListener("hashchange", function () {
    var id = logFromHash();
    if (id) { show(id); }
    reveal();
  });
  reveal();

  function reveal() {
    var node = hashTarget();
    if (!node) { return; }
    var found = node;
    while (node && node !== document.body) {
      if (node.tagName === "DETAILS") { node.open = true; }
      node = node.parentElement;
    }
    found.scrollIntoView();
  }
})();
