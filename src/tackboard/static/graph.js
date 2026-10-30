/* Dependency graph renderer: hand-written SVG, no third-party code.
 *
 * The server always renders the dependency outline underneath this canvas, so
 * the page is usable with JavaScript disabled. This script replaces that
 * outline with an interactive graph once the data has been fetched and drawn;
 * if anything fails, the outline stays exactly where it is.
 *
 * Navigation is Pointer Events only: mouse, pen and touch share one code path.
 * A drag pans (from empty space or a node); a click selects; a double-click
 * zooms. The camera lives in module state and is written to the viewport
 * transform at most once per animation frame.
 *
 * Every node is one uniform rounded rectangle so the text always stays inside
 * its container. Status is carried by a corner glyph, the border style and the
 * colour; the label stays as text. A claimed node carries a double ring and a
 * holder badge on its own row; blocked and eligible nodes carry additional,
 * non-colour cues in their text and corner marks.
 */
(function () {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";

  var root = document.getElementById("graph");
  if (!root) { return; }

  var canvas = document.getElementById("graph-canvas");
  var fallback = document.getElementById("graph-fallback");
  var selectionPanel = document.getElementById("graph-selection");
  var graphStatus = document.getElementById("graph-status");
  var minimap = document.getElementById("minimap");
  var minimapSvg = document.getElementById("minimap-svg");
  var minimapToggle = document.getElementById("minimap-toggle");
  var minimapViewRect = null;
  var taskId = root.getAttribute("data-task") || "";

  var NODE_W = 224;
  var NODE_H = 72;
  var GAP_X = 76;
  var GAP_Y = 26;
  var MIN_SCALE = 0.15;
  var MAX_SCALE = 4;

  /* A click and a drag are separated by 4 CSS pixels. */
  var CLICK_RADIUS = 4;
  var PATH_LIMIT = 200;
  var LOD_FULL = 0.8;
  var LOD_COMPACT = 0.45;
  var IDLE_NODE_LIMIT = 500;
  var MIN_VISIBLE = 120;
  var TOOLTIP_MARGIN = 8;
  /* The tooltip's near corner sits exactly at the pointer: no offset. */
  var TOOLTIP_OFFSET = 0;
  var SELECT_DELAY = 200;
  var DUR_LAYOUT = 240;
  var DUR_SLOW = 400;
  var MINIMAP_KEY = "tackboard.minimap";
  var MINIMAP_BREAKPOINT = 900;

  var STATUS_LABEL = {
    backlog: "backlog",
    ready: "ready",
    in_progress: "in progress",
    review: "review",
    done: "done",
    cancelled: "cancelled"
  };

  /* Status glyphs. Keep in sync with STATUS_GLYPHS in web.py and GLYPHS in
   * board.js; the test suite asserts all three maps agree. */
  var STATUS_GLYPH = {
    backlog: "\u2026",
    ready: "\u25b7",
    in_progress: "\u25b6",
    review: "\u25d0",
    done: "\u2713",
    cancelled: "\u2715"
  };

  var PRIORITY_PIPS = {
    urgent: "\u2581\u2581\u2581\u2581",
    high: "\u2581\u2581\u2581",
    normal: "\u2581\u2581",
    low: "\u2581"
  };

  /* Camera: scale and translate in CSS pixels. 1 user unit = 1 CSS pixel. */
  var view = { scale: 1, tx: 0, ty: 0 };
  var userOwnsCamera = false;

  var dirty = false;
  var rafId = 0;

  var plan = null;
  var data = null;
  var svg = null;
  var viewport = null;
  var tooltip = null;
  var tooltipBg = null;
  var tooltipRows = [];
  var tooltipNode = null;
  var nodeEls = {};
  var edgeEls = {};
  var nodesById = {};
  var edges = [];
  var prereqMap = {};
  var depMap = {};
  var blockedByMap = {};

  var selectedId = null;
  var hoveredNode = null;
  var hoverTimer = 0;
  var selectTimer = 0;
  var panning = false;
  var suppressClick = false;
  var drag = null;
  var minimapDrag = null;
  var lastPointer = { x: 0, y: 0 };
  var tooltipVisible = false;
  var minimapCollapsed = false;
  var minimapPref = null;
  var lastDownNodeId = null;
  var lastClickNodeId = null;

  var HIDE_CLOSED_KEY = "tackboard.hideClosed";
  var fullData = null;
  var visibleData = null;
  var hiddenIds = {};
  var hiddenLinkCount = {};
  var hideClosed = false;
  var claimHighlight = true;
  var focusClaimed = false;
  var activeId = null;
  var pointers = {};
  var pinch = null;
  var lastTap = { time: 0, x: 0, y: 0 };
  var tapHandled = false;
  var globalEventsBound = false;
  var announceTimer = 0;

  var W = 0;
  var H = 0;
  var canvasRect = null;
  var minimapRect = null;
  var minimapInverse = null;
  var reduceMotion = !!(window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  /* ----------------------------------------------------------------------- */
  /* small helpers                                                           */
  /* ----------------------------------------------------------------------- */

  function el(name, attrs) {
    var node = document.createElementNS(NS, name);
    var keys = Object.keys(attrs || {});
    for (var i = 0; i < keys.length; i += 1) {
      node.setAttribute(keys[i], attrs[keys[i]]);
    }
    return node;
  }

  function textEl(attrs, value) {
    var node = el("text", attrs || {});
    node.textContent = value;
    return node;
  }

  function setText(node, value) {
    node.textContent = value;
  }

  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }

  function truncate(value, limit) {
    var text = String(value === null || value === undefined ? "" : value);
    return text.length > limit ? text.slice(0, limit - 1) + "\u2026" : text;
  }

  function closest(node, selector) {
    while (node && node !== svg && node !== document) {
      if (node.matches && node.matches(selector)) { return node; }
      node = node.parentNode;
    }
    return null;
  }

  function reducedDuration(duration) {
    return reduceMotion ? 0 : duration;
  }

  function isTerminal(status) {
    return status === "done" || status === "cancelled";
  }

  function relativeAge(iso) {
    if (!iso) { return ""; }
    var stamp = Date.parse(iso);
    if (isNaN(stamp)) { return ""; }
    var seconds = Math.max(0, Math.round((Date.now() - stamp) / 1000));
    if (seconds < 60) { return "just now"; }
    var minutes = Math.floor(seconds / 60);
    if (minutes < 60) { return minutes + "m"; }
    var hours = Math.floor(minutes / 60);
    if (hours < 24) { return hours + "h"; }
    var days = Math.floor(hours / 24);
    if (days < 7) { return days + "d"; }
    var weeks = Math.floor(days / 7);
    if (weeks < 5) { return weeks + "w"; }
    return Math.floor(days / 30) + "mo";
  }

  function clipTo(value, limit) {
    var text = String(value === null || value === undefined ? "" : value);
    if (limit < 1) { return ""; }
    return text.length > limit ? text.slice(0, limit - 1) + "\u2026" : text;
  }

  /* The claim badge is drawn inside the card, so its label has to fit the card:
   * a long holder name plus an age plus the stale word is wider than a
   * fixed-width badge, and the overflow used to spill past the badge's right
   * edge. Candidates run from most to least detailed and the first one that
   * fits wins — the age is dropped before the stale word, because staleness is
   * the signal that must never be cut. The full holder stays in the badge's
   * <title>, the outline and the tooltip. */
  var BADGE_PAD = 10;
  var BADGE_LIMIT = NODE_W - 12;
  var BADGE_CHAR_W = 6.6;

  function claimBadgeLabel(holder, age, staleText) {
    var budget = Math.floor((BADGE_LIMIT - BADGE_PAD) / BADGE_CHAR_W);
    var ageText = age ? " \u00b7 " + age : "";
    /* The age may be dropped to make room; the stale word may not. A stale
     * label therefore never considers a suffix without it. */
    var candidates = [];
    if (staleText) {
      if (ageText) { candidates.push(ageText + staleText); }
      candidates.push(staleText);
    } else {
      if (ageText) { candidates.push(ageText); }
      candidates.push("");
    }
    for (var i = 0; i < candidates.length; i += 1) {
      var suffix = candidates[i];
      var name = clipTo(holder, budget - suffix.length - 1);
      var label = "@" + name + suffix;
      /* Prefer a name long enough to stay recognisable; when the holder was
       * short enough not to be clipped, any length is fine. */
      if (label.length <= budget && (name === holder || name.length >= 4)) {
        return label;
      }
    }
    var tail = staleText || "";
    return clipTo("@" + holder, Math.max(1, budget - tail.length)) + tail;
  }

  function announce(message) {
    if (!graphStatus) { return; }
    window.clearTimeout(announceTimer);
    setText(graphStatus, message);
  }

  /* ----------------------------------------------------------------------- */
  /* layout: longest-path columns and row stacking                           */
  /* ----------------------------------------------------------------------- */

  function computeDepths(nodes, edges) {
    var known = {};
    var depth = {};
    nodes.forEach(function (node) {
      known[node.id] = true;
      depth[node.id] = 0;
    });
    var changed = true;
    var rounds = 0;
    while (changed && rounds <= nodes.length) {
      changed = false;
      rounds += 1;
      edges.forEach(function (edge) {
        if (!known[edge.source] || !known[edge.target]) { return; }
        var candidate = depth[edge.source] + 1;
        if (candidate > depth[edge.target]) {
          depth[edge.target] = candidate;
          changed = true;
        }
      });
    }
    return depth;
  }

  function layout(graph) {
    var depth = computeDepths(graph.nodes, graph.edges);
    var perColumn = {};
    var positions = {};
    var maxDepth = 0;
    var maxRows = 0;
    graph.nodes.forEach(function (node) {
      var column = depth[node.id] || 0;
      var row = perColumn[column] || 0;
      perColumn[column] = row + 1;
      positions[node.id] = {
        x: column * (NODE_W + GAP_X),
        y: row * (NODE_H + GAP_Y),
        depth: column
      };
      maxDepth = Math.max(maxDepth, column);
      maxRows = Math.max(maxRows, row + 1);
    });
    return {
      positions: positions,
      width: (maxDepth + 1) * (NODE_W + GAP_X),
      height: Math.max(maxRows, 1) * (NODE_H + GAP_Y)
    };
  }

  /* ----------------------------------------------------------------------- */
  /* adjacency and derived facts                                             */
  /* ----------------------------------------------------------------------- */

  function buildAdjacency(graph, edgeList) {
    prereqMap = {};
    depMap = {};
    blockedByMap = {};
    nodesById = {};
    var statusById = {};
    var links = edgeList || graph.edges;
    graph.nodes.forEach(function (node) {
      prereqMap[node.id] = [];
      depMap[node.id] = [];
      blockedByMap[node.id] = [];
      nodesById[node.id] = node;
      statusById[node.id] = node.status;
    });
    links.forEach(function (edge) {
      if (!prereqMap[edge.target] || !depMap[edge.source]) { return; }
      prereqMap[edge.target].push(edge.source);
      depMap[edge.source].push(edge.target);
      if (!isTerminal(statusById[edge.source])) {
        blockedByMap[edge.target].push(edge.source);
      }
    });
  }

  function useVisibleAdjacency(graph) {
    var visiblePrereq = {};
    var visibleDep = {};
    graph.nodes.forEach(function (node) {
      visiblePrereq[node.id] = [];
      visibleDep[node.id] = [];
    });
    graph.edges.forEach(function (edge) {
      if (visiblePrereq[edge.target]) { visiblePrereq[edge.target].push(edge.source); }
      if (visibleDep[edge.source]) { visibleDep[edge.source].push(edge.target); }
    });
    prereqMap = visiblePrereq;
    depMap = visibleDep;
  }

  function nodeLabel(node) {
    var label = STATUS_LABEL[node.status] || node.status;
    var parts = [node.id + ", " + node.title + ", " + label];
    if (node.reviewable) { parts.push("reviewable"); }
    if (node.claim_stale) { parts.push("stale claim"); }
    if (node.priority) { parts.push("priority " + node.priority); }
    if (node.assignee) { parts.push("assignee " + node.assignee); }
    if (node.claimed_by) {
      var age = relativeAge(node.claimed_at);
      parts.push("claimed by " + node.claimed_by + (age ? " " + age + " ago" : ""));
    }
    var hiddenLinks = hiddenLinkCount[node.id] || 0;
    if (hiddenLinks) {
      parts.push(hiddenLinks + " hidden link" + (hiddenLinks === 1 ? "" : "s"));
    }
    var blocked = blockedByMap[node.id] || [];
    if (blocked.length) { parts.push("blocked by " + blocked.join(", ")); }
    return parts.join(", ");
  }

  function nodeTitleText(node) {
    var label = STATUS_LABEL[node.status] || node.status;
    var text = node.id + " \u2014 " + node.title + " (" + label;
    if (node.reviewable) { text += ", reviewable"; }
    if (node.claim_stale) { text += ", stale claim"; }
    if (node.claimed_by) {
      text += ", claimed by " + node.claimed_by;
      if (node.claimed_at) { text += " since " + node.claimed_at; }
    }
    var hiddenLinks = hiddenLinkCount[node.id] || 0;
    if (hiddenLinks) {
      text += ", " + hiddenLinks + " hidden link" + (hiddenLinks === 1 ? "" : "s");
    }
    return text + ")";
  }

  /* ----------------------------------------------------------------------- */
  /* shapes                                                                  */
  /* ----------------------------------------------------------------------- */

  function shapeElement() {
    /* One uniform card for every status: the text container is always a full
     * rectangle, so no title or label can cross the outline. Status is carried
     * by the corner glyph, the border style and the colour applied by CSS. */
    return el("rect", {
      "class": "node__shape",
      x: 0,
      y: 0,
      width: NODE_W,
      height: NODE_H,
      rx: 8
    });
  }

  function glyphElement(status) {
    /* A small status glyph in the card's top-left corner. It is decorative:
     * the status label and the node's aria-label carry the meaning. */
    return textEl(
      { "class": "node__glyph", x: 8, y: 22, "aria-hidden": "true" },
      STATUS_GLYPH[status] || "\u2022"
    );
  }

  /* ----------------------------------------------------------------------- */
  /* building the graph                                                      */
  /* ----------------------------------------------------------------------- */

  function buildEdgesFragment(graph) {
    var fragment = document.createDocumentFragment();
    edgeEls = {};
    edges = graph.edges;
    graph.edges.forEach(function (edge) {
      var source = plan.positions[edge.source];
      var target = plan.positions[edge.target];
      if (!source || !target) { return; }
      var x1 = source.x + NODE_W;
      var y1 = source.y + NODE_H / 2;
      var x2 = target.x;
      var y2 = target.y + NODE_H / 2;
      var mid = (x1 + x2) / 2;
      var d = "M " + x1 + " " + y1 + " C " + mid + " " + y1 + ", " +
        mid + " " + y2 + ", " + x2 + " " + y2;
      var claimedEdge = !!(
        (nodesById[edge.source] && nodesById[edge.source].claimed_by) ||
        (nodesById[edge.target] && nodesById[edge.target].claimed_by)
      );
      var group = el("g", {
        "class": "edge",
        "data-source": edge.source,
        "data-target": edge.target,
        "data-claimed": claimedEdge ? "true" : "false"
      });
      group.appendChild(el("title", {}));
      group.lastChild.textContent = edge.source + " must finish before " + edge.target;
      group.appendChild(el("path", { "class": "edge__line", d: d }));
      group.appendChild(el("path", {
        "class": "edge__hit",
        d: d,
        "stroke-width": "14",
        stroke: "transparent",
        fill: "none",
        "pointer-events": "stroke"
      }));
      edgeEls[edge.source + "->" + edge.target] = group;
      fragment.appendChild(group);
    });
    return fragment;
  }

  function buildNodesFragment(graph) {
    var fragment = document.createDocumentFragment();
    nodeEls = {};
    graph.nodes.forEach(function (node) {
      var place = plan.positions[node.id];
      if (!place) { return; }
      var group = el("g", {
        "class": "node",
        id: "node-" + node.id,
        role: "link",
        "data-id": node.id,
        "data-status": node.status,
        "data-claimed": node.claimed_by ? "true" : "false",
        "data-blocked": node.blocked ? "true" : "false",
        "data-eligible": node.eligible ? "true" : "false",
        "data-reviewable": node.reviewable ? "true" : "false",
        "data-active": "false",
        transform: "translate(" + place.x + "," + place.y + ")",
        "aria-label": nodeLabel(node)
      });
      group.appendChild(el("title", {}));
      group.lastChild.textContent = nodeTitleText(node);

      group.appendChild(el("rect", {
        "class": "node__hit",
        x: -6,
        y: -6,
        width: NODE_W + 12,
        height: NODE_H + 12,
        fill: "transparent",
        "pointer-events": "all"
      }));

      if (node.claimed_by) {
        /* Two concentric rounded rects: the inner ring sits 4 px outside the
         * status shape with a 2 px gap, the outer ring 4 px further out. The
         * bounding box is used even on diamonds and hexagons. */
        group.appendChild(el("rect", {
          "class": "node__claim-ring",
          x: -8,
          y: -8,
          width: NODE_W + 16,
          height: NODE_H + 16,
          rx: 10,
          "aria-hidden": "true"
        }));
        group.appendChild(el("rect", {
          "class": "node__claim-ring node__claim-ring--inner",
          x: -4,
          y: -4,
          width: NODE_W + 8,
          height: NODE_H + 8,
          rx: 8,
          "aria-hidden": "true"
        }));
      }

      group.appendChild(shapeElement());
      group.appendChild(glyphElement(node.status));

      group.appendChild(textEl({ "class": "node__id", x: 28, y: 20 }, node.id));
      group.appendChild(textEl(
        { "class": "node__title", x: 10, y: 36 },
        truncate(node.title, 22)
      ));
      var statusText = STATUS_LABEL[node.status] || node.status;
      if (node.blocked) { statusText += " \u00b7 blocked"; }
      group.appendChild(textEl({ "class": "node__status", x: 10, y: 50 }, statusText));

      var hiddenLinks = hiddenLinkCount[node.id] || 0;
      if (hiddenLinks) {
        var markText = "+" + hiddenLinks;
        var markWidth = Math.max(22, markText.length * 7 + 8);
        var markX = NODE_W - markWidth - 6;
        var markY = 6;
        group.appendChild(el("rect", {
          "class": "node__elided",
          x: markX,
          y: markY,
          width: markWidth,
          height: 13,
          rx: 3,
          "aria-hidden": "true"
        }));
        group.appendChild(textEl(
          { "class": "node__elided-text", x: markX + 4, y: markY + 10 },
          markText
        ));
      }

      if (node.claimed_by) {
        var fullHolder = String(node.claimed_by);
        var age = relativeAge(node.claimed_at);
        var staleText = node.claim_stale ? " \u00b7 stale" : "";
        var badgeText = claimBadgeLabel(fullHolder, age, staleText);
        var badgeWidth = Math.min(
          BADGE_LIMIT, Math.max(52, badgeText.length * BADGE_CHAR_W + BADGE_PAD)
        );
        var badgeX = NODE_W - badgeWidth - 4;
        var badge = el("g", { "class": "node__claim-badge-group" });
        badge.appendChild(el("title", {}));
        badge.lastChild.textContent =
          "@" + fullHolder + (node.claimed_at ? " since " + node.claimed_at : "");
        badge.appendChild(el("rect", {
          "class": "node__claim-badge",
          "data-stale": node.claim_stale ? "true" : "false",
          x: badgeX,
          y: NODE_H - 16,
          width: badgeWidth,
          height: 13,
          rx: 3,
          "aria-hidden": "true"
        }));
        badge.appendChild(textEl(
          { "class": "node__claim-text", x: badgeX + 4, y: NODE_H - 6 },
          badgeText
        ));
        group.appendChild(badge);
      }

      if (node.eligible) {
        group.appendChild(el("path", {
          "class": "node__corner-mark",
          d: "M 0 " + NODE_H + " L 12 " + NODE_H + " L 0 " + (NODE_H - 12) + " Z",
          "aria-hidden": "true"
        }));
      }

      if (node.reviewable) {
        /* Design: "second parallel line 3 px inside the top edge". The card is
         * a full rectangle, so the top edge is y=0 and the marker sits at y=3.
         * The ends stop short of the rounded corners and use round caps, so the
         * marker reads as a deliberate overlay rather than a stray stroke. */
        group.appendChild(el("line", {
          "class": "node__double-edge",
          x1: 16,
          y1: 3,
          x2: NODE_W - 16,
          y2: 3,
          "aria-hidden": "true"
        }));
      }

      nodeEls[node.id] = group;
      fragment.appendChild(group);
    });
    return fragment;
  }

  function buildTooltip() {
    var group = el("g", {
      "class": "tooltip",
      id: "graph-tooltip",
      role: "tooltip",
      "aria-hidden": "true",
      display: "none"
    });
    tooltipBg = el("rect", { "class": "tooltip__bg", x: 0, y: 0, width: 1, height: 1, rx: 6 });
    group.appendChild(tooltipBg);
    tooltipRows = [];
    for (var i = 0; i < 6; i += 1) {
      var rowClass = i === 0 ? "tooltip__row tooltip__title" : "tooltip__row";
      var row = textEl({ "class": rowClass, x: 10, y: 18 + i * 18 }, "");
      tooltipRows.push(row);
      group.appendChild(row);
    }
    return group;
  }

  function render(graph, initial) {
    var previousPlan = plan;
    var previousView = { scale: view.scale, tx: view.tx, ty: view.ty };
    var anchor = null;
    if (previousPlan) {
      if (selectedId && previousPlan.positions[selectedId]) {
        var anchorPlace = previousPlan.positions[selectedId];
        anchor = {
          gx: anchorPlace.x + NODE_W / 2,
          gy: anchorPlace.y + NODE_H / 2,
          sx: (anchorPlace.x + NODE_W / 2) * previousView.scale + previousView.tx,
          sy: (anchorPlace.y + NODE_H / 2) * previousView.scale + previousView.ty
        };
      } else {
        anchor = {
          gx: (W / 2 - previousView.tx) / previousView.scale,
          gy: (H / 2 - previousView.ty) / previousView.scale,
          sx: W / 2,
          sy: H / 2
        };
      }
    }

    plan = layout(graph);
    canvas.classList.remove("is-hidden");
    canvasRect = canvas.getBoundingClientRect();
    W = Math.max(1, Math.round(canvasRect.width));
    H = Math.max(1, Math.round(canvasRect.height));

    buildAdjacency(fullData || graph, fullData ? fullData.edges : graph.edges);
    useVisibleAdjacency(graph);

    svg = el("svg", {
      "class": "graph-canvas__svg",
      id: "graph-svg",
      viewBox: "0 0 " + W + " " + H,
      role: "group",
      "aria-roledescription": "dependency graph",
      "aria-label": "Dependency graph: " + graph.nodes.length + " tasks, " +
        graph.edges.length + " links. Press question mark for shortcuts.",
      "aria-activedescendant": "",
      tabindex: "0"
    });

    viewport = el("g", {
      "class": "viewport",
      transform: "translate(0,0) scale(1)",
      "data-lod": "full",
      "data-claim-highlight": claimHighlight ? "on" : "off",
      "data-focus-claimed": focusClaimed ? "true" : "false"
    });

    viewport.appendChild(buildEdgesFragment(graph));
    viewport.appendChild(buildNodesFragment(graph));
    svg.appendChild(viewport);

    tooltip = buildTooltip();
    svg.appendChild(tooltip);

    var previous = canvas.querySelector("svg:not(.minimap__svg)");
    if (previous) { previous.remove(); }
    canvas.appendChild(svg);
    if (fallback) {
      if ("open" in fallback) { fallback.open = false; }
      else { fallback.hidden = true; }
    }

    buildMinimap(graph);
    attachEvents();
    syncClaimLayer();
    if (activeId && !nodeEls[activeId]) { activeId = null; }

    if (initial || !previousPlan) {
      fitAll(true);
      return;
    }
    view.scale = clamp(previousView.scale, MIN_SCALE, MAX_SCALE);
    if (anchor) {
      view.tx = anchor.sx - anchor.gx * view.scale;
      view.ty = anchor.sy - anchor.gy * view.scale;
    }
    clampPan();
    requestFlush();
  }

  function buildMinimap(graph) {
    if (!minimap || !minimapSvg) { return; }
    minimapSvg.textContent = "";
    minimapSvg.setAttribute("viewBox", "0 0 " + plan.width + " " + plan.height);
    minimapSvg.setAttribute("preserveAspectRatio", "xMidYMid meet");

    var edgeLayer = document.createDocumentFragment();
    graph.edges.forEach(function (edge) {
      var source = plan.positions[edge.source];
      var target = plan.positions[edge.target];
      if (!source || !target) { return; }
      edgeLayer.appendChild(el("line", {
        "class": "minimap__edge",
        x1: source.x + NODE_W,
        y1: source.y + NODE_H / 2,
        x2: target.x,
        y2: target.y + NODE_H / 2
      }));
    });
    minimapSvg.appendChild(edgeLayer);

    var drawn = 0;
    graph.nodes.forEach(function (node) {
      var place = plan.positions[node.id];
      if (!place) { return; }
      drawn += 1;
      var group = el("g", {
        "class": "minimap__node node",
        "data-status": node.status,
        transform: "translate(" + place.x + "," + place.y + ")"
      });
      if (node.claimed_by) {
        group.appendChild(el("rect", {
          "class": "minimap__ring",
          x: -4,
          y: -4,
          width: NODE_W + 8,
          height: NODE_H + 8,
          rx: 8,
          "aria-hidden": "true"
        }));
      }
      group.appendChild(shapeElement());
      minimapSvg.appendChild(group);
    });

    minimapViewRect = el("rect", {
      "class": "minimap__viewport",
      x: 0,
      y: 0,
      width: 1,
      height: 1,
      "aria-hidden": "true"
    });
    minimapSvg.appendChild(minimapViewRect);

    var hidden = hiddenCount();
    var label = "Overview: " + graph.nodes.length + " visible nodes, " +
      graph.edges.length + " visible edges" +
      (hidden ? ", " + hidden + " hidden" : "");
    minimapSvg.setAttribute("aria-label", label);

    minimapRect = minimapSvg.getBoundingClientRect();
    attachMinimapEvents();
    applyMinimapPreference();
  }

  /* ----------------------------------------------------------------------- */
  /* camera: dirty flag and one write per frame                              */
  /* ----------------------------------------------------------------------- */

  function requestFlush() {
    dirty = true;
    if (rafId) { return; }
    rafId = window.requestAnimationFrame(function () {
      rafId = 0;
      if (!dirty) { return; }
      dirty = false;
      flushView();
    });
  }

  function flushView() {
    if (!viewport) { return; }
    viewport.setAttribute(
      "transform",
      "translate(" + view.tx + "," + view.ty + ") scale(" + view.scale + ")"
    );
    var lod = "shape";
    if (view.scale >= LOD_FULL) { lod = "full"; }
    else if (view.scale >= LOD_COMPACT) { lod = "compact"; }
    viewport.setAttribute("data-lod", lod);
    updateMinimapViewport();
  }

  function clampPan() {
    if (!plan) { return; }
    var width = plan.width * view.scale;
    var height = plan.height * view.scale;
    var minTx = MIN_VISIBLE - width;
    var maxTx = W - MIN_VISIBLE;
    var minTy = MIN_VISIBLE - height;
    var maxTy = H - MIN_VISIBLE;
    if (minTx > maxTx) { view.tx = (W - width) / 2; }
    else { view.tx = clamp(view.tx, minTx, maxTx); }
    if (minTy > maxTy) { view.ty = (H - height) / 2; }
    else { view.ty = clamp(view.ty, minTy, maxTy); }
  }

  function zoomAt(cx, cy, factor) {
    var next = clamp(view.scale * factor, MIN_SCALE, MAX_SCALE);
    var ratio = next / view.scale;
    view.tx = cx - (cx - view.tx) * ratio;
    view.ty = cy - (cy - view.ty) * ratio;
    view.scale = next;
    userOwnsCamera = true;
    clampPan();
    requestFlush();
  }

  function panBy(dx, dy) {
    view.tx += dx;
    view.ty += dy;
    userOwnsCamera = true;
    clampPan();
    requestFlush();
  }

  function fitTarget() {
    var pad = 12;
    var scale = Math.min(
      (W - pad * 2) / Math.max(1, plan.width),
      (H - pad * 2) / Math.max(1, plan.height)
    );
    scale = clamp(scale, MIN_SCALE, MAX_SCALE);
    scale = Math.min(scale, 1);
    return {
      scale: scale,
      tx: (W - plan.width * scale) / 2,
      ty: (H - plan.height * scale) / 2
    };
  }

  function fitAll(instant) {
    if (!plan) { return; }
    var target = fitTarget();
    userOwnsCamera = false;
    if (instant) {
      view.scale = target.scale;
      view.tx = target.tx;
      view.ty = target.ty;
      requestFlush();
      return;
    }
    animateTo(target, DUR_SLOW);
  }

  function resetZoom() {
    if (!plan) { return; }
    var cx = plan.width / 2;
    var cy = plan.height / 2;
    if (selectedId && plan.positions[selectedId]) {
      cx = plan.positions[selectedId].x + NODE_W / 2;
      cy = plan.positions[selectedId].y + NODE_H / 2;
    }
    animateTo({
      scale: 1,
      tx: W / 2 - cx,
      ty: H / 2 - cy
    }, DUR_LAYOUT);
  }

  function animateTo(target, duration) {
    var ms = reducedDuration(duration);
    if (ms <= 0) {
      view.scale = target.scale;
      view.tx = target.tx;
      view.ty = target.ty;
      userOwnsCamera = true;
      requestFlush();
      return;
    }
    var start = { scale: view.scale, tx: view.tx, ty: view.ty };
    var began = 0;
    function step(now) {
      if (!began) { began = now; }
      var t = clamp((now - began) / ms, 0, 1);
      var eased = t * t * (3 - 2 * t);
      view.scale = start.scale + (target.scale - start.scale) * eased;
      view.tx = start.tx + (target.tx - start.tx) * eased;
      view.ty = start.ty + (target.ty - start.ty) * eased;
      userOwnsCamera = true;
      requestFlush();
      if (t < 1) { window.requestAnimationFrame(step); }
    }
    window.requestAnimationFrame(step);
  }

  /* ----------------------------------------------------------------------- */
  /* pointer navigation                                                      */
  /* ----------------------------------------------------------------------- */

  function pointInCanvas(event) {
    /* Re-read the rect on every event: the canvas scrolls and reflows (a long
     * page above it, the toolbar wrapping, the sidebar collapsing), so a cached
     * rect goes stale and drag/zoom coordinates inherit the scroll offset. */
    canvasRect = canvas.getBoundingClientRect();
    return {
      x: event.clientX - canvasRect.left,
      y: event.clientY - canvasRect.top
    };
  }

  /* The tooltip is a child of the SVG and its translate() is in the SVG's user
   * units, while the pointer arrives in CSS pixels. Those two only agree when
   * the SVG's on-screen box has the same size as its viewBox. When the box is
   * taller than the viewBox — a tall canvas column, a wrapped toolbar — the
   * drawing is letterboxed and scaled, so CSS pixels put the tooltip below and
   * right of the cursor by the letterbox plus the scale error. The screen CTM
   * already folds in scroll, scale and letterbox, so its inverse is the whole
   * conversion. Camera maths (pan, pinch, zoom) keeps using CSS pixels. */
  function pointInSvg(event) {
    var ctm = svg && svg.getScreenCTM ? svg.getScreenCTM() : null;
    if (!ctm) { return pointInCanvas(event); }
    var point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    var mapped = point.matrixTransform(ctm.inverse());
    return { x: mapped.x, y: mapped.y };
  }

  function endDrag(event) {
    if (!drag) { return; }
    drag = null;
    panning = false;
    canvas.removeAttribute("data-panning");
    if (event && event.pointerId !== undefined) {
      try { svg.releasePointerCapture(event.pointerId); } catch (e) { /* already released */ }
    }
  }

  function attachEvents() {
    svg.addEventListener("pointerdown", function (event) {
      if (event.pointerType === "mouse" && event.button !== 0) { return; }
      hideTooltip();
      clearHoverTimer();
      window.clearTimeout(selectTimer);
      var point = pointInCanvas(event);
      pointers[event.pointerId] = point;
      if (Object.keys(pointers).length === 2) {
        startPinch();
        event.preventDefault();
        return;
      }
      if (pinch) {
        /* A third pointer is tracked but ignored by the pinch maths. */
        event.preventDefault();
        return;
      }
      var downNode = closest(event.target, ".node");
      lastDownNodeId = downNode ? downNode.getAttribute("data-id") : null;
      drag = {
        id: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        startTime: Date.now(),
        lastX: event.clientX,
        lastY: event.clientY,
        moved: false,
        pointerType: event.pointerType || "mouse"
      };
      panning = true;
      canvas.setAttribute("data-panning", "true");
      try { svg.setPointerCapture(event.pointerId); } catch (e) { /* capture unsupported */ }
      lastPointer = pointInSvg(event);
    });

    svg.addEventListener("pointermove", function (event) {
      if (pointers[event.pointerId]) {
        pointers[event.pointerId] = pointInCanvas(event);
      }
      if (pinch) {
        if (pinch.ids.indexOf(event.pointerId) >= 0) {
          var first = pointers[pinch.ids[0]];
          var second = pointers[pinch.ids[1]];
          if (first && second) {
            var distance = Math.max(1, Math.hypot(first.x - second.x, first.y - second.y));
            var ratio = distance / pinch.dist;
            var nextScale = clamp(pinch.scale * ratio, MIN_SCALE, MAX_SCALE);
            var mid = { x: (first.x + second.x) / 2, y: (first.y + second.y) / 2 };
            view.scale = nextScale;
            view.tx = mid.x - (pinch.mid.x - pinch.tx) * ratio;
            view.ty = mid.y - (pinch.mid.y - pinch.ty) * ratio;
            userOwnsCamera = true;
            clampPan();
            requestFlush();
          }
        }
        event.preventDefault();
        return;
      }
      if (drag && drag.id === event.pointerId) {
        var dx = event.clientX - drag.lastX;
        var dy = event.clientY - drag.lastY;
        var totalX = event.clientX - drag.startX;
        var totalY = event.clientY - drag.startY;
        if (Math.hypot(totalX, totalY) > CLICK_RADIUS) {
          drag.moved = true;
        }
        drag.lastX = event.clientX;
        drag.lastY = event.clientY;
        lastPointer = pointInSvg(event);
        view.tx += dx;
        view.ty += dy;
        userOwnsCamera = true;
        clampPan();
        requestFlush();
        event.preventDefault();
        return;
      }
      lastPointer = pointInSvg(event);
      if (tooltipVisible) { positionTooltip(lastPointer.x, lastPointer.y); }
    });

    svg.addEventListener("pointerup", function (event) {
      var point = pointInCanvas(event);
      delete pointers[event.pointerId];
      if (pinch && pinch.ids.indexOf(event.pointerId) >= 0) {
        pinch = null;
        drag = null;
        panning = false;
        canvas.removeAttribute("data-panning");
        return;
      }
      if (!drag || drag.id !== event.pointerId) { return; }
      var moved = drag.moved;
      var duration = Date.now() - drag.startTime;
      var pointerType = drag.pointerType;
      var downId = lastDownNodeId;
      endDrag(event);
      if (moved) {
        suppressClick = true;
        window.setTimeout(function () { suppressClick = false; }, 0);
        return;
      }
      if (pointerType !== "mouse" && duration < 250) {
        handleTap(point, downId);
      }
    });

    svg.addEventListener("pointercancel", function (event) {
      delete pointers[event.pointerId];
      if (pinch && pinch.ids.indexOf(event.pointerId) >= 0) { pinch = null; }
      endDrag(event);
      suppressClick = false;
    });

    svg.addEventListener("lostpointercapture", function () {
      endDrag(null);
    });

    svg.addEventListener("click", function (event) {
      if (suppressClick) {
        suppressClick = false;
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      if (tapHandled) {
        tapHandled = false;
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      var id = lastDownNodeId;
      lastDownNodeId = null;
      if (!id) {
        lastClickNodeId = null;
        window.clearTimeout(selectTimer);
        clearSelection();
        return;
      }
      lastClickNodeId = id;
      window.clearTimeout(selectTimer);
      selectTimer = window.setTimeout(function () { selectNode(id); }, SELECT_DELAY);
      event.preventDefault();
    });

    svg.addEventListener("dblclick", function (event) {
      event.preventDefault();
      window.clearTimeout(selectTimer);
      var point = pointInCanvas(event);
      var id = lastClickNodeId;
      lastClickNodeId = null;
      if (id && plan.positions[id]) {
        var place = plan.positions[id];
        animateTo({
          scale: clamp(1.4, MIN_SCALE, MAX_SCALE),
          tx: W / 2 - (place.x + NODE_W / 2) * 1.4,
          ty: H / 2 - (place.y + NODE_H / 2) * 1.4
        }, DUR_LAYOUT);
        return;
      }
      zoomAt(point.x, point.y, 1.6);
    });

    svg.addEventListener("wheel", function (event) {
      event.preventDefault();
      var point = pointInCanvas(event);
      var dx = event.deltaX;
      var dy = event.deltaY;
      var mode = event.deltaMode;
      if (mode === 1) { dx *= 16; dy *= 16; }
      else if (mode === 2) { dx *= H; dy *= H; }

      if (event.ctrlKey) {
        zoomAt(point.x, point.y, Math.exp(-dy * 0.010));
      } else if (event.shiftKey) {
        panBy(-dx, 0);
      } else if (mode !== 0 || Math.abs(dy) >= 40) {
        zoomAt(point.x, point.y, Math.exp(-dy * 0.0015));
      } else {
        panBy(-dx, -dy);
      }
    }, { passive: false });

    svg.addEventListener("pointerover", function (event) {
      var edge = closest(event.target, ".edge");
      if (edge) {
        edge.setAttribute("data-highlight", "true");
        var source = edge.getAttribute("data-source");
        var target = edge.getAttribute("data-target");
        if (nodeEls[source]) { nodeEls[source].setAttribute("data-hover", "true"); }
        if (nodeEls[target]) { nodeEls[target].setAttribute("data-hover", "true"); }
        return;
      }
      var node = closest(event.target, ".node");
      if (!node || node === hoveredNode) { return; }
      hoveredNode = node;
      clearHoverTimer();
      hideTooltip();
      hoverTimer = window.setTimeout(function () {
        if (hoveredNode === node && !panning) { showTooltip(node); }
      }, 100);
    });

    svg.addEventListener("pointerout", function (event) {
      var edge = closest(event.target, ".edge");
      if (edge) {
        var to = event.relatedTarget;
        if (to && edge.contains(to)) { return; }
        edge.removeAttribute("data-highlight");
        var source = edge.getAttribute("data-source");
        var target = edge.getAttribute("data-target");
        if (nodeEls[source]) { nodeEls[source].removeAttribute("data-hover"); }
        if (nodeEls[target]) { nodeEls[target].removeAttribute("data-hover"); }
        return;
      }
      var node = closest(event.target, ".node");
      if (!node) { return; }
      var inside = event.relatedTarget;
      if (inside && node.contains(inside)) { return; }
      if (hoveredNode === node) {
        hoveredNode = null;
        clearHoverTimer();
        hideTooltip();
      }
    });

    svg.addEventListener("focus", function () {
      var count = visibleData ? visibleData.nodes.length : 0;
      announce("Graph focused. " + count +
        " tasks. Arrow keys pan or move along links.");
    });

    if (!globalEventsBound) {
      globalEventsBound = true;
      document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
          hideTooltip();
          clearSelection();
          clearActive();
        }
      });
      window.addEventListener("resize", onResize);
    }
  }

  function clearHoverTimer() {
    if (hoverTimer) {
      window.clearTimeout(hoverTimer);
      hoverTimer = 0;
    }
  }

  function onResize() {
    if (!svg) { return; }
    canvasRect = canvas.getBoundingClientRect();
    W = Math.max(1, Math.round(canvasRect.width));
    H = Math.max(1, Math.round(canvasRect.height));
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    if (minimapSvg) { minimapRect = minimapSvg.getBoundingClientRect(); }
    minimapInverse = null;
    if (!userOwnsCamera) { fitAll(true); }
    else { clampPan(); requestFlush(); }
  }

  /* ----------------------------------------------------------------------- */
  /* selection and path highlighting                                         */
  /* ----------------------------------------------------------------------- */

  function pathClosure(startId) {
    var seen = {};
    var queue = [startId];
    seen[startId] = true;
    var count = 0;
    while (queue.length) {
      var current = queue.shift();
      count += 1;
      if (count > PATH_LIMIT) { return null; }
      var next = (prereqMap[current] || []).concat(depMap[current] || []);
      for (var i = 0; i < next.length; i += 1) {
        var id = next[i];
        if (!seen[id]) {
          seen[id] = true;
          queue.push(id);
        }
      }
    }
    return seen;
  }

  function clearPathMarks() {
    var key;
    for (key in nodeEls) {
      if (Object.prototype.hasOwnProperty.call(nodeEls, key)) {
        nodeEls[key].removeAttribute("data-path");
        nodeEls[key].removeAttribute("data-dimmed");
      }
    }
    for (key in edgeEls) {
      if (Object.prototype.hasOwnProperty.call(edgeEls, key)) {
        edgeEls[key].removeAttribute("data-path");
        edgeEls[key].removeAttribute("data-dimmed");
      }
    }
  }

  function applyPathMarks(closure) {
    clearPathMarks();
    if (!closure) { return; }
    var key;
    for (key in nodeEls) {
      if (!Object.prototype.hasOwnProperty.call(nodeEls, key)) { continue; }
      if (closure[key]) { nodeEls[key].setAttribute("data-path", "true"); }
      else { nodeEls[key].setAttribute("data-dimmed", "true"); }
    }
    edges.forEach(function (edge) {
      var group = edgeEls[edge.source + "->" + edge.target];
      if (!group) { return; }
      if (closure[edge.source] && closure[edge.target]) {
        group.setAttribute("data-path", "true");
      } else {
        group.setAttribute("data-dimmed", "true");
      }
    });
  }

  function selectNode(id) {
    var node = nodesById[id];
    if (!node) { return; }
    lastClickNodeId = null;
    selectedId = id;
    hideTooltip();
    var key;
    for (key in nodeEls) {
      if (!Object.prototype.hasOwnProperty.call(nodeEls, key)) { continue; }
      if (key === id) { nodeEls[key].setAttribute("data-selected", "true"); }
      else { nodeEls[key].removeAttribute("data-selected"); }
    }
    var closure = pathClosure(id);
    var tooLarge = closure === null;
    if (tooLarge) { clearPathMarks(); }
    else { applyPathMarks(closure); }
    renderSelectionPanel(node, tooLarge);
    /* Selecting a node also makes it the active descendant so arrow keys move
     * from it instead of panning; the canvas takes focus so the ARIA
     * active-descendant relationship applies. */
    setActive(id);
    if (svg && typeof svg.focus === "function") {
      try { svg.focus({ preventScroll: true }); } catch (e) { svg.focus(); }
    }
  }

  function clearSelection() {
    lastClickNodeId = null;
    selectedId = null;
    hideTooltip();
    clearPathMarks();
    var key;
    for (key in nodeEls) {
      if (Object.prototype.hasOwnProperty.call(nodeEls, key)) {
        nodeEls[key].removeAttribute("data-selected");
      }
    }
    if (selectionPanel) {
      selectionPanel.hidden = true;
      while (selectionPanel.firstChild) { selectionPanel.removeChild(selectionPanel.firstChild); }
    }
    if (graphStatus) { setText(graphStatus, ""); }
  }

  function panelRow(list, term, value) {
    var dt = document.createElement("dt");
    dt.textContent = term;
    var dd = document.createElement("dd");
    dd.textContent = value;
    list.appendChild(dt);
    list.appendChild(dd);
    return dd;
  }

  function renderSelectionPanel(node, tooLarge) {
    if (!selectionPanel) { return; }
    while (selectionPanel.firstChild) { selectionPanel.removeChild(selectionPanel.firstChild); }

    var heading = document.createElement("h2");
    heading.className = "graph-selection__heading";
    heading.textContent = "Selected task";
    selectionPanel.appendChild(heading);

    var list = document.createElement("dl");
    list.className = "graph-selection__facts";
    panelRow(list, "ID", node.id);
    panelRow(list, "title", node.title);
    var statusCell = panelRow(list, "status", "");
    var chip = document.createElement("span");
    chip.className = "status-chip";
    chip.setAttribute("data-status", node.status);
    var glyph = document.createElement("span");
    glyph.className = "status-chip__glyph";
    glyph.setAttribute("aria-hidden", "true");
    glyph.textContent = STATUS_GLYPH[node.status] || "\u2022";
    chip.appendChild(glyph);
    chip.appendChild(document.createTextNode(
      " " + (STATUS_LABEL[node.status] || node.status)
    ));
    statusCell.textContent = "";
    statusCell.appendChild(chip);

    var priorityCell = panelRow(list, "priority", "");
    var priority = document.createElement("span");
    priority.className = "priority";
    priority.setAttribute("data-priority", node.priority || "normal");
    var pips = document.createElement("span");
    pips.className = "priority__pips";
    pips.setAttribute("aria-hidden", "true");
    pips.textContent = PRIORITY_PIPS[node.priority] || "";
    priority.appendChild(pips);
    priority.appendChild(document.createTextNode(
      " " + (node.priority || "normal")
    ));
    priorityCell.textContent = "";
    priorityCell.appendChild(priority);

    panelRow(list, "assignee", node.assignee || "\u2014");
    panelRow(list, "claim", node.claimed_by ? "claimed by " + node.claimed_by : "\u2014");
    var blocked = blockedByMap[node.id] || [];
    panelRow(list, "blocked reason", blocked.length ? blocked.join(", ") + " not done" : "\u2014");
    selectionPanel.appendChild(list);

    var direction = document.createElement("p");
    direction.className = "graph-selection__direction";
    direction.textContent = "Prerequisites flow left to right. A \u2192 B means B depends on A.";
    selectionPanel.appendChild(direction);

    var link = document.createElement("a");
    link.className = "graph-selection__link";
    link.href = "/task/" + encodeURIComponent(node.id);
    link.textContent = "Open task page";
    selectionPanel.appendChild(link);

    selectionPanel.hidden = false;
    if (graphStatus) {
      setText(graphStatus, tooLarge ? "Path too large to highlight" : "");
    }
  }

  /* ----------------------------------------------------------------------- */
  /* tooltip                                                                 */
  /* ----------------------------------------------------------------------- */

  function tooltipLines(node) {
    var label = STATUS_LABEL[node.status] || node.status;
    var lines = [];
    lines.push(truncate(node.id + " \u2014 " + node.title, 44));
    lines.push(STATUS_GLYPH[node.status] + " " + label + " \u00b7 " +
      (node.priority || "normal"));
    var blocked = blockedByMap[node.id] || [];
    if (node.assignee) { lines.push("assignee: " + truncate(node.assignee, 40)); }
    if (node.claimed_by) {
      var age = relativeAge(node.claimed_at);
      lines.push("claimed by " + truncate(node.claimed_by, 24) +
        (age ? " (" + age + " ago)" : ""));
      if (node.claimed_at) { lines.push("claimed at " + node.claimed_at); }
    }
    var hiddenLinks = hiddenLinkCount[node.id] || 0;
    if (hiddenLinks) {
      lines.push(hiddenLinks + " hidden link" + (hiddenLinks === 1 ? "" : "s"));
    }
    if (blocked.length) { lines.push("blocked by " + truncate(blocked.join(", "), 40)); }
    return lines.slice(0, 6);
  }

  function positionTooltip(px, py) {
    if (!tooltip || !tooltipVisible || !tooltipNode) { return; }
    var lines = tooltipLines(tooltipNode);
    var longest = 0;
    lines.forEach(function (line) { longest = Math.max(longest, line.length); });
    var width = Math.min(352, Math.max(120, longest * 6.4 + 20));
    var height = 12 + lines.length * 18;
    /* px/py and the tooltip's transform are in SVG user units. The edge checks
     * must use the same space, so read the viewBox size — W/H are CSS pixels
     * and differ from user units whenever the canvas box is taller than the
     * viewBox (the same letterbox that moved the tooltip). */
    var box = svg && svg.viewBox && svg.viewBox.baseVal;
    var boxW = box && box.width ? box.width : W;
    var boxH = box && box.height ? box.height : H;
    var x = px + TOOLTIP_OFFSET;
    var y = py + TOOLTIP_OFFSET;
    if (x + width > boxW - TOOLTIP_MARGIN) { x = px - width - TOOLTIP_OFFSET; }
    if (x < TOOLTIP_MARGIN) { x = TOOLTIP_MARGIN; }
    if (y + height > boxH - TOOLTIP_MARGIN) { y = py - height - TOOLTIP_OFFSET; }
    if (y < TOOLTIP_MARGIN) { y = TOOLTIP_MARGIN; }
    tooltip.setAttribute("transform", "translate(" + x + "," + y + ")");
  }

  function showTooltip(nodeEl) {
    if (!tooltip || !nodeEl) { return; }
    var id = nodeEl.getAttribute("data-id");
    var node = nodesById[id];
    if (!node) { return; }
    var lines = tooltipLines(node);
    var longest = 0;
    lines.forEach(function (line) { longest = Math.max(longest, line.length); });
    var width = Math.min(352, Math.max(120, longest * 6.4 + 20));
    var height = 12 + lines.length * 18;
    tooltipBg.setAttribute("width", String(width));
    tooltipBg.setAttribute("height", String(height));
    for (var i = 0; i < tooltipRows.length; i += 1) {
      if (i < lines.length) {
        tooltipRows[i].setAttribute("display", "inline");
        setText(tooltipRows[i], lines[i]);
      } else {
        tooltipRows[i].setAttribute("display", "none");
      }
    }
    tooltip.setAttribute("display", "inline");
    tooltipVisible = true;
    tooltipNode = node;
    positionTooltip(lastPointer.x, lastPointer.y);
  }

  function hideTooltip() {
    if (!tooltip || !tooltipVisible) { return; }
    tooltip.setAttribute("display", "none");
    tooltipVisible = false;
    tooltipNode = null;
  }

  /* ----------------------------------------------------------------------- */
  /* minimap                                                                 */
  /* ----------------------------------------------------------------------- */

  function updateMinimapViewport() {
    if (!minimapViewRect || !plan) { return; }
    var rect = minimapViewRect;
    var left = clamp(-view.tx / view.scale, 0, plan.width);
    var top = clamp(-view.ty / view.scale, 0, plan.height);
    var right = clamp((W - view.tx) / view.scale, 0, plan.width);
    var bottom = clamp((H - view.ty) / view.scale, 0, plan.height);
    var width = Math.max(1, right - left);
    var height = Math.max(1, bottom - top);
    rect.setAttribute("x", String(left));
    rect.setAttribute("y", String(top));
    rect.setAttribute("width", String(width));
    rect.setAttribute("height", String(height));
  }

  function minimapPoint(event) {
    if (minimapInverse) {
      var point = minimapSvg.createSVGPoint();
      point.x = event.clientX;
      point.y = event.clientY;
      var user = point.matrixTransform(minimapInverse);
      return {
        x: clamp(user.x, 0, plan.width),
        y: clamp(user.y, 0, plan.height)
      };
    }
    if (!minimapRect || !minimapRect.width) {
      minimapRect = minimapSvg.getBoundingClientRect();
    }
    var x = event.clientX - minimapRect.left;
    var y = event.clientY - minimapRect.top;
    var gx = clamp((x / Math.max(1, minimapRect.width)) * plan.width, 0, plan.width);
    var gy = clamp((y / Math.max(1, minimapRect.height)) * plan.height, 0, plan.height);
    return { x: gx, y: gy };
  }

  function centreOnGraphPoint(gx, gy) {
    view.tx = W / 2 - gx * view.scale;
    view.ty = H / 2 - gy * view.scale;
    userOwnsCamera = true;
    clampPan();
    requestFlush();
  }

  function endMinimapDrag(event) {
    if (!minimapDrag) { return; }
    minimapDrag = null;
    if (event && event.pointerId !== undefined) {
      try { minimapSvg.releasePointerCapture(event.pointerId); } catch (e) { /* released */ }
    }
  }

  function attachMinimapEvents() {
    if (!minimapSvg) { return; }

    minimapSvg.addEventListener("pointerdown", function (event) {
      if (event.pointerType === "mouse" && event.button !== 0) { return; }
      event.stopPropagation();
      var ctm = minimapSvg.getScreenCTM();
      minimapInverse = ctm ? ctm.inverse() : null;
      var point = minimapPoint(event);
      minimapDrag = { id: event.pointerId, moved: false, point: point };
      try { minimapSvg.setPointerCapture(event.pointerId); } catch (e) { /* capture */ }
      event.preventDefault();
    });

    minimapSvg.addEventListener("pointermove", function (event) {
      if (!minimapDrag || minimapDrag.id !== event.pointerId) { return; }
      var point = minimapPoint(event);
      minimapDrag.moved = true;
      minimapDrag.point = point;
      centreOnGraphPoint(point.x, point.y);
      event.preventDefault();
    });

    minimapSvg.addEventListener("pointerup", function (event) {
      if (!minimapDrag || minimapDrag.id !== event.pointerId) { return; }
      var point = minimapPoint(event);
      var moved = minimapDrag.moved;
      endMinimapDrag(event);
      if (!moved) {
        animateTo({
          scale: view.scale,
          tx: W / 2 - point.x * view.scale,
          ty: H / 2 - point.y * view.scale
        }, DUR_LAYOUT);
      }
    });

    minimapSvg.addEventListener("pointercancel", function (event) {
      endMinimapDrag(event);
    });

    if (minimapToggle) {
      minimapToggle.addEventListener("click", function () {
        setMinimapCollapsed(!minimapCollapsed, true);
      });
    }
  }

  function readMinimapPreference() {
    try {
      var value = window.localStorage.getItem(MINIMAP_KEY);
      if (value === "1" || value === "0") { return value === "1"; }
    } catch (e) { /* storage unavailable */ }
    return null;
  }

  function writeMinimapPreference(collapsed) {
    try {
      window.localStorage.setItem(MINIMAP_KEY, collapsed ? "1" : "0");
    } catch (e) { /* session only */ }
  }

  function setMinimapCollapsed(collapsed, remember) {
    minimapCollapsed = collapsed;
    if (!minimap) { return; }
    minimap.setAttribute("data-collapsed", collapsed ? "true" : "false");
    if (minimapToggle) {
      minimapToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
      setText(minimapToggle, collapsed ? "Expand minimap" : "Collapse minimap");
    }
    if (remember) { writeMinimapPreference(collapsed); }
  }

  function applyMinimapPreference() {
    if (!minimap) { return; }
    minimapPref = readMinimapPreference();
    var small = !!(window.matchMedia &&
      window.matchMedia("(max-width: " + MINIMAP_BREAKPOINT + "px)").matches);
    var collapsed = minimapPref === null ? small : minimapPref;
    setMinimapCollapsed(collapsed, false);
  }

  /* ----------------------------------------------------------------------- */
  /* toolbar                                                                 */
  /* ----------------------------------------------------------------------- */

  function hiddenCount() {
    return Object.keys(hiddenIds).length;
  }

  function readHideClosedPreference() {
    var fromUrl = null;
    try {
      var params = new URLSearchParams(window.location.search);
      if (params.has("hide_closed")) {
        fromUrl = params.get("hide_closed") === "1";
      }
    } catch (e) { fromUrl = null; }
    if (fromUrl !== null) { return fromUrl; }
    try {
      var stored = window.localStorage.getItem(HIDE_CLOSED_KEY);
      if (stored === "1" || stored === "0") { return stored === "1"; }
    } catch (e) { /* storage unavailable */ }
    return false;
  }

  function writeHidePreference(value) {
    try {
      window.localStorage.setItem(HIDE_CLOSED_KEY, value ? "1" : "0");
    } catch (e) { /* session only */ }
  }

  function updateHiddenCount(count) {
    var element = document.getElementById("graph-hidden-count");
    if (!element) { return; }
    setText(element, count + " hidden");
    element.setAttribute(
      "title",
      count ? count + " nodes hidden and their edges" : "No nodes hidden"
    );
  }

  function updateUrl() {
    try {
      var url = new URL(window.location.href);
      url.searchParams.set("hide_closed", hideClosed ? "1" : "0");
      window.history.replaceState(null, "", url.toString());
    } catch (e) { /* history unavailable */ }
  }

  function syncClaimLayer() {
    if (viewport) {
      viewport.setAttribute("data-claim-highlight", claimHighlight ? "on" : "off");
      viewport.setAttribute("data-focus-claimed", focusClaimed ? "true" : "false");
    }
    var claimedButton = document.querySelector(
      '[data-graph-action="toggle-claimed"]'
    );
    if (claimedButton) {
      claimedButton.setAttribute("aria-pressed", claimHighlight ? "true" : "false");
    }
    var focusButton = document.querySelector(
      '[data-graph-action="focus-claimed"]'
    );
    if (focusButton) {
      focusButton.disabled = !claimHighlight;
      focusButton.setAttribute("aria-pressed", focusClaimed ? "true" : "false");
    }
  }

  function toggleClaimHighlight() {
    claimHighlight = !claimHighlight;
    if (!claimHighlight) { focusClaimed = false; }
    syncClaimLayer();
    announce(claimHighlight
      ? "Claimed highlight on."
      : "Claimed highlight off; the claim stays in the tooltip and outline.");
  }

  function toggleFocusClaimed() {
    if (!claimHighlight) { return; }
    focusClaimed = !focusClaimed;
    syncClaimLayer();
    var total = visibleData ? visibleData.nodes.length : 0;
    var claimed = visibleData
      ? visibleData.nodes.filter(function (node) { return node.claimed_by; }).length
      : 0;
    announce(focusClaimed
      ? claimed + " of " + total + " tasks are claimed."
      : "Focus claimed off.");
  }

  function setHideClosed(value, remember) {
    hideClosed = !!value;
    var button = document.querySelector(
      '[data-graph-action="toggle-hide-closed"]'
    );
    if (button) { button.setAttribute("aria-pressed", hideClosed ? "true" : "false"); }
    if (remember) {
      writeHidePreference(hideClosed);
      updateUrl();
    }
    var selectionCleared = rebuildVisible();
    if (!selectionCleared) {
      announce(hideClosed
        ? hiddenCount() + " done or cancelled tasks hidden."
        : "Showing done and cancelled tasks again.");
    }
  }

  function toggleHideClosed() {
    setHideClosed(!hideClosed, true);
  }

  function rebuildVisible() {
    if (!fullData) { return false; }
    var closed = {};
    if (hideClosed) {
      fullData.nodes.forEach(function (node) {
        if (isTerminal(node.status)) { closed[node.id] = true; }
      });
    }
    hiddenIds = closed;
    var nodes = fullData.nodes.filter(function (node) {
      return !closed[node.id];
    });
    var visible = {};
    nodes.forEach(function (node) { visible[node.id] = true; });
    var edges = fullData.edges.filter(function (edge) {
      return visible[edge.source] && visible[edge.target];
    });
    hiddenLinkCount = {};
    fullData.edges.forEach(function (edge) {
      if (closed[edge.source] && visible[edge.target]) {
        hiddenLinkCount[edge.target] = (hiddenLinkCount[edge.target] || 0) + 1;
      }
      if (closed[edge.target] && visible[edge.source]) {
        hiddenLinkCount[edge.source] = (hiddenLinkCount[edge.source] || 0) + 1;
      }
    });
    updateHiddenCount(Object.keys(closed).length);
    var selectionCleared = false;
    if (selectedId && closed[selectedId]) {
      var hiddenSelection = selectedId;
      clearSelection();
      announce("Selection cleared: " + hiddenSelection + " is hidden.");
      selectionCleared = true;
    }
    if (activeId && closed[activeId]) { clearActive(); }
    visibleData = { nodes: nodes, edges: edges };
    render(visibleData, false);
    return selectionCleared;
  }

  function zoomAtActive(factor) {
    var cx = W / 2;
    var cy = H / 2;
    if (activeId && plan && plan.positions[activeId]) {
      var place = plan.positions[activeId];
      cx = (place.x + NODE_W / 2) * view.scale + view.tx;
      cy = (place.y + NODE_H / 2) * view.scale + view.ty;
    }
    zoomAt(cx, cy, factor);
  }

  function ensureVisible(id) {
    if (!plan || !plan.positions[id]) { return; }
    var place = plan.positions[id];
    var cx = (place.x + NODE_W / 2) * view.scale + view.tx;
    var cy = (place.y + NODE_H / 2) * view.scale + view.ty;
    var pad = 40;
    if (cx < pad || cx > W - pad || cy < pad || cy > H - pad) {
      view.tx = W / 2 - (place.x + NODE_W / 2) * view.scale;
      view.ty = H / 2 - (place.y + NODE_H / 2) * view.scale;
      userOwnsCamera = true;
      clampPan();
      requestFlush();
    }
  }

  function setActive(id, recenter) {
    if (activeId && nodeEls[activeId]) {
      nodeEls[activeId].setAttribute("data-active", "false");
    }
    activeId = id;
    if (!svg) { return; }
    if (!id) {
      svg.setAttribute("aria-activedescendant", "");
      return;
    }
    var element = nodeEls[id];
    if (!element) {
      activeId = null;
      svg.setAttribute("aria-activedescendant", "");
      return;
    }
    element.setAttribute("data-active", "true");
    svg.setAttribute("aria-activedescendant", "node-" + id);
    /* Keyboard movement between active nodes must not move the camera; the
     * gate treats a viewport transform change while a node is active as a
     * defect. Only click-driven selection recentres. */
    if (recenter !== false) { ensureVisible(id); }
  }

  function clearActive() {
    setActive(null);
  }

  function moveActive(direction) {
    if (!plan) { return; }
    if (!activeId) {
      var step = 64;
      if (direction === "ArrowLeft") { panBy(step, 0); }
      else if (direction === "ArrowRight") { panBy(-step, 0); }
      else if (direction === "ArrowUp") { panBy(0, step); }
      else if (direction === "ArrowDown") { panBy(0, -step); }
      return;
    }
    var target = null;
    if (direction === "ArrowLeft") {
      target = (prereqMap[activeId] || [])[0] || null;
    } else if (direction === "ArrowRight") {
      target = (depMap[activeId] || [])[0] || null;
    } else {
      var place = plan.positions[activeId];
      if (!place) { return; }
      var best = null;
      var bestDistance = Infinity;
      Object.keys(plan.positions).forEach(function (key) {
        if (key === activeId) { return; }
        var candidate = plan.positions[key];
        if (candidate.depth !== place.depth) { return; }
        var dy = candidate.y - place.y;
        if (direction === "ArrowUp" && dy >= 0) { return; }
        if (direction === "ArrowDown" && dy <= 0) { return; }
        var distance = Math.abs(dy);
        if (distance < bestDistance) { bestDistance = distance; best = key; }
      });
      target = best;
    }
    if (target) { setActive(target, false); }
  }

  function startPinch() {
    var ids = Object.keys(pointers);
    if (ids.length < 2) { return; }
    var first = pointers[ids[0]];
    var second = pointers[ids[1]];
    if (!first || !second) { return; }
    pinch = {
      ids: [ids[0], ids[1]],
      dist: Math.max(1, Math.hypot(first.x - second.x, first.y - second.y)),
      mid: { x: (first.x + second.x) / 2, y: (first.y + second.y) / 2 },
      scale: view.scale,
      tx: view.tx,
      ty: view.ty
    };
    drag = null;
    panning = false;
    canvas.removeAttribute("data-panning");
  }

  function handleTap(point, downId) {
    var now = Date.now();
    var doubleTap = now - lastTap.time < 300 &&
      Math.hypot(point.x - lastTap.x, point.y - lastTap.y) < 30;
    lastTap = { time: now, x: point.x, y: point.y };
    tapHandled = true;
    window.setTimeout(function () { tapHandled = false; }, 350);
    if (doubleTap) {
      if (downId && plan.positions[downId]) {
        var place = plan.positions[downId];
        animateTo({
          scale: clamp(1.4, MIN_SCALE, MAX_SCALE),
          tx: W / 2 - (place.x + NODE_W / 2) * 1.4,
          ty: H / 2 - (place.y + NODE_H / 2) * 1.4
        }, DUR_LAYOUT);
      } else {
        zoomAt(point.x, point.y, 1.6);
      }
      return;
    }
    if (downId) { selectNode(downId); }
    else { clearSelection(); clearActive(); }
  }

  function onGraphCommand(event) {
    var detail = event.detail || {};
    var action = detail.action;
    if (action === "toggle-claimed") { toggleClaimHighlight(); }
    else if (action === "focus-claimed") { toggleFocusClaimed(); }
    else if (action === "toggle-hide-closed") { toggleHideClosed(); }
    else if (action === "zoom-in") { zoomAtActive(1.25); }
    else if (action === "zoom-out") { zoomAtActive(0.8); }
    else if (action === "fit-all") { fitAll(false); }
    else if (action === "zoom-reset") { resetZoom(); }
    else if (action === "escape") {
      hideTooltip();
      clearSelection();
      clearActive();
    } else if (action === "activate") {
      if (activeId) {
        window.location.assign("/task/" + encodeURIComponent(activeId));
      }
    } else if (action === "move") {
      moveActive(detail.detail);
    }
  }

  function attachControls() {
    var buttons = document.querySelectorAll("[data-graph-action]");
    Array.prototype.forEach.call(buttons, function (button) {
      button.addEventListener("click", function () {
        var action = button.getAttribute("data-graph-action");
        if (action === "zoom-in") { zoomAtActive(1.25); }
        else if (action === "zoom-out") { zoomAtActive(0.8); }
        else if (action === "fit-all") { fitAll(false); }
        else if (action === "zoom-reset") { resetZoom(); }
        else if (action === "toggle-claimed") { toggleClaimHighlight(); }
        else if (action === "focus-claimed") { toggleFocusClaimed(); }
        else if (action === "toggle-hide-closed") { toggleHideClosed(); }
      });
    });
    syncClaimLayer();
  }

  /* ----------------------------------------------------------------------- */
  /* entry                                                                   */
  /* ----------------------------------------------------------------------- */

  function show(graph) {
    if (!graph.nodes.length) { return; }
    var staleAttr = root.getAttribute("data-stale-claims") || "";
    var staleIds = staleAttr.split(/\s+/).filter(Boolean);
    graph.nodes.forEach(function (node) {
      if (staleIds.indexOf(node.id) !== -1) { node.claim_stale = true; }
    });
    fullData = graph;
    hideClosed = readHideClosedPreference();
    updateHiddenCount(0);
    rebuildVisible();
    attachControls();
    syncClaimLayer();
    root.addEventListener("tack:graph-command", onGraphCommand);
  }

  function buildWhenIdle(graph) {
    if (graph.nodes.length > IDLE_NODE_LIMIT &&
        typeof window.requestIdleCallback === "function") {
      window.requestIdleCallback(function () { show(graph); }, { timeout: 200 });
      return;
    }
    show(graph);
  }

  var url = "/api/graph" + (taskId ? "?task=" + encodeURIComponent(taskId) : "");
  fetch(url, { headers: { Accept: "application/json" } })
    .then(function (response) {
      if (!response.ok) { throw new Error("HTTP " + response.status); }
      return response.json();
    })
    .then(buildWhenIdle)
    .catch(function () {
      /* Leave the server-rendered dependency outline in place. */
    });
})();
