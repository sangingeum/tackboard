# tackboard web view — design system and UX specification

Status: design leg deliverable (board card T-000041). Implementation-ready.
Scope: the read-only web view served by `tack serve`. Production code is not
changed by this document; four implementation legs consume it.

Companion artifact: `prototype.html` in this directory — a static, review-only
gallery that demonstrates the tokens, the status language, the theme cycle,
the claimed treatment, the hide-closed toggle, the tooltip and the shortcut
overlay. It is never served by `tack` and is never referenced from a
production template.

Reading order for an implementer: §1 tokens → §2 theme → §3 status language →
the component you are building in §4 → §5 layout → §6 interaction → §7 the two
graph features → §8 keyboard/ARIA → §9 responsive → §10 mapping and risks.

---

## 0. Design stance

The web view is an **operator instrument**, not a product page. The person
looking at it is checking whether work is moving and what is stuck. Three
consequences drive every decision below.

1. **Density over decoration.** A 14 px base with 4 px rhythm fits more tasks
   per screen than a 16 px marketing layout. Chrome is one row; the data is the
   page. This follows Nielsen's "aesthetic and minimalist design" heuristic
   (Nielsen Norman Group, *10 Usability Heuristics for User Interface Design*,
   heuristic 8) — every element that is not task data must earn its pixels.
2. **State must survive without colour.** Colour is the fastest channel and the
   least reliable one: roughly 1 in 12 men has a colour-vision deficiency, and
   the view is also read on bad projectors and in forced-colors mode. Every
   status therefore carries a shape, a glyph, and a text label; colour is the
   fourth cue, never the first. This is WCAG 2.2 SC 1.4.1 (*Use of Color*) and
   SC 1.4.11 (*Non-text Contrast*).
3. **The view must never lie.** A read-only view that silently shows stale or
   filtered data is worse than a slow one. So: visible last-updated time, an
   explicit pause control, an explicit hidden count, and derived flags
   (`blocked`, `eligible`) computed from the **full** graph even when the view
   is filtered. This is Nielsen heuristic 1 (*Visibility of system status*).

Vocabulary follows the house terminology rule: concept names are natural
language. No coordinate labels (`B2`, `G3`), no "layer" numbers. The one
exception is the document's own numbered sections, which are ordinary document
structure, not concept names.

Sources relied on, and where each is applied:

| Source | Applied in |
| --- | --- |
| W3C, *Web Content Accessibility Guidelines (WCAG) 2.2* — SC 1.4.1, 1.4.3, 1.4.11, 2.1.1, 2.4.7, 2.5.8, 2.3.3, 4.1.3 | §1 contrast, §3 non-colour cues, §8 keyboard/focus/live region, §9 touch targets |
| W3C WAI, *ARIA Authoring Practices Guide (APG)* — composite widget and dialog patterns | §4 component roles, §8 roving `aria-activedescendant`, help dialog |
| MDN Web Docs — CSS custom properties, `prefers-color-scheme`, `prefers-reduced-motion`, `localStorage`, Pointer Events, `requestAnimationFrame` | §1–§2, §6 |
| web.dev (Google) — *Interaction to Next Paint*, *Content-visibility*, *Accessible focus indicators*, *Reduced motion* | §6 diff/FLIP budget, §8 focus ring, motion policy |
| Nielsen Norman Group — *10 Usability Heuristics* (1 status, 2 real-world match, 3 user control, 6 recognition over recall, 8 minimalism) and *Response times: the 3 important limits* | §0 stance, §6 polling/feedback, §7 toggle reversibility |
| Material Design 3 — *State layers* and *Motion easing & duration* tokens; Apple HIG — *Touch targets* (44×44 pt) | §1 motion tokens, §4 hover/pressed states, §9 touch |

Where a rule below cites "APG" it means the ARIA Authoring Practices Guide
pattern of that name; where it cites "SC n.n.n" it means that WCAG 2.2 success
criterion.

---

## 1. Design tokens

All tokens are CSS custom properties declared on `:root` (light values) and
overridden for dark. Naming is by role: raw palette tokens carry a colour name,
semantic tokens carry a job. Components may use semantic tokens only; raw
palette tokens exist so the theme block has one place to swap values.

### 1.1 Raw palette — light

```css
:root {
  /* neutrals */
  --tack-white:      #ffffff;
  --tack-gray-25:    #f7f8fa;  /* page */
  --tack-gray-50:    #f1f3f6;
  --tack-gray-100:   #eef1f4;  /* rails, column tracks */
  --tack-gray-200:   #dde2e8;  /* decorative borders only */
  --tack-gray-300:   #c8cfd8;
  --tack-gray-500:   #767f8c;  /* interactive/UI borders */
  --tack-gray-600:   #57616d;  /* muted text */
  --tack-gray-900:   #161a1f;  /* ink */

  /* status hues — light tints and 700-weight text/line colours */
  --tack-blue-50:    #e8f1f8;  --tack-blue-700:    #15577f;  /* ready */
  --tack-amber-50:   #fbf1e2;  --tack-amber-700:   #8a5a12;  /* in progress */
  --tack-violet-50:  #f2ecfa;  --tack-violet-700:  #5f3f8a;  /* review */
  --tack-green-50:   #e7f4ec;  --tack-green-700:   #27633f;  /* done */
  --tack-slate-500:  #6b7683;                                 /* cancelled */
  --tack-red-50:     #fbeaec;  --tack-red-700:     #9f2532;  /* blocked */
  --tack-teal-700:   #0d6b68;                                 /* claimed */
  --tack-focus:      #0b63c4;
}
```

### 1.2 Raw palette — dark

```css
[data-theme="dark"] {
  --tack-white:      #0d1014;  /* inverted role: "page" in dark */
  --tack-gray-25:    #141a20;  /* surface */
  --tack-gray-50:    #1a2129;
  --tack-gray-100:   #1d242c;  /* raised */
  --tack-gray-200:   #2b3540;  /* decorative borders */
  --tack-gray-300:   #3a4653;
  --tack-gray-500:   #6b7785;  /* interactive/UI borders */
  --tack-gray-600:   #8b97a4;  /* muted text */
  --tack-gray-900:   #e8edf2;  /* ink */

  --tack-blue-50:    #16232e;  --tack-blue-700:    #7fc0ec;
  --tack-amber-50:   #2b2419;  --tack-amber-700:   #e0b070;
  --tack-violet-50:  #251e33;  --tack-violet-700:  #c3a6ea;
  --tack-green-50:   #16281d;  --tack-green-700:   #7fcf9f;
  --tack-slate-500:  #9aa5b1;
  --tack-red-50:     #2a1a1e;  --tack-red-700:     #f0939c;
  --tack-teal-700:   #6fd0cb;
  --tack-focus:      #8cc4f5;
}
```

Dark values are not a mechanical inversion. Each hue is re-picked so that text
sits at ≥4.5:1 on the dark surfaces and lines sit at ≥3:1; the light tints are
replaced by dark tints of the same hue so that a card and its status text keep
the same relationship in both themes.

### 1.3 Semantic tokens

```css
:root {
  /* surfaces */
  --bg-page:        var(--tack-gray-25);
  --bg-surface:     var(--tack-white);
  --bg-raised:      var(--tack-gray-50);
  --bg-sunken:      var(--tack-gray-100);
  --bg-overlay:     rgb(13 16 20 / 0.55);

  /* text */
  --fg-default:     var(--tack-gray-900);
  --fg-muted:       var(--tack-gray-600);
  --fg-on-accent:   var(--tack-white);

  /* lines */
  --border-subtle:  var(--tack-gray-200);   /* decorative, no contrast duty */
  --border-strong:  var(--tack-gray-500);   /* inputs, chips, focusable edges */
  --border-focus:   var(--tack-focus);

  /* accent (links, active filter, primary action) */
  --accent:         var(--tack-blue-700);
  --accent-hover:   #0f4a6b;
  --accent-soft:    var(--tack-blue-50);

  /* status: text colour / tint / border colour */
  --status-backlog-fg:    var(--tack-gray-600);
  --status-backlog-bg:    var(--tack-white);
  --status-backlog-line:  var(--tack-gray-300);
  --status-ready-fg:      var(--tack-blue-700);
  --status-ready-bg:      var(--tack-blue-50);
  --status-ready-line:    var(--tack-blue-700);
  --status-in-progress-fg:   var(--tack-amber-700);
  --status-in-progress-bg:   var(--tack-amber-50);
  --status-in-progress-line: var(--tack-amber-700);
  --status-review-fg:     var(--tack-violet-700);
  --status-review-bg:     var(--tack-violet-50);
  --status-review-line:   var(--tack-violet-700);
  --status-done-fg:       var(--tack-green-700);
  --status-done-bg:       var(--tack-green-50);
  --status-done-line:     var(--tack-green-700);
  --status-cancelled-fg:  var(--tack-slate-500);
  --status-cancelled-bg:  var(--tack-gray-100);
  --status-cancelled-line: var(--tack-slate-500);

  /* derived states (orthogonal to status) */
  --state-blocked-fg:     var(--tack-red-700);
  --state-blocked-bg:     var(--tack-red-50);
  --state-blocked-line:   var(--tack-red-700);
  --state-claimed-fg:     var(--tack-teal-700);
  --state-claimed-line:   var(--tack-teal-700);
  --state-claimed-bg:     var(--tack-gray-25);
  --state-eligible-fg:    var(--tack-blue-700);
  --state-reviewable-fg:  var(--tack-violet-700);

  /* feedback */
  --diff-highlight: var(--tack-blue-50);
  --diff-outline:   var(--tack-focus);

  /* spacing — 4 px base */
  --space-1: 4px;   --space-2: 8px;   --space-3: 12px;
  --space-4: 16px;  --space-6: 24px;  --space-8: 32px;
  --space-12: 48px; --space-16: 64px;

  /* radius */
  --radius-xs: 2px; --radius-sm: 4px; --radius-md: 6px;
  --radius-lg: 10px; --radius-pill: 999px;

  /* shadow */
  --shadow-1: 0 1px 2px rgb(13 16 20 / 0.06), 0 0 0 1px rgb(13 16 20 / 0.04);
  --shadow-2: 0 2px 6px rgb(13 16 20 / 0.10), 0 0 0 1px rgb(13 16 20 / 0.05);
  --shadow-3: 0 8px 24px rgb(13 16 20 / 0.16), 0 0 0 1px rgb(13 16 20 / 0.06);
  --focus-ring: 0 0 0 2px var(--bg-surface), 0 0 0 4px var(--border-focus);

  /* type */
  --font-sans: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue",
               Arial, "Noto Sans", sans-serif;
  --font-mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas,
               "Liberation Mono", monospace;
  --text-2xs: 0.6875rem;  /* 11px — chips, legend */
  --text-xs:  0.75rem;    /* 12px — metadata, ids */
  --text-sm:  0.8125rem;  /* 13px — table/facts labels */
  --text-base:0.875rem;   /* 14px — body, cards */
  --text-md:  1rem;       /* 16px — section headings */
  --text-lg:  1.25rem;    /* 20px — page title */
  --text-xl:  1.5625rem;  /* 25px — task title */
  --text-2xl: 1.953rem;   /* 31.25px — board title on wide screens */
  --leading-tight: 1.2; --leading-normal: 1.45; --leading-loose: 1.6;
  --weight-regular: 400; --weight-medium: 500; --weight-semibold: 600;
  --weight-bold: 700;

  /* motion */
  --dur-instant: 0ms;
  --dur-micro: 120ms;    /* hover, chip toggle, focus */
  --dur-standard: 180ms; /* popover, tooltip, overlay */
  --dur-layout: 240ms;   /* FLIP, count bump, programmatic zoom */
  --dur-slow: 400ms;     /* fit-to-screen animation */
  --ease-standard: cubic-bezier(0.2, 0, 0, 1);
  --ease-emphasized: cubic-bezier(0.4, 0, 0.2, 1);
  --ease-exit: cubic-bezier(0.4, 0, 1, 1);

  /* layout */
  --topbar-height: 56px;
  --column-width: 16rem;
  --content-max: 96rem;
  --graph-height: 62vh;
  --touch-target: 44px;
}
```

Semantic overrides in dark change only values, never names:

```css
[data-theme="dark"] {
  --bg-page: var(--tack-gray-25);
  --bg-surface: var(--tack-gray-50);
  --bg-raised: var(--tack-gray-100);
  --bg-sunken: #10161c;
  --bg-overlay: rgb(0 0 0 / 0.62);
  --accent-hover: #a8d6f5;
  --diff-highlight: #1b2b38;
}
```

`--border-subtle` is decorative only (card edges, column tracks) and carries no
contrast obligation. Anything that communicates a boundary the user must
perceive — input borders, chip outlines, the focus ring — uses
`--border-strong` or `--border-focus`, both measured at ≥3:1 against their
adjacent surface.

### 1.4 Measured contrast pairs

Ratios were computed with `calc` from the WCAG 2.x relative-luminance
definition, `L = 0.2126 R + 0.7152 G + 0.0722 B` over linearised sRGB channels,
and `ratio = (L_lighter + 0.05) / (L_darker + 0.05)`. The WCAG piecewise linear
toe affects only channels ≤ 10/255 and changes L by less than 0.0004, which
moves no ratio below by more than 0.01 — no AA verdict changes. All pairs were
checked with `calc check` against the computed value.

