# DataDragon redesign: Phase 1 audit

Branch `redesign/v2`. No application code was changed for this audit. "Before" screenshots of every page (1440×1000,
the only theme that exists today: dark) are in `docs/redesign/before/`.

## 1. What the app actually is

The brief describes a SaaS with onboarding, dashboards, a chart builder, sharing, settings, billing and auth.
**None of those exist in this codebase**, and the rule is "don't invent features", so they are out of scope:

| Brief says | Reality |
|---|---|
| Onboarding, auth, settings, billing | No accounts at all. An anonymous session cookie scopes jobs and results to one browser. |
| Connect datasets | Upload only (Excel, CSV, PDF). Results of one tool can be reused as input of the next ("earlier results"). |
| Dashboards, chart builder, sharing | No dashboards. Charts appear inside Column Analyzer and the Data Readiness Pipeline (fixed charts, PNG and PDF export). Nothing is shared; results are downloaded. |
| Tailwind / theme config | No Tailwind, no build step. Hand-written CSS. |

**Stack:** Python 3.12+, Flask, pandas; server-rendered Jinja templates; vanilla JavaScript (`static/js/common.js`,
`charts.js`); Chart.js, jsPDF and html2canvas from CDNs with SRI hashes; a strict CSP; no bundler, no framework,
no TypeScript. Tests are pytest (about 1,065) plus node tests for `common.js`.

**Asset location:** there is no `public/`. Brand assets go in `static/images/brand/`; masters in `design/source/`.

## 2. Map

**Pages (20 templates on one shared `base.html`, except the landing page):**

- Hub: `/` and `/landing` (tool cards by category, search, "Generate Test File")
- Pipeline: `/data-readiness-pipeline` (6-step guided flow, charts, PDF report, resumable)
- Quality and analysis: `/column-analyzer`, `/duplicate-finder`, `/natural-key-finder`, `/data-validation`,
  `/column-normalizer`, `/data-anonymizer`
- Transformation: `/transpose`, `/pivot-generator`, `/excel-splitter`, `/find-replace`, `/row-filter`,
  `/column-operations`, `/calculated-columns`
- Comparison and merging: `/data-merge`, `/data-comparison`, `/column-comparison`
- Documents: `/pdf-to-word`
- Information: `/security-info`

**Shared pieces:** `templates/base.html` (45 lines: background, back link, title block), `static/css/main.css`
(1,096 lines: tokens, buttons, cards, forms, alerts, modal, table, badges, utilities), `static/css/a11y.css`
(focus ring, reduced motion), `common.js` (upload zones, earlier-results picker, job runner with progress and cancel,
modal), `charts.js` (Okabe-Ito palette, bar and doughnut helpers, PNG export).

**Where style is defined:** tokens are CSS variables in `:root` of `main.css` (colour, gradient, border, shadow,
spacing 4–64 px, radius 6–24 px, transitions). Font: Inter from Google Fonts. No type scale tokens.

## 3. Core user flows (the ones that exist)

1. **Find a tool:** hub → search or scan categories → open a tool.
2. **Run a tool:** upload (or pick an earlier result) → preview and options → run → progress (SSE, cancellable) →
   result summary and preview → download.
3. **Chain tools:** result of tool A appears under "earlier results" in tool B.
4. **Guided pipeline:** upload → shape analysis → gap triage → key discovery → anonymize → PDF report.
5. **Profile a file:** Column Analyzer → statistics, charts, PDF report.
6. **Reassurance:** Security & Privacy page.

## 4. Findings, ranked by impact

### High

1. **Most of the styling is not shared.** `main.css` has 1,096 lines; the templates carry about **13,000 lines of
   page-local `<style>`** (170 to 1,500 per page). The same components are re-declared page after page: the
   earlier-results list in 16 templates, the progress block in 14–16, the "lava lamp" loader in 14, the help modal
   in 12, the preview table in 13, results summary in 12. They have drifted, so a redesign cannot be done by
   changing tokens alone. This is the main piece of work: lift these into shared components first.
