# Design system — the Source Ledger UI

The Serve layer's design spec. Every token below is a CSS custom property declared in
the `:root` block of [`app/templates/index.html`](../../app/templates/index.html),
which is the single source of truth — this document describes it, it does not define
it.

## The idea

**Two surfaces, deliberately different.** The page is split into a bright application
shell where you *do* things and a dark terminal panel where you *read* what happened.
The contrast is the design: input is warm and inviting, output is a machine record.

```
┌─────────────────────────────────────────┐
│  ▣ Source Ledger               [chips]    │   app shell
│  ┌───────────────────────────────────┐  │   light, orange accent
│  │  New search                       │  │
│  │  [ textarea                     ] │  │
│  │  Search with (•DDG)(Yahoo)(…)     │  │   ← one fill-in-the-blank sentence
│  │  for up to [10] URLs from [any…]  │  │
│  │                        [ Search ] │  │
│  └───────────────────────────────────┘  │
├─────────────────────────────────────────┤
│  saved_searches  [Dedupe][Store][Clear] │   terminal panel
│  # │ Question │ URLs │ Time │ Engine…   │   dark, green monospace
└─────────────────────────────────────────┘
```

## Tokens

### App shell

| Token | Value | Use |
|---|---|---|
| `--bg` | `#f3f4f7` | Page background, under two soft radial gradients |
| `--surface` | `#ffffff` | Cards, chips |
| `--surface-2` | `#f7f8fa` | Select options |
| `--line` | `#dfe3eb` | Borders |
| `--line-soft` | `#e9ecf1` | Subdued dividers |
| `--text` | `#1d2433` | Body text |
| `--muted` | `#5d6678` | Labels, sentence connectives, secondary text |
| `--accent` | `#f5821f` | The brand orange — buttons, active toggles, focus |
| `--accent-strong` | `#ff9838` | Gradient top end |
| `--accent-deep` | `#c1650e` | Active toggle text — darkened for contrast on a tint |
| `--accent-ink` | `#1d1206` | Text **on** orange. Near-black, never white. |
| `--focus` | `#e8940f` | Focus rings |
| `--ok` / `--err` | `#178a4c` / `#d84a2a` | Status messages |

### Terminal panel

| Token | Value | Use |
|---|---|---|
| `--term-bg` | `#0d120c` | Panel background — near-black with a green cast |
| `--term-bg-2` | `#121a10` | Panel header |
| `--term-line` | `#223318` | Panel borders |
| `--term-green` | `#5ee98a` | Primary text, links |
| `--term-green-dim` | `#559d6c` | Column headers, metadata, secondary text |
| `--term-amber` | `#e8c766` | Row IDs and link hover — the one warm note |

## Type

| Context | Family | Why |
|---|---|---|
| App shell | `"Segoe UI", system-ui, -apple-system, sans-serif` | Native, no webfont, no layout shift |
| Terminal panel | `"Consolas", "SF Mono", monospace` | URLs are strings to compare, not prose to read |

`line-height: 1.5`. Field labels are `0.75rem`, uppercase, `letter-spacing: 1.2px`.

## Patterns worth reusing

**Options as a sentence.** The search form is not a stack of labelled fields — it is
one line of prose with the controls embedded in it:

> Search with **(DuckDuckGo)(Yahoo)** for up to **[10]** URLs from **[any time]** in
> **[Worldwide]** with **[moderate]** safe search.

Selects and number inputs are transparent, bold, with a bottom border only, so they
read as filled-in blanks. It fits five options into one line that a stakeholder
understands without a legend.

**Toggles without JavaScript.** Engine pills are `<input type="checkbox">` visually
hidden (`position: absolute; opacity: 0`) with a styled `<label for>` beside them,
driven by `input:checked + label`. Keyboard and screen-reader behaviour is the native
checkbox's; `input:focus-visible + label` restores the focus ring the hidden input
would otherwise take with it.

**Radial gradients, not flat colour.** Two large, low-opacity radials (orange top
left, green top right) over `--bg`. Enough to stop the page reading as a form on
white, subtle enough not to compete.

**Glow as hierarchy.** Terminal text carries a faint `text-shadow` in its own colour —
stronger on the panel heading, barely there on rows. It signals "machine output"
without reducing legibility.

## Rules

- **Never white text on `--accent`.** Orange is a light colour; text on it is
  `--accent-ink` (`#1d1206`). This is the token most likely to be got wrong.
- **The terminal palette stays in the terminal panel.** Green on dark means *stored
  record*. Using it in the shell breaks the one distinction the design makes.
- **Amber is punctuation.** Row IDs and link hover only. It stops being an accent the
  moment it is used for a third thing.
- **Every interactive element gets `:focus-visible`.** There is no JavaScript to
  rescue a broken tab order.
- **`target="_blank"` always pairs with `rel="noopener noreferrer"`.** Asserted in
  [`tests/test_server.py`](../../tests/test_server.py).
- **Autoescaping is a security control, not a formatting detail.** Result URLs and
  queries are user-influenced. `{{ }}`, never `| safe`, on anything from the database.

## Changing it

Edit the `:root` block in `app/templates/index.html` and update the tables here. There
is no build step, no preprocessor, and no stylesheet to rebuild — the page is the
system. If a JavaScript front-end ever replaces it (see
[`app/README.md`](../../app/README.md)), these tokens port directly as CSS custom
properties or a Tailwind theme extension.