Normal text requires ≥4.5:1; large text (≥24 px, or ≥18.66 px bold) and
non-text UI boundaries require ≥3:1.

Light theme:

| # | Pair | Ratio | Requirement | Result |
| --- | --- | --- | --- | --- |
| L01 | ink on surface | 17.48 | 4.5 | pass |
| L02 | ink on page | 16.45 | 4.5 | pass |
| L03 | muted text on surface | 6.30 | 4.5 | pass |
| L04 | muted text on page | 5.92 | 4.5 | pass |
| L05 | muted text on rail | 5.55 | 4.5 | pass |
| L06 | ready text on surface | 7.77 | 4.5 | pass |
| L07 | ready text on ready tint | 6.80 | 4.5 | pass |
| L08 | in-progress text on surface | 5.91 | 4.5 | pass |
| L09 | in-progress text on its tint | 5.29 | 4.5 | pass |
| L10 | review text on surface | 8.19 | 4.5 | pass |
| L11 | review text on its tint | 7.08 | 4.5 | pass |
| L12 | done text on surface | 7.14 | 4.5 | pass |
| L13 | done text on its tint | 6.30 | 4.5 | pass |
| L14 | cancelled text on surface | 4.62 | 4.5 | pass (tightest) |
| L15 | blocked text on surface | 7.54 | 4.5 | pass |
| L16 | blocked text on its tint | 6.49 | 4.5 | pass |
| L17 | claimed text on surface | 6.33 | 4.5 | pass |
| L18 | interactive border on surface | 4.05 | 3.0 | pass |
| L19 | interactive border on page | 3.81 | 3.0 | pass |
| L20 | focus ring on surface | 5.84 | 3.0 | pass |
| L21 | focus ring on page | 5.50 | 3.0 | pass |
| L22–L26 | ink on each status tint | 15.05–15.63 | 4.5 | pass |

Dark theme:

| # | Pair | Ratio | Requirement | Result |
| --- | --- | --- | --- | --- |
| D01 | ink on surface | 14.88 | 4.5 | pass |
| D02 | ink on page | 16.19 | 4.5 | pass |
| D03 | muted text on surface | 5.89 | 4.5 | pass |
| D04 | muted text on page | 6.41 | 4.5 | pass |
| D05 | muted text on raised | 5.27 | 4.5 | pass |
| D06 | ready text on surface | 8.89 | 4.5 | pass |
| D07 | ready text on raised | 7.94 | 4.5 | pass |
| D08 | in-progress text on surface | 8.86 | 4.5 | pass |
| D09 | review text on surface | 8.31 | 4.5 | pass |
| D10 | done text on surface | 9.45 | 4.5 | pass |
| D11 | cancelled text on surface | 7.00 | 4.5 | pass |
| D12 | blocked text on surface | 7.81 | 4.5 | pass |
| D13 | claimed text on surface | 9.65 | 4.5 | pass |
| D14 | interactive border on surface | 3.84 | 3.0 | pass |
| D15 | interactive border on page | 4.18 | 3.0 | pass |
| D16 | focus ring on surface | 9.45 | 3.0 | pass |
| D17 | focus ring on page | 10.29 | 3.0 | pass |
| D18 | ink on raised | 13.29 | 4.5 | pass |
| D19–D24 | status text on its dark tint | 6.26–8.36 | 4.5 | pass |

Two design consequences follow from the table and are binding:

- **`--status-cancelled-fg` is the weakest pair (4.62).** Do not lighten it, do
  not place it on `--bg-sunken`, and do not use it for text smaller than
  12 px with reduced opacity. Cancelled cards additionally carry a strikethrough
  title and the "✕" glyph, so the state never depends on this colour.
- **`--border-subtle` is exempt but `--border-strong` is not.** Never swap them
  for convenience; a chip or input drawn with `--border-subtle` on the page
  background falls to 1.3:1 and fails SC 1.4.11.

### 1.5 Type scale and spacing arithmetic

The interface ramp is 11/12/13/14 px for dense chrome and 16/20/25/31.25 px for
headings. The heading steps are a clean 1.25 major-third: `16 × 1.25 = 20.00`,
`20 × 1.25 = 25.00`, `25 × 1.25 = 31.25`; `20 / 16 = 1.2500` (verified with
`calc eval` and `calc check`). The dense ramp is deliberately not geometric —
it is tuned so that 12 px metadata and 14 px body text remain legible at the
same optical weight. Documented as "interface ramp" so nobody "fixes" it into a
geometric scale and loses density.

Spacing is a 4 px base with doubling for layout steps: 4, 8, 12, 16, 24, 32,
48, 64. Multiples of the base are 1×, 2×, 3×, 4×, 6×, 8×, 12×, 16×. Use the
named token, never a raw pixel value.

Type is set in `--font-sans` (system stack) everywhere except task IDs, counts,
timestamps and code, which use `--font-mono`. No web fonts, no `@font-face`, no
external hosts — this is both a hard constraint and the reason the page paints
instantly.

### 1.6 Motion policy

Every transition uses a named duration and easing token. `prefers-reduced-motion:
reduce` collapses all of them to `--dur-instant` and disables inertia, FLIP and
count animation; state still changes, it just does not animate. SC 2.3.3
(*Animation from Interactions*) is the governing criterion. Motion is never the
only signal that something changed: a changed card also gets a static
`data-changed="true"` attribute for one poll cycle, which assistive technology
and forced-colors mode can perceive without animation.

---

## 2. Theme model

### 2.1 Attribute contract

`<html>` always carries `data-theme` with exactly one of three values:

| Value | Meaning | Resolved tokens |
| --- | --- | --- |
| `auto` | follow the operating system | light, or dark under `prefers-color-scheme: dark` |
| `light` | force light | light |
| `dark` | force dark | dark |

The server renders `data-theme="auto"` in `base.html`, so the attribute is
always present before any script runs and a no-JS reader gets the correct
theme. `<meta name="color-scheme" content="light dark">` is added so native
form controls and scrollbars match.

### 2.2 Cascade

```css
:root, [data-theme="light"] { color-scheme: light; /* light token block */ }

[data-theme="dark"] { color-scheme: dark; /* dark token block */ }

@media (prefers-color-scheme: dark) {
  [data-theme="auto"] { color-scheme: dark; /* dark token block */ }
}

@media (prefers-reduced-motion: reduce) {
  :root { --dur-micro: 0ms; --dur-standard: 0ms;
          --dur-layout: 0ms; --dur-slow: 0ms; }
}
```

`auto` is the only value that consults the media query; `light` and `dark` are
explicit and must not be overridden by the OS. Order matters: the dark block for
`[data-theme="dark"]` sits outside the media query so an explicit choice always
wins.

### 2.3 Cycle order and control

The cycle is **auto → light → dark → auto**. One button in the top bar, always
visible:

```html
<button type="button" class="theme-toggle" id="theme-toggle"
        aria-label="Theme: auto (follows system)" title="Theme: auto">
  <span class="theme-toggle__glyph" aria-hidden="true">◐</span>
  <span class="theme-toggle__label">auto</span>
</button>
```

Glyphs: `◐` auto, `☀` light, `☾` dark. The visible label is the current mode.
`aria-label` repeats the mode and its meaning; the glyph is `aria-hidden`.
The button is a plain `<button>` with `aria-pressed` **not** used (it is a
cycle, not a toggle); the label text is the state. Keyboard `t` invokes the same
handler.

### 2.4 Persistence and failure modes

Storage key: `tackboard.theme`. Every access is wrapped:

```js
function readTheme() {
  try { var v = window.localStorage.getItem("tackboard.theme");
        return (v === "light" || v === "dark" || v === "auto") ? v : "auto"; }
  catch (e) { return "auto"; }
}
function writeTheme(v) {
  try { window.localStorage.setItem("tackboard.theme", v); } catch (e) { /* session only */ }
}
```

Behaviour when storage is unavailable (private browsing, disabled storage,
quota error, `file://` in some browsers): reads fall back to `auto`, writes are
silently dropped, and the button keeps working for the life of the page because
the in-memory value is authoritative. Nothing throws, nothing logs to the
console in production. This is the "behave correctly when storage is
unavailable" requirement; the user still gets a working toggle, only the
preference is not remembered.

### 2.5 No-flash bootstrap

`static/theme.js` is **external and synchronous**, loaded in `<head>` before
the stylesheet link. Inline scripts are forbidden by CSP, so the bootstrap
cannot be inlined; a synchronous external script blocks parsing just long
enough to set the attribute before first paint.

```html
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>… — tackboard</title>
  <script src="/static/theme.js"></script>
  <link rel="stylesheet" href="/static/app.css">
</head>
```

`theme.js` does exactly three things: read the stored theme, set
`document.documentElement.dataset.theme`, and register a `matchMedia`
listener that re-applies tokens when the OS changes **and** the mode is `auto`.
It must not touch the DOM before `<html>` exists, so it runs at the end of
`<head>` with the attribute write guarded by a null check. It also sets the
`<meta name="theme-color">` value if present. No `defer`, no `async`, no
`type="module"` — those reintroduce a flash.

The toggle handler lives in the same file so there is one owner of theme state.
It reads the current attribute, advances the cycle, writes storage, updates
`aria-label`, `title`, glyph and label text. It never writes inline styles.

### 2.6 Theme and the rest of the system

- **Graph viewport stability across theme change.** The viewport transform is
  JS state, not CSS; a theme swap only changes CSS custom properties, so pan and
  zoom are untouched. No re-render, no relayout, no flash.
- **Print.** `@media print` forces light tokens and hides the theme toggle, the
  refresh control, the minimap and the keyboard overlay.
- **Forced colors.** Under `forced-colors: active`, borders fall back to system
  colours; the design must still work because status is encoded in shape and
  text, not fill. Add `forced-color-adjust: none` nowhere — let the OS win.
- **Chart-free.** No canvas 2D for UI chrome; SVG only, so theme changes are
  pure CSS.

---

## 3. One visual language for six statuses and all priorities

### 3.1 Status table (binding on board, graph and task page)

Status is styled from the `data-status` attribute carrying the raw JSON value.
The attribute is the single source of truth; components never get a
`status-in-progress` class name that could drift from the API value.

| Status | Node shape (graph) | Glyph | Text label | Board cue | Task cue |
| --- | --- | --- | --- | --- | --- |
| `backlog` | rounded rectangle, dashed `6 4` | `…` | Backlog | 4 px left border, dashed `--border-strong`; muted title | chip |
| `ready` | circle | `▷` | Ready | 4 px left border solid ready; title weight 600 | chip + "eligible" marker when true |
| `in_progress` | diamond | `▶` | In progress | 4 px left border solid in-progress | chip |
| `review` | hexagon | `◐` | Review | 4 px left border solid review | chip + "reviewable" marker when true |
| `done` | square | `✓` | Done | 4 px left border solid done; title not struck | chip |
| `cancelled` | octagon | `✕` | Cancelled | 4 px left border dotted cancelled; title struck through | chip; title struck through |

Every row satisfies "distinguishable without colour": six different outline
shapes, six different glyphs, six different words. A monochrome screenshot
still separates all six.

Rules:

- The glyph is always `aria-hidden="true"`; the adjacent text label carries the
  meaning for assistive technology. Never ship a glyph without its label in the
  same accessible name or the same visible row.
- The board's 4 px left border is a *status* cue; the chip repeats it in words.
  A card without a chip (compact list mode) still shows the border plus the
  glyph in its meta row.
- On the task page the chip is the first item of the facts list, and the title
  block repeats the glyph.

### 3.2 Priority table

Priority ranks come from `PRIORITY_RANK` (low 1, normal 2, high 3, urgent 4).
The visual cue is a count of filled pips plus the word — never colour alone,
and never the badge colour as the only difference.

| Priority | Pips | Word | Extra cue |
| --- | --- | --- | --- |
| `low` | `▁` | low | muted text |
| `normal` | `▁▁` | normal | default text |
| `high` | `▁▁▁` | high | medium weight |
| `urgent` | `▁▁▁▁` | urgent | bold weight + `!` prefix in the chip |

```html
<span class="priority" data-priority="urgent">
  <span class="priority__pips" aria-hidden="true">▁▁▁▁</span>
  <span class="priority__word">urgent</span>
</span>
```

Rendered as text, the chip reads "urgent" (or "! urgent"). Sorting is by rank,
descending, then by sequence — the API already returns that order for the board.

### 3.3 Derived states are a separate layer

`blocked`, `eligible`, `reviewable` and `claimed` are **not** statuses. A task
can be `in_progress` and `claimed` and `blocked` at once. Each derived state is
an overlay that never changes the status shape:

| Derived state | Non-colour cue | Text |
| --- | --- | --- |
| `blocked` | 45° hatch fill overlay + 3 px border; `⊘` badge | "blocked" + reason |
| `claimed` | double ring outside the status outline (2 px ring, 2 px gap) + holder badge | "claimed by agent-x" |
| `eligible` | corner triangle tab at the top-right of the node; `▷` badge on cards | "eligible" |
| `reviewable` | second parallel line 3 px inside the top edge; `◐` badge | "reviewable" |

`claimed` and `in_progress` are deliberately separate layers: an `in_progress`
task that nobody holds is diamond + amber and no ring; a `ready` task held by an
agent is circle + blue and a double ring with a holder badge. The graph must
never merge these two facts into one shape. The API agrees: `status` is stored,
`claimed_by`/`claimed_at` come from the claims table.

