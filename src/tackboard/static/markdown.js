/* Task Markdown renderer: build DOM nodes from a small, safe subset.
 *
 * Hand-written, no library, no build step. Task text is only ever written with
 * textContent; no HTML string injection is used. Links accept absolute http(s)
 * URLs only; every other destination is rendered as plain text so the reader
 * still sees where it pointed. The server keeps a <pre class="description"> or
 * <pre class="note-text"> fallback; this script replaces that node only after a
 * successful build and leaves it in place, with data-markdown="fallback", when
 * parsing throws.
 */
(function () {
  "use strict";

  var MAX_URL_DISPLAY = 80;

  function makeEl(tag, text) {
    var node = document.createElement(tag);
    if (text !== undefined && text !== null) {
      node.textContent = text;
    }
    return node;
  }

  function isHttpUrl(url) {
    return /^https?:\/\//i.test(url);
  }

  function truncateUrl(url, max) {
    var limit = max || MAX_URL_DISPLAY;
    if (url.length <= limit) {
      return url;
    }
    return url.slice(0, limit - 1) + "\u2026";
  }

  function stripCredentials(url) {
    try {
      var parsed = new URL(url);
      parsed.username = "";
      parsed.password = "";
      return parsed.toString();
    } catch (error) {
      return null;
    }
  }

  function appendRejected(parent, text, url) {
    parent.appendChild(
      document.createTextNode(text + " (" + truncateUrl(url) + ")")
    );
  }

  function appendLink(parent, text, url) {
    if (!isHttpUrl(url)) {
      appendRejected(parent, text, url);
      return;
    }
    var clean = stripCredentials(url);
    if (clean === null) {
      appendRejected(parent, text, url);
      return;
    }
    var link = makeEl("a", text);
    link.setAttribute("href", clean);
    link.setAttribute("rel", "noopener noreferrer");
    link.setAttribute("target", "_blank");
    link.setAttribute("title", clean);
    var hint = makeEl("span", " (opens in a new tab)");
    hint.setAttribute("class", "sr-only");
    link.appendChild(hint);
    parent.appendChild(link);
  }

  function parseInline(text, parent) {
    var index = 0;
    var buffer = "";

    function flush() {
      if (buffer) {
        parent.appendChild(document.createTextNode(buffer));
        buffer = "";
      }
    }

    while (index < text.length) {
      var char = text.charAt(index);
      if (char === "`") {
        var codeEnd = text.indexOf("`", index + 1);
        if (codeEnd !== -1) {
          flush();
          parent.appendChild(makeEl("code", text.slice(index + 1, codeEnd)));
          index = codeEnd + 1;
          continue;
        }
      } else if (char === "[") {
        var closeBracket = text.indexOf("]", index + 1);
        if (closeBracket !== -1 && text.charAt(closeBracket + 1) === "(") {
          var closeParen = text.indexOf(")", closeBracket + 2);
          if (closeParen !== -1) {
            var linkText = text.slice(index + 1, closeBracket);
            var url = text.slice(closeBracket + 2, closeParen);
            flush();
            appendLink(parent, linkText, url);
            index = closeParen + 1;
            continue;
          }
        }
      } else if (char === "*" && text.charAt(index + 1) === "*") {
        var boldEnd = text.indexOf("**", index + 2);
        if (boldEnd !== -1) {
          flush();
          var strong = makeEl("strong");
          parseInline(text.slice(index + 2, boldEnd), strong);
          parent.appendChild(strong);
          index = boldEnd + 2;
          continue;
        }
      } else if (char === "*") {
        var italicEnd = text.indexOf("*", index + 1);
        if (italicEnd !== -1) {
          flush();
          var emphasis = makeEl("em");
          parseInline(text.slice(index + 1, italicEnd), emphasis);
          parent.appendChild(emphasis);
          index = italicEnd + 1;
          continue;
        }
      }
      buffer += char;
      index += 1;
    }
    flush();
  }

  function parseMarkdown(text) {
    var fragment = document.createDocumentFragment();
    var lines = text.replace(/\r\n?/g, "\n").split("\n");
    var index = 0;
    var paragraph = [];
    var listStack = [];
    var inFence = false;
    var fenceLines = [];

    function flushParagraph() {
      if (paragraph.length) {
        var para = makeEl("p");
        parseInline(paragraph.join(" "), para);
        fragment.appendChild(para);
        paragraph = [];
      }
    }

    function flushList() {
      listStack = [];
    }

    while (index < lines.length) {
      var line = lines[index];
      if (inFence) {
        if (/^```\s*$/.test(line)) {
          var pre = makeEl("pre");
          pre.appendChild(makeEl("code", fenceLines.join("\n")));
          fragment.appendChild(pre);
          inFence = false;
          fenceLines = [];
        } else {
          fenceLines.push(line);
        }
        index += 1;
        continue;
      }

      var fence = /^```\s*(.*)$/.exec(line);
      if (fence) {
        flushParagraph();
        flushList();
        inFence = true;
        index += 1;
        continue;
      }

      if (line.trim() === "") {
        flushParagraph();
        flushList();
        index += 1;
        continue;
      }

      var heading = /^(#{1,4})\s+(.*)$/.exec(line);
      if (heading) {
        flushParagraph();
        flushList();
        var level = heading[1].length + 1;
        var headingNode = makeEl("h" + level);
        parseInline(heading[2], headingNode);
        fragment.appendChild(headingNode);
        index += 1;
        continue;
      }

      var item = /^(\s*)([-*+]|(\d+)\.)\s+(.*)$/.exec(line);
      if (item) {
        flushParagraph();
        var indent = item[1].length;
        var depth = Math.min(Math.floor(indent / 2), 2);
        var ordered = Boolean(item[3]);
        var content = item[4];
        while (
          listStack.length &&
          listStack[listStack.length - 1].depth > depth
        ) {
          listStack.pop();
        }
        var top = listStack[listStack.length - 1];
        if (!top || top.depth < depth) {
          var parentList = top ? top.node : fragment;
          var newList = makeEl(ordered ? "ol" : "ul");
          var lastItem = top ? top.node.lastElementChild : null;
          if (lastItem) {
            lastItem.appendChild(newList);
          } else {
            parentList.appendChild(newList);
          }
          listStack.push({ node: newList, depth: depth });
          top = listStack[listStack.length - 1];
        } else if (
          top.depth === depth &&
          top.node.tagName.toLowerCase() !== (ordered ? "ol" : "ul")
        ) {
          var previous = listStack.pop();
          var siblingList = makeEl(ordered ? "ol" : "ul");
          previous.node.parentNode.appendChild(siblingList);
          listStack.push({ node: siblingList, depth: depth });
          top = listStack[listStack.length - 1];
        }
        var listItem = makeEl("li");
        parseInline(content, listItem);
        top.node.appendChild(listItem);
        index += 1;
        continue;
      }

      flushList();
      paragraph.push(line);
      index += 1;
    }

    if (inFence) {
      throw new Error("Unclosed fenced code block");
    }
    flushParagraph();
    return fragment;
  }

  function renderElement(element) {
    var text = element.textContent || "";
    var fragment;
    try {
      fragment = parseMarkdown(text);
    } catch (error) {
      element.setAttribute("data-markdown", "fallback");
      return;
    }
    var container = makeEl("div");
    container.setAttribute("class", "prose");
    container.appendChild(fragment);
    if (element.parentNode) {
      element.parentNode.replaceChild(container, element);
    }
  }

  function boot() {
    var targets = document.querySelectorAll(".description, .note-text");
    for (var index = 0; index < targets.length; index += 1) {
      renderElement(targets[index]);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
