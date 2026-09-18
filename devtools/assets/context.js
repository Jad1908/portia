/* Three interactions: filter, open everything, follow a link into a fold.
 *
 * No framework and no build step — the page has to be one file you can open
 * from anywhere, including a copy you kept from before a prompt edit.
 */

(function () {
  "use strict";

  /* The filter matches a card's name *and* its text, because both are things
     you arrive with: sometimes you want `record_step`, and sometimes you want
     the sentence you half-remember writing and cannot find. */
  var box = document.querySelector("[data-find]");
  var cards = Array.prototype.slice.call(document.querySelectorAll(".card[data-name]"));

  if (box) {
    box.addEventListener("input", function () {
      var needle = box.value.trim().toLowerCase();
      cards.forEach(function (card) {
        var hit = !needle
          || (card.dataset.name || "").toLowerCase().indexOf(needle) !== -1
          || card.textContent.toLowerCase().indexOf(needle) !== -1;
        card.classList.toggle("hidden", !hit);
      });
      /* A section with nothing left in it is noise, so it goes too — but only
         one that had cards to begin with. */
      document.querySelectorAll(".sec").forEach(function (section) {
        var own = section.querySelectorAll(".card[data-name]");
        var left = section.querySelectorAll(".card[data-name]:not(.hidden)");
        section.classList.toggle("hidden", own.length > 0 && left.length === 0);
      });
    });
  }

  document.querySelectorAll("[data-act]").forEach(function (button) {
    button.addEventListener("click", function () {
      var open = button.dataset.act === "expand";
      document.querySelectorAll("details").forEach(function (d) { d.open = open; });
    });
  });

  /* A link from the ladder or from a message row may land inside a closed
     <details>; open every ancestor before scrolling to it. */
  window.addEventListener("hashchange", reveal);
  reveal();

  function reveal() {
    if (!window.location.hash || window.location.hash === "#") { return; }
    var node;
    try { node = document.querySelector(window.location.hash); } catch (e) { return; }
    if (!node) { return; }
    var found = node;
    while (node && node !== document.body) {
      if (node.tagName === "DETAILS") { node.open = true; }
      node = node.parentElement;
    }
    found.scrollIntoView();
  }
})();
