/* Dependency graph renderer: hand-written SVG, no third-party code.
 *
 * The server always renders the dependency outline underneath this canvas, so
 * the page is usable with JavaScript disabled. This script replaces that
 * outline with an interactive graph once the data has been fetched and drawn;
 * if anything fails, the outline stays exactly where it is.
 *
 * Nodes are distinguished by shape as well as colour: done/backlog/blocked are
 * squares (solid, dashed, and thick-bordered respectively), eligible is a
 * circle, in progress a diamond, review a hexagon, cancelled a dotted square.
 */
(function () {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";
  var root = document.getElementById("graph");
  if (!root) { return; }

  var canvas = document.getElementById("graph-canvas");
  var fallback = document.getElementById("graph-fallback");
  var taskId = root.getAttribute("data-task") || "";

  var NODE_W = 176;
  var NODE_H = 56;
  var GAP_X = 76;
  var GAP_Y = 26;
  var MIN_SCALE = 0.15;
  var MAX_SCALE = 4;

  var SHAPES = {
    done: "rect",
    cancelled: "rect",
    backlog: "rect",
    blocked: "rect",
    eligible: "circle",
    "in-progress": "polygon",
    review: "polygon"
  };

  var LABELS = {
    done: "done",
    cancelled: "cancelled",
    backlog: "backlog",
    blocked: "blocked",
    eligible: "eligible",
    "in-progress": "in progress",
    review: "review"
  };

  function kindOf(node) {
    if (node.status === "done") { return "done"; }
    if (node.status === "cancelled") { return "cancelled"; }
    if (node.blocked) { return "blocked"; }
    if (node.status === "in_progress") { return "in-progress"; }
    if (node.status === "review") { return "review"; }
    if (node.eligible) { return "eligible"; }
    return "backlog";
  }

  function el(name, attrs) {
    var node = document.createElementNS(NS, name);
    var keys = Object.keys(attrs || {});
    for (var i = 0; i < keys.length; i += 1) {
      node.setAttribute(keys[i], attrs[keys[i]]);
    }
    return node;
  }

  function label(x, y, cls, value) {
    var node = el("text", { x: x, y: y, "class": cls });
    node.textContent = value;
    return node;
  }

  function truncate(value, limit) {
    var text = String(value);
    return text.length > limit ? text.slice(0, limit - 1) + "\u2026" : text;
  }

  /* Longest-path depths, relaxed until stable (the graph is a DAG). */
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

  function layout(data) {
    var depth = computeDepths(data.nodes, data.edges);
    var perColumn = {};
    var positions = {};
    var maxDepth = 0;
    var maxRows = 0;
    data.nodes.forEach(function (node) {
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

  function nodeShape(kind) {
    var shape = SHAPES[kind] || "rect";
    if (shape === "circle") {
      return el("circle", { cx: NODE_W / 2, cy: NODE_H / 2, r: Math.min(NODE_W, NODE_H) / 2 });
    }
    if (shape === "polygon") {
      var points = kind === "review"
        ? [[NODE_W * 0.22, 0], [NODE_W * 0.78, 0], [NODE_W, NODE_H / 2],
           [NODE_W * 0.78, NODE_H], [NODE_W * 0.22, NODE_H], [0, NODE_H / 2]]
        : [[NODE_W / 2, 0], [NODE_W, NODE_H / 2], [NODE_W / 2, NODE_H], [0, NODE_H / 2]];
      return el("polygon", {
        points: points.map(function (point) { return point.join(","); }).join(" ")
      });
    }
    return el("rect", { x: 0, y: 0, width: NODE_W, height: NODE_H, rx: 6 });
  }

  function buildGraph(data) {
    var plan = layout(data);
    var svg = el("svg", {
      viewBox: "0 0 " + plan.width + " " + plan.height,
      role: "img",
      "aria-label": "Dependency graph"
    });
    var viewport = el("g", { transform: "translate(0,0) scale(1)" });
    svg.appendChild(viewport);

    data.edges.forEach(function (edge) {
      var source = plan.positions[edge.source];
      var target = plan.positions[edge.target];
      if (!source || !target) { return; }
      var x1 = source.x + NODE_W;
      var y1 = source.y + NODE_H / 2;
      var x2 = target.x;
      var y2 = target.y + NODE_H / 2;
      var mid = (x1 + x2) / 2;
      viewport.appendChild(el("path", {
        "class": "graph-edge",
        d: "M " + x1 + " " + y1 + " C " + mid + " " + y1 + ", " + mid + " " + y2 +
           ", " + x2 + " " + y2
      }));
    });

    data.nodes.forEach(function (node) {
      var place = plan.positions[node.id];
      if (!place) { return; }
      var kind = kindOf(node);
      var group = el("g", {
        "class": "graph-node shape-" + kind,
        transform: "translate(" + place.x + "," + place.y + ")"
      });
      var link = el("a", { href: "/task/" + encodeURIComponent(node.id) });
      link.appendChild(nodeShape(kind));
      link.appendChild(label(10, 20, "graph-node-id", node.id));
      link.appendChild(label(10, 36, "graph-node-label", truncate(node.title, 26)));
      link.appendChild(label(10, 50, "graph-node-status", LABELS[kind]));
      group.appendChild(link);
      viewport.appendChild(group);
    });

    return { svg: svg, viewport: viewport, plan: plan };
  }

  function attachControls(svg, viewport, plan) {
    var scale = 1;
    var tx = 0;
    var ty = 0;

    function apply() {
      viewport.setAttribute(
        "transform",
        "translate(" + tx + "," + ty + ") scale(" + scale + ")"
      );
    }

    function zoomBy(factor) {
      var next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale * factor));
      var ratio = next / scale;
      tx = tx * ratio;
      ty = ty * ratio;
      scale = next;
      apply();
    }

    var dragging = false;
    var lastX = 0;
    var lastY = 0;

    svg.addEventListener("wheel", function (event) {
      event.preventDefault();
      zoomBy(event.deltaY < 0 ? 1.12 : 1 / 1.12);
    }, { passive: false });

    svg.addEventListener("mousedown", function (event) {
      dragging = true;
      lastX = event.clientX;
      lastY = event.clientY;
      svg.classList.add("is-panning");
    });
    window.addEventListener("mousemove", function (event) {
      if (!dragging) { return; }
      tx += event.clientX - lastX;
      ty += event.clientY - lastY;
      lastX = event.clientX;
      lastY = event.clientY;
      apply();
    });
    window.addEventListener("mouseup", function () {
      dragging = false;
      svg.classList.remove("is-panning");
    });

    var actions = {
      "zoom-in": function () { zoomBy(1.25); },
      "zoom-out": function () { zoomBy(0.8); },
      fit: function () {
        scale = 1;
        tx = 0;
        ty = 0;
        svg.setAttribute("viewBox", "0 0 " + plan.width + " " + plan.height);
        apply();
      }
    };

    var buttons = root.querySelectorAll("[data-graph-action]");
    Array.prototype.forEach.call(buttons, function (button) {
      button.addEventListener("click", function () {
        var action = actions[button.getAttribute("data-graph-action")];
        if (action) { action(); }
      });
    });
  }

  function show(data) {
    if (!data.nodes.length) { return; }
    var built = buildGraph(data);
    canvas.textContent = "";
    canvas.appendChild(built.svg);
    attachControls(built.svg, built.viewport, built.plan);
    canvas.classList.remove("is-hidden");
    if (fallback) { fallback.hidden = true; }
  }

  var url = "/api/graph" + (taskId ? "?task=" + encodeURIComponent(taskId) : "");
  fetch(url, { headers: { Accept: "application/json" } })
    .then(function (response) {
      if (!response.ok) { throw new Error("HTTP " + response.status); }
      return response.json();
    })
    .then(show)
    .catch(function () {
      /* Leave the server-rendered dependency outline in place. */
    });
})();
