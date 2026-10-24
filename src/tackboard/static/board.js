/* Board live updates: poll /api/board, honour ETag/304, diff in place.
 *
 * Hand-written, no library, no build step. Task text is only ever written with
 * textContent; innerHTML is never used. Motion uses the Web Animations API so
 * no inline style attribute is written (the CSP would block one).
 */
(function () {
  "use strict";

  var POLL_MS = 5000;
  var MAX_MS = 60000;
  var LIMIT = 20;
  var STATUS_ORDER = ["backlog", "ready", "in_progress", "review", "done", "cancelled"];
  var TERMINAL = { done: true, cancelled: true };
  var GLYPHS = {
    backlog: "…",
    ready: "▷",
    in_progress: "▶",
    review: "◐",
    done: "✓",
    cancelled: "✕"
  };
  var LABELS = {
    backlog: "backlog",
    ready: "ready",
    in_progress: "in progress",
    review: "review",
    done: "done",
    cancelled: "cancelled"
  };
  var PIPS = { low: "▁", normal: "▁▁", high: "▁▁▁", urgent: "▁▁▁▁" };

  var board = document.getElementById("board");
  if (!board || !window.fetch) { return; }

  var stamp = document.getElementById("last-updated");
  var toggle = document.getElementById("poll-toggle");
  var refresh = document.getElementById("poll-refresh");
  var retry = document.getElementById("poll-retry");
  var errorLine = document.getElementById("poll-error");
  var group = document.querySelector(".refresh");
  var live = document.getElementById("live-region");

  var timer = null;
  var inFlight = false;
  var paused = false;
  var failures = 0;
  var interval = POLL_MS;
  var lastEtag = "";
  var lastBody = "";
  var lastUpdated = Date.now();
  var previousCounts = null;
  var baselineReady = false;
  var selectedId = null;
  var known = new Map();

  function reducedMotion() {
    try {
      return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (e) {
      return false;
    }
  }

  function el(name, attrs) {
    var node = document.createElement(name);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        if (key === "text") {
          node.textContent = attrs[key];
        } else {
          node.setAttribute(key, attrs[key]);
        }
      });
    }
    return node;
  }

  function setText(node, value) {
    if (node && node.textContent !== value) { node.textContent = value; }
  }

  function setAttr(node, name, value) {
    if (node && node.getAttribute(name) !== value) { node.setAttribute(name, value); }
  }

  function ageWords(iso, now) {
    var stampMs = Date.parse(iso);
    if (isNaN(stampMs)) { return ""; }
    var seconds = Math.max(0, Math.floor((now - stampMs) / 1000));
    if (seconds < 60) { return seconds + "s"; }
    var minutes = Math.floor(seconds / 60);
    if (minutes < 60) { return minutes + "m"; }
    var hours = Math.floor(minutes / 60);
    if (hours < 24) { return hours + "h"; }
    return Math.floor(hours / 24) + "d";
  }

  function relativeStamp(milliseconds) {
    var seconds = Math.max(0, Math.floor((Date.now() - milliseconds) / 1000));
    if (seconds < 5) { return "just now"; }
    if (seconds < 60) { return seconds + "s ago"; }
    var minutes = Math.floor(seconds / 60);
    if (minutes < 60) { return minutes + " min ago"; }
    var hours = Math.floor(minutes / 60);
    if (hours < 24) { return hours + " h ago"; }
    return Math.floor(hours / 24) + "d ago";
  }

  function tick() {
    if (!stamp) { return; }
    if (paused) {
      stamp.textContent = "paused — last updated " + relativeStamp(lastUpdated);
    } else {
      stamp.textContent = "updated " + relativeStamp(lastUpdated);
    }
  }

  function statusChip(status) {
    var chip = el("span", { "class": "status-chip", "data-status": status });
    var glyph = el("span", { "class": "status-chip__glyph", "aria-hidden": "true" });
    glyph.textContent = GLYPHS[status] || "•";
    chip.appendChild(glyph);
    chip.appendChild(document.createTextNode(" " + (LABELS[status] || status)));
    return chip;
  }

  function stateChip(state, text) {
    var chip = el("span", { "class": "state-chip", "data-state": state });
    chip.textContent = text;
    return chip;
  }

  function blockerText(task) {
    var blockers = task.blockers || [];
    if (blockers.length > 0) {
      var first = blockers[0];
      var code = String(first.code || "blocked").replace(/_/g, " ");
      var detail = first.task_id || first.reason || "";
      return detail ? code + " " + detail : code;
    }
    return "prerequisite unfinished";
  }

  function claimTitle(task, now) {
    var age = ageWords(task.claimed_at, now);
    return task.claimed_by + " — claimed " + (age ? age + " ago" : "recently") +
      " (" + task.claimed_at + ")";
  }

  function buildCard(task, now) {
    var card = el("li", {
      "class": "card",
      "data-task": task.id,
      "data-status": task.status,
      "data-priority": task.priority,
      "data-blocked": task.blocked ? "true" : "false",
      "data-claimed": task.claimed_by ? "true" : "false",
      tabindex: "-1"
    });

    var head = el("div", { "class": "card__head" });
    var glyph = el("span", {
      "class": "card__glyph",
      "data-status": task.status,
      "aria-hidden": "true"
    });
    glyph.textContent = GLYPHS[task.status] || "•";
    head.appendChild(glyph);
    var id = el("a", { "class": "card__id", href: "/task/" + encodeURIComponent(task.id) });
    id.textContent = task.id;
    head.appendChild(id);
    var title = el("span", { "class": "card__title", title: task.title });
    title.textContent = task.title;
    head.appendChild(title);
    card.appendChild(head);

    var meta = el("div", { "class": "card__meta" });
    meta.appendChild(priorityEl(task.priority));
    var assignee = el("span", { "class": "card__assignee" });
    assignee.textContent = task.assignee || "-";
    meta.appendChild(assignee);
    meta.appendChild(statusChip(task.status));
    if (task.eligible) { meta.appendChild(stateChip("eligible", "▷ eligible")); }
    if (task.reviewable) { meta.appendChild(stateChip("reviewable", "◐ reviewable")); }
    card.appendChild(meta);

    var labels = task.labels || [];
    if (labels.length > 0) { card.appendChild(labelsEl(labels)); }
    if (task.blocked) { card.appendChild(blockedEl(task)); }
    if (task.claimed_by) { card.appendChild(claimEl(task, now)); }
    return card;
  }

  function priorityEl(priority) {
    var node = el("span", { "class": "priority", "data-priority": priority });
    var pips = el("span", { "class": "priority__pips", "aria-hidden": "true" });
    pips.textContent = PIPS[priority] || "";
    node.appendChild(pips);
    var word = el("span", { "class": "priority__word" });
    word.textContent = priority;
    node.appendChild(word);
    return node;
  }

  function labelsEl(labels) {
    var list = el("ul", { "class": "card__labels", "aria-label": "Labels" });
    labels.forEach(function (label) {
      var item = el("li", { "class": "tag" });
      item.textContent = label;
      list.appendChild(item);
    });
    return list;
  }

  function blockedEl(task) {
    var node = el("p", { "class": "card__blocked" });
    node.textContent = "blocked: " + (task.blocked_reason || blockerText(task));
    return node;
  }

  function claimEl(task, now) {
    var node = el("p", { "class": "card__claim" });
    var badge = el("span", { "class": "claim-badge", title: claimTitle(task, now) });
    badge.textContent = "@" + task.claimed_by;
    node.appendChild(badge);
    node.appendChild(document.createTextNode(" "));
    var age = el("time", {
      "class": "card__claim-age",
      datetime: task.claimed_at,
      title: task.claimed_at
    });
    age.textContent = ageWords(task.claimed_at, now);
    node.appendChild(age);
    return node;
  }

  function syncCard(card, task, now) {
    var snap = known.get(task.id);
    var next = JSON.stringify(task);
    var old = snap ? JSON.parse(snap) : null;
    var changed = snap !== next;
    known.set(task.id, next);

    setAttr(card, "data-status", task.status);
    setAttr(card, "data-priority", task.priority);
    setAttr(card, "data-blocked", task.blocked ? "true" : "false");
    setAttr(card, "data-claimed", task.claimed_by ? "true" : "false");

    var glyph = card.querySelector(".card__glyph");
    setAttr(glyph, "data-status", task.status);
    setText(glyph, GLYPHS[task.status] || "•");

    var id = card.querySelector(".card__id");
    setAttr(id, "href", "/task/" + encodeURIComponent(task.id));
    setText(id, task.id);

    var title = card.querySelector(".card__title");
    setAttr(title, "title", task.title);
    setText(title, task.title);

    var priority = card.querySelector(".priority");
    setAttr(priority, "data-priority", task.priority);
    setText(priority.querySelector(".priority__pips"), PIPS[task.priority] || "");
    setText(priority.querySelector(".priority__word"), task.priority);

    setText(card.querySelector(".card__assignee"), task.assignee || "-");

    if (!old || old.status !== task.status) {
      var meta = card.querySelector(".card__meta");
      var oldChip = meta ? meta.querySelector(".status-chip") : null;
      if (oldChip) { oldChip.replaceWith(statusChip(task.status)); }
      else if (meta) { meta.appendChild(statusChip(task.status)); }
    }

    if (!old || old.eligible !== task.eligible || old.reviewable !== task.reviewable) {
      var metaNode = card.querySelector(".card__meta");
      if (metaNode) {
        Array.prototype.slice.call(metaNode.querySelectorAll(".state-chip")).forEach(
          function (node) { node.remove(); }
        );
        if (task.eligible) { metaNode.appendChild(stateChip("eligible", "▷ eligible")); }
        if (task.reviewable) { metaNode.appendChild(stateChip("reviewable", "◐ reviewable")); }
      }
    }

    if (!old || JSON.stringify(old.labels || []) !== JSON.stringify(task.labels || [])) {
      var oldLabels = card.querySelector(".card__labels");
      if (oldLabels) { oldLabels.remove(); }
      if ((task.labels || []).length > 0) { card.appendChild(labelsEl(task.labels)); }
    }

    if (!old || old.blocked !== task.blocked ||
        old.blocked_reason !== task.blocked_reason ||
        JSON.stringify(old.blockers || []) !== JSON.stringify(task.blockers || [])) {
      var oldBlocked = card.querySelector(".card__blocked");
      if (oldBlocked) { oldBlocked.remove(); }
      if (task.blocked) { card.appendChild(blockedEl(task)); }
    }

    if (!old || old.claimed_by !== task.claimed_by || old.claimed_at !== task.claimed_at) {
      var oldClaim = card.querySelector(".card__claim");
      if (oldClaim) { oldClaim.remove(); }
      if (task.claimed_by) { card.appendChild(claimEl(task, now)); }
    } else if (task.claimed_by) {
      var ageNode = card.querySelector(".card__claim-age");
      var badge = card.querySelector(".claim-badge");
      setText(ageNode, ageWords(task.claimed_at, now));
      if (badge) { setAttr(badge, "title", claimTitle(task, now)); }
    }
    return changed;
  }

  function markChanged(card) {
    setAttr(card, "data-changed", "true");
    window.setTimeout(function () {
      card.removeAttribute("data-changed");
    }, 1200);
  }

  function animateCount(node) {
    if (!node || reducedMotion() || typeof node.animate !== "function") { return; }
    node.animate(
      [
        { opacity: "0.6", transform: "scale(1)" },
        { opacity: "1", transform: "scale(1.06)" },
        { opacity: "1", transform: "scale(1)" }
      ],
      { duration: 240, easing: "cubic-bezier(0.2, 0, 0, 1)" }
    );
  }

  function updateSummary(counts) {
    if (!counts) { return; }
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-count]"),
      function (node) {
        var key = node.getAttribute("data-count");
        if (!(key in counts)) { return; }
        var value = String(counts[key]);
        var valueNode = node.querySelector(".count__value");
        if (valueNode) {
          if (valueNode.textContent !== value) {
            valueNode.textContent = value;
            animateCount(valueNode);
          }
        } else if (node.textContent !== value) {
          node.textContent = value;
          animateCount(node);
        }
      }
    );
    var line = document.querySelector(".counts-line");
    if (line) {
      var order = ["tasks"].concat(STATUS_ORDER, ["eligible", "reviewable", "blocked", "claimed"]);
      line.textContent = order.map(function (key) {
        return key + "=" + counts[key];
      }).join(" ");
    }
  }

  function announce(counts) {
    if (!live || !counts) { return; }
    if (previousCounts === null) { previousCounts = counts; return; }
    var changed = JSON.stringify(previousCounts) !== JSON.stringify(counts);
    previousCounts = counts;
    if (!changed) { return; }
    live.textContent = "Board updated: " + counts.tasks + " tasks, " +
      counts.eligible + " eligible, " + counts.blocked + " blocked, " +
      counts.claimed + " claimed.";
  }

  function syncMore(column, status, params, counts) {
    var existing = column.querySelector(".column__more");
    var total = counts ? counts[status] || 0 : 0;
    var show = TERMINAL[status] && !params.get("status") && total > LIMIT;
    if (!show) {
      if (existing) { existing.remove(); }
      return;
    }
    if (!existing) {
      existing = el("a", { "class": "column__more" });
      column.appendChild(existing);
    }
    var next = new URLSearchParams(params.toString());
    next.set("status", status);
    next.delete("claimed");
    setAttr(existing, "href", "/board?" + next.toString());
    setText(existing, "show all " + total);
  }

  function setSelection(taskId) {
    selectedId = taskId;
    Array.prototype.forEach.call(board.querySelectorAll(".card"), function (card) {
      if (card.dataset.task === selectedId) { setAttr(card, "data-selected", "true"); }
      else { card.removeAttribute("data-selected"); }
    });
  }

  function restoreSelection() {
    if (!selectedId) { return; }
    var found = false;
    Array.prototype.forEach.call(board.querySelectorAll(".card"), function (card) {
      if (card.dataset.task === selectedId) {
        setAttr(card, "data-selected", "true");
        found = true;
      }
    });
    if (!found) {
      selectedId = null;
      if (live) { live.textContent = "Selection cleared: the task is no longer on the board."; }
    }
  }

  function desiredColumns(payload, params) {
    var tasks = payload.tasks || [];
    if (params.get("claimed")) {
      tasks = tasks.filter(function (task) { return task.claimed_by; });
    }
    var grouped = {};
    STATUS_ORDER.forEach(function (status) { grouped[status] = []; });
    tasks.forEach(function (task) {
      if (grouped[task.status]) { grouped[task.status].push(task); }
    });
    var statusSelected = params.get("status");
    var result = {};
    STATUS_ORDER.forEach(function (status) {
      var group = grouped[status];
      if (TERMINAL[status] && !statusSelected) {
        group = group.slice().sort(function (left, right) {
          return String(right.updated_at).localeCompare(String(left.updated_at));
        });
        group = group.slice(0, LIMIT);
      }
      result[status] = group;
    });
    return result;
  }

  function applyPayload(payload, params) {
    var desired = desiredColumns(payload, params);
    var scrollX = board.scrollLeft;
    var scrollY = window.scrollY || document.documentElement.scrollTop || 0;
    var now = Date.now();
    var before = new Map();
    Array.prototype.forEach.call(board.querySelectorAll(".card"), function (card) {
      before.set(card.dataset.task, card.getBoundingClientRect());
    });

    var moved = [];
    var created = [];

    STATUS_ORDER.forEach(function (status) {
      var column = board.querySelector('.column[data-status="' + status + '"]');
      if (!column) { return; }
      var list = column.querySelector(".cards");
      if (!list) { return; }
      var tasks = desired[status];
      var desiredIds = tasks.map(function (task) { return task.id; });
      var existing = new Map();
      Array.prototype.slice.call(list.children).forEach(function (card) {
        if (card.dataset && card.dataset.task) { existing.set(card.dataset.task, card); }
      });

      existing.forEach(function (card, id) {
        if (desiredIds.indexOf(id) < 0) {
          card.remove();
          existing.delete(id);
          known.delete(id);
        }
      });

      tasks.forEach(function (task, index) {
        var card = existing.get(task.id);
        if (!card) {
          card = buildCard(task, now);
          setAttr(card, "data-new", "true");
          list.insertBefore(card, list.children[index] || null);
          created.push(card);
          if (baselineReady) { markChanged(card); }
        } else {
          var changed = syncCard(card, task, now);
          if (changed && baselineReady) { markChanged(card); }
        }
        var current = list.children[index];
        if (current !== card) { list.insertBefore(card, current || null); }
        moved.push(card);
      });

      setAttr(column, "data-empty", tasks.length > 0 ? "false" : "true");
      var empty = column.querySelector(".column__empty");
      if (empty) {
        if (tasks.length > 0) { empty.setAttribute("hidden", ""); }
        else { empty.removeAttribute("hidden"); }
      }
      syncMore(column, status, params, payload.counts);
    });

    if (!reducedMotion()) {
      moved.forEach(function (card) {
        var prior = before.get(card.dataset.task);
        if (!prior) {
          if (card.getAttribute("data-new") === "true" && typeof card.animate === "function") {
            card.animate([{ opacity: "0" }, { opacity: "1" }], {
              duration: 180,
              easing: "cubic-bezier(0.2, 0, 0, 1)"
            });
          }
          card.removeAttribute("data-new");
          return;
        }
        var after = card.getBoundingClientRect();
        var dx = prior.left - after.left;
        var dy = prior.top - after.top;
        var sx = prior.width && after.width ? prior.width / after.width : 1;
        var sy = prior.height && after.height ? prior.height / after.height : 1;
        if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5 ||
            Math.abs(sx - 1) > 0.01 || Math.abs(sy - 1) > 0.01) {
          card.animate(
            [
              { transform: "translate(" + dx + "px," + dy + "px) scale(" + sx + "," + sy + ")" },
              { transform: "none" }
            ],
            { duration: 240, easing: "cubic-bezier(0.2, 0, 0, 1)" }
          );
        }
        card.removeAttribute("data-new");
      });
    } else {
      moved.concat(created).forEach(function (card) { card.removeAttribute("data-new"); });
    }

    board.scrollLeft = scrollX;
    window.scrollTo(window.scrollX, scrollY);
    updateSummary(payload.counts);
    restoreSelection();
    baselineReady = true;
  }

  function showError(message) {
    if (errorLine) {
      var text = errorLine.querySelector(".refresh__error-text");
      if (text) { text.textContent = message; }
      if (retry) { retry.removeAttribute("hidden"); }
      errorLine.removeAttribute("hidden");
    }
  }

  function clearError() {
    if (errorLine) {
      var text = errorLine.querySelector(".refresh__error-text");
      if (text) { text.textContent = ""; }
      if (retry) { retry.setAttribute("hidden", ""); }
      errorLine.setAttribute("hidden", "");
    }
  }

  function setPaused(value) {
    paused = value;
    if (group) { setAttr(group, "data-paused", value ? "true" : "false"); }
    if (toggle) {
      toggle.setAttribute("aria-pressed", value ? "true" : "false");
      toggle.textContent = value ? "Resume updates" : "Pause updates";
    }
    if (refresh) {
      if (value) { refresh.removeAttribute("hidden"); }
      else { refresh.setAttribute("hidden", ""); }
    }
    tick();
  }

  function stopTimer() {
    if (timer !== null) {
      window.clearTimeout(timer);
      timer = null;
    }
  }

  function schedule(milliseconds) {
    stopTimer();
    timer = window.setTimeout(pollNow, milliseconds);
  }

  function fail(message, stop) {
    failures += 1;
    showError(message);
    if (stop || failures >= 3) {
      stopTimer();
      if (retry) { retry.removeAttribute("hidden"); }
      if (live) { live.textContent = "Updates paused — Retry."; }
      tick();
      return;
    }
    interval = Math.min(interval * 2, MAX_MS);
    if (stamp) { stamp.textContent = "retrying in " + Math.round(interval / 1000) + "s"; }
    schedule(interval);
  }

  function succeed() {
    failures = 0;
    interval = POLL_MS;
    lastUpdated = Date.now();
    clearError();
    if (retry) { retry.setAttribute("hidden", ""); }
    tick();
    schedule(POLL_MS);
  }

  function pollNow() {
    if (inFlight || paused || document.hidden) { return; }
    inFlight = true;
    var headers = {};
    if (lastEtag) { headers["If-None-Match"] = lastEtag; }
    fetch("/api/board" + window.location.search, {
      method: "GET",
      headers: headers,
      cache: "no-store",
      credentials: "same-origin",
      redirect: "error"
    })
      .then(function (response) {
        if (response.status === 304) { succeed(); return null; }
        if (!response.ok) {
          var stop = [401, 403, 404].indexOf(response.status) >= 0;
          fail("Update failed: HTTP " + response.status + ".", stop);
          return null;
        }
        var tag = response.headers.get("ETag");
        if (tag) { lastEtag = tag; }
        return response.text().then(function (text) {
          if (text === lastBody) { succeed(); return; }
          lastBody = text;
          var payload;
          try { payload = JSON.parse(text); }
          catch (e) { fail("Update failed: the board returned invalid JSON.", true); return; }
          applyPayload(payload, new URLSearchParams(window.location.search));
          announce(payload.counts);
          succeed();
        });
      })
      .catch(function () {
        fail("Update failed: the board could not be reached.", false);
      })
      .then(function () { inFlight = false; });
  }

  if (toggle) {
    toggle.addEventListener("click", function () {
      if (paused) { setPaused(false); pollNow(); }
      else { setPaused(true); stopTimer(); }
    });
  }
  if (refresh) {
    refresh.addEventListener("click", function () { pollNow(); });
  }
  if (retry) {
    retry.addEventListener("click", function () {
      failures = 0;
      interval = POLL_MS;
      clearError();
      retry.setAttribute("hidden", "");
      pollNow();
    });
  }

  board.addEventListener("click", function (event) {
    var target = event.target;
    var card = target && target.closest ? target.closest(".card") : null;
    if (!card) { setSelection(null); return; }
    if (target.closest && target.closest("a")) { return; }
    setSelection(card.dataset.task);
  });

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      stopTimer();
    } else if (!paused) {
      pollNow();
    }
  });

  window.setInterval(tick, 1000);
  tick();
  schedule(POLL_MS);
})();