Priority and status never compete for the same channel: status owns the outline
shape and the left border; priority owns the pips and the badge text.

### 3.4 Contrast and legibility duties for the language

Each status text colour is measured against its own tint and against the surface
(§1.4). Each status line colour is measured against the surface at ≥3:1. The
cancelled pair is the tightest and is therefore always accompanied by the
strikethrough and the glyph. In forced-colors mode the outline shapes survive
because they are geometry, not paint.

---

## 4. Component anatomy

Conventions used in this section: `E` marks a required element, `R` a required
ARIA attribute or role, `S` a state attribute or class. Class names are flat and
hyphenated; components use `data-*` for state so CSS selectors and JS handlers
read the same vocabulary as the JSON API.

### 4.1 Application shell and top bar

```html
<header class="topbar">
  <a class="brand" href="/board">tackboard</a>
  <nav class="nav" aria-label="Views">
    <a href="/board" aria-current="page">Board</a>
    <a href="/graph">Graph</a>
  </nav>
  <p class="identity">
    <span class="identity__label">project</span>
    <strong class="identity__value" data-project="tackboard">tackboard</strong>
    <span class="identity__label">database</span>
    <code class="identity__value">~/tackboard.sqlite3</code>
  </p>
  <button type="button" class="theme-toggle" id="theme-toggle">…</button>
</header>
```

- E: project name and database path, always visible, never truncated without a
  `title` carrying the full value. This is a hard constraint, not a nicety — the
  operator must know which board they are reading.
- R: `<nav aria-label="Views">`; the current view's link gets
  `aria-current="page"`.
- S: `data-theme` lives on `<html>`, not here.
- The top bar is 56 px tall, sticky at the top (`position: sticky; top: 0`),
  with `background: var(--bg-surface)` and `border-bottom: 1px solid
  var(--border-subtle)`. It must not cover focused content: `scroll-padding-top:
  var(--topbar-height)` on `<html>`.

### 4.2 Board summary bar

```html
<section class="summary" aria-labelledby="summary-heading">
  <h2 id="summary-heading" class="sr-only">Board summary</h2>
  <dl class="summary__counts">
    <div class="summary__item">
      <dt>total</dt>
      <dd><a class="count" href="/board" data-filter="all">41</a></dd>
    </div>
    <div class="summary__item">
      <dt>eligible</dt>
      <dd><a class="count" href="/board?eligible=1" data-filter="eligible"
             aria-current="false">7</a></dd>
    </div>
    <div class="summary__item">
      <dt>blocked</dt>
      <dd><a class="count" href="/board?blocked=1" data-filter="blocked">3</a></dd>
    </div>
    <div class="summary__item">
      <dt>claimed</dt>
      <dd><a class="count" href="/board?claimed=1" data-filter="claimed">2</a></dd>
    </div>
  </dl>
  <ul class="summary__status" aria-label="Counts by status">
    <li><a class="count" href="/board?status=backlog" data-filter="status:backlog">
      <span class="status-chip" data-status="backlog"><span class="status-chip__glyph"
        aria-hidden="true">…</span> backlog</span>
      <span class="count__value">12</span></a></li>
    …
  </ul>
</section>
```

- E: total, eligible, blocked, claimed, and one count per status. These are the
  same numbers `tack board --count` prints; the web layer must not recompute
  them differently.
- Counts are `<a>` elements so they work without JavaScript and are keyboard
  reachable in one tab stop each. JS intercepts `click` and applies the filter
  in place without a navigation. Active filter → `aria-current="true"`.
- R: the group is a `<dl>` for the headline four (term/definition pairs read
  well) and a `<ul>` for the per-status row. Each `<a>` has an accessible name
  that includes the number: "12 backlog tasks".
- S: `data-filter` is the machine-readable filter; `aria-current` is the state.
- The count value animates on change with a 240 ms opacity/scale bump; the
  number itself is updated immediately (never animate the text content).

### 4.3 Filter chips

```html
<form class="filters" method="get" action="/board" role="search"
      aria-label="Filter tasks">
  <div class="filters__chips" role="group" aria-label="Active filters">
    <button type="button" class="chip" data-remove="status" aria-label="Remove status filter: ready">
      <span class="status-chip" data-status="ready">▷ ready</span>
      <span class="chip__x" aria-hidden="true">✕</span>
    </button>
    <button type="button" class="chip" data-remove="blocked" aria-label="Remove blocked filter">
      blocked <span class="chip__x" aria-hidden="true">✕</span>
    </button>
  </div>

  <details class="filters__more">
    <summary class="chip chip--add">Add filter</summary>
    <fieldset class="filters__panel">
      <legend>Status</legend>
      <label><input type="checkbox" name="status" value="ready"> ready</label>
      …
      <label for="filter-assignee">assignee</label>
      <input id="filter-assignee" type="text" name="assignee" value="">
      <label for="filter-label">label</label>
      <input id="filter-label" type="text" name="label" value="">
      <label><input type="checkbox" name="blocked" value="1"> blocked</label>
      <label><input type="checkbox" name="eligible" value="1"> eligible</label>
      <button type="submit">Apply</button>
      <a class="reset" href="/board">Reset all</a>
    </fieldset>
  </details>
</form>
```

- E: a visible active-filter row, an add-filter control, and a reset action.
  With no filters the row shows "No filters — showing everything" rather than
  an empty box.
- R: `<form role="search">`, `role="group"` on the chip row, each remove button
  labelled with the full filter name and value.
- S: active chip = `aria-current="true"` on its `<a>`/`data-active="true"`;
  the remove buttons are `<button type="button">` so Enter does not submit the
  form. The `details`/`summary` disclosure is native and needs no ARIA; the
  summary text toggles "Add filter"/"Close filters" on `toggle`.
- Without JS the form submits as a GET to `/board` and the same filters are
  applied server-side. With JS the form's `submit` is intercepted and the URL is
  updated with `history.replaceState`, then the board is diffed in place.
- Chips are 32 px tall in pointer-fine contexts and at least 44 px in
  `@media (pointer: coarse)`.

### 4.4 Board card

```html
<li class="card" data-task="T-000041" data-status="in_progress"
    data-priority="high" data-blocked="false" data-claimed="true"
    tabindex="-1">
  <div class="card__head">
    <span class="card__glyph" data-status="in_progress" aria-hidden="true">▶</span>
    <a class="card__id" href="/task/T-000041">T-000041</a>
    <span class="card__title">Design system and UX specification</span>
  </div>
  <div class="card__meta">
    <span class="priority" data-priority="high">…</span>
    <span class="card__assignee">agent-a</span>
  </div>
  <ul class="card__labels" aria-label="Labels">
    <li class="tag">web-redesign</li>
  </ul>
  <p class="card__blocked" hidden>blocked: prerequisite T-000039 unfinished</p>
  <p class="card__claim">
    <span class="claim-badge" title="agent-a (claimed 12 min ago)">@agent-a</span>
    <span class="card__claim-age">12m</span>
  </p>
</li>
```

- E: ID, title, status glyph + chip, priority pips + word, assignee, labels,
  blocked reason when blocked, claim holder and claim age when claimed.
- R: the card is a list item inside the column's `<ol>`/`<ul>`; the ID is the
  only link (title text follows it as plain text so the accessible name stays
  "T-000041"). `data-claimed`/`data-blocked` mirror the API booleans so CSS and
  JS never parse text.
- S: hover = `--bg-raised` + `border-color: var(--border-strong)`; focus-visible
  = `--focus-ring`; selected (`j`/`k` cursor) = `data-selected="true"` with a
  2 px focus-coloured outline inset; changed = `data-changed="true"` for one
  poll cycle (a static background tint plus the 1.2 s fade); hidden = not in the
  DOM after the exit animation.
- Text alternative: the card's accessible name is the ID plus title; the meta
  row is read as separate text, and the blocked reason is a real `<p>`, not a
  tooltip-only string. Claim age is rendered as text ("12m"), with the full
  timestamp in `title` and in the claim badge's `title`.
- Long titles wrap to two lines and then clamp with an ellipsis plus a `title`
  carrying the full text. No truncation of the ID.

### 4.5 Status columns

```html
<section class="column" data-status="in_progress" aria-labelledby="col-in_progress">
  <h2 class="column__title" id="col-in_progress">
    <span class="status-chip" data-status="in_progress">▶ in progress</span>
    <span class="column__count"><span class="column__number">4</span>
      <span class="sr-only">tasks</span></span>
  </h2>
  <ol class="cards">…</ol>
  <a class="column__more" href="/board?status=in_progress">show all 9</a>
</section>
```

- E: six columns in the fixed status order backlog, ready, in_progress, review,
  done, cancelled; each shows its count and its "show all" link when the
  terminal limit truncates it.
- R: `<section aria-labelledby>` with a real `<h2>`; the list is `<ol>` because
  order is meaningful (priority rank, then sequence).
- S: the column is a scroll container on narrow screens (`scroll-snap-align:
  start`); `data-empty="true"` renders "No tasks" in muted text rather than
  collapsing the column, so the six-column mental model stays stable.
- The `done`/`cancelled` limit stays 20 most recently updated, with the existing
  "show all" link — a design constraint carried over from the brief.

### 4.6 Task header and facts

```html
<header class="task-header">
  <p class="task-header__eyebrow">
    <span class="status-chip" data-status="review">◐ review</span>
    <span class="priority" data-priority="high">▁▁▁ high</span>
  </p>
  <h1 class="task-header__title">
    <span class="task-header__id">T-000041</span>
    Design system and UX specification for the read-only web view
  </h1>
  <ul class="readiness" aria-label="Readiness">
    <li><span class="state-chip" data-state="eligible">▷ eligible</span></li>
    <li><span class="state-chip" data-state="reviewable">◐ reviewable</span></li>
    <li><span class="state-chip" data-state="blocked">⊘ blocked</span></li>
    <li><span class="state-chip" data-state="claimed">◎ claimed by agent-a</span></li>
  </ul>
</header>

<dl class="facts">
  <dt>status</dt><dd>…</dd>
  <dt>priority</dt><dd>…</dd>
  <dt>assignee</dt><dd>…</dd>
  <dt>labels</dt><dd>…</dd>
  <dt>claim</dt><dd>…</dd>
  <dt>dependency ready</dt><dd>yes / no</dd>
  <dt>created</dt><dd><time datetime="…">…</time></dd>
  <dt>updated</dt><dd><time datetime="…">…</time></dd>
  <dt>version</dt><dd>…</dd>
</dl>
```

- E: status, readiness summary, derived blockers, and the facts list.
- R: `<header>` inside `<main>`; one `<h1>` per page; `<time datetime>` for every
  timestamp so machines and screen readers get the ISO value while the eye gets
  a short form.
- S: a readiness chip is present only when true; absent states are not rendered
  as "not eligible" noise. The *absence* is announced by the summary sentence in
  the facts region: "Readiness: eligible, not blocked."
- The claim cell shows "none" or "agent-x, claimed 12m ago"; the full ISO time
  goes in `title`.

### 4.7 Blockers

```html
<section class="panel" aria-labelledby="blockers-heading">
  <h2 id="blockers-heading">Derived blockers</h2>
  <ul class="blockers">
    <li class="blocker" data-kind="prerequisite_unfinished">
      <span class="blocker__code">prerequisite_unfinished</span>
      <span class="blocker__text">T-000039 is not done</span>
    </li>
  </ul>
</section>
```

- E: each blocker's machine code and a human sentence. Codes are the API's
  codes; the sentence is derived client-side or server-side from the same data.
- R: `<section aria-labelledby>`; `<ul>` of `<li>`.
- S: `data-kind` mirrors the code for styling; the code text is visible, not
  hidden — operators and agents read the same vocabulary.
- Empty state: "No derived blockers." as a single `<p>`, not an empty list.

### 4.8 Prerequisite and dependent links

```html
<nav class="links" aria-labelledby="prereq-heading">
  <h2 id="prereq-heading">Prerequisites</h2>
  <ul class="link-list">
    <li class="link-list__item" data-status="done">
      <a href="/task/T-000039">T-000039</a>
      <span class="link-list__title">Schema migration for claims</span>
      <span class="status-chip" data-status="done">✓ done</span>
    </li>
  </ul>
</nav>
```

- E: both lists; each row shows ID, title and status chip. An unfinished
  prerequisite additionally carries the text "not done" so the blocking cause is
  visible without reading status semantics.
- R: `<nav aria-labelledby>` per list, or one `<nav>` with two labelled
  sections. Direction is stated in words: "Prerequisites — these must finish
  before this task can start" and "Dependents — these wait for this task".
  The graph legend repeats "A → B means B depends on A"; the task page states it
  in the same words so the two views agree.
- S: `data-status` on the row tints the chip and the left border; hover
  underlines the title, focus gets the ring.

### 4.9 Notes and history timeline

```html
<ol class="timeline" aria-label="Notes and history, newest first">
  <li class="timeline__item" data-kind="note">
    <p class="timeline__meta">
      <time datetime="2026-10-24T09:37:06Z">24 Oct 09:37</time>
      <span class="timeline__actor">agent-a</span>
      <span class="timeline__kind">note</span>
    </p>
    <div class="prose">…rendered markdown…</div>
  </li>
  <li class="timeline__item" data-kind="event">
    <p class="timeline__meta">…</p>
    <p class="timeline__payload">status: ready → in_progress</p>
  </li>
</ol>
```

