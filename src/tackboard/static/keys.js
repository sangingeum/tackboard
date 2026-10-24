/* Global keyboard shortcuts and the shortcut help dialog.
 *
 * One handler for the whole page. It refuses to act while a text field has
 * focus and suspends every binding except Escape while a dialog is open. The
 * graph-specific commands are dispatched as one CustomEvent on #graph so this
 * file never reaches into the renderer's internals; board commands act on the
 * cards directly. Every binding in the design's shortcut table is here.
 */
(function () {
  "use strict";

  var SEQUENCE_MS = 1000;
  var gPending = false;
  var gTimer = 0;
  var cardIndex = -1;

  function closest(node, selector) {
    while (node && node !== document) {
      if (node.closest) { return node.closest(selector); }
      if (node.matches && node.matches(selector)) { return node; }
      node = node.parentNode;
    }
    return null;
  }

  function isTyping(target) {
    if (!target) { return false; }
    return !!(closest(target, "input, textarea, select, [contenteditable]"));
  }

  function helpDialog() {
    return document.getElementById("help-dialog");
  }

  function dialogOpen() {
    var dialog = helpDialog();
    return !!(dialog && dialog.open);
  }

  function openHelp(opener) {
    var dialog = helpDialog();
    if (!dialog) { return; }
    dialog.__opener = opener || document.activeElement;
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
    var close = dialog.querySelector("[data-close-help]");
    if (close) { close.focus(); }
  }

  function closeHelp() {
    var dialog = helpDialog();
    if (!dialog) { return; }
    if (typeof dialog.close === "function" && dialog.open) {
      dialog.close();
    } else {
      dialog.removeAttribute("open");
    }
    var opener = dialog.__opener;
    if (opener && opener.focus) { opener.focus(); }
  }

  function dispatchGraph(action, detail) {
    var root = document.getElementById("graph");
    if (!root) { return false; }
    root.dispatchEvent(new CustomEvent("tack:graph-command", {
      bubbles: true,
      detail: { action: action, detail: detail || null }
    }));
    return true;
  }

  function cards() {
    return Array.prototype.slice.call(
      document.querySelectorAll("#board .card[data-task]")
    );
  }

  function moveCard(delta) {
    var list = cards();
    if (!list.length) { return; }
    cardIndex = Math.max(0, Math.min(list.length - 1, cardIndex + delta));
    var card = list[cardIndex];
    if (!card) { return; }
    card.setAttribute("tabindex", "0");
    card.focus();
    if (card.scrollIntoView) {
      card.scrollIntoView({ block: "nearest" });
    }
  }

  function clearCardCursor() {
    cards().forEach(function (card) {
      card.setAttribute("tabindex", "-1");
    });
    cardIndex = -1;
  }

  function focusFilter() {
    var details = document.querySelector(".filters__more");
    if (details) { details.open = true; }
    var input = document.getElementById("filter-assignee") ||
      document.querySelector(".filters input");
    if (input) { input.focus(); }
  }

  function openFilters() {
    var details = document.querySelector(".filters__more");
    if (!details) { return; }
    details.open = true;
    var first = details.querySelector("input, select, button");
    if (first) { first.focus(); }
  }

  document.addEventListener("keydown", function (event) {
    if (event.defaultPrevented) { return; }
    if (isTyping(event.target)) { return; }

    var key = event.key;
    var open = dialogOpen();

    if (open) {
      if (key === "Escape") {
        event.preventDefault();
        closeHelp();
      }
      /* Everything else belongs to the dialog: Tab is native. */
      return;
    }

    if (gPending) {
      gPending = false;
      window.clearTimeout(gTimer);
      if (key === "b") {
        event.preventDefault();
        window.location.assign("/board");
        return;
      }
      if (key === "g") {
        event.preventDefault();
        window.location.assign("/graph");
        return;
      }
      /* An unrecognised second key starts a fresh sequence below. */
    }

    if (key === "g") {
      gPending = true;
      window.clearTimeout(gTimer);
      gTimer = window.setTimeout(function () { gPending = false; }, SEQUENCE_MS);
      event.preventDefault();
      return;
    }

    if (key === "?") {
      event.preventDefault();
      openHelp(event.target);
      return;
    }

    if (key === "Escape") {
      dispatchGraph("escape");
      clearCardCursor();
      return;
    }

    if (key === "t") {
      var theme = document.getElementById("theme-toggle");
      if (theme) {
        event.preventDefault();
        theme.click();
      }
      return;
    }

    if (key === "c") {
      if (dispatchGraph("toggle-claimed")) { event.preventDefault(); }
      return;
    }

    if (key === "h") {
      if (dispatchGraph("toggle-hide-closed")) { event.preventDefault(); }
      return;
    }

    if (key === "+" || key === "=") {
      if (dispatchGraph("zoom-in")) { event.preventDefault(); }
      return;
    }

    if (key === "-" || key === "_") {
      if (dispatchGraph("zoom-out")) { event.preventDefault(); }
      return;
    }

    if (key === "0") {
      if (dispatchGraph("fit-all")) { event.preventDefault(); }
      return;
    }

    if (key === "/") {
      event.preventDefault();
      focusFilter();
      return;
    }

    if (key === "f") {
      event.preventDefault();
      openFilters();
      return;
    }

    if (key === "j" || key === "k") {
      if (!document.getElementById("board")) { return; }
      event.preventDefault();
      moveCard(key === "j" ? 1 : -1);
      return;
    }

    if (key === "Enter") {
      var active = document.activeElement;
      var tag = active && active.tagName ? active.tagName.toLowerCase() : "";
      if (tag === "button" || tag === "a" || tag === "summary") { return; }
      if (active && active.classList && active.classList.contains("card")) {
        var link = active.querySelector("a.card__id");
        if (link) {
          event.preventDefault();
          link.click();
        }
        return;
      }
      if (document.getElementById("graph")) {
        if (dispatchGraph("activate")) { event.preventDefault(); }
      }
      return;
    }

    if (key === "ArrowLeft" || key === "ArrowRight" ||
        key === "ArrowUp" || key === "ArrowDown") {
      if (document.getElementById("graph")) {
        if (dispatchGraph("move", key)) { event.preventDefault(); }
      }
    }
  });

  document.addEventListener("click", function (event) {
    var opener = closest(event.target, "[data-help-open]");
    if (opener) {
      event.preventDefault();
      openHelp(opener);
      return;
    }
    var closer = closest(event.target, "[data-close-help]");
    if (closer) {
      event.preventDefault();
      closeHelp();
      return;
    }
    var dialog = helpDialog();
    if (dialog && dialog.open && event.target === dialog) {
      closeHelp();
    }
  });
})();
