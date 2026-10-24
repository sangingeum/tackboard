/* Theme bootstrap — external and synchronous, loaded in <head>.
   Mirrors the production contract in docs/web-redesign/design-system.md §2:
   data-theme is one of auto|light|dark, storage is always guarded, and the
   attribute is written before first paint so there is no flash. */
(function () {
  "use strict";

  var KEY = "tackboard.theme";
  var ORDER = ["auto", "light", "dark"];
  var GLYPHS = { auto: "\u25D0", light: "\u2600", dark: "\u263E" };
  var LABELS = {
    auto: "Theme: auto (follows system)",
    light: "Theme: light",
    dark: "Theme: dark"
  };

  function readStored() {
    try {
      var value = window.localStorage.getItem(KEY);
      if (value === "light" || value === "dark" || value === "auto") {
        return value;
      }
    } catch (error) {
      /* Storage unavailable: fall back to auto. */
    }
    return "auto";
  }

  function writeStored(value) {
    try {
      window.localStorage.setItem(KEY, value);
    } catch (error) {
      /* Storage unavailable: the in-memory value still works. */
    }
  }

  var current = readStored();

  function apply(mode) {
    current = mode;
    document.documentElement.setAttribute("data-theme", mode);
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      var dark = mode === "dark" ||
        (mode === "auto" && window.matchMedia("(prefers-color-scheme: dark)").matches);
      meta.setAttribute("content", dark ? "#141a20" : "#f7f8fa");
    }
    updateButton();
  }

  function updateButton() {
    var button = document.getElementById("theme-toggle");
    if (!button) { return; }
    var glyph = button.querySelector(".theme-toggle__glyph");
    var label = button.querySelector(".theme-toggle__label");
    if (glyph) { glyph.textContent = GLYPHS[current]; }
    if (label) { label.textContent = current; }
    button.setAttribute("aria-label", LABELS[current]);
    button.setAttribute("title", LABELS[current]);
  }

  function cycle() {
    var next = ORDER[(ORDER.indexOf(current) + 1) % ORDER.length];
    writeStored(next);
    apply(next);
  }

  /* Write the attribute as early as possible. */
  document.documentElement.setAttribute("data-theme", current);

  document.addEventListener("DOMContentLoaded", function () {
    updateButton();
    var button = document.getElementById("theme-toggle");
    if (button) {
      button.addEventListener("click", cycle);
    }
    var query = window.matchMedia("(prefers-color-scheme: dark)");
    var onChange = function () {
      if (current === "auto") { apply("auto"); }
    };
    if (query.addEventListener) {
      query.addEventListener("change", onChange);
    } else if (query.addListener) {
      query.addListener(onChange);
    }
  });

  window.tackTheme = { cycle: cycle, current: function () { return current; } };
})();