- E: notes and events in one reverse-chronological list, distinguished by
  `data-kind` and by a visible kind word ("note", "status", "claim", "release").
- R: `<ol>`; each item's meta line is real text, not a title attribute. The
  event payload is text (never parsed as HTML).
- S: `data-kind="note"` gets the prose treatment; `data-kind="event"` gets
  monospace payload styling. A vertical rule on the left of the list is the
  timeline spine (2 px, `--border-subtle`).
- The API returns at most 20 events and 5 notes for the task page; the design
  keeps those limits and shows "Earlier history is available through `tack
  history`" as a footer line, so the view does not imply completeness.

### 4.10 Markdown prose

```html
<div class="prose">
  <p>Paragraph text with <code>inline code</code> and a
     <a href="https://example.com" rel="noopener noreferrer"
        target="_blank">link<span class="sr-only"> (opens in a new tab)</span></a>.</p>
  <h3>Heading</h3>
  <ul><li>item</li></ul>
  <pre><code>fenced block</code></pre>
</div>
```

- E: paragraphs, headings (h2–h4 only — h1 belongs to the page), ordered and
  unordered lists (nested to depth 3), fenced and inline code, bold, italic,
  and `http(s)` links.
- R: the container has no role; the semantic elements carry the structure. A
  link's accessible name gains " (opens in a new tab)" via a visually hidden
  suffix when `target="_blank"` is used.
- S: `.prose` sets `--leading-loose`, a measure of 68 characters
  (`max-width: 68ch`) and 16 px body size — the one place the page relaxes to
  reading typography.
- **No-JS fallback:** the server renders `<pre class="description">` with
  `white-space: pre-wrap` and escaped text. The JS renderer replaces that node
  only after it has built a valid tree; if parsing throws, the `<pre>` stays.

### 4.11 Graph: node, edge, tooltip, legend, minimap, toolbar

Node (SVG, one `<g>` per task):

```html
<g class="node" id="node-T-000041" role="link" tabindex="-1"
   aria-label="T-000041, Design system and UX specification, in progress,
               priority high, claimed by agent-a, blocked by T-000039"
   data-status="in_progress" data-claimed="true" data-blocked="false"
   transform="translate(240,64)">
  <title>T-000041 — Design system and UX specification (in progress, claimed by agent-a)</title>
  <rect class="node__shape" width="176" height="56" rx="6"></rect>
  <g class="node__claim-ring" aria-hidden="true"><rect …></rect><rect …></rect></g>
  <text class="node__id">T-000041</text>
  <text class="node__title">Design system and UX…</text>
  <text class="node__status">▶ in progress</text>
  <g class="node__claim-badge" aria-hidden="true">
    <rect …></rect><text>@agent-a</text>
  </g>
  <path class="node__corner-mark" d="…"></path>   <!-- eligible tab -->
  <path class="node__double-edge" d="…"></path>   <!-- reviewable -->
  <path class="node__elided" d="…"></path>        <!-- +N marker -->
</g>
```

- E: the `<title>` child gives a native tooltip and a fallback name; the
  `aria-label` carries the same facts as text. The claim ring and badges are
  `aria-hidden` decoration — the words are in the label.
- R: `role="link"` because activation opens the task page; `tabindex="-1"` for
  roving focus (the canvas owns the tab stop and uses
  `aria-activedescendant`). APG composite-widget pattern.
- S: `data-claimed`, `data-blocked`, `data-eligible`, `data-reviewable`,
  `data-selected`, `data-dimmed`, `data-hidden`.
- Text alternative: the `aria-label` sentence always names ID, title, status,
  priority, assignee, claim holder and blocked reason when present. It is the
  same sentence the tooltip shows, so screen-reader and sighted users read one
  description, not two.

Edge:

```html
<g class="edge" data-source="T-000039" data-target="T-000041">
  <path class="edge__line" d="M 416 92 C 520 92, 520 92, 560 92"></path>
  <path class="edge__hit" d="M 416 92 C 520 92, 520 92, 560 92"
        stroke-width="14" stroke="transparent" fill="none"
        pointer-events="stroke"></path>
  <title>T-000039 must finish before T-000041</title>
</g>
```

- E: one visible curve and one wider invisible hit curve (14 px) so hovering is
  forgiving. The `<title>` states the direction in words, not the arrow alone.
- R: the edge group has no role; it is decoration plus a hover target. The
  direction is stated in the legend and in each endpoint's `aria-label`
  ("depends on T-000039" / "T-000041 depends on this").
- S: `data-highlight="true"` while hovered or on the selected path; dimmed
  edges drop to 35 % opacity via `data-dimmed`.

Tooltip:

```html
<div class="tooltip" id="graph-tooltip" role="tooltip" hidden>
  <p class="tooltip__title"><span class="tooltip__id">T-000041</span> Design system…</p>
  <p class="tooltip__row"><span class="status-chip" data-status="in_progress">▶ in progress</span>
     <span class="priority" data-priority="high">▁▁▁ high</span></p>
  <p class="tooltip__row">assignee: agent-a · claimed by agent-a, 12m</p>
  <p class="tooltip__row">blocked: T-000039 not done</p>
</div>
```

- E: ID, title, status, priority, assignee, claim holder/age, blocked reason —
  the same fields as the brief.
- R: `role="tooltip"`, referenced by the active node's `aria-describedby` while
  shown. `pointer-events: none` so it never steals hover. It is rendered as an
  **SVG `<g>` inside the canvas**, positioned with a `transform` attribute and
  clamped to the viewBox — not as an HTML overlay. Reason: CSP `style-src`
  falls back to `'self'`, so a `style=""` attribute (including one written by
  `element.style`) is blocked and logs a violation; SVG presentation attributes
  are not styles and are allowed. This keeps the tooltip CSP-clean and
  automatically inside the canvas coordinate space.
- S: `hidden` when absent; `data-placement="right|left|bottom|top"` after flip.
  Placement: preferred right of the pointer/node, 14 px offset; flip to left
  when it would exceed the viewport; shift vertically to stay within 8 px of the
  edges. Max width 22 rem; title clamped to two lines.

Legend:

```html
<section class="legend" aria-labelledby="legend-heading">
  <h2 id="legend-heading" class="legend__heading">Legend</h2>
  <ul class="legend__items">
    <li><svg class="legend__shape" data-status="backlog" …></svg> backlog</li>
    … one per status …
    <li><svg class="legend__claimed" …></svg> claimed — double ring + holder badge</li>
    <li><svg class="legend__blocked" …></svg> blocked — hatched</li>
    <li><svg class="legend__eligible" …></svg> eligible — corner tab</li>
    <li><svg class="legend__reviewable" …></svg> reviewable — double top edge</li>
    <li class="legend__direction">A → B means B depends on A</li>
  </ul>
</section>
```

- E: six status entries, four derived-state entries, and the direction sentence.
  "Claimed" is required by the brief; the other three are required by §3.3.
- R: `<section aria-labelledby>`; each entry's shape is `aria-hidden` and the
  word carries the meaning. Collapsible on narrow screens via `<details>`.
- S: active toggles highlight their legend row (`aria-pressed` on the toggle
  buttons, `data-active` on the row).

Minimap:

```html
<div class="minimap" id="minimap" hidden>
  <svg class="minimap__svg" role="img" aria-label="Overview of the whole graph"></svg>
  <div class="minimap__viewport"></div>
  <button type="button" class="minimap__collapse" aria-expanded="true"
          aria-controls="minimap">Collapse minimap</button>
</div>
```

- E: a miniature of the visible subgraph with status shapes (not just colours),
  claimed rings, and a draggable viewport rectangle. Lower-right corner,
  200×140 px maximum, 35 % width on small screens.
- R: `role="img"` with an `aria-label` that states node and edge counts and how
  many are hidden. The viewport rectangle is `aria-hidden` (it is a pointer
  affordance); the keyboard equivalent is the "Fit all"/arrow-pan controls.
- S: `data-collapsed="true"` collapses to a 44 px button; collapsed by default
  under 900 px. Dragging the rectangle pans the main canvas; clicking elsewhere
  recentres on that point.

Toolbar:

```html
<div class="graph-toolbar" role="toolbar" aria-label="Graph controls"
     aria-orientation="horizontal">
  <button type="button" data-graph-action="zoom-in" aria-label="Zoom in">+</button>
  <button type="button" data-graph-action="zoom-out" aria-label="Zoom out">−</button>
  <button type="button" data-graph-action="fit-all">Fit all</button>
  <button type="button" data-graph-action="zoom-reset">100%</button>
  <button type="button" id="highlight-claimed" aria-pressed="true">Highlight claimed</button>
  <button type="button" id="focus-claimed" aria-pressed="false">Focus claimed</button>
  <button type="button" id="hide-closed" aria-pressed="false">Hide done &amp; cancelled</button>
  <span class="graph-toolbar__count" id="hidden-count" role="status"></span>
</div>
```

- E: zoom in, zoom out, fit all, 100 %, the two claimed controls and the
  hide-closed toggle. The brief's "fit to selection" action appears when a node
  is selected.
- R: `role="toolbar"` with arrow-key traversal inside the toolbar (APG toolbar
  pattern), each button a `<button type="button">`; toggles use `aria-pressed`
  and keep their label stable (never "Hide"/"Show" text swaps — the pressed
  state carries the meaning).
- S: `data-hidden-count` on the count element; the count is a live region so
  "12 hidden" is announced.

### 4.12 Pause/resume and last-updated control

```html
<div class="refresh" role="group" aria-label="Live updates">
  <button type="button" id="poll-toggle" aria-pressed="false">Pause updates</button>
  <p class="refresh__stamp">
    <span id="last-updated">updated just now</span>
    <span class="refresh__dot" aria-hidden="true">●</span>
  </p>
</div>
```

- E: a visible pause/resume control and a relative "last updated" indicator that
  ticks (0–59 s, then "N min ago"). The dot is a static visual liveness cue, not
  the only cue.
- R: `role="group"`; the button uses `aria-pressed="true"` when paused and its
  label stays "Pause updates" (pressed state is the meaning) — or, if the team
  prefers a label swap, both label and `aria-pressed` must change together.
  `aria-live="off"` on the stamp: a ticking timer must not be announced every
  second. The polite live region (§6.6) announces data changes instead.
- S: `data-paused="true"` on the group; the stamp becomes "paused — last updated
  12s ago" and a "Refresh now" button appears.

### 4.13 Keyboard help overlay

```html
<dialog class="help" id="help" aria-labelledby="help-heading" hidden>
  <h2 id="help-heading">Keyboard shortcuts</h2>
  <table class="help__table">… key / action rows …</table>
  <button type="button" class="help__close" data-close-help>Close</button>
