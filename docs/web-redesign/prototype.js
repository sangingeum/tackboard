/* tackboard web redesign — review prototype behaviour.
   Static artifact only. No network calls, no libraries, no element.style.
   All state changes use attributes and classes so the prototype models the
   production CSP (style-src 'self' blocks inline style attributes). */
(function () {
  "use strict";

  var DATA = window.TACK_PROTOTYPE;
  if (!DATA) { return; }

  var GLYPH = {
    backlog: "\u2026",
    ready: "\u25B7",
    in_progress: "\u25B6",
    review: "\u25D0",
    done: "\u2713",
    cancelled: "\u2715"
  };
  var WORD = {
    backlog: "backlog",
    ready: "ready",
    in_progress: "in progress",
    review: "review",
    done: "done",
    cancelled: "cancelled"
  };
  var PRIO_PIPS = { low: "\u2581", normal: "\u2581\u2581", high: "\u2581\u2581\u2581", urgent: "\u2581\u2581\u2581\u2581" };
  var CLOSED = { done: true, cancelled: true };
  var NODE_W = 148;
  var NODE_H = 78;
  var GAP_X = 70;
  var GAP_Y = 26;
  var MIN_SCALE = 0.15;
  var MAX_SCALE = 4;
  var DRAG_THRESHOLD = 4;
  var HOVER_DELAY = 100;
  var EDGE_PAD = 120;

  function qs(sel, root) { return (root || document).querySelector(sel); }
  function qsa(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }
  function el(name, attrs, text) {
    var node = document.createElement(name);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    }
    if (text !== undefined) { node.textContent = text; }
    return node;
  }
  function svgEl(name, attrs, text) {
    var node = document.createElementNS("http://www.w3.org/2000/svg", name);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    }
    if (text !== undefined) { node.textContent = text; }
    return node;
  }
  function truncate(value, limit) {
    var text = String(value);
    return text.length > limit ? text.slice(0, limit - 1) + "\u2026" : text;
  }
  function nodeById(id) {
    for (var i = 0; i < DATA.tasks.length; i += 1) {
      if (DATA.tasks[i].id === id) { return DATA.tasks[i]; }
    }
    return null;
  }

  /* ------------------------------------------------------------- markdown */

  function safeHref(dest) {
    return /^https?:\/\//i.test(dest) ? dest : null;
  }

  function inlineNodes(text) {
    var out = [];
    var buffer = "";
    var i = 0;

    function flush() {
      if (buffer) { out.push(document.createTextNode(buffer)); buffer = ""; }
    }

    while (i < text.length) {
      var ch = text.charAt(i);

      if (ch === "`") {
        var endCode = text.indexOf("`", i + 1);
        if (endCode !== -1) {
          flush();
          out.push(el("code", null, text.slice(i + 1, endCode)));
          i = endCode + 1;
          continue;
        }
      }

      if (ch === "*" && text.charAt(i + 1) === "*") {
        var endBold = text.indexOf("**", i + 2);
        if (endBold !== -1) {
          flush();
          var strong = el("strong");
          inlineNodes(text.slice(i + 2, endBold)).forEach(function (child) {
            strong.appendChild(child);
          });
          out.push(strong);
          i = endBold + 2;
          continue;
        }
      }

      if (ch === "*") {
        var endEm = text.indexOf("*", i + 1);
        if (endEm !== -1) {
          flush();
          var em = el("em");
          inlineNodes(text.slice(i + 1, endEm)).forEach(function (child) {
            em.appendChild(child);
          });
          out.push(em);
          i = endEm + 1;
          continue;
        }
      }

      if (ch === "[") {
        var closeBracket = text.indexOf("]", i + 1);
        var openParen = closeBracket !== -1 ? text.indexOf("(", closeBracket + 1) : -1;
        var closeParen = openParen !== -1 ? text.indexOf(")", openParen + 1) : -1;
        if (closeBracket !== -1 && openParen === closeBracket + 1 && closeParen !== -1) {
          flush();
          var label = text.slice(i + 1, closeBracket);
          var dest = text.slice(openParen + 1, closeParen);
          var href = safeHref(dest);
          if (href) {
            var link = el("a", { href: href, rel: "noopener noreferrer", target: "_blank" });
            inlineNodes(label).forEach(function (child) { link.appendChild(child); });
            link.appendChild(el("span", { "class": "sr-only" }, " (opens in a new tab)"));
            out.push(link);
          } else {
            out.push(document.createTextNode(label + " (" + dest + ")"));
          }
          i = closeParen + 1;
          continue;
        }
      }

      buffer += ch;
      i += 1;
    }
    flush();
    return out;
  }

  function renderMarkdown(source) {
    var root = document.createDocumentFragment();
    var lines = source.replace(/\r\n/g, "\n").split("\n");
    var i = 0;

    function paragraph(text) {
      var p = el("p");
      inlineNodes(text).forEach(function (child) { p.appendChild(child); });
      root.appendChild(p);
    }

    while (i < lines.length) {
      var line = lines[i];

      if (!line.trim()) { i += 1; continue; }

      if (/^```/.test(line)) {
        var code = [];
        i += 1;
        while (i < lines.length && !/^```/.test(lines[i])) { code.push(lines[i]); i += 1; }
        i += 1;
        var pre = el("pre");
        pre.appendChild(el("code", null, code.join("\n")));
        root.appendChild(pre);
        continue;
      }

      var heading = /^(#{2,4})\s+(.*)$/.exec(line);
      if (heading) {
        var level = String(heading[1].length + 1);
        var h = el("h" + level);
        inlineNodes(heading[2]).forEach(function (child) { h.appendChild(child); });
        root.appendChild(h);
        i += 1;
        continue;
      }

      if (/^\s*[-*+]\s+/.test(line)) {
        var ul = el("ul");
        while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
          var item = lines[i].replace(/^\s*[-*+]\s+/, "");
          var li = el("li");
          inlineNodes(item).forEach(function (child) { li.appendChild(child); });
          ul.appendChild(li);
          i += 1;
        }
        root.appendChild(ul);
        continue;
      }

      if (/^\s*\d+\.\s+/.test(line)) {
        var ol = el("ol");
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
          var oitem = lines[i].replace(/^\s*\d+\.\s+/, "");
          var oli = el("li");
          inlineNodes(oitem).forEach(function (child) { oli.appendChild(child); });
          ol.appendChild(oli);
          i += 1;
        }
        root.appendChild(ol);
        continue;
      }

      if (/^>\s?/.test(line)) {
        var quote = el("blockquote");
        var qtext = line.replace(/^>\s?/, "");
        inlineNodes(qtext).forEach(function (child) { quote.appendChild(child); });
        root.appendChild(quote);
        i += 1;
        continue;
      }

      var buf = [line.trim()];
      i += 1;
      while (i < lines.length && lines[i].trim() &&
             !/^```/.test(lines[i]) && !/^#{2,4}\s/.test(lines[i]) &&
             !/^\s*[-*+]\s+/.test(lines[i]) && !/^\s*\d+\.\s+/.test(lines[i]) &&
             !/^>\s?/.test(lines[i])) {
        buf.push(lines[i].trim());
        i += 1;
      }
      paragraph(buf.join(" "));
    }
    return root;
  }

  function mountMarkdown() {
    var fallback = qs("#description-fallback");
    var target = qs("#description-prose");
    if (!fallback || !target) { return; }
    try {
      target.appendChild(renderMarkdown(fallback.textContent || ""));
      fallback.hidden = true;
    } catch (error) {
      /* Leave the escaped preformatted fallback in place. */
      target.setAttribute("data-markdown", "fallback");
    }
  }

  /* ---------------------------------------------------------------- board */

  var filters = { status: null, blocked: false, eligible: false, claimed: false, text: "" };
  var cards = qsa(".card");
  var selectedCard = -1;

  function cardMatches(card) {
    if (filters.status && card.getAttribute("data-status") !== filters.status) { return false; }
    if (filters.blocked && card.getAttribute("data-blocked") !== "true") { return false; }
    if (filters.eligible && card.getAttribute("data-eligible") !== "true") { return false; }
    if (filters.claimed && card.getAttribute("data-claimed") !== "true") { return false; }
    if (filters.text) {
      var haystack = (card.textContent || "").toLowerCase();
      if (haystack.indexOf(filters.text.toLowerCase()) === -1) { return false; }
    }
    return true;
  }

  function visibleCards() {
    return cards.filter(function (card) { return cardMatches(card); });
  }

  function filterLabel() {
    var parts = [];
    if (filters.status) { parts.push(WORD[filters.status]); }
    if (filters.blocked) { parts.push("blocked"); }
    if (filters.eligible) { parts.push("eligible"); }
    if (filters.claimed) { parts.push("claimed"); }
    if (filters.text) { parts.push("\u201c" + filters.text + "\u201d"); }
    return parts;
  }

  function renderActiveFilters() {
    var host = qs("#active-filters");
    if (!host) { return; }
    host.textContent = "";
    var active = filterLabel();
    if (!active.length) {
      host.appendChild(el("span", { "class": "section__note" },
        "No filters \u2014 showing everything"));
      return;
    }
    active.forEach(function (label, index) {
      var chip = el("button", {
        type: "button",
        "class": "chip",
        "data-remove-index": String(index),
        "aria-label": "Remove filter: " + label
      });
      chip.appendChild(el("span", null, label));
      chip.appendChild(el("span", { "class": "chip__x", "aria-hidden": "true" }, "\u2715"));
      host.appendChild(chip);
    });
  }

  function applyFilters() {
    var shown = 0;
    cards.forEach(function (card) {
      var match = cardMatches(card);
      card.classList.toggle("is-filtered-out", !match);
      if (match) { shown += 1; }
    });
    var summary = qs("#filter-summary");
    if (summary) {
      summary.textContent = shown === DATA.summary.total
        ? "Showing all " + DATA.summary.total + " tasks"
        : "Showing " + shown + " of " + DATA.summary.total + " tasks";
    }
    qsa("[data-filter]").forEach(function (link) {
      var key = link.getAttribute("data-filter");
      var on = key === "all"
        ? !filters.status && !filters.blocked && !filters.eligible && !filters.claimed && !filters.text
        : key.indexOf("status:") === 0
          ? filters.status === key.slice(7)
          : Boolean(filters[key]);
      link.setAttribute("aria-current", on ? "true" : "false");
    });
    renderActiveFilters();
    selectedCard = -1;
  }

  function toggleFilter(key) {
    if (key === "all") {
      filters = { status: null, blocked: false, eligible: false, claimed: false, text: "" };
      return;
    }
    if (key.indexOf("status:") === 0) {
      var next = key.slice(7);
      filters.status = filters.status === next ? null : next;
      return;
    }
    filters[key] = !filters[key];
  }

  function setupBoard() {
    qsa("[data-filter]").forEach(function (link) {
      link.addEventListener("click", function (event) {
        event.preventDefault();
        toggleFilter(link.getAttribute("data-filter"));
        applyFilters();
      });
    });

    var host = qs("#active-filters");
    if (host) {
      host.addEventListener("click", function (event) {
        var button = event.target.closest("[data-remove-index]");
        if (!button) { return; }
        var label = button.textContent.replace("\u2715", "").trim();
        if (label === "blocked" || label === "eligible" || label === "claimed") {
          filters[label] = false;
        } else if (label === "No filters \u2014 showing everything") {
          filters = { status: null, blocked: false, eligible: false, claimed: false, text: "" };
        } else {
          filters.status = null;
        }
        applyFilters();
      });
    }

    var form = qs("#filter-form");
    if (form) {
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        var assignee = qs("#filter-assignee");
        var label = qs("#filter-label");
        var text = [assignee && assignee.value, label && label.value]
          .filter(Boolean).join(" ").trim();
        filters.text = text;
        var blocked = form.querySelector('input[name="blocked"]');
        var eligible = form.querySelector('input[name="eligible"]');
        filters.blocked = Boolean(blocked && blocked.checked);
        filters.eligible = Boolean(eligible && eligible.checked);
        applyFilters();
        var more = qs("#filters-more");
        if (more) { more.open = false; }
      });
    }

    var reset = qs("#reset-filters");
    if (reset) {
      reset.addEventListener("click", function (event) {
        event.preventDefault();
        filters = { status: null, blocked: false, eligible: false, claimed: false, text: "" };
        var assignee = qs("#filter-assignee");
        var label = qs("#filter-label");
        if (assignee) { assignee.value = ""; }
        if (label) { label.value = ""; }
        qsa('#filter-form input[type="checkbox"]').forEach(function (box) {
          box.checked = false;
        });
        applyFilters();
      });
    }

    document.addEventListener("keydown", function (event) {
      if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) { return; }
      var help = document.getElementById("help");
      if (help && help.open) { return; }
      if (document.activeElement && document.activeElement.closest &&
          document.activeElement.closest("#graph")) { return; }
      var tag = event.target && event.target.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") { return; }
      var list = visibleCards();
      if (!list.length) { return; }
      if (event.key === "j" || event.key === "k") {
        event.preventDefault();
        if (selectedCard >= 0 && cards[selectedCard]) {
          cards[selectedCard].removeAttribute("data-selected");
        }
        var currentIndex = list.indexOf(cards[selectedCard]);
        var next = event.key === "j"
          ? (currentIndex + 1) % list.length
          : (currentIndex <= 0 ? list.length - 1 : currentIndex - 1);
        selectedCard = cards.indexOf(list[next]);
        cards[selectedCard].setAttribute("data-selected", "true");
        cards[selectedCard].scrollIntoView({ block: "nearest" });
      }
      if (event.key === "Enter" && selectedCard >= 0 && cards[selectedCard]) {
        var link = cards[selectedCard].querySelector(".card__id");
        if (link) { link.click(); }
      }
    });

    applyFilters();
  }

  /* -------------------------------------------------------- last updated */

  var poll = { paused: false, last: Date.now(), timer: null };

  function stampText() {
    var seconds = Math.max(0, Math.floor((Date.now() - poll.last) / 1000));
    var human = seconds < 60 ? seconds + "s ago" : Math.floor(seconds / 60) + "m ago";
    var stamp = qs("#last-updated");
    if (!stamp) { return; }
    stamp.textContent = poll.paused
      ? "paused \u2014 last updated " + human
      : "updated " + human;
    var group = stamp.closest(".refresh");
    if (group) { group.setAttribute("data-paused", poll.paused ? "true" : "false"); }
  }

  function startPollTimer() {
    if (poll.timer !== null) { window.clearInterval(poll.timer); }
    poll.timer = window.setInterval(stampText, 1000);
    stampText();
  }

  function setupRefresh() {
    var button = qs("#poll-toggle");
    if (button) {
      button.addEventListener("click", function () {
        poll.paused = !poll.paused;
        button.setAttribute("aria-pressed", poll.paused ? "true" : "false");
        if (!poll.paused) { poll.last = Date.now(); }
        stampText();
      });
    }
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        if (poll.timer !== null) { window.clearInterval(poll.timer); poll.timer = null; }
      } else {
        poll.last = Date.now();
        if (!poll.paused) { startPollTimer(); }
      }
    });
    startPollTimer();
  }

  /* ---------------------------------------------------------------- graph */

  var view = { scale: 1, tx: 0, ty: 0 };
  var activeId = null;
  var selectedId = null;
  var hoverTimer = null;
  var svg = null;
  var viewport = null;
  var minimapSvg = null;
  var minimapState = { scale: 1, ox: 0, oy: 0 };

  function visibleGraph() {
    var hidden = {};
    var nodes = DATA.tasks.filter(function (task) {
      var isClosed = CLOSED[task.status] === true;
      var hide = hideClosed && isClosed;
      if (hide) { hidden[task.id] = true; }
      return !hide;
    });
    var edges = DATA.edges.filter(function (edge) {
      return !hidden[edge.source] && !hidden[edge.target];
    });
    var hiddenCount = DATA.tasks.length - nodes.length;
    var hiddenEdges = DATA.edges.length - edges.length;
    return { nodes: nodes, edges: edges, hidden: hidden, hiddenCount: hiddenCount, hiddenEdges: hiddenEdges };
  }

  var hideClosed = false;
  var highlightClaimed = true;
  var focusClaimed = false;

  function layout(nodes, edges) {
    var ids = {};
    var depth = {};
    var incoming = {};
    nodes.forEach(function (task) {
      ids[task.id] = task;
      depth[task.id] = 0;
      incoming[task.id] = [];
    });
    edges.forEach(function (edge) {
      if (incoming[edge.target]) { incoming[edge.target].push(edge.source); }
    });
    var changed = true;
    var rounds = 0;
    while (changed && rounds <= nodes.length) {
      changed = false;
      rounds += 1;
      edges.forEach(function (edge) {
        if (!ids[edge.source] || !ids[edge.target]) { return; }
        var candidate = depth[edge.source] + 1;
        if (candidate > depth[edge.target]) { depth[edge.target] = candidate; changed = true; }
      });
    }
    var layers = {};
    nodes.forEach(function (task) {
      var d = depth[task.id] || 0;
      if (!layers[d]) { layers[d] = []; }
      layers[d].push(task.id);
    });
    var positions = {};
    var maxDepth = 0;
    Object.keys(layers).forEach(function (key) {
      var d = Number(key);
      maxDepth = Math.max(maxDepth, d);
      var order = layers[key];
      order.sort(function (a, b) {
        var sa = 0, ca = 0, sb = 0, cb = 0;
        incoming[a].forEach(function (source) { if (positions[source]) { sa += positions[source].y; ca += 1; } });
        incoming[b].forEach(function (source) { if (positions[source]) { sb += positions[source].y; cb += 1; } });
        var va = ca ? sa / ca : 0;
        var vb = cb ? sb / cb : 0;
        return va - vb;
      });
      order.forEach(function (id, row) {
        positions[id] = { x: d * (NODE_W + GAP_X), y: row * (NODE_H + GAP_Y), depth: d };
      });
    });
    var maxRows = 1;
    Object.keys(layers).forEach(function (key) { maxRows = Math.max(maxRows, layers[key].length); });
    return { positions: positions, width: (maxDepth + 1) * (NODE_W + GAP_X), height: maxRows * (NODE_H + GAP_Y) };
  }

  function shapeFor(status) {
    if (status === "ready") {
      return svgEl("circle", { cx: String(NODE_W / 2), cy: String(NODE_H / 2), r: String(NODE_H / 2 - 2) });
    }
    if (status === "in_progress") {
      return svgEl("polygon", {
        points: [NODE_W / 2 + ",0", NODE_W + "," + NODE_H / 2,
                 NODE_W / 2 + "," + NODE_H, "0," + NODE_H / 2].join(" ")
      });
    }
    if (status === "review") {
      return svgEl("polygon", {
        points: [32 + ",0", 116 + ",0", NODE_W + "," + NODE_H / 2,
                 116 + "," + NODE_H, 32 + "," + NODE_H, "0," + NODE_H / 2].join(" ")
      });
    }
    if (status === "cancelled") {
      return svgEl("polygon", {
        points: [22 + ",0", 126 + ",0", NODE_W + ",22", NODE_W + "," + (NODE_H - 22),
                 126 + "," + NODE_H, 22 + "," + NODE_H, "0," + (NODE_H - 22), "0,22"].join(" ")
      });
    }
    var rx = status === "done" ? 0 : 6;
    return svgEl("rect", { x: "0", y: "0", width: String(NODE_W), height: String(NODE_H), rx: String(rx) });
  }

  function addNodeText(group, task) {
    var centered = task.status === "ready";
    var anchor = centered ? "middle" : "start";
    var baseX = centered ? NODE_W / 2 : 14;
    var idText = svgEl("text", {
      "class": "node__id", x: String(baseX), y: centered ? "30" : "26",
      "text-anchor": anchor
    }, task.id);
    var titleText = svgEl("text", {
      "class": "node__title", x: String(baseX), y: centered ? "44" : "42",
      "text-anchor": anchor
    }, truncate(task.title, centered ? 22 : 26));
    var statusText = svgEl("text", {
      "class": "node__status", x: String(baseX), y: centered ? "58" : "62",
      "text-anchor": anchor
    }, GLYPH[task.status] + " " + WORD[task.status]);
    group.appendChild(idText);
    group.appendChild(titleText);
    group.appendChild(statusText);
  }

  function buildNode(task, position, elided) {
    var group = svgEl("g", {
      "class": "node",
      id: "node-" + task.id,
      "data-id": task.id,
      "data-status": task.status,
      "data-priority": task.priority,
      "data-claimed": task.claimed_by ? "true" : "false",
      "data-blocked": task.blocked ? "true" : "false",
      "data-eligible": task.eligible ? "true" : "false",
      "data-reviewable": task.reviewable ? "true" : "false",
      transform: "translate(" + position.x + "," + position.y + ")"
    });

    var title = svgEl("title");
    title.textContent = task.id + " \u2014 " + task.title + " (" + WORD[task.status] +
      (task.claimed_by ? ", claimed by " + task.claimed_by : "") +
      (task.blocked ? ", blocked" : "") + ")";
    group.appendChild(title);

    var shape = shapeFor(task.status);
    shape.setAttribute("class", "node__shape");
    group.appendChild(shape);

    if (task.blocked) {
      /* Hatch is clipped to the status shape, so a diamond does not get a
         rectangular overlay. A ⊘ badge repeats the state as a glyph. */
      var clipId = "clip-" + task.id;
      var clip = svgEl("clipPath", { id: clipId });
      clip.appendChild(shapeFor(task.status));
      group.appendChild(clip);
      group.appendChild(svgEl("rect", {
        "class": "node__hatch", x: "0", y: "0",
        width: String(NODE_W), height: String(NODE_H),
        "clip-path": "url(#" + clipId + ")"
      }));
      var mark = svgEl("g", { "class": "node__blocked-mark" });
      mark.appendChild(svgEl("circle", { cx: "13", cy: "13", r: "7" }));
      mark.appendChild(svgEl("line", { x1: "8", y1: "18", x2: "18", y2: "8" }));
      group.appendChild(mark);
    }

    if (task.reviewable) {
      group.appendChild(svgEl("line", {
        "class": "node__double-edge", x1: "6", y1: "4", x2: String(NODE_W - 6), y2: "4"
      }));
    }

    addNodeText(group, task);

    if (task.eligible) {
      group.appendChild(svgEl("polygon", {
        "class": "node__corner",
        points: [NODE_W - 18 + ",0", NODE_W + ",0", NODE_W + ",18"].join(" ")
      }));
    }

    if (task.claimed_by) {
      var ring = svgEl("rect", {
        "class": "node__ring", x: "-5", y: "-5",
        width: String(NODE_W + 10), height: String(NODE_H + 10), rx: "10"
      });
      group.appendChild(ring);

      var badge = svgEl("g", { "class": "node__claim-badge" });
      badge.appendChild(svgEl("rect", {
        "class": "node__badge-bg", x: String(NODE_W - 74), y: String(NODE_H - 20),
        width: "72", height: "16", rx: "8"
      }));
      badge.appendChild(svgEl("text", {
        "class": "node__badge-text", x: String(NODE_W - 38), y: String(NODE_H - 9),
        "text-anchor": "middle"
      }, truncate("@" + task.claimed_by, 12)));
      group.appendChild(badge);
    }

    if (elided > 0) {
      var mark = svgEl("g", { "class": "node__elided" });
      mark.appendChild(svgEl("rect", {
        "class": "node__elided-bg", x: "6", y: String(NODE_H - 20),
        width: "28", height: "16", rx: "4"
      }));
      mark.appendChild(svgEl("text", {
        "class": "node__elided-text", x: "20", y: String(NODE_H - 9),
        "text-anchor": "middle"
      }, "+" + elided));
      group.appendChild(mark);
    }

    group.addEventListener("pointerenter", function (event) {
      scheduleTooltip(task.id, event);
      highlightIncident(task.id, true);
    });
    group.addEventListener("pointermove", function (event) {
      moveTooltip(event);
    });
    group.addEventListener("pointerleave", function () {
      cancelTooltip();
      highlightIncident(task.id, false);
    });

    return group;
  }

  function buildEdge(edge, positions) {
    var source = positions[edge.source];
    var target = positions[edge.target];
    if (!source || !target) { return null; }
    var x1 = source.x + NODE_W;
    var y1 = source.y + NODE_H / 2;
    var x2 = target.x;
    var y2 = target.y + NODE_H / 2;
    var mid = (x1 + x2) / 2;
    var d = "M " + x1 + " " + y1 + " C " + mid + " " + y1 + ", " + mid + " " + y2 +
      ", " + x2 + " " + y2;

    var group = svgEl("g", {
      "class": "edge",
      "data-source": edge.source,
      "data-target": edge.target
    });
    var title = svgEl("title");
    title.textContent = edge.source + " must finish before " + edge.target;
    group.appendChild(title);
    group.appendChild(svgEl("path", { "class": "edge__line", d: d }));
    var hit = svgEl("path", { "class": "edge__hit", d: d });
    group.appendChild(hit);
    hit.addEventListener("pointerenter", function () {
      group.setAttribute("data-highlight", "true");
      setNodeHighlight(edge.source, true);
      setNodeHighlight(edge.target, true);
    });
    hit.addEventListener("pointerleave", function () {
      group.removeAttribute("data-highlight");
      setNodeHighlight(edge.source, false);
      setNodeHighlight(edge.target, false);
    });
    return group;
  }

  function setNodeHighlight(id, on) {
    var node = qs('.node[data-id="' + id + '"]');
    if (!node) { return; }
    if (on) { node.setAttribute("data-hover", "true"); }
    else { node.removeAttribute("data-hover"); }
  }

  function highlightIncident(id, on) {
    qsa('.edge[data-source="' + id + '"], .edge[data-target="' + id + '"]').forEach(function (edge) {
      if (on) { edge.setAttribute("data-highlight", "true"); }
      else { edge.removeAttribute("data-highlight"); }
    });
  }

  function renderGraph() {
    var data = visibleGraph();
    var plan = layout(data.nodes, data.edges);
    lastPlan = plan;
    var elidedFor = {};
    data.nodes.forEach(function (task) {
      var count = 0;
      DATA.edges.forEach(function (edge) {
        if (edge.source === task.id && data.hidden[edge.target]) { count += 1; }
        if (edge.target === task.id && data.hidden[edge.source]) { count += 1; }
      });
      elidedFor[task.id] = count;
    });

    viewport.textContent = "";
    var edgeFragment = document.createDocumentFragment();
    data.edges.forEach(function (edge) {
      var group = buildEdge(edge, plan.positions);
      if (group) { edgeFragment.appendChild(group); }
    });
    viewport.appendChild(edgeFragment);

    var nodeFragment = document.createDocumentFragment();
    data.nodes.forEach(function (task) {
      nodeFragment.appendChild(buildNode(task, plan.positions[task.id], elidedFor[task.id] || 0));
    });
    viewport.appendChild(nodeFragment);

    var count = qs("#graph-count");
    if (count) {
      count.textContent = data.nodes.length + " nodes \u00b7 " + data.edges.length + " edges" +
        (data.hiddenCount ? " \u00b7 " + data.hiddenCount + " hidden" : "");
    }
    var hiddenCount = qs("#hidden-count");
    if (hiddenCount) {
      hiddenCount.textContent = data.hiddenCount
        ? data.hiddenCount + " hidden \u00b7 " + data.hiddenEdges + " links elided"
        : "";
    }
    var aria = qs("#graph-svg");
    if (aria) {
      aria.setAttribute("aria-label", "Dependency graph: " + data.nodes.length + " tasks, " +
        data.edges.length + " links." + (data.hiddenCount ? " " + data.hiddenCount +
        " hidden." : "") + " Press question mark for shortcuts.");
    }
    renderOutline(data);
    renderMinimap(data, plan);
    applyView();
    if (selectedId && qs('.node[data-id="' + selectedId + '"]')) {
      selectNode(selectedId);
    } else if (selectedId) {
      clearSelection();
    }
    updateFocusClaimedAvailability();
  }

  /* --------------------------------------------------------------- outline */

  function renderOutline(data) {
    var host = qs("#outline-list");
    if (!host) { return; }
    host.textContent = "";
    var plan = layout(data.nodes, data.edges);
    var depths = {};
    data.nodes.forEach(function (task) { depths[task.id] = plan.positions[task.id].depth; });
    var prereqs = {};
    data.edges.forEach(function (edge) {
      if (!prereqs[edge.target]) { prereqs[edge.target] = []; }
      prereqs[edge.target].push(edge.source);
    });
    var sorted = data.nodes.slice().sort(function (a, b) {
      var da = depths[a.id] || 0, db = depths[b.id] || 0;
      if (da !== db) { return da - db; }
      return DATA.tasks.indexOf(a) - DATA.tasks.indexOf(b);
    });
    sorted.forEach(function (task) {
      var li = el("li", { "class": "outline__item" });
      var depth = Math.min(depths[task.id] || 0, 12);
      li.setAttribute("data-depth", String(depth));
      var pad = "padding-left:" + (depth * 16) + "px";
      li.setAttribute("data-indent", String(depth));
      li.appendChild(el("span", { "class": "outline__id" }, task.id + " "));
      li.appendChild(el("span", { "class": "status-chip", "data-status": task.status },
        GLYPH[task.status] + " " + WORD[task.status]));
      li.appendChild(document.createTextNode(" " + task.title + " "));
      var needs = (prereqs[task.id] || []);
      if (needs.length) {
        li.appendChild(el("span", { "class": "outline__needs" }, "needs: " + needs.join(", ")));
      } else {
        li.appendChild(el("span", { "class": "outline__needs" }, "needs: none"));
      }
      if (task.claimed_by) {
        li.appendChild(el("span", { "class": "outline__claim" },
          " [claimed by " + task.claimed_by + "]"));
      }
      host.appendChild(li);
    });
    /* Indentation without inline styles: depth classes. */
    qsa("#outline-list .outline__item").forEach(function (li) {
      li.className = "outline__item outline-depth-" + Math.min(Number(li.getAttribute("data-depth")), 12);
    });
  }

  /* -------------------------------------------------------------- minimap */

  function renderMinimap(data, plan) {
    var box = qs("#minimap");
    if (!box) { return; }
    if (!minimapSvg) { return; }
    minimapSvg.textContent = "";
    var rect = minimapSvg.getBoundingClientRect();
    var mw = rect.width || 180;
    var mh = rect.height || 110;
    var bounds = { minX: 0, minY: 0, maxX: plan.width, maxY: plan.height };
    var bw = Math.max(bounds.maxX - bounds.minX, 1);
    var bh = Math.max(bounds.maxY - bounds.minY, 1);
    var scale = Math.min((mw - 10) / bw, (mh - 10) / bh);
    var ox = (mw - bw * scale) / 2 - bounds.minX * scale;
    var oy = (mh - bh * scale) / 2 - bounds.minY * scale;
    minimapState = { scale: scale, ox: ox, oy: oy };
    minimapSvg.setAttribute("viewBox", "0 0 " + mw + " " + mh);

    data.nodes.forEach(function (task) {
      var pos = plan.positions[task.id];
      if (!pos) { return; }
      var node = svgEl("rect", {
        "class": "minimap__node",
        "data-status": task.status,
        "data-claimed": task.claimed_by ? "true" : "false",
        x: String(pos.x * scale + ox), y: String(pos.y * scale + oy),
        width: String(Math.max(NODE_W * scale, 3)),
        height: String(Math.max(NODE_H * scale, 2)),
        rx: "1"
      });
      if (task.claimed_by) { node.setAttribute("stroke-width", "2"); }
      minimapSvg.appendChild(node);
    });

    var viewportRect = svgEl("rect", { "class": "minimap__viewport", id: "minimap-viewport" });
    minimapSvg.appendChild(viewportRect);
    updateMinimapViewport();
  }

  function updateMinimapViewport() {
    if (!minimapSvg) { return; }
    var rect = minimapSvg.getBoundingClientRect();
    var mw = rect.width || 180;
    var mh = rect.height || 110;
    var vp = qs("#minimap-viewport");
    if (!vp) { return; }
    var x = (-view.tx / view.scale) * minimapState.scale + minimapState.ox;
    var y = (-view.ty / view.scale) * minimapState.scale + minimapState.oy;
    var w = (svg.clientWidth / view.scale) * minimapState.scale;
    var h = (svg.clientHeight / view.scale) * minimapState.scale;
    x = Math.max(0, Math.min(mw - 4, x));
    y = Math.max(0, Math.min(mh - 4, y));
    w = Math.min(w, mw - x);
    h = Math.min(h, mh - y);
    vp.setAttribute("x", String(x));
    vp.setAttribute("y", String(y));
    vp.setAttribute("width", String(Math.max(w, 4)));
    vp.setAttribute("height", String(Math.max(h, 4)));
  }

  function minimapToGraph(clientX, clientY) {
    var rect = minimapSvg.getBoundingClientRect();
    var mx = clientX - rect.left;
    var my = clientY - rect.top;
    return {
      x: (mx - minimapState.ox) / minimapState.scale,
      y: (my - minimapState.oy) / minimapState.scale
    };
  }

  /* ---------------------------------------------------------- pan and zoom */

  function applyView() {
    viewport.setAttribute("transform",
      "translate(" + view.tx + " " + view.ty + ") scale(" + view.scale + ")");
    var root = qs("#graph-root");
    if (root) {
      var lod = view.scale >= 0.8 ? "full" : view.scale >= 0.45 ? "compact" : "shape";
      root.setAttribute("data-lod", lod);
    }
    updateMinimapViewport();
  }

  function clampPan() {
    var plan = lastPlan;
    if (!plan) { return; }
    var left = view.tx;
    var top = view.ty;
    var right = view.tx + plan.width * view.scale;
    var bottom = view.ty + plan.height * view.scale;
    var pad = EDGE_PAD;
    if (right < pad) { view.tx += pad - right; }
    if (left > svg.clientWidth - pad) { view.tx = svg.clientWidth - pad; }
    if (bottom < pad) { view.ty += pad - bottom; }
    if (top > svg.clientHeight - pad) { view.ty = svg.clientHeight - pad; }
  }

  var lastPlan = null;

  function zoomAt(cx, cy, factor) {
    var next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, view.scale * factor));
    var ratio = next / view.scale;
    view.tx = cx - (cx - view.tx) * ratio;
    view.ty = cy - (cy - view.ty) * ratio;
    view.scale = next;
    clampPan();
    applyView();
  }

  function fitAll(animate) {
    var plan = lastPlan;
    if (!plan || !svg) { return; }
    var w = svg.clientWidth;
    var h = svg.clientHeight;
    var next = Math.min((w - 80) / Math.max(plan.width, 1),
                        (h - 80) / Math.max(plan.height, 1), 1.2);
    next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, next));
    var tx = (w - plan.width * next) / 2;
    var ty = (h - plan.height * next) / 2;
    if (animate && !prefersReduced()) {
      animateViewTo(next, tx, ty);
    } else {
      view = { scale: next, tx: tx, ty: ty };
      applyView();
    }
  }

  function animateViewTo(scale, tx, ty) {
    var from = { scale: view.scale, tx: view.tx, ty: view.ty };
    var start = 0;
    function step(now) {
      if (!start) { start = now; }
      var t = Math.min(1, (now - start) / 240);
      var eased = 1 - Math.pow(1 - t, 3);
      view.scale = from.scale + (scale - from.scale) * eased;
      view.tx = from.tx + (tx - from.tx) * eased;
      view.ty = from.ty + (ty - from.ty) * eased;
      applyView();
      if (t < 1) { window.requestAnimationFrame(step); }
    }
    window.requestAnimationFrame(step);
  }

  function prefersReduced() {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function pointerPoint(event) {
    var rect = svg.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  function setupPanZoom() {
    var drag = null;

    svg.addEventListener("pointerdown", function (event) {
      if (event.button !== 0 && event.pointerType === "mouse") { return; }
      var hit = event.target.closest ? event.target.closest(".node") : null;
      drag = {
        id: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        lastX: event.clientX,
        lastY: event.clientY,
        moved: false,
        nodeId: hit ? hit.getAttribute("data-id") : null
      };
      svg.setPointerCapture(event.pointerId);
      hideTooltip();
    });

    svg.addEventListener("pointermove", function (event) {
      if (drag && drag.id === event.pointerId) {
        var dx = event.clientX - drag.lastX;
        var dy = event.clientY - drag.lastY;
        var total = Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY);
        if (!drag.moved && total >= DRAG_THRESHOLD) {
          drag.moved = true;
          svg.setAttribute("data-panning", "true");
        }
        if (drag.moved) {
          view.tx += dx;
          view.ty += dy;
          drag.lastX = event.clientX;
          drag.lastY = event.clientY;
          clampPan();
          applyView();
        }
        return;
      }
      moveTooltip(event);
    });

    function endDrag(event) {
      if (!drag || drag.id !== event.pointerId) { return; }
      var moved = drag.moved;
      var hitId = drag.nodeId;
      drag = null;
      svg.removeAttribute("data-panning");
      if (svg.hasPointerCapture && svg.hasPointerCapture(event.pointerId)) {
        svg.releasePointerCapture(event.pointerId);
      }
      if (!moved) {
        /* Pointer capture retargets events to the svg, so the node hit is
           recorded at pointerdown time, not read from event.target here. */
        if (hitId) { selectNode(hitId); }
        else { clearSelection(); }
      }
    }

    svg.addEventListener("pointerup", endDrag);
    svg.addEventListener("pointercancel", function (event) {
      drag = null;
      svg.removeAttribute("data-panning");
      if (svg.hasPointerCapture && svg.hasPointerCapture(event.pointerId)) {
        svg.releasePointerCapture(event.pointerId);
      }
    });

    svg.addEventListener("wheel", function (event) {
      event.preventDefault();
      var delta = event.deltaY;
      if (event.deltaMode === 1) { delta *= 16; }
      else if (event.deltaMode === 2) { delta *= svg.clientHeight; }
      var point = pointerPoint(event);
      if (event.ctrlKey) {
        zoomAt(point.x, point.y, Math.exp(-delta * 0.010));
      } else if (event.shiftKey) {
        view.tx -= (event.deltaX || 0);
        clampPan();
        applyView();
      } else if (event.deltaMode !== 0 || Math.abs(delta) >= 40) {
        zoomAt(point.x, point.y, Math.exp(-delta * 0.0015));
      } else {
        view.tx -= (event.deltaX || 0);
        view.ty -= delta;
        clampPan();
        applyView();
      }
      hideTooltip();
    }, { passive: false });

    svg.addEventListener("dblclick", function (event) {
      var node = event.target.closest ? event.target.closest(".node") : null;
      if (node) {
        var id = node.getAttribute("data-id");
        var pos = lastPlan.positions[id];
        if (pos) {
          var targetScale = Math.min(MAX_SCALE, Math.max(1.4, view.scale));
          var tx = svg.clientWidth / 2 - (pos.x + NODE_W / 2) * targetScale;
          var ty = svg.clientHeight / 2 - (pos.y + NODE_H / 2) * targetScale;
          if (prefersReduced()) { view = { scale: targetScale, tx: tx, ty: ty }; applyView(); }
          else { animateViewTo(targetScale, tx, ty); }
        }
      } else {
        var p = pointerPoint(event);
        zoomAt(p.x, p.y, 1.6);
      }
    });

    qsa("[data-graph-action]").forEach(function (button) {
      button.addEventListener("click", function () {
        var action = button.getAttribute("data-graph-action");
        var cx = svg.clientWidth / 2;
        var cy = svg.clientHeight / 2;
        if (action === "zoom-in") { zoomAt(cx, cy, 1.25); }
        if (action === "zoom-out") { zoomAt(cx, cy, 0.8); }
        if (action === "fit-all") { fitAll(true); }
        if (action === "zoom-reset") {
          zoomAt(cx, cy, 1 / view.scale);
        }
      });
    });

    if (minimapSvg) {
      minimapSvg.addEventListener("pointerdown", function (event) {
        var p = minimapToGraph(event.clientX, event.clientY);
        view.tx = svg.clientWidth / 2 - p.x * view.scale;
        view.ty = svg.clientHeight / 2 - p.y * view.scale;
        clampPan();
        applyView();
        hideTooltip();
      });
    }
  }

  /* ------------------------------------------------------------- tooltip */

  function tooltipLines(task) {
    return [
      "status: " + WORD[task.status] + " \u00b7 priority: " + task.priority,
      "assignee: " + (task.assignee || "unassigned") +
        (task.claimed_by ? " \u00b7 claimed by " + task.claimed_by : ""),
      task.blocked ? "blocked: " + (task.blocked_reason || "blocked") : "not blocked"
    ];
  }

  function scheduleTooltip(id, event) {
    cancelTooltip();
    hoverTimer = window.setTimeout(function () { showTooltip(id, event); }, HOVER_DELAY);
  }

  function cancelTooltip() {
    if (hoverTimer !== null) { window.clearTimeout(hoverTimer); hoverTimer = null; }
    hideTooltip();
  }

  function showTooltip(id, event) {
    hoverTimer = null;
    var task = nodeById(id);
    var tip = qs("#graph-tooltip");
    if (!task || !tip) { return; }
    var lines = tooltipLines(task);
    var texts = qsa("text", tip);
    var idText = tip.querySelector(".tooltip__id");
    var titleText = tip.querySelector(".tooltip__title");
    var rows = qsa(".tooltip__row", tip);
    if (idText) { idText.textContent = task.id; }
    if (titleText) { titleText.textContent = truncate(task.title, 34); }
    if (rows[0]) { rows[0].textContent = lines[0]; }
    if (rows[1]) { rows[1].textContent = lines[1]; }
    if (rows[2]) { rows[2].textContent = lines[2]; }
    tip.hidden = false;
    placeTooltip(event);
    var node = qs('.node[data-id="' + id + '"]');
    if (node && node.id) { svg.setAttribute("aria-describedby", "graph-tooltip"); }
    void texts;
  }

  function placeTooltip(event) {
    var tip = qs("#graph-tooltip");
    if (!tip || tip.hidden) { return; }
    var p = pointerPoint(event);
    var w = 264;
    var h = 86;
    var x = Math.max(8, Math.min(svg.clientWidth - w - 8, p.x + 14));
    var y = Math.max(8, Math.min(svg.clientHeight - h - 8, p.y + 14));
    tip.setAttribute("transform", "translate(" + x + " " + y + ")");
  }

  function moveTooltip(event) {
    if (hoverTimer === null && !qs("#graph-tooltip").hidden) { placeTooltip(event); }
  }

  function hideTooltip() {
    var tip = qs("#graph-tooltip");
    if (tip) { tip.hidden = true; }
    svg.removeAttribute("aria-describedby");
  }

  /* ----------------------------------------------------------- selection */

  function adjacency() {
    var pre = {};
    var dep = {};
    DATA.edges.forEach(function (edge) {
      if (!pre[edge.target]) { pre[edge.target] = []; }
      pre[edge.target].push(edge.source);
      if (!dep[edge.source]) { dep[edge.source] = []; }
      dep[edge.source].push(edge.target);
    });
    return { pre: pre, dep: dep };
  }

  function closure(id) {
    var map = adjacency();
    var seen = {};
    var stack = [id];
    while (stack.length) {
      var current = stack.pop();
      (map.pre[current] || []).forEach(function (next) {
        if (!seen[next]) { seen[next] = true; stack.push(next); }
      });
      (map.dep[current] || []).forEach(function (next) {
        if (!seen[next]) { seen[next] = true; stack.push(next); }
      });
    }
    seen[id] = true;
    return seen;
  }

  function selectNode(id) {
    if (!id) { return; }
    selectedId = id;
    activeId = id;
    var keep = closure(id);
    var graph = qs("#graph-root");
    if (graph) { graph.setAttribute("data-dimmed", "true"); }
    qsa(".node").forEach(function (node) {
      var nodeId = node.getAttribute("data-id");
      if (keep[nodeId]) { node.removeAttribute("data-dimmed"); }
      else { node.setAttribute("data-dimmed", "true"); }
      node.setAttribute("data-selected", nodeId === id ? "true" : "false");
      node.setAttribute("data-active", nodeId === id ? "true" : "false");
    });
    qsa(".edge").forEach(function (edge) {
      var onPath = keep[edge.getAttribute("data-source")] && keep[edge.getAttribute("data-target")];
      if (onPath) { edge.removeAttribute("data-dimmed"); }
      else { edge.setAttribute("data-dimmed", "true"); }
    });
    svg.setAttribute("aria-activedescendant", "node-" + id);
    showSelectionPanel(id);
  }

  function clearSelection() {
    selectedId = null;
    var graph = qs("#graph-root");
    if (graph) { graph.setAttribute("data-dimmed", "false"); }
    qsa(".node").forEach(function (node) {
      node.removeAttribute("data-dimmed");
      node.setAttribute("data-selected", "false");
      node.setAttribute("data-active", "false");
    });
    qsa(".edge").forEach(function (edge) { edge.removeAttribute("data-dimmed"); });
    svg.removeAttribute("aria-activedescendant");
    var panel = qs("#selection-panel");
    if (panel) { panel.hidden = true; }
    hideTooltip();
  }

  function showSelectionPanel(id) {
    var task = nodeById(id);
    var panel = qs("#selection-panel");
    if (!task || !panel) { return; }
    var title = qs("#selection-title");
    var meta = qs("#selection-meta");
    var link = qs("#selection-link");
    if (title) { title.textContent = task.id + " \u2014 " + task.title; }
    if (meta) {
      meta.textContent = WORD[task.status] + " \u00b7 " + task.priority +
        (task.assignee ? " \u00b7 " + task.assignee : "") +
        (task.claimed_by ? " \u00b7 claimed by " + task.claimed_by : "") +
        (task.blocked ? " \u00b7 blocked" : "");
    }
    if (link) { link.setAttribute("href", "#task"); }
    panel.hidden = false;
  }

  /* ------------------------------------------------------- claimed toggles */

  function updateFocusClaimedAvailability() {
    var focusButton = qs("#focus-claimed");
    if (!focusButton) { return; }
    if (!highlightClaimed) {
      focusClaimed = false;
      focusButton.setAttribute("aria-pressed", "false");
      focusButton.setAttribute("disabled", "disabled");
      var graph = qs("#graph-root");
      if (graph) { graph.setAttribute("data-focus-claimed", "false"); }
    } else {
      focusButton.removeAttribute("disabled");
    }
  }

  function setupClaimedToggles() {
    var highlight = qs("#highlight-claimed");
    var focus = qs("#focus-claimed");
    var graph = qs("#graph-root");

    if (highlight) {
      highlight.addEventListener("click", function () {
        highlightClaimed = !highlightClaimed;
        highlight.setAttribute("aria-pressed", highlightClaimed ? "true" : "false");
        if (graph) { graph.setAttribute("data-claim-highlight", highlightClaimed ? "true" : "false"); }
        updateFocusClaimedAvailability();
        announce(highlightClaimed ? "Highlight claimed on." : "Highlight claimed off.");
      });
    }

    if (focus) {
      focus.addEventListener("click", function () {
        if (!highlightClaimed) { return; }
        focusClaimed = !focusClaimed;
        focus.setAttribute("aria-pressed", focusClaimed ? "true" : "false");
        if (graph) { graph.setAttribute("data-focus-claimed", focusClaimed ? "true" : "false"); }
        var claimed = DATA.tasks.filter(function (task) { return Boolean(task.claimed_by); }).length;
        announce(focusClaimed
          ? "Focusing claimed tasks: " + claimed + " of " + DATA.tasks.length + "."
          : "Focus claimed off.");
      });
    }
  }

  /* ---------------------------------------------------------- hide closed */

  function setupHideClosed() {
    var button = qs("#hide-closed");
    if (!button) { return; }
    button.addEventListener("click", function () {
      hideClosed = !hideClosed;
      button.setAttribute("aria-pressed", hideClosed ? "true" : "false");
      renderGraph();
      var data = visibleGraph();
      announce(hideClosed
        ? "Hiding done and cancelled: " + data.hiddenCount + " tasks hidden."
        : "Showing all tasks.");
      try {
        var url = new URL(window.location.href);
        if (hideClosed) { url.searchParams.set("hide_closed", "1"); }
        else { url.searchParams.delete("hide_closed"); }
        window.history.replaceState({}, "", url.toString());
      } catch (error) {
        /* file:// or a restricted context: the toggle still works in memory. */
      }
      try {
        window.localStorage.setItem("tackboard.hideClosed", hideClosed ? "1" : "0");
      } catch (error) {
        /* Storage unavailable: URL and memory still carry the state. */
      }
    });

    try {
      var param = new URL(window.location.href).searchParams.get("hide_closed");
      if (param === "1" || param === "0") {
        hideClosed = param === "1";
      } else {
        hideClosed = window.localStorage.getItem("tackboard.hideClosed") === "1";
      }
    } catch (error) {
      hideClosed = false;
    }
    button.setAttribute("aria-pressed", hideClosed ? "true" : "false");
  }

  /* ------------------------------------------------------------- announce */

  function announce(message) {
    var region = qs("#board-live");
    if (!region) { return; }
    region.textContent = "";
    window.setTimeout(function () { region.textContent = message; }, 40);
  }

  /* ------------------------------------------------------ graph keyboard */

  function setupGraphKeyboard() {
    svg.addEventListener("keydown", function (event) {
      var key = event.key;
      if (key === "Escape") { clearSelection(); return; }
      if (key === "Enter") {
        if (selectedId) {
          var target = qs("#task");
          if (target) { target.scrollIntoView({ block: "start" }); }
          event.preventDefault();
        }
        return;
      }
      var step = 40;
      if (key === "ArrowLeft" || key === "ArrowRight" || key === "ArrowUp" || key === "ArrowDown") {
        event.preventDefault();
        if (selectedId && (key === "ArrowLeft" || key === "ArrowRight")) {
          var map = adjacency();
          var next = key === "ArrowLeft"
            ? (map.pre[selectedId] || [])[0]
            : (map.dep[selectedId] || [])[0];
          if (next) { selectNode(next); return; }
        }
        if (key === "ArrowLeft") { view.tx += step; }
        if (key === "ArrowRight") { view.tx -= step; }
        if (key === "ArrowUp") { view.ty += step; }
        if (key === "ArrowDown") { view.ty -= step; }
        clampPan();
        applyView();
        return;
      }
      if (key === "+" || key === "=") { zoomAt(svg.clientWidth / 2, svg.clientHeight / 2, 1.2); }
      if (key === "-" || key === "_") { zoomAt(svg.clientWidth / 2, svg.clientHeight / 2, 0.8); }
      if (key === "0") { fitAll(true); }
    });

    svg.addEventListener("focus", function () {
      if (!selectedId) {
        var first = qs(".node");
        if (first) {
          activeId = first.getAttribute("data-id");
          first.setAttribute("data-active", "true");
          svg.setAttribute("aria-activedescendant", "node-" + activeId);
        }
      }
    });
  }

  /* ----------------------------------------------------- global shortcuts */

  function isTyping(target) {
    if (!target) { return false; }
    var tag = target.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" ||
      target.isContentEditable === true;
  }

  function setupShortcuts() {
    var help = qs("#help");
    var sequence = { key: null, at: 0 };

    qsa("#help-open").forEach(function (button) {
      button.addEventListener("click", function () { openHelp(); });
    });
    var close = qs("#help-close");
    if (close) { close.addEventListener("click", function () { closeHelp(); }); }
    if (help) {
      help.addEventListener("click", function (event) {
        if (event.target === help) { closeHelp(); }
      });
      help.addEventListener("close", function () { restoreFocus(); });
    }

    document.addEventListener("keydown", function (event) {
      if (isTyping(event.target)) { return; }
      if (help && help.open) {
        if (event.key === "Escape") { closeHelp(); }
        return;
      }
      if (event.ctrlKey || event.metaKey || event.altKey) { return; }
      var key = event.key;

      if (key === "?") { event.preventDefault(); openHelp(); return; }
      if (key === "t") { event.preventDefault(); window.tackTheme.cycle(); return; }
      if (key === "/") {
        event.preventDefault();
        var more = qs("#filters-more");
        if (more) { more.open = true; }
        var input = qs("#filter-assignee");
        if (input) { input.focus(); }
        return;
      }
      if (key === "f") {
        event.preventDefault();
        var details = qs("#filters-more");
        if (details) {
          details.open = true;
          var first = details.querySelector("input");
          if (first) { first.focus(); }
        }
        return;
      }
      if (key === "c") {
        var highlight = qs("#highlight-claimed");
        if (highlight) { highlight.click(); }
        return;
      }
      if (key === "h") {
        var hide = qs("#hide-closed");
        if (hide) { hide.click(); }
        return;
      }
      /* Two-key "g" sequences. The pending key is consumed first, so g-g
         navigates to the graph instead of cancelling itself. */
      if (sequence.key === "g" && Date.now() - sequence.at < 1000) {
        sequence.key = null;
        if (key === "b") { event.preventDefault(); window.location.hash = "board"; return; }
        if (key === "g") { event.preventDefault(); window.location.hash = "graph"; return; }
      }
      if (key === "g") {
        sequence.key = "g";
        sequence.at = Date.now();
      }
    });
  }

  var helpOpener = null;

  function openHelp() {
    var help = qs("#help");
    if (!help) { return; }
    helpOpener = document.activeElement;
    if (typeof help.showModal === "function") { help.showModal(); }
    else { help.setAttribute("open", "open"); }
  }

  function closeHelp() {
    var help = qs("#help");
    if (!help) { return; }
    if (typeof help.close === "function" && help.open) { help.close(); }
    else { help.removeAttribute("open"); restoreFocus(); }
  }

  function restoreFocus() {
    if (helpOpener && typeof helpOpener.focus === "function") { helpOpener.focus(); }
  }

  /* ------------------------------------------------------------------ init */

  function init() {
    svg = qs("#graph-svg");
    viewport = qs("#graph-viewport");
    minimapSvg = qs("#minimap-svg");
    if (!svg || !viewport) { return; }

    mountMarkdown();
    setupBoard();
    setupRefresh();
    setupClaimedToggles();
    setupHideClosed();
    setupShortcuts();
    setupGraphKeyboard();
    setupPanZoom();

    svg.setAttribute("viewBox", "0 0 " + svg.clientWidth + " " + svg.clientHeight);
    var minimap = qs("#minimap");
    if (minimap) { minimap.hidden = false; }
    renderGraph();
    fitAll(false);

    window.addEventListener("resize", function () {
      svg.setAttribute("viewBox", "0 0 " + svg.clientWidth + " " + svg.clientHeight);
      clampPan();
      applyView();
      var data = visibleGraph();
      var plan = layout(data.nodes, data.edges);
      lastPlan = plan;
      renderMinimap(data, plan);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
