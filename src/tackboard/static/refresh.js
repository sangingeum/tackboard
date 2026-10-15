/* Auto-refresh: reload the page on an interval so the board always shows a
   consistent snapshot taken by the server. The toggle pauses it. */
(function () {
  "use strict";

  var toggle = document.getElementById("autorefresh");
  if (!toggle) { return; }

  var seconds = 5;
  var timer = null;

  function stop() {
    if (timer !== null) {
      window.clearInterval(timer);
      timer = null;
    }
  }

  function start() {
    stop();
    timer = window.setInterval(function () {
      window.location.reload();
    }, seconds * 1000);
  }

  toggle.addEventListener("change", function () {
    if (toggle.checked) { start(); } else { stop(); }
  });

  if (toggle.checked) { start(); }
})();
