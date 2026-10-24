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
 * Nodes are distinguished by shape as well as colour: backlog is a dashed
 * rounded rectangle, ready a circle, in progress a diamond, review a hexagon,
 * done a square and cancelled a dotted square. A claimed node carries a double
 * ring and a holder badge; blocked and eligible nodes carry additional,
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

  var NODE_W = 176;
  var NODE_H = 56;
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

  function buildAdjacency(graph) {
    prereqMap = {};
    depMap = {};
    blockedByMap = {};
    var statusById = {};
    graph.nodes.forEach(function (node) {
      prereqMap[node.id] = [];
      depMap[node.id] = [];
      blockedByMap[node.id] = [];
      nodesById[node.id] = node;
      statusById[node.id] = node.status;
    });
    graph.edges.forEach(function (edge) {
      if (!prereqMap[edge.target] || !depMap[edge.source]) { return; }
      prereqMap[edge.target].push(edge.source);
      depMap[edge.source].push(edge.target);
      if (!isTerminal(statusById[edge.source])) {
        blockedByMap[edge.target].push(edge.source);
      }
    });
  }

  function nodeLabel(node) {
    var label = STATUS_LABEL[node.status] || node.status;
    var parts = [node.id + ", " + node.title + ", " + label];
    if (node.priority) { parts.push("priority " + node.priority); }
    if (node.assignee) { parts.push("assignee " + node.assignee); }
    if (node.claimed_by) { parts.push("claimed by " + node.claimed_by); }
    var blocked = blockedByMap[node.id] || [];
    if (blocked.length) { parts.push("blocked by " + blocked.join(", ")); }
    return parts.join(", ");
  }

  function nodeTitleText(node) {
    var label = STATUS_LABEL[node.status] || node.status;
    var text = node.id + " \u2014 " + node.title + " (" + label;
    if (node.claimed_by) { text += ", claimed by " + node.claimed_by; }
    return text + ")";
  }

  /* ----------------------------------------------------------------------- */
  /* shapes                                                                  */
  /* ----------------------------------------------------------------------- */

  function shapeElement(status) {
    var attrs = { "class": "node__shape" };
    if (status === "ready") {
      attrs.cx = NODE_W / 2;
      attrs.cy = NODE_H / 2;
      attrs.r = Math.min(NODE_W, NODE_H) / 2;
      return el("circle", attrs);
    }
    if (status === "in_progress") {
      attrs.points = [[NODE_W / 2, 0], [NODE_W, NODE_H / 2],
        [NODE_W / 2, NODE_H], [0, NODE_H / 2]]
        .map(function (point) { return point.join(","); }).join(" ");
      return el("polygon", attrs);
    }
    if (status === "review") {
      attrs.points = [[NODE_W * 0.22, 0], [NODE_W * 0.78, 0], [NODE_W, NODE_H / 2],
        [NODE_W * 0.78, NODE_H], [NODE_W * 0.22, NODE_H], [0, NODE_H / 2]]
        .map(function (point) { return point.join(","); }).join(" ");
      return el("polygon", attrs);
    }
    attrs.x = 0;
    attrs.y = 0;
    attrs.width = NODE_W;
    attrs.height = NODE_H;
    attrs.rx = status === "done" || status === "cancelled" ? 2 : 6;
    return el("rect", attrs);
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
      var group = el("g", {
        "class": "edge",
        "data-source": edge.source,
        "data-target": edge.target
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
        role: "button",
        tabindex: "-1",
        "data-id": node.id,
        "data-status": node.status,
        "data-claimed": node.claimed_by ? "true" : "false",
        "data-blocked": node.blocked ? "true" : "false",
        "data-eligible": node.eligible ? "true" : "false",
        "data-reviewable": node.reviewable ? "true" : "false",
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
        group.appendChild(el("rect", {
          "class": "node__claim-ring",
          x: -4,
          y: -4,
          width: NODE_W + 8,
          height: NODE_H + 8,
          rx: 8,
          "aria-hidden": "true"
        }));
      }

      group.appendChild(shapeElement(node.status));

      group.appendChild(textEl({ "class": "node__id", x: 10, y: 20 }, node.id));
      group.appendChild(textEl(
        { "class": "node__title", x: 10, y: 36 },
        truncate(node.title, 26)
      ));
      var statusText = STATUS_LABEL[node.status] || node.status;
      if (node.blocked) { statusText += " \u00b7 blocked"; }
      group.appendChild(textEl({ "class": "node__status", x: 10, y: 50 }, statusText));

      if (node.claimed_by) {
        var holder = truncate("@" + node.claimed_by, 13);
        var badgeWidth = Math.max(52, holder.length * 6.4 + 12);
        var badgeX = NODE_W - badgeWidth - 4;
        group.appendChild(el("rect", {
          "class": "node__claim-badge",
          x: badgeX,
          y: NODE_H - 16,
          width: badgeWidth,
          height: 13,
          rx: 3,
          "aria-hidden": "true"
        }));
        group.appendChild(textEl(
          { "class": "node__claim-text", x: badgeX + 5, y: NODE_H - 6 },
          holder
        ));
      }

      if (node.eligible) {
        group.appendChild(el("path", {
          "class": "node__corner-mark",
          d: "M " + (NODE_W - 12) + " 0 L " + NODE_W + " 0 L " + NODE_W + " 12 Z",
          "aria-hidden": "true"
        }));
      }

      if (node.reviewable) {
        group.appendChild(el("path", {
          "class": "node__double-edge",
          d: "M 6 4 L " + (NODE_W - 6) + " 4",
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
    for (var i = 0; i < 5; i += 1) {
      var rowClass = i === 0 ? "tooltip__row tooltip__title" : "tooltip__row";
      var row = textEl({ "class": rowClass, x: 10, y: 18 + i * 18 }, "");
      tooltipRows.push(row);
      group.appendChild(row);
    }
    return group;
  }

  function render(graph) {
    plan = layout(graph);
    canvas.classList.remove("is-hidden");
    canvasRect = canvas.getBoundingClientRect();
    W = Math.max(1, Math.round(canvasRect.width));
    H = Math.max(1, Math.round(canvasRect.height));

    svg = el("svg", {
      viewBox: "0 0 " + W + " " + H,
      role: "application",
      tabindex: "0",
      "aria-label": "Dependency graph"
    });

    viewport = el("g", {
      "class": "viewport",
      transform: "translate(0,0) scale(1)",
      "data-lod": "full"
    });

    var edgesFragment = buildEdgesFragment(graph);
    var nodesFragment = buildNodesFragment(graph);
    viewport.appendChild(edgesFragment);
    viewport.appendChild(nodesFragment);
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
    fitAll(true);
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
      group.appendChild(shapeElement(node.status));
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

    var hidden = graph.nodes.length - drawn;
    var label = "Overview of the whole graph: " + graph.nodes.length +
      " nodes, " + graph.edges.length + " edges, " + hidden + " hidden";
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
    if (!canvasRect) { canvasRect = canvas.getBoundingClientRect(); }
    return {
      x: event.clientX - canvasRect.left,
      y: event.clientY - canvasRect.top
    };
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
      var downNode = closest(event.target, ".node");
      lastDownNodeId = downNode ? downNode.getAttribute("data-id") : null;
      drag = {
        id: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        lastX: event.clientX,
        lastY: event.clientY,
        moved: false
      };
      panning = true;
      canvas.setAttribute("data-panning", "true");
      try { svg.setPointerCapture(event.pointerId); } catch (e) { /* capture unsupported */ }
      lastPointer = point;
    });

    svg.addEventListener("pointermove", function (event) {
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
        lastPointer = pointInCanvas(event);
        view.tx += dx;
        view.ty += dy;
        userOwnsCamera = true;
        clampPan();
        requestFlush();
        event.preventDefault();
        return;
      }
      lastPointer = pointInCanvas(event);
      if (tooltipVisible) { positionTooltip(lastPointer.x, lastPointer.y); }
    });

    svg.addEventListener("pointerup", function (event) {
      if (!drag || drag.id !== event.pointerId) { return; }
      var moved = drag.moved;
      endDrag(event);
      if (moved) {
        suppressClick = true;
        window.setTimeout(function () { suppressClick = false; }, 0);
      }
    });

    svg.addEventListener("pointercancel", function (event) {
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

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        hideTooltip();
        clearSelection();
      }
    });

    window.addEventListener("resize", onResize);
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
    if (node.claimed_by) { lines.push("claimed by " + truncate(node.claimed_by, 40)); }
    if (blocked.length) { lines.push("blocked by " + truncate(blocked.join(", "), 40)); }
    return lines.slice(0, 5);
  }

  function positionTooltip(px, py) {
    if (!tooltip || !tooltipVisible || !tooltipNode) { return; }
    var lines = tooltipLines(tooltipNode);
    var longest = 0;
    lines.forEach(function (line) { longest = Math.max(longest, line.length); });
    var width = Math.min(352, Math.max(120, longest * 6.4 + 20));
    var height = 12 + lines.length * 18;
    var x = px + 14;
    var y = py + 12;
    if (x + width > W - TOOLTIP_MARGIN) { x = px - width - 14; }
    if (x < TOOLTIP_MARGIN) { x = TOOLTIP_MARGIN; }
    if (y + height > H - TOOLTIP_MARGIN) { y = py - height - 12; }
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

  function attachControls() {
    var buttons = document.querySelectorAll("[data-graph-action]");
    Array.prototype.forEach.call(buttons, function (button) {
      button.addEventListener("click", function () {
        var action = button.getAttribute("data-graph-action");
        if (action === "zoom-in") { zoomAt(W / 2, H / 2, 1.25); }
        else if (action === "zoom-out") { zoomAt(W / 2, H / 2, 0.8); }
        else if (action === "fit-all") { fitAll(false); }
        else if (action === "zoom-reset") { resetZoom(); }
      });
    });
  }

  /* ----------------------------------------------------------------------- */
  /* entry                                                                   */
  /* ----------------------------------------------------------------------- */

  function show(graph) {
    if (!graph.nodes.length) { return; }
    data = graph;
    buildAdjacency(graph);
    render(graph);
    attachControls();
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
