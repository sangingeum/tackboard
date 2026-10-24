/* Theme bootstrap and cycle control.
 *
 * Loaded synchronously in <head> before the stylesheet so the attribute is set
 * before first paint. External by design: the Content-Security-Policy forbids
 * inline scripts. Storage access is guarded; when storage is unavailable the
 * toggle still works for the life of the page, only the preference is not
 * remembered. No inline styles are written.
 */
(function () {
  "use strict";

  var STORAGE_KEY = "tackboard.theme";
  var MODES = ["auto", "light", "dark"];
  var GLYPHS = { auto: "◐", light: "☀", dark: "☾" };
  var MEANINGS = {
    auto: "Theme: auto (follows system)",
    light: "Theme: light",
    dark: "Theme: dark"
  };
  var root = document.documentElement;
  if (!root) { return; }

  var media = null;
  try {
    media = window.matchMedia
      ? window.matchMedia("(prefers-color-scheme: dark)")
      : null;
  } catch (e) {
    media = null;
  }

  function readTheme() {
    try {
      var value = window.localStorage.getItem(STORAGE_KEY);
      return MODES.indexOf(value) >= 0 ? value : "auto";
    } catch (e) {
      return "auto";
    }
  }

  function writeTheme(value) {
    try {
      window.localStorage.setItem(STORAGE_KEY, value);
    } catch (e) {
      /* session only */
    }
  }

  var mode = readTheme();

  function updateMeta() {
    var meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) { return; }
    var dark = mode === "dark" || (mode === "auto" && media && media.matches);
    meta.setAttribute("content", dark ? "#0d1014" : "#ffffff");
  }

  function updateButton() {
    var button = document.getElementById("theme-toggle");
    if (!button) { return; }
    var glyph = button.querySelector(".theme-toggle__glyph");
    var label = button.querySelector(".theme-toggle__label");
    if (glyph) { glyph.textContent = GLYPHS[mode]; }
    if (label) { label.textContent = mode; }
    button.setAttribute("aria-label", MEANINGS[mode]);
    button.setAttribute("title", MEANINGS[mode]);
    button.setAttribute("data-theme-mode", mode);
  }

  function apply() {
    root.setAttribute("data-theme", mode);
    updateMeta();
    updateButton();
  }

  function cycle() {
    var index = MODES.indexOf(mode);
    mode = MODES[(index + 1) % MODES.length];
    writeTheme(mode);
    apply();
  }

  function onSystemChange() {
    if (mode === "auto") { apply(); }
  }

  /* Set the attribute immediately: this is the no-flash step. */
  root.setAttribute("data-theme", mode);

  function bind() {
    var button = document.getElementById("theme-toggle");
    if (button) { button.addEventListener("click", cycle); }
    updateMeta();
    updateButton();
    if (media) {
      if (media.addEventListener) {
        media.addEventListener("change", onSystemChange);
      } else if (media.addListener) {
        media.addListener(onSystemChange);
      }
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