</dialog>
```

- E: the full shortcut table from §8; closable with `Esc`, the Close button and
  a click on the backdrop.
- R: native `<dialog>` gives `role="dialog"`, focus containment and `Esc`
  handling for free (APG modal dialog pattern). If `<dialog>` is unavailable,
  fall back to a `<div role="dialog" aria-modal="true">` with a manual focus
  trap. `aria-labelledby` points at the heading.
- S: `hidden` attribute plus `showModal()`; the opener is remembered and
  refocused on close. While open, shortcuts are suspended except `Esc`.
- The overlay is reachable from the `?` key and from a "Keyboard shortcuts"
  button in the top bar, so it is discoverable without knowing the key.

### 4.14 Empty, error and loading states

- Empty board: "No tasks match these filters." plus a Reset link — a real
  sentence, not a blank column set.
- Empty graph: "No dependencies to draw." plus the outline remains.
- Error page: keep the existing `error.html` shape; add `role="alert"` to the
  message paragraph so it is announced. Status code, message and "Back to the
  board" link stay.
- Loading: skeleton cards only on first paint when JS is doing the initial
  fetch; afterwards updates are in-place and never blank the board. Skeletons
  are `aria-hidden` with a single polite "Loading board…" status.

---

## 5. Layout wireframes

Breakpoints are literal values in `@media` (custom properties cannot be used in
media queries): **small 640 px**, **medium 900 px**, **large 1200 px**. Content
is capped at `--content-max` (96 rem) and centred.

### 5.1 `/board` — desktop (≥1200 px)

```
┌──────────────────────────────────────────────────────────────────────────┐
│ tackboard   Board  Graph            project tackboard   db …/board.sqlite3  ◐│
├──────────────────────────────────────────────────────────────────────────┤
│ memo (optional, collapsible)                                             │
├──────────────────────────────────────────────────────────────────────────┤
│ total 41 · eligible 7 · blocked 3 · claimed 2                            │
│  …backlog 12   ▷ready 7   ▶in progress 4   ◐review 2   ✓done 20   ✕cancel │
├──────────────────────────────────────────────────────────────────────────┤
│ [✕ status: ready] [✕ blocked]                    [+ Add filter] [Reset]  │
├───────────┬───────────┬───────────┬───────────┬───────────┬──────────────┤
│ …backlog  │ ▷ready    │ ▶in prog. │ ◐review   │ ✓done     │ ✕cancelled   │
│ ┌───────┐ │ ┌───────┐ │ ┌───────┐ │ ┌───────┐ │ ┌───────┐ │ ┌──────────┐ │
│ │card   │ │ │card   │ │ │card   │ │ │card   │ │ │card   │ │ │card      │ │
│ └───────┘ │ └───────┘ │ └───────┘ │ └───────┘ │ └───────┘ │ └──────────┘ │
│           │           │           │           │ show all  │ show all     │
├───────────┴───────────┴───────────┴───────────┴───────────┴──────────────┤
│ [Pause updates]   updated 2s ago                          ●              │
└──────────────────────────────────────────────────────────────────────────┘
```

Columns are 16 rem wide with `--space-3` gaps; when six do not fit the board
scrolls horizontally (`overflow-x: auto`, `scroll-snap-type: x proximity`).
The summary and filter rows stay sticky under the top bar so filters remain
reachable while a column is scrolled.

### 5.2 `/board` — tablet (640–900 px)

```
┌──────────────────────────────────────────────┐
│ tackboard  Board Graph      tackboard  ◐        │
├──────────────────────────────────────────────┤
│ total 41 · eligible 7 · blocked 3 · claimed 2│
│ ◀ …backlog 12  ▷ready 7  ▶in prog 4  ◐rev 2 ▶│  ← summary row scrolls
├──────────────────────────────────────────────┤
│ [✕ ready]                       [+ filter]   │
├──────────────────────────────────────────────┤
│ ┌──────────────────┐ ┌──────────────────┐    │
│ │ ▷ready  (7)      │ │ ▶in progress (4) │ →  │  ← horizontal snap
│ │ ┌──────────────┐ │ │ ┌──────────────┐ │    │
│ │ │ card         │ │ │ │ card         │ │    │
│ │ └──────────────┘ │ │ └──────────────┘ │    │
│ └──────────────────┘ └──────────────────┘    │
│      scroll-snap-align: start                │
├──────────────────────────────────────────────┤
│ [Pause] updated 2s ago                       │
└──────────────────────────────────────────────┘
```

Columns become full-bleed snap panels at 85 % viewport width; the next column's
edge peeks in so the gesture is discoverable. Status order is preserved.

### 5.3 `/board` — mobile (<640 px)

```
┌────────────────────────────┐
│ tackboard        ◐   ?     │
├────────────────────────────┤
│ tackboard · …/board.sqlite3   │  ← identity wraps, never hidden
├────────────────────────────┤
│ total 41  eligible 7  …    │  ← summary as a 2-row wrapped grid
├────────────────────────────┤
│ [backlog][ready][in prog][r…│  ← status tablist (scrolls)
├────────────────────────────┤
│ ▷ ready  7                 │
│ ┌────────────────────────┐ │
│ │ T-000041            ▷  │ │
│ │ Design system and UX…  │ │
│ │ ▁▁▁ high · agent-a      │ │
│ │ [web-redesign]  @agent-a│ │
│ └────────────────────────┘ │
│ …                          │
├────────────────────────────┤
│ [Pause] updated 2s ago     │
└────────────────────────────┘
```

Below 640 px the columns collapse into a **status tablist**: six tabs
(`role="tablist"`, arrow-key navigation, `aria-selected`), one panel at a time,
`role="tabpanel"` with `aria-labelledby`. This is the "status tabs" option the
brief allows; it beats horizontal snap on a phone because the tab labels fit in
one row and the card width is the full viewport.

### 5.4 `/task/<id>` — desktop (≥1200 px)

```
┌──────────────────────────────────────────────────────────────────────────┐
│ top bar (project, database, theme)                                       │
├──────────────────────────────────────────────────────────────────────────┤
│ ◐ review · ▁▁▁ high                                                      │
│ T-000041  Design system and UX specification for the read-only web view   │
│ [▷ eligible] [⊘ blocked] [◎ claimed by agent-a]                           │
├───────────────────────────────────┬──────────────────────────────────────┤
│ Description (markdown prose)      │ Facts                                │
│ ┌───────────────────────────────┐ │ status      ◐ review                 │
│ │ paragraph, lists, code…       │ │ priority    ▁▁▁ high                 │
│ └───────────────────────────────┘ │ assignee    agent-a                   │
│                                   │ labels      web-redesign             │
│ Derived blockers                  │ claim       agent-a, 12m ago          │
│ • prerequisite_unfinished …       │ dep. ready  yes                      │
│                                   │ created / updated / version          │
│ Prerequisites (must finish first) │                                      │
│ • T-000039 ✓ done                 │                                      │
│ Dependents (wait for this)        │                                      │
│ • T-000044 …                      │                                      │
├───────────────────────────────────┴──────────────────────────────────────┤
│ Notes and history — timeline (newest first)                              │
│ │ 24 Oct 09:37  agent-a  note   …                                         │
│ │ 24 Oct 09:31  agent-a  status  ready → in_progress                      │
└──────────────────────────────────────────────────────────────────────────┘
```

Two columns: prose 2/3, facts 1/3. The facts column is `position: sticky; top:
calc(var(--topbar-height) + var(--space-4))` so it stays in view while long
notes scroll. Below 1200 px it becomes one column with facts first (operators
scan facts before prose).

### 5.5 `/task/<id>` — mobile (<640 px)

Single column, in this order: title, readiness chips, facts (collapsed
`<details>` "Facts" open by default? no — keep open; facts are the point),
blockers, prerequisites, dependents, description, timeline. Sticky top bar only.
Every interactive row is ≥44 px tall.

### 5.6 `/graph` — desktop (≥1200 px)

```
┌──────────────────────────────────────────────────────────────────────────┐
│ top bar                                                                  │
├──────────────────────────────────────────────────────────────────────────┤
│ [+][−][Fit all][100%] │ [Highlight claimed ✓][Focus claimed][Hide done ✓]│
│ 41 nodes · 63 edges · 12 hidden          [task ▾ neighbourhood] [whole]  │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   ┌────┐      ┌────┐        ┌────┐        ┌────┐                        │
│   │…   │─────▶│▷   │───────▶│▶   │───────▶│✓   │                        │
│   └────┘      └────┘        └────┘        └────┘                        │
│      │                         ▲                                         │
│      └─────────────────────────┘                                         │
│                                                                          │
│                                        ┌──────────────┐                 │
│                                        │  minimap     │                 │
│                                        │ ▢▢▢  [▭]     │                 │
│                                        └──────────────┘                 │
├──────────────────────────────────────────────────────────────────────────┤
│ ▸ Legend (6 statuses · 4 derived states · A → B means B depends on A)    │
├──────────────────────────────────────────────────────────────────────────┤
│ Dependency outline (server-rendered; works without JavaScript)            │
│   T-000039 ✓ done  needs: none                                           │
│     T-000041 ▶ in progress  needs: T-000039   [claimed by agent-a]        │
└──────────────────────────────────────────────────────────────────────────┘
```

Canvas height `--graph-height` (62 vh, min 26 rem). The toolbar is one row at
large widths; it wraps at medium. The minimap sits 12 px from the bottom-right
corner with `--shadow-2` and a 1 px `--border-strong` edge. The outline below is
always rendered by the server and is not removed when JS succeeds — it collapses
behind a `<details>` ("Dependency outline") so it stays available but does not
duplicate the canvas visually. With JS off the `<details>` is `open`.

### 5.7 `/graph` — mobile (<640 px)

```
┌────────────────────────────┐
│ tackboard        ◐   ?     │
├────────────────────────────┤
│ [＋][−][⤢][100%] [⋯ more] │  ← compact toolbar, 44 px targets
├────────────────────────────┤
│ 41 nodes · 63 edges · 12 hidden │
├────────────────────────────┤
│                            │
│      (canvas, full width)  │
│                            │
│                    [▣]     │  ← minimap collapsed to a button
├────────────────────────────┤
│ ▸ Legend                   │
├────────────────────────────┤
│ ▸ Dependency outline       │
└────────────────────────────┘
```

The toolbar collapses to four icon buttons plus a "more" disclosure holding the
claimed and hide-closed toggles; the hidden count stays visible as text because
it is a state fact. Pinch zooms, one-finger drag pans, tap selects, double-tap
zooms. The minimap is collapsed by default below 900 px and opens as an overlay
sheet when the button is pressed.

### 5.8 Breakpoints and touch targets (summary)

| Width | Board | Graph | Task |
| --- | --- | --- | --- |
| ≥1200 px | 6 columns, horizontal scroll if needed | full toolbar, minimap expanded | 2 columns |
| 900–1200 px | 4–5 columns visible, scroll | toolbar wraps to two rows | 2 columns |
| 640–900 px | scroll-snap panels at 85 vw | compact toolbar, minimap collapsed | 1 column |
| <640 px | status tablist, one panel | compact toolbar, minimap button | 1 column |

Touch-target rule: every interactive element is at least 44×44 CSS px in
`@media (pointer: coarse)`; on fine pointers the visual box may be smaller but
the hit area is padded to 32 px minimum with `::before`/`padding`. Spacing
between adjacent targets is ≥8 px so a miss does not hit the neighbour. This is
WCAG SC 2.5.8 (Target Size, Minimum) and Apple HIG's 44 pt guidance.

---

## 6. Interaction specification

### 6.1 Polling, ETag and 304

- **Interval.** 5000 ms between successful polls (matches the current
  `AUTO_REFRESH_SECONDS`). One request in flight at a time; a new poll is
  scheduled only after the previous one settles.
- **Hidden tab.** `document.visibilityState === "hidden"` stops the timer. On
  `visibilitychange` to `visible`, poll immediately once, then resume the 5 s
  cadence. Rationale: a background tab that keeps polling burns battery and
  server time for data nobody sees; a tab that resumes stale is worse. This is
  the "pause when hidden" requirement, and it is not the same as the user's
  explicit pause.
- **Visible pause.** The pause control stops the timer and sets
  `data-paused="true"`; resume restarts with an immediate poll. Pause is
  per-session in memory (not persisted): a user who returns tomorrow should not
  find a silently frozen board.
- **ETag.** The JSON endpoints compute a strong validator from the canonical
  response body: `sha256(body)` truncated to 16 bytes, hex, wrapped in quotes.
  Store it on the response as `ETag`. `If-None-Match` is compared after
  stripping an optional `W/` prefix; a match returns **304 with no body** and
  the headers `ETag`, `Cache-Control: no-cache`, the CSP header and
  `X-Content-Type-Options: nosniff`. A 304 must never omit the security headers —
  the brief's "CSP and nosniff still on every response" includes 304s.
- **304 handling in the client.** A 304 is a successful poll: keep the DOM
  untouched, update the last-updated stamp, do not announce anything, do not
  reset filters/scroll/selection/pan/zoom.
- **Backoff.** Network error or 5xx → interval doubles to a 60 s ceiling; the
  stamp shows "retrying in Ns". After three consecutive failures auto-poll stops
  and an inline "Updates paused — Retry" control appears (polite announcement).
  Any success resets to 5 s. 401/403/404 are not retried; they show the error
  state.
- **No caching of the HTML.** The HTML pages stay `no-store`; only the JSON
  endpoints use ETag validators.

### 6.2 Diffing and FLIP

- **Keying.** Every diffable element carries a stable key in `data-task` (board)
  or `data-node`/`data-edge` (graph). The diff is a keyed reconciliation over
  the new JSON: for each key, update in place, create if new, remove if absent.
  No index-based diffing — an index-based diff loses selection and scroll.
- **Board.** Columns are fixed; only their card lists diff. For a card whose
  fields changed, update the text nodes in place and set `data-changed="true"`
  for one cycle (1200 ms fade, static attribute remains so forced-colors and
  assistive technology see it). For a card that moved columns, remove from the
  old list and insert into the new one at the correct rank, then FLIP.
- **FLIP.** Record `getBoundingClientRect()` for each moved/created element
  before the DOM change (one read pass), apply the change, record the after
  rect, compute `deltaX/deltaY/deltaWidth/deltaHeight`, set an inverse
  transform as a **Web Animations API** keyframe (`element.animate(...)`) from
  the inverse transform to `none`. Duration `--dur-layout`, easing
  `--ease-standard`, `fill: "none"`. Reduced motion skips FLIP entirely.
  **Do not write `element.style`** — CSP `style-src 'self'` blocks inline style
  attributes, so a FLIP implemented with inline styles silently does nothing in
  production. WAAPI keyframes are not style attributes and are the sanctioned
  path. If WAAPI is unavailable, fall back to a static changed-state class with
  no movement.
- **Removal.** Fade and collapse with a WAAPI opacity/height keyframe for
  `--dur-micro`, then remove from the DOM. During the exit the element is
  `aria-hidden="true"` and `inert` so it cannot be focused or announced.
- **No-op guard.** If the canonical JSON body hash equals the last hash, skip
  the whole diff. This is the cheapest and most effective guard against
  pointless layout work; it also makes the ETag path and the diff path agree.
- **Change highlight.** New cards get a 1.2 s background fade from
  `--diff-highlight` to transparent plus a 2 px inset outline in
  `--diff-outline`; changed cards get the same outline without the fade. The
  highlight is never the only signal: `data-changed` stays set and the live
  region announces the summary.
- **Count animation.** The number text updates immediately; the wrapper gets a
  240 ms opacity/scale bump (`1 → 1.06 → 1`). Never animate the digits
  themselves. Reduced motion: no bump.

### 6.3 Preservation across refresh

| State | Where it lives | Survives polling | Survives reload |
| --- | --- | --- | --- |
| Filters | URL query string (`history.replaceState`) | yes | yes |
| Scroll per column | `element.scrollTop` captured before diff, restored after | yes | no (browser may restore) |
| Window scroll | same capture/restore on the board container | yes | no |
| Selected card | module state keyed by task ID | yes, if the task still exists | no |
| Selected node | module state keyed by task ID | yes, if the node still exists | only via `?task=` in neighbourhood view |
| Graph pan/zoom | JS transform state | yes — never touched by a refresh | no (explicitly excluded from URL) |
| Theme | `localStorage` + attribute | yes | yes |
| Hide-closed | URL param + `localStorage` | yes | yes |
| Pause | module state | yes | no |

Rules: a refresh must never reset the transform or the scroll position; a
refresh must clear the selection only when the selected task no longer exists in
the new payload (and then announce "selection cleared"). The URL stays free of
transient viewport state; only `?task=` (neighbourhood) and `?hide_closed=1`
persist.

### 6.4 Board filters and count clicks

- Clicking a summary count applies exactly one filter dimension: `total` clears
  all filters; `eligible` sets `eligible=1`; `blocked` sets `blocked=1`;
  `claimed` sets a client-side `claimed=1` view filter (the API has no claimed
  filter — the design filters the already-fetched board payload by
  `claimed_by !== null`); a status count sets `status=<value>`.
- Filter semantics are AND across dimensions and OR within a dimension (two
  status chips = either status). This matches the server's current `status`/
  `priority`/`assignee`/`label`/`blocked`/`eligible` parameters; `claimed` is
  the one client-only dimension and must be documented in the UI as a view
  filter, not a server filter.
- Clicking an active count again clears that dimension. "Reset" clears all.
- Filter changes update the URL immediately (replaceState) so a copied URL
  reproduces the view; the back button therefore does not walk through every
  chip toggle (replaceState, not pushState — deliberate, to keep history clean).
- Filtering never hides a card without updating the count; the count is the
  filtered count and the summary always states "showing N of M" when any filter
  is active.

### 6.5 Task Markdown: DOM building and sanitisation

The renderer is a small in-house parser with a hard security rule: **task text
never becomes HTML**. It builds nodes with `document.createElement` and
`textContent`; `innerHTML`, `outerHTML`, `insertAdjacentHTML`,
`document.write`, `eval` and `new Function` are forbidden in the web assets and
should be grep-checked in a test.

Supported subset: paragraphs; `#`–`####` headings (rendered as `h2`–`h5`,
because `h1` belongs to the page title); `-`/`*`/`+` unordered lists and `1.`
ordered lists, nested to depth 3; fenced code blocks (```), with the info string
ignored; inline code; `**bold**` and `*italic*`; `[text](url)` links.

Link handling, in order:

1. Parse the destination. Accept only absolute `http:` and `https:` URLs
   (case-insensitive scheme). Reject everything else — `javascript:`, `data:`,
   `file:`, `mailto:`, relative paths, protocol-relative `//host` — and render
   the link text as plain text with the original destination shown after it in
   parentheses, so the reader still sees where it pointed.
2. Set `rel="noopener noreferrer"` on every accepted link.
3. Set `target="_blank"` and append a visually hidden " (opens in a new tab)" to
   the accessible name. Rationale: a read-only dashboard should not lose its
   filters and scroll when the operator follows a link.
4. If a URL contains credentials (`user:pass@`), strip them and keep the rest.
5. Truncate the displayed URL text at 80 characters with an ellipsis; the full
   href stays in the attribute and in `title`.

Everything else is text: no raw HTML pass-through, no entities beyond normal
escaping, no images (a remote image would violate the no-external-host rule and
a `data:` image would violate the CSP), no tables (not in the brief's subset),
no autolinks. Fenced code is `<pre><code>` with `white-space: pre` and
horizontal scroll; long lines do not wrap the page.

No-JS fallback: the server already renders `<pre class="description">` with
escaped text and `white-space: pre-wrap`. The JS renderer replaces that node
only after a successful build; on any parse error it leaves the `<pre>` in place
and sets a non-fatal `data-markdown="fallback"` attribute. The same renderer
runs for note bodies in the timeline, one instance per note, capped at the five
notes the API returns.

### 6.6 Live region

```html
<p class="sr-only" id="board-live" role="status" aria-live="polite"
   aria-atomic="true"></p>
```

- One polite region per page. It announces a short sentence only when the
  summary counts change materially: "Board updated: 41 total, 7 eligible, 3
  blocked, 2 claimed." A change of one card's title does not announce.
- Debounce 800 ms and never announce more than once per poll cycle. A 304
  announces nothing. A failed poll announces "Updates paused: connection lost."
- The region is `sr-only` (visually hidden but present) — never `display: none`,
  which would remove it from the accessibility tree.
- Status, claimed state and hidden count are all available as text in the
  outline and in the toolbar, not only in this region; the region is an
  addition, not the only channel.

### 6.7 Graph pointer navigation

All input goes through Pointer Events so mouse, pen and touch share one code
path. The canvas `<svg>` gets `touch-action: none` so the browser does not
interpret a drag as a page scroll; the rest of the page keeps normal scrolling.

**Panning.**

- `pointerdown` on the canvas (empty space or a node — nodes are not draggable)
  records the pointer ID, start point and start transform. Drag from a node pans
  the graph, as the brief requires.
- A **4 px threshold** in CSS pixels separates a click from a drag. Movement
  below the threshold leaves the click intact; crossing it marks the gesture as
  a pan and suppresses the subsequent `click` on the node. Measure with
  `Math.hypot(dx, dy)` against a squared threshold to avoid a square dead zone.
- Call `setPointerCapture(pointerId)` on the SVG so the drag continues when the
  pointer leaves the canvas or crosses nodes. Handle `pointercancel` and
  `lostpointercapture` by ending the drag cleanly.
- Cursor: `grab` when idle over the canvas, `grabbing` while panning. Set via
  `data-panning="true"` on the container; CSS owns the cursor.
- Optional inertia after release: velocity from the last two samples, decay over
  400 ms with `--ease-exit`, stopped by any new pointerdown. Disabled under
  reduced motion and when the gesture ended on a node click.

**Zooming.**

- **Zoom at the cursor.** Given cursor point `(cx, cy)` in canvas coordinates,
  scale `s`, translate `(tx, ty)`, and next scale `s' = clamp(s · k, 0.15, 4)`:
  `tx' = cx − (cx − tx) · (s'/s)` and `ty' = cy − (cy − ty) · (s'/s)`. The point
  under the cursor stays fixed. Never zoom toward the canvas centre.
- **Wheel.** A non-passive `wheel` listener on the canvas only; `preventDefault`
  only when the event target is inside the canvas. Normalise `deltaMode`:
  mode 0 = pixels, mode 1 = lines × 16, mode 2 = pages × canvas height. Then:
  - `ctrlKey` (pinch gesture on trackpads) → zoom with
    `k = exp(−normalisedDeltaY × 0.010)` (a stronger factor, because a pinch
    delta is small).
  - otherwise, `shiftKey` → pan horizontally by `−normalisedDeltaX`.
  - otherwise, if `deltaMode !== 0` or `|deltaY| ≥ 40` → zoom with
    `k = exp(−normalisedDeltaY × 0.0015)`; this is the mouse-wheel path.
  - otherwise (smooth, small deltas: a trackpad two-finger scroll) → pan by
    `(−deltaX, −deltaY)` in canvas coordinates. This is the "trackpad scroll
    pans" requirement, implemented by magnitude heuristic; the 40 px threshold
    is a documented constant so it can be tuned without redesign.
- **Clamp.** Scale 0.15–4.0. Panning is soft-clamped: compute the graph's
  bounding box in screen space; keep at least 120 px of it inside each edge of
  the canvas. The graph can never be lost entirely off-screen. A "Fit all" action
  always recovers.
- **Buttons and animation.** `+`, `−`, `Fit all`, `100%` are real buttons with
  44 px targets. Programmatic zoom/fit animates over `--dur-layout` (fit over
  `--dur-slow`) with `--ease-standard`; reduced motion applies instantly. `100%`
  resets scale to 1 centred on the selection, or on the graph centre when
  nothing is selected.

**Clicking and selecting.**

- Single click on a node selects it and highlights the full transitive
  prerequisite and dependent paths; everything else dims to 35 % opacity but
  stays in the DOM (never hidden). Click on empty canvas clears the selection.
- A click is only a click if the pointer moved less than 4 px; a drag that starts
  on a node selects nothing.
- Double-click a node: centre and zoom to it (`scale = clamp(1.4, 0.15, 4)`),
  animated; double-click empty canvas: zoom in at that point by 1.6×. The second
  click of a double-click must not also toggle selection — suppress the single
  click when a double-click is detected (a 250 ms window is too slow; use the
  `dblclick` event and cancel the pending single-click timer).
- The selected node opens a **selection panel** (below the canvas, above the
  outline) with ID, title, status, priority, assignee, claim, blocked reason and
  a clearly visible "Open task page" link. A plain click never navigates; the
  link navigates. Middle-click and Ctrl/Cmd-click on that link must open a new
  tab — so it is a real `<a href="/task/…">`, not a JS handler.
- Hovering an edge highlights it and its two endpoint nodes. The edge hit area
  is 14 px wide (invisible stroke) so it is easy to hit; the visible line stays
  1.5 px.

**Hover intent and tooltip stability.**

- Node hover uses a ~100 ms intent delay before the tooltip appears; leaving the
  node cancels the timer. The node's pointer hit area is padded (transparent
  stroke or a `<rect>` larger than the shape) so moving between the shape edge
  and its label does not flicker.
- The tooltip follows the pointer but is clamped to the canvas bounds with an
  8 px margin. Placement: right of the pointer, 14 px offset; flip left if it
  would overflow; shift vertically to fit. `pointer-events: none` so it never
  blocks the node underneath. It hides on pointerleave, on pan start, on
  selection change and on `Esc`.
- Tooltip content is the same sentence as the node's `aria-label`; the visual
  layout is a title row, a status/priority row and up to three meta rows.

**Performance and level of detail.**

- All geometry lives under **one** `<g class="viewport">` whose `transform`
  attribute is written at most once per animation frame. Keep the transform in
  JS state; set a `dirty` flag on pointer/wheel/keyboard input and flush in
  `requestAnimationFrame`. Never read layout (offsetWidth, getBoundingClientRect)
  inside the pointermove handler — read once at gesture start.
- Node and edge elements are created once per data payload into a
  `DocumentFragment` and appended in one operation. The diff (§6.2) updates
  attributes and text only; it never recreates the `<g>` wrappers unless the set
  of nodes changed.
- Level of detail by scale: `s ≥ 0.8` show ID, title and status text;
  `0.45 ≤ s < 0.8` hide the title, keep ID and status; `s < 0.45` hide all text,
  keep shapes (the shape is the status). The hit area stays full-size at every
  level. LOD is applied by a single class on the viewport
  (`data-lod="full|compact|shape"`), so it is one attribute write, not a loop
  over hundreds of nodes.
- At several hundred nodes the layout is computed once per payload on the main
  thread; the longest-path layering is O(V·E) in the worst case. If the payload
  exceeds 500 nodes, defer the layout to an idle callback and keep the previous
  frame visible; document the threshold as a constant.
- `content-visibility: auto` is applied to the outline list (off-screen rows),
  not to the SVG.

### 6.8 Path highlighting

- Selection or hover computes the transitive closure over two adjacency maps
  (prerequisites and dependents) with a BFS. Nodes in the closure get
  `data-path="true"`; everything else gets `data-dimmed="true"` (35 % opacity,
  not hidden). Edges whose both endpoints are in the closure and whose direction
  lies on the path get `data-path="true"`.
- The closure is capped at 200 nodes; beyond that the design dims nothing and
  shows "Path too large to highlight" rather than a misleading partial path.
- Direction is stated in words in the legend and in the selection panel:
  "Prerequisites flow left to right. A → B means B depends on A."
- Hover highlighting is transient (cleared on pointerleave); selection
  highlighting persists until the selection changes or `Esc` clears it. Hovering
  a different node while one is selected shows the hover path *in addition*, with
  hover at a lighter weight; the selected path stays the strongest.

### 6.9 Minimap

- The minimap renders the same visible subgraph as the canvas at a scale that
  fits its box (200×140 max). It uses the same status shapes and the same claimed
  rings; hidden nodes are absent and the label states the hidden count.
- The viewport rectangle is the canvas's visible region mapped into minimap
  coordinates. Dragging it pans the canvas live (same pointer capture rules, no
  4 px threshold needed — any movement pans). Clicking elsewhere in the minimap
  recentres the canvas on that point, animated over `--dur-layout`.
- The rectangle is `aria-hidden`; the keyboard path is "Fit all" plus arrow-key
  panning. Collapsing the minimap leaves a 44 px button that re-expands it and
  remembers the collapsed state in `localStorage` (`tackboard.minimap`,
  guarded); the default is collapsed below 900 px and expanded above.
- The minimap must not intercept page scroll: `touch-action: none` on the
  minimap only, and its wheel events do not zoom the main canvas (they are
  ignored).

### 6.10 Stable viewport across refresh and theme change

- The transform lives in module state and is never recomputed from the payload.
  A poll that changes node positions does **not** move the camera; it only
  redraws geometry under the same transform. If the layout changes materially,
  the design keeps the camera and lets the graph shift beneath it — that is the
  lesser surprise, and "Fit all" is one click away.
- A theme change touches CSS custom properties only. No JS re-render, no
  relayout, no transform write.
- A resize recomputes the fit only when the user has not manually panned or
  zoomed; once the user owns the camera, resizes preserve scale and clamp the
  translate to keep 120 px on-screen.

### 6.11 Keyboard help and shortcuts

See §8 for the table. The overlay opens on `?`, from the top-bar button, and
from the "Keyboard shortcuts" link in the footer. It is a modal dialog; while it
is open only `Esc` and Tab are handled. Closing restores focus to the opener.

---

## 7. Claimed highlight and hide-closed design

### 7.1 Claimed: the visual treatment

Claim is an **overlay layer**, never a status. A claimed node renders:

1. The status shape, unchanged (circle, diamond, hexagon, square, octagon,
   dashed rectangle).
2. A **double ring** outside it: an outer rounded rect 4 px outside the shape
   with a 2 px `--state-claimed-line` stroke and a 2 px gap, so the ring reads as
   a ring even in monochrome. On a diamond/hexagon node the ring follows the
   bounding box, not the polygon — a rectangle ring is unambiguous and cheap to
   draw.
3. A **holder badge**: a small rounded rect at the node's bottom-right with the
   agent ID truncated to 12 characters plus an ellipsis; full ID in the badge's
   `<title>` and in the node's `aria-label`. Optional claim age appended after a
   middle dot ("@agent-a · 12m").
4. A **legend entry**: "claimed — double ring + holder badge".

The board card shows the same badge in its meta row and the same words in the
tooltip. The task page shows a "claimed by agent-x" chip plus the age. The
server-rendered outline appends `[claimed by agent-x]` to the line.

When "Highlight claimed" is **on** (default), the ring and badge are drawn and
the legend row is active. When **off**, the ring and badge are not drawn, but the
fact remains available: the node's `aria-label`, the `<title>`, the tooltip and
the outline text all still say who holds it. Turning the highlight off must
never remove the information — only the emphasis. This is the "reversible, never
hides" rule applied to a visual layer.

"Focus claimed" is a sub-mode, enabled only while "Highlight claimed" is on:
non-claimed nodes and edges drop to 35 % opacity (dimmed, still present and still
focusable), claimed nodes keep full contrast. A polite announcement states "3 of
41 tasks are claimed." Turning it off restores full opacity. It never hides
anything and never changes the selection.

Keyboard: `c` toggles "Highlight claimed"; the sub-mode has no default shortcut
(available from the toolbar) so the shortcut table stays as the brief specifies.

### 7.2 Claimed: the data source decision

**Decision: extend the shared service-layer graph node projection with additive,
optional `claimed_by` and `claimed_at`; keep `schema_version: 1`.**

What exists today (verified in the repository at the baseline commit):

- `views.graph_document` already adds five fields on top of the CLI nodes —
  `dependency_ready`, `eligible`, `reviewable`, `blocked`, `claimed_by` — and
  `tests/test_web.py` asserts exactly that set. So `/api/graph` already carries
  `claimed_by`; it does **not** carry `claimed_at`.
- `dependencies._graph_data` (the shared projection behind
  `tack graph --json` and `/api/graph`) emits only `id`, `title`, `status`,
  `priority`, `assignee` per node. So `tack graph --json` currently has **no**
  claim data at all, and the brief's "keep the CLI and the API equivalent" is not
  true for claims today.
- `service.project_tasks_by_id` already returns `claimed_by` and `claimed_at`
  for each task, so the values exist one layer up; the graph projection simply
  drops them.

Trade-off:

| Option | Cost | Benefit | Verdict |
| --- | --- | --- | --- |
| Add `claimed_by`/`claimed_at` to `_graph_data` nodes | one function, one test set, CHANGELOG/README note; additive only | CLI JSON and API stay equivalent; one request; no client merge; claim age available without a second call | **chosen** |
| Merge claim data client-side from `/api/board` | second request per poll; board and graph snapshots can disagree by one poll; age computed client-side; CLI still lacks the data | no production Python change | rejected |

Implementation shape for the later legs (no code here, per §10's rule):

- In `dependencies._graph_data`, add `"claimed_by"` and `"claimed_at"` to each
  node dict. The values come from the same per-task claim lookup the module
  already performs in `derived_flags`; a single `_claims_for`-style batch query
  keeps it to one extra query per payload.
- In `views.graph_document`, stop re-merging `claimed_by` from the projection
  (it is now on the node) and pass `claimed_at` through; keep the other four
  derived flags where they are. Update the field-set assertion in
  `tests/test_web.py` to include `claimed_at`.
- Keep `schema_version: 1`. Do not remove or rename any field. Document the two
  new optional fields in `README.md` (JSON output section and Web view section)
  and in `CHANGELOG.md` under "Added". Add a test that `tack graph --json` and
  `/api/graph` agree on the claim fields for the same database.
- Optional-field discipline: consumers must treat `claimed_by`/`claimed_at` as
  possibly absent or null; the design does too (`data-claimed="false"` when
  absent).

Why not the client-side merge: the brief's own preference order puts the shared
projection first, and the equivalence requirement is explicit. A client merge
also doubles the polling traffic and lets the board and graph disagree about who
holds a task, which is exactly the kind of quiet inconsistency this view exists
to prevent.

### 7.3 Hide done and cancelled

**Toggle.** A toolbar button "Hide done & cancelled", default **off**, with
`aria-pressed`. The pressed state is the meaning; the label does not swap. The
brief's exact wording is used as the visible label so it matches the
documentation.

**Behaviour when on.**

1. Remove every node whose `status` is `done` or `cancelled`, and every edge
   whose `source` or `target` is one of those nodes. Removal is from the SVG and
   from the in-memory payload view — not from the JSON source of truth.
2. Re-run the same longest-path layering over the remaining subgraph so there
   are no empty columns or gaps. Then translate the new layout so the viewport
   stays anchored: if the selected node is still visible, its screen position is
   preserved; otherwise the graph coordinate at the viewport centre is
   preserved. Scale is never changed by the toggle.
3. Show the hidden count as text next to the toggle and in the minimap label:
   "12 hidden" (nodes hidden, plus "and their edges" in the tooltip).
4. For each remaining visible node, count its hidden direct prerequisites and
   hidden direct dependents; render a `+N` marker at the node's bottom-right
   (and include "3 hidden links" in the `aria-label` and the tooltip). The marker
   is a glyph plus text in the label — not colour-only.
5. If the selected node becomes hidden, clear the selection and announce
   "Selection cleared: T-000044 is hidden."
6. The minimap draws only visible nodes and edges; the neighbourhood view
   (`?task=`) applies the same filter to its already-narrowed subgraph.
7. **Derived semantics do not change.** `blocked`, `eligible`, `reviewable` and
   `dependency_ready` are computed from the **full** graph by the server and are
   carried on each node; the client filters for display and never recomputes
   those flags from the filtered set. A task blocked by a hidden cancelled
   prerequisite stays blocked and still shows the `⊘` marker and the reason text.
   This is the single most important rule in this section.

**Persistence.**

- URL: `?hide_closed=1` (and `?hide_closed=0` to force off). The server honours
  it when rendering the outline, so the no-JS path works.
- JS: toggling updates the URL with `history.replaceState` (never `pushState`),
  preserving `?task=` when present. The URL is shareable and reload-safe.
- `localStorage` key `tackboard.hideClosed`, read/written in try/catch; the URL
  parameter wins when present (even `hide_closed=0`).
- The state survives polling because it is module state plus the URL, not a
  DOM-derived value; the diff never resets it.

**Server-side outline.** `_graph_page` reads `hide_closed` from the query, filters
the document's nodes and edges for the outline only, and renders
`nodes=X of Y · edges=A of B` so the truncation is visible. It also appends
`[claimed by agent-x]` to claimed lines. The JSON endpoints keep returning the
full graph; the client filters. This split is deliberate: the server's job is
the no-JS outline, the client's job is the interactive canvas, and the JSON stays
one canonical payload that both can reason about.

### 7.4 Interaction between the two features

- Hide-closed and highlight-claimed compose: a hidden claimed node is simply
  absent; the claimed count in the summary bar stays a **full-board** count and
  is labelled "claimed" without a "shown" qualifier, while the graph toolbar's
  hidden count is explicitly "12 hidden".
- Focus-claimed with hide-closed on dims only the visible non-claimed nodes.
- Both toggles are reversible and never mutate the payload.

---

## 8. Keyboard and accessibility

### 8.1 Shortcut table (binding)

Shortcuts are ignored while an `<input>`, `<textarea>`, `<select>`, or
`contenteditable` element has focus. The handler checks
`event.target.closest("input, textarea, select, [contenteditable]")` first.
Shortcuts are also suspended while the help dialog or any modal is open, except
`Esc`.

| Key | Action | Scope |
| --- | --- | --- |
| `g` then `b` | Go to board | global |
| `g` then `g` | Go to graph | global |
| `/` | Focus the search/filter input | board |
| `f` | Open the filters disclosure and focus its first control | board |
| `j` / `k` | Next / previous card; moves the selection cursor and scrolls it into view | board |
| `Enter` | Open the focused card, or the selected node's task page | board, graph |
| `t` | Cycle theme (auto → light → dark) | global |
| `c` | Toggle "Highlight claimed" | graph |
| `h` | Toggle "Hide done & cancelled" | graph |
| `+` / `-` / `0` | Zoom in / out / fit all | graph |
| Arrow keys | Pan the graph; with a node active, move along edges (left = prerequisite, right = dependent, up/down = sibling in the same layer) | graph |
| `?` | Show the shortcut help overlay | global |
| `Esc` | Close the overlay / clear the selection / close the tooltip | global |

The two-key `g` sequences use a 1000 ms window; a second key outside the window
starts a new sequence. The first key does not trigger an action on its own, so
typing "g" in a text field is unaffected (the guard runs first anyway).

### 8.2 Canvas focus, role and name

```html
<svg class="graph-canvas__svg" id="graph-svg" role="group"
     aria-roledescription="dependency graph"
     aria-label="Dependency graph: 41 tasks, 63 links. Press question mark for shortcuts."
     aria-activedescendant="" tabindex="0">
```

- `role="group"` + `aria-roledescription` names the widget without claiming
  `application` semantics (which would swallow screen-reader shortcuts). This is
  the APG composite-widget guidance.
- The SVG has one tab stop. Inside it, the active node is tracked with
  `aria-activedescendant="node-T-000041"`; each node `<g>` has a stable `id`,
  `role="link"` and an `aria-label` sentence. Arrow keys move the active
  descendant and scroll it into view; `Enter` opens its task page; `Esc` clears
  the selection and the active descendant.
- The visible focus ring is drawn on the **node**, not only on the SVG:
  `[data-active="true"] .node__shape { stroke-width: 3; outline: none; }` plus a
  CSS `outline: 2px solid var(--border-focus); outline-offset: 3px` on the SVG
  itself for the "canvas is focused" state. Both are visible against both themes
  (focus ring measured ≥5.5:1). SC 2.4.7 (*Focus Visible*) and 1.4.11.
- A visually hidden live sentence describes the canvas state on focus: "Graph
  focused. 41 tasks. Arrow keys pan or move along links."

### 8.3 Keyboard reachability parity

Every mouse action has a keyboard equivalent:

| Mouse action | Keyboard equivalent |
| --- | --- |
| Pan by dragging | Arrow keys |
| Zoom at cursor | `+` / `-`; zoom at the active node's centre |
| Fit all | `0` |
| Select a node | Arrow keys to move the active node; `Enter` to select |
| Open task page | `Enter` on the active node (opens), or the selection panel link |
| Clear selection | `Esc` |
| Hover a node for the tooltip | Focus the node; the tooltip appears after the same 100 ms intent delay |
| Hover an edge | Move the active node; its incident edges highlight |
| Toggle claimed highlight | `c` |
| Toggle hide-closed | `h` |
| Minimap drag/click | `0` (fit all) + arrow pan; the minimap is a redundant affordance |
| Double-click to zoom | `+` at the active node |

The selection panel's "Open task page" is a real link, so Enter activates it and
middle/Ctrl-click opens a new tab without any JS.

### 8.4 Logical tab order

Per page: skip link → top bar brand → view nav → identity (not focusable) →
theme toggle → summary counts → filter chips → add-filter summary → board
columns' cards in visual order → refresh controls → footer links. On the graph:
skip link → top bar → toolbar buttons → canvas (one stop) → selection panel link
→ legend summary → outline summary. The skip link jumps to `#main-content`.
No positive `tabindex` anywhere; order is DOM order. The `sr-only` skip link is
the first focusable element on every page.

### 8.5 Accessibility targets and how they are met

- **WCAG 2.2 AA, both themes.** Every text pair in §1.4 passes 4.5:1 and every
  UI boundary passes 3:1. The tightest pair (cancelled, 4.62) is backed by shape
  and glyph.
- **Colour never alone.** §3's six shapes, six glyphs and six words; §3.3's four
  derived-state cues; the claim ring and badge; the hidden `+N` marker; the
  changed-card outline. A greyscale screenshot still carries the state.
- **Status, claimed state and hidden count as text.** In the card chip, the node
  `aria-label`, the `<title>`, the tooltip, the legend, the toolbar count and the
  server outline. The outline is the no-JS guarantee.
- **Focus visible.** Two-layer ring (`--focus-ring`) on every focusable element,
  measured ≥5.5:1 against both surfaces; never `outline: none` without a
  replacement.
- **Keyboard operability.** §8.1–8.3; no mouse-only action.
- **Live region.** One polite region, debounced, summary-only (§6.6).
- **Reduced motion.** SC 2.3.3; all durations collapse, FLIP and inertia are
  disabled, and state changes remain perceivable statically.
- **Zoom and reflow.** Text scales to 200 % without loss of content (rem-based
  type, no fixed-height text containers); at 400 % zoom the layout reflows to the
  single-column/mobile breakpoint. SC 1.4.4 and 1.4.10.
- **Target size.** 44×44 CSS px on coarse pointers; 24×24 minimum everywhere
  (SC 2.5.8).
- **Name, role, value.** Native elements first; ARIA only where native semantics
  do not exist (the SVG composite and the toggles' pressed state).
- **No keyboard trap.** The help dialog traps focus while open and releases it on
  close; the canvas releases Tab to the next control.
- **Consistent help.** The `?` overlay and the footer link are the same dialog.

---

## 9. Responsive behaviour

### 9.1 Board

- **≥1200 px:** six columns side by side, horizontal scroll when the total width
  exceeds the viewport (`scroll-snap-type: x proximity`).
- **640–1200 px:** columns remain columns but narrower (14–15 rem) and the board
  is a snap scroller; the summary and filter rows scroll horizontally as one
  strip so they never wrap into three lines.
- **<640 px:** status **tabs**. `role="tablist"` with six tabs, arrow-key
  navigation, `aria-selected`, one `role="tabpanel"` showing that status's
  cards. The tab strip scrolls horizontally if labels do not fit. Tab labels use
  the glyph plus the short word ("backlog", "ready", "in prog.", "review",
  "done", "cancelled"). The tab panel keeps its own scroll position per tab so
  switching back does not lose place.

### 9.2 Graph

- Touch pan with one pointer; pinch zoom with two pointers tracked by pointer ID
  (distance ratio → scale factor; midpoint → anchor). A tap (<4 px, <250 ms)
  selects; a double-tap zooms at the tap point. Two-finger tracking must ignore a
  third pointer and must not jump when one finger lifts (recompute the baseline
  distance from the remaining pair).
- The minimap is collapsed by default below 900 px and opens as an overlay sheet;
  above 900 px it is expanded and remembered in storage.
- The toolbar becomes a compact bar: four icon buttons (zoom in, zoom out, fit,
  100 %) plus a "more" disclosure for the claimed and hide-closed toggles and
  the neighbourhood form. Every control is ≥44 px.
- The canvas fills the width and `--graph-height` (62 vh) but never less than
  26 rem; the outline sits below and is collapsible.

### 9.3 Task page

- Single column below 1200 px. Order: title, readiness, facts, blockers,
  prerequisites, dependents, description, timeline. Facts first because an
  operator on a phone is usually checking "why is this stuck".
- The facts grid collapses from two columns to stacked term/value pairs; long
  values wrap and never truncate without a `title`.
- The timeline spine stays; timestamps shorten to "24 Oct 09:37" and the full ISO
  value stays in `<time datetime>`.

### 9.4 Touch targets and spacing

- 44×44 CSS px minimum for buttons, chips, tabs, toolbar controls and the
  minimap handle in `@media (pointer: coarse)`.
- 8 px minimum between adjacent targets.
- Cards remain full-width tap targets; the ID link is the only navigation target
  and is padded to 44 px vertically by the card head row.
- Hover-only affordances are duplicated on touch: the tooltip appears on tap and
  is dismissed by tapping elsewhere or `Esc` (a visible close affordance is not
  needed because tap-away is standard, but the tooltip must not block the tap
  target).

---

## 10. Implementation mapping and risks

### 10.1 Mapping brief section → production area (no code)

| Design area | Likely production file/module | Test area |
| --- | --- | --- |
| Tokens, theme block, component CSS | `src/tackboard/static/app.css` (replaced wholesale) | CSS is not unit-tested; add a smoke check for forbidden constructs (§10.2) |
| Theme bootstrap and toggle | new `src/tackboard/static/theme.js`; `templates/base.html` (`<head>` script, toggle markup) | `tests/test_web.py` header/markup assertions; a new no-inline check |
| Shell, top bar, identity | `templates/base.html`, `web.py::_shell` | `tests/test_web.py` (project name + database path always present) |
| Summary bar and filters | `web.py::_board_page`, `_counts_line`, `_filter_form`; `templates/board.html`, `filters.html`, new `summary.html` | `tests/test_web.py` board page and filter tests |
| Board cards and columns | `web.py::_card`, `_board_columns`; `templates/card.html`, `column.html`, `show_all.html` | `tests/test_web.py` card/column and terminal-limit tests |
| Task header, facts, blockers | `web.py::_task_page`; `templates/task.html`, new partials | `tests/test_web.py` task page tests |
| Prereq/dependent links | `web.py::_task_page`; `templates/task.html` | `tests/test_web.py` |
| Notes/history timeline | `web.py::_task_page`; `templates/task.html`, `note.html`, `history.html` | `tests/test_web.py` |
| Markdown renderer | new `src/tackboard/static/markdown.js`; `templates/task.html` (`<pre>` fallback stays) | new JS-behaviour tests are out of scope for pytest; the no-`innerHTML` grep check covers the security rule |
| Polling, ETag/304, diff/FLIP | `web.py` API handlers (`_json_response`, new ETag branch); `views.py` (canonical body); new `static/board.js`; replace `static/refresh.js` | `tests/test_web.py` (ETag/304 headers, 405, CSP/nosniff on 304) |
| Live region | `templates/base.html`, `static/board.js` | `tests/test_web.py` markup assertions |
| Graph renderer, pointer nav, LOD | `src/tackboard/static/graph.js` (rewritten); `templates/graph.html` | manual/headless checks per the brief's acceptance list |
| Graph toolbar, legend, minimap | `templates/graph.html`; `graph.js`; `app.css` | `tests/test_web.py` legend/markup assertions |
| Claimed treatment | `graph.js`, `app.css`, `templates/graph.html` (legend); `web.py::_dependency_list` (outline text) | `tests/test_web.py` claimed-in-outline and legend tests |
| Claim data source | `dependencies.py::_graph_data` (+ `claimed_by`/`claimed_at`); `views.py::graph_document`; `README.md`, `CHANGELOG.md` | `tests/test_dependencies.py` graph JSON; `tests/test_web.py` field-set assertion; a CLI/API equivalence test |
| Hide-closed toggle | `web.py::_graph_page` (`hide_closed` query, outline filtering, counts); `graph.js`; `graph.html` | `tests/test_web.py` (`?hide_closed=1` removes nodes/edges from outline; blocked flags preserved; hidden count) |
| Keyboard shortcuts and help | new `src/tackboard/static/keys.js`; `templates/base.html` (dialog) | manual/headless; markup assertions for the dialog |
| Responsive/touch | `app.css` media queries; `board.js` tab handling | manual/headless at 375/768/1280 widths |
| Header discipline | `web.py` response builder (`CONTENT_SECURITY_POLICY`, nosniff, ETag on 304) | `tests/test_web.py` header tests on every route including static assets |

### 10.2 Risks and mitigations

1. **Additive-field compatibility.** `claimed_by`/`claimed_at` on graph nodes are
   additive and optional; older consumers ignore unknown keys. Risk: a consumer
   that asserts an exact key set breaks. Mitigation: update the one assertion in
   `tests/test_web.py` that enumerates added fields; document the fields as
   optional; keep `schema_version: 1`.
2. **ETag/304 header discipline.** A 304 that drops the CSP or nosniff header is
   a security regression. Mitigation: build every response through one helper
   that always sets `X-Content-Type-Options: nosniff` and the CSP; test a 304
   explicitly. Also keep `Cache-Control: no-cache` so a stale 304 is not served
   from a proxy without revalidation.
3. **FLIP/diff complexity.** A keyed diff plus FLIP is the largest new JS
   surface. Risks: losing scroll/selection, animating a no-op, jank on large
   boards. Mitigations: the body-hash no-op guard; keyed reconciliation only;
   FLIP only for elements whose rect actually changed; one read pass and one
   write pass per frame; cap animations to moved/created elements; disable under
   reduced motion. If the diff proves too costly, the fallback is to update in
   place without FLIP — the design's non-animated cues still carry the change.
4. **Pointer-event edge cases.** Lost capture, `pointercancel`, multi-touch
   baseline jumps, click-after-drag, and pen barrel buttons. Mitigations: one
   gesture state machine with explicit start/move/end/cancel; capture on the
   canvas; ignore extra pointers during pinch; suppress click when the threshold
   was crossed; test with a trackpad and a touch device, not only a mouse.
5. **No-JS outline parity.** The outline must keep working and must honour
   `hide_closed=1`. Risk: the new template drops the outline or filters it
   inconsistently with the canvas. Mitigation: keep `_dependency_list` as the
   server path, filter nodes/edges before rendering, keep the flags from the full
   document, and test with JS disabled.
6. **Performance at several hundred nodes.** Layout is O(V·E) in the worst case;
   per-node attribute writes can add up. Mitigations: LOD via one viewport class;
   one transform write per frame; `DocumentFragment` insertion; defer layout
   above 500 nodes to an idle callback; avoid `getBoundingClientRect` in
   pointermove.
7. **Markdown security.** The renderer is the highest-risk new code. Mitigation:
   `createElement`/`textContent` only; a grep check in the test suite for
   `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `eval`, `new Function`,
   `document.write` in `static/`; scheme allow-list; `rel` on every external
   link; no raw HTML.
8. **Theme flash.** A wrong-order script or a `defer` reintroduces the flash.
   Mitigation: synchronous external script before the stylesheet; server renders
   `data-theme="auto"`; no inline script; test by hard-reloading in dark mode.
9. **Claimed vs in-progress conflation.** The most likely design regression in
   implementation. Mitigation: the claim ring is drawn outside the status shape,
   the two facts come from different fields, and the legend states both; review
   against §3.3 and §7.1.
10. **Hidden-count correctness.** Counting hidden nodes is easy; counting hidden
    *edges* and the per-node `+N` elided links is where bugs live. Mitigation:
    compute the hidden set once per payload and derive all counts from it; test
    the "blocked by a hidden cancelled prerequisite" case explicitly.

### 10.3 Recommended deferrals

- **SSE/WebSocket push.** Out of scope per the brief; polling with ETag is
  enough for a read-only view. Defer until the board is large enough that 5 s
  polling is measurably expensive.
- **Server-side SVG rendering.** The no-JS outline already covers the fallback;
  a server-rendered SVG would duplicate layout logic. Defer.
- **Node dragging / manual layout.** A DAG view should keep a deterministic
  layout; free positioning adds a persistence problem with no operator benefit.
  Defer.
- **Markdown tables, images and raw HTML.** Not in the brief's subset and each
  widens the sanitiser's attack surface. Defer with a written rationale.
- **Cross-board links and multi-project views.** One board per database is an
  architecture principle; a multi-board view would break the identity contract.
  Defer.
- **Persisting pan/zoom in the URL.** Explicitly excluded by the brief and noisy
  in history; keep the camera in memory only.
- **Client-side claim merge from `/api/board`.** Rejected in §7.2; revisit only
  if the shared projection cannot be extended.

### 10.4 Definition of done for the implementation legs

The four legs are done when: all new CSS/JS is external with no inline
script/style/`style=` attribute; every response (including 304 and static
assets) carries CSP `default-src 'self'` and `nosniff`; the no-JS outline renders
the full and hidden-closed views correctly; `?hide_closed=1` removes the right
nodes and edges while blocked flags still come from the full graph; claimed
nodes carry the double ring and holder badge and the outline prints the holder;
all 50 contrast pairs still pass; keyboard reaches every mouse action; the help
dialog opens on `?` and closes on `Esc`; and the pytest suite stays warn-clean
with the updated field-set assertion.

---

## 11. Owner decisions (locked 2026-10-24)

These two display/persistence questions were open at the end of the design leg.
The owner has decided; implementation legs must treat them as settled.

1. **Claim age display — relative age in the badge, full timestamp in the
   tooltip.** A claimed card or node shows the holder's agent ID plus a compact
   relative age (for example `12m`) in the badge title. The full ISO-8601
   `claimed_at` value appears in the tooltip and in the `<title>` element. This
   keeps dense surfaces readable while leaving the exact timestamp one hover
   away. The server-rendered outline prints `[claimed by <agent> since
   <timestamp>]` so the information survives without JavaScript.
2. **Hide-closed persistence — the URL query wins over `localStorage`; default
   off.** `?hide_closed=1` in the request URL is authoritative when present, in
   both the JavaScript canvas and the server-rendered outline. With no query
   parameter, the guarded `localStorage` preference applies; with neither, the
   default is "show everything". The JavaScript toggle writes the same query
   parameter with `history.replaceState` so the state stays shareable and
   reload-safe. A storage failure degrades to the default without an error.