2. **Two component vocabularies.** `main.css` defines `.file-upload-zone`, `.modal`, `.progress-bar`, `.table`,
   `.alert`; pages mostly use their own `.upload-area`, `.help-modal`, `.progress-container`, `.preview-table`,
   `.error`. The shared versions are partly dead code.
3. **Dark only.** No light theme. About 430 literal `rgba(255,255,255,…)` / `rgba(…)` colours in templates and
   fixed dark colours in `charts.js` (`TEXT`, `GRID`, tooltip, `#12121a` export background) assume a dark page, so a
   light theme needs those replaced by tokens.
4. **Generic identity.** Dark page, purple-to-blue gradients, glowing orbs, Inter: exactly the "generic
   purple-gradient SaaS" look the brief wants to leave. Six accent colours and six gradients are used decoratively
   (a different colour per category and tool) rather than to carry meaning.
5. **No app shell.** Tool pages have only a centred "Back to Tools" link. Moving between tools always goes through
   the hub; there is no persistent navigation, no indication of which category a tool belongs to, and the wordmark
   is absent from tool pages.
6. **Weak hierarchy on tool pages.** The page opens with a large gradient title, a centred description, and a
   disabled primary button over an otherwise empty page (see `before/data-merge.png`). Options, preview and results
   appear later as stacked cards with equal weight; the primary action is not anchored.

### Medium

7. **States are inconsistent.** Loading: a decorative lava-lamp animation plus a progress bar, duplicated per
   page. Empty: most pages have no designed "nothing yet" or "no results" state (for example zero duplicates found
   shows an empty table area). Error: seven pages have their own `.error` block; others use the shared alert.
   Messages are good (authored server-side) but their presentation differs.
8. **Tables.** Preview tables differ in density, header style and number alignment between pages; numbers are not
   tabular; long values wrap unpredictably.
9. **Charts.** One colour-blind-safe palette (good), but chart chrome is hard-coded for dark and uses Inter;
   PNG/PDF export paints a dark background.
10. **Type.** One family, sizes set ad hoc per page (no scale); monospace is declared six different ways
    (`Courier New`, `Monaco`, `SF Mono`, …) for formulas, regex and file names.
11. **Logo.** A small low-contrast globe-like raster mark (128/256 px PNG) next to a plain Inter wordmark; it does
    not read as a dragon. No SVG, no favicon set.

### Low

12. **Inline styles:** about 200 `style="…"` attributes in templates (CSP currently needs `'unsafe-inline'` for
    styles because of them).
13. **Responsive:** works at 375 px after the earlier pass, but through per-page media queries; breakpoints differ
    (640, 768, 1024).
14. **Copy tone:** "Enterprise data processing toolkit", "Apriori algorithm" in a card description; the audience is
    analysts and ops leads, so plainer wording would fit better (wording only, no feature change).

### Accessibility (WCAG 2.2 AA)

Already in place from the earlier hardening work: visible focus ring, keyboard-operable upload zones, reduced-motion
support, modal focus handling, text alternatives on charts, a contrast script (`scripts/contrast.py`) and axe checks.
Gaps a redesign must close or keep closed:

- Contrast is only verified for the dark tokens; every new token pair (both themes) needs checking, including
  placeholder text, disabled buttons (the dimmed primary button in `before/data-merge.png` is borderline) and
  coloured badges.
- Colour is sometimes the only signal (category colours, changed/added/removed in comparisons need text or icon
  labels kept alongside).
- No skip link and no landmark navigation (there is no nav to land on yet).
- Target size: some icon buttons (modal close, remove-file) are under 24×24 px.

## 5. What this means for the plan

- Phase 4 order, adapted to what exists: tokens and themes → shared components (extracted from the 16 duplicated
  blocks) → app shell and hub → tool pages by category → pipeline and Column Analyzer (charts) → Security page.
- No business logic, routes, API contracts or data handling change. Element ids and form field names used by the
  page scripts and tests stay as they are.
- Risk: the tests include golden snapshots of tool output (unaffected) and some tests that read template text
  (`test_accessibility`, `test_hub_and_aliases`, `test_keyboard_uploads`); those are the ones to watch.
