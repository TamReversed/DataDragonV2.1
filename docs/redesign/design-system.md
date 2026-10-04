# DataDragon design system: Ember & Ink

A field notebook for data, kept by a clever dragon. Paper and ink, one ember.

- **Tokens:** `static/css/tokens.css` (CSS variables, prefix `--dd-`). There is no Tailwind or build step in this
  project, so the variables are the theme config.
- **Live specimen:** `docs/redesign/design-system.html` (open in a browser; "Switch theme", or add `#dark` / `#light`).
  Screenshots: `design-system_light.png`, `design-system_dark.png`. Rebuild with `python scripts/build_design_preview.py`.
- **Status:** tokens and assets exist; the app's pages do not use them yet. That is Phase 4.

## Themes

Light is the default. Dark applies when the system asks for it, unless the page sets `data-theme="light"` on
`<html>`; `data-theme="dark"` forces dark. Phase 4 adds a toggle that stores the choice.

## Colour

| Token | Light | Dark | Use |
|---|---|---|---|
| `--dd-bg` | `#F6F1E7` | `#0F1B1E` | Page |
| `--dd-surface` | `#FFFDF8` | `#16262A` | Panels, inputs, table body |
| `--dd-sunken` | `#EDE6D6` | `#0B1517` | Table header, code, disabled |
| `--dd-selected` | `#DCEBE6` | `#1F3A3C` | Selected row or tree item |
| `--dd-ink`, `-2`, `-3` | `#14211F` `#44524E` `#5E6A66` | `#F2ECDD` `#BFC8C0` `#98A49E` | Text: primary, secondary, hints |
| `--dd-rule` | `#D9D1BF` | `#2A3D40` | Decorative hairlines |
| `--dd-line` | `#6F7A74` | `#6E7C78` | Borders of controls (3:1) |
| `--dd-teal` | `#17605C` | `#8FE0CF` | Links, focus ring, selection marks |
| `--dd-ember` | `#E4572E` | `#FF7A45` | The mark and decoration |
| `--dd-primary` / `--dd-on-primary` | `#C4411B` / white | `#FF7A45` / `#0F1B1E` | Primary button |
| `--dd-ok`, `--dd-warn`, `--dd-bad`, `--dd-info` (+ `-bg`) | see file | see file | Status text/icon and tinted background |

**Rules**

1. **Ember** is for the single primary action of a page and for the dragon. Nothing else is orange.
2. **Teal** means "you can interact with this" (links, focus, selection).
3. **Green, amber, red** mean status and always come with an icon or word, never colour alone.
4. No gradients, no per-tool or per-category colours, no glow.
5. In light mode the brand ember (`#E4572E`) is too light for white text, so buttons use `--dd-primary`.

**Contrast:** `python scripts/contrast.py` checks 102 token pairs across both themes (text on every surface 4.5:1,
status on its tint, control borders, focus ring and chart colours 3:1). All pass.

## Data-visualisation palette

`--dd-viz-1` … `--dd-viz-8`, in this order: teal, ember, blue, pink, gold, green, sky, grey. The hues come from the
Okabe-Ito colour-blind-safe set; the light theme uses darker versions so every series reaches 3:1 on the surface.
Charts read these variables plus `--dd-viz-grid` and `--dd-viz-text`, so axes and legends follow the theme. Series
are also told apart by order, labels and direct value labels, not colour alone.

## Type

| Role | Family | Where |
|---|---|---|
| Display | Fraunces (serif, true italic) | Page titles, headings, big numbers. Pattern: plain word + *italic word* |
| UI | Instrument Sans | Body text, buttons, form labels |
| Data | JetBrains Mono, tabular figures | Tables, file names, formulas, the tool tree, status lines |
| Pixel | Pixelify Sans, 12–13 px only | Step counters, progress percent, small labels. Never sentences |

Scale: 12, 13, 15 (body), 18, 24, 36, and a fluid 44–72 px hub headline. Line height 1.55 for text, 1.1 for display.
The fonts are loaded from Google Fonts for now (the app's CSP already allows it); self-hosting them is a follow-up.

## Space, shape, elevation, motion

- **Spacing:** 4, 8, 12, 16, 24, 32, 48, 64 px.
- **Radius:** 2 px (small), 4 px (default), 8 px (dialogs only). Document-like, nearly square.
- **Elevation:** flat. Borders separate things; only floating layers (dialogs, menus) have a shadow.
- **Motion:** 120 ms and 180 ms with one easing curve. Progress fills pixel blocks. Under
  `prefers-reduced-motion` the durations are zero.
- **Layout:** 272 px sidebar, 1080 px content column.

## Components (specified here, built in Phase 4)

Button (primary, default, quiet, disabled) · text field and select · upload drop zone · tool tree · table (mono,
sunken header, numbers right-aligned, selected row with a teal edge) · stat (serif number + pixel label) · message
(ok, info, warning, error) · terminal status line (`> …`) · pixel-block progress · dialog · chart wrapper.
Each is shown in the specimen page.

## Brand assets

| Asset | File | Source |
|---|---|---|
| Mark | `static/images/brand/datadragon-mark.svg`, `-dark.svg`, `favicon.svg`, PNG fallbacks; inline version `templates/brand/mark_paths.svg` | Higgsfield (Recraft V4.1 vector), cleaned by `scripts/build_brand.py`: metadata and background removed, the grid under-layer dropped so the gaps between cells are transparent, coordinates rounded (56 KB → 12.6 KB) |
| Wordmark | Text: "Data" + italic "Dragon" in Fraunces | Code |
| State illustrations: no file yet, nothing found, something went wrong, finished | `templates/brand/empty_upload.svg`, `empty_results.svg`, `error.svg`, `done.svg` | The mark plus simple props drawn in code |
| Scale motif (loose cells settling into a table) | `templates/brand/scales.svg` | Code |

All of these are inline SVG that use `currentColor` and the tokens, so they recolour for dark mode without a second
set of files.

**What was generated and not used.** Five Higgsfield illustrations (four states and a hero) and three other mark
candidates are in `design/source/brand/rejected/`. The illustrations were well drawn but each showed a different
dragon, so they failed the consistency requirement; the image model that outputs vectors does not accept a
reference image. The states were composed from the approved mark instead. `remove_background` and `upscale_image`
were not needed because the mark is a vector.

**Not done:** onboarding/marketing imagery beyond the scale motif (the app has no onboarding or marketing pages),
and the optional looping hero video (not generated; it would need its own credit approval).

## Higgsfield credits

| Step | Images | Model |
|---|---|---|
| Phase 2 moodboards | 3 | GPT Image 2.5 |
| Mark candidates | 4 | Recraft V4.1 vector ×2, GPT Image 2.5 (medium) ×2 |
| Illustrations and hero | 5 | Recraft V4.1 vector |

By account balance: 616.5 before Phase 2, 597.25 after Phase 3. Phase 2 cost 0.75 credits and Phase 3 cost 18.5
(19.25 in total). Vector and medium-quality images cost more than the 1.25 and 0.25 quoted by the preflight for
standard images.
