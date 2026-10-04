# DataDragon redesign: summary

Branch `redesign/v2` (local; not pushed). Design system: **Ember & Ink** (`design-system.md`).
Screenshots: `before/` (20 pages, the old dark theme) and `after/` (the same 20 pages in light and dark, plus three
side-by-side sheets `after/compare_*.png`: before, after light, after dark).

## What changed

| Area | Before | After |
|---|---|---|
| Identity | Dark only, purple-blue gradients, glowing orbs, Inter, a globe-like raster logo | Paper-and-ink light theme and a river-ink dark theme; one ember accent; serif display type; a dragon mark built from cells |
| Navigation | A "Back to Tools" link on each tool page | A persistent sidebar: mark, search (`/`), tool tree with the current tool marked, theme switch; collapses behind a "tools" button at 768 px and below |
| Hub | Cards in a grid, a different colour per category | A headline, the scale motif, and one compact list of the 18 tools by category |
| Styling | 1,096 shared lines and about 13,000 lines of page-local CSS; the same blocks re-declared in 12–16 templates; about 430 literal colours | Tokens (`tokens.css`) and a component library (`main.css`); about 5,400 lines of page-local CSS left, none with literal `rgba()` colours |
| Tool pages | Headers, help buttons, upload zones and tables differed page to page | One header pattern, one upload zone (the dragon waits for a file), data tables in mono with tabular figures, pixel-cell progress |
| Charts | Colours and chrome fixed for a dark page | Colours from the tokens (colour-blind-safe hues, 3:1 on the surface in both themes); re-colour on a live theme switch; PDF reports always render light |
| States | Loading animation duplicated per page; "nothing found" was a grey sentence | Shared progress and waiting cells; illustrated "nothing found" and "all passed" blocks (Duplicate Finder, Data Comparison, Data Validation); a not-found page |
| Accessibility | Focus ring, keyboard uploads and reduced motion were already in place | Kept, plus: skip link, navigation landmark with `aria-current`, 102 token pairs contrast-checked in both themes, decorative art hidden from assistive technology |

Not changed: routes, request and response formats, file handling, tool logic, output files. The one server-side
addition is a 404 handler that serves the not-found page to browsers only (scripts and API calls get the same
response as before). Element ids and form field names used by the page scripts are unchanged.

## Verification

| Check | Result |
|---|---|
| `pytest` | 1,065 passed, 1 skipped |
| `pyflakes` (app modules and scripts) | clean |
| `scripts/check_innerhtml.py` | 0 unsafe interpolations |
| `scripts/contrast.py` | 102 pairs, 0 failures |
| `pip-audit` | no known vulnerabilities |
| axe-core 4.10.2 (WCAG 2.0/2.1/2.2 A and AA) | 0 violations on the hub, Duplicate Finder, Data Merge, the pipeline, Security (light, 375 px) and Column Analyzer with results (dark, desktop) |
| Sideways scrolling at 375 px | none on those pages |
| Run with a test file in the browser | All 18 tools and every stage of the pipeline, in light and dark, with no script errors |

The project has no type checker and no JavaScript linter, so there was nothing to run for those.

Tests: nine tests that pinned old presentation details (gradient orbs, the old logo, the purple focus ring, chart hex
codes) were rewritten to assert the new equivalents, and `tests/test_charts_js.py` was added. It builds a bar chart
and a doughnut in node; it exists because a naming mistake during this work broke every bar chart and no test noticed.

## Higgsfield assets and credits

| Asset | Where it is used |
|---|---|
| Dragon mark (Recraft V4.1 vector, master `design/source/brand/mark-a.svg`) | Sidebar on every page, favicon, and inside the four state illustrations |
| Three moodboards (GPT Image 2.5) | `docs/redesign/directions/` only; they set the direction and are not shipped |
| Three other mark candidates, four illustrations and a hero (rejected) | `design/source/brand/rejected/`; not shipped |

The state illustrations and the scale motif are drawn in code around the mark, because the generated illustrations
each showed a different dragon. `remove_background` and `upscale_image` were not needed (the mark is a vector).
No video was generated.

Credits: 616.5 before, 597.25 after. **19.25 spent** (0.75 on moodboards, 18.5 on the mark candidates and the
illustrations). Nothing was generated in Phases 4 and 5.

## Known gaps

1. **Every tool has now been run with data in both themes** (a scripted headless browser, 17 runs plus the
   whole pipeline), and what that showed was fixed: a chart that grew without end, a crushed result table on Data
   Merge, pipeline charts wider than the page, off-style table headers and download buttons, unrounded pivot
   numbers. The runs used one small test file each; unusual data (very wide, very long, other scripts) was not tried.
2. **Page-local CSS remains** (about 5,400 lines) for tool-specific layouts. It reads the tokens through aliases
   (`--accent-purple` now means teal, and so on), which works but hides the intent. Some result headings may still be
   teal where the rule says ink.
3. **The brief's product does not exist here.** Onboarding, auth, dashboards, a chart builder, sharing, settings and
   billing were not built, because the rule was to redesign what exists.
4. **Tablet:** at 768 px the sidebar collapses to a top bar rather than staying beside the content.
5. **Fonts cover Latin and Latin Extended only.** Text in other scripts (for example in file names) falls back to
   system fonts.
6. **Inline styles** (about 200 `style="…"` attributes) are still there, so the CSP still needs `'unsafe-inline'`
   for styles.
7. **Charts on a page that is printed in dark mode** print dark; only the PDF export forces light.

## Suggested follow-ups

- Replace the alias variables in page CSS with the `--dd-*` tokens, then delete the alias block from `main.css`.
- Move inline styles and scripts to files, then tighten the CSP.
- Open a pull request for `redesign/v2` into `main`. The hardening work (PR #1) is already merged, so the redesign
  PR contains only its own 12 commits and merges cleanly.
