# DataDragon redesign: artistic direction

One direction, **"Ember & Ink"**, derived from what current design-award winners and the competing data tools
are doing. Moodboards (Higgsfield, `gpt_image_2_5`, 3 images, 0.75 credits) are in `directions/`:

- `directions/ember-ink_light.png`: hub and upload, light theme, palette, type, scale motif
- `directions/ember-ink_dark.png`: a tool page with results, dark theme, chart palette
- `directions/ember-ink_marks.png`: six dragon-mark concepts and two wordmarks

The moodboards are concepts. Tool names, numbers and UI details in them are invented by the image model; the real
UI is built in code from the real pages.

## What the evidence says

Homepages reviewed on 4 October 2026 (screenshots taken for comparison, not kept in the repo).

| Who | What they look like | Take-away |
|---|---|---|
| **Rows** | Cream page, maroon heavy sans, yellow button, product screenshot | Warm light surface, one loud accent |
| **Equals** | Warm white, large serif headline, pastel **pixel blocks** as decoration | Serif for trust; pixel motif reads as "cells" |
| **Datablist** | White, black sans, dark button, diagram cards | Plain and functional; little identity |
| **Datawrapper** | White, one teal, the charts are the decoration | Let the data be the visual |
| **Gigasheet** | White with faint grid, navy, yellow button | Grid paper as a data cue |
| **Flatfile (now Obvious)** | Pale green wash, centred, soft | Soft wash backgrounds are current |
| **OpenRefine** | Documentation-plain, blue diamond | The closest functional rival has no design to speak of |
| **Affinity** (Webby 2026 People's Voice, Best Visual Design – Function) | Black, huge serif with an italic word, lime button, cut-out art | Expressive serif + italic, one accent, art at the edges |
| **FOLLOW.ART** (Webby 2026 winner, same category) | Art-led, dark | (Site blocked automated viewing; listed for the record) |
| Trend write-ups for 2026 | Monochrome UI with colour reserved for status; serif headlines; calm layouts; product shown early | Colour means something or it isn't there |

Three conclusions:

1. **Every competitor is light, warm and calm.** DataDragon's dark purple-gradient look is the odd one out, and
   not in a good way: it reads as a template. Light should be a first-class theme, with dark as its equal.
2. **Serif display type is what winners and the stronger competitors use** to look trustworthy with numbers
   (Equals, Affinity). Nobody in the data-cleaning space pairs it with a character; that is the open position.
3. **Pixels are already a data idiom** (Equals' blocks, Gigasheet's grid). That lets the pixel/terminal/explorer
   feel you liked on paintriver.art come in as a motif that makes sense here, without making body text hard to read.

## Ember & Ink

**Idea:** a field notebook for data, kept by a clever dragon. Paper and ink, one ember.

| | |
|---|---|
| **Palette, light** | Paper `#F6F1E7` page, white surfaces, ink `#14211F` text, river teal `#1F6F6B` links and selection, **ember `#E4572E`** for the one primary action and the mark |
| **Palette, dark** | River ink `#0F1B1E` page, warm off-white `#F2ECDD` text, mint `#8FE0CF` links, **ember `#FF7A45`** primary. A faint painterly wash in one corner only |
| **Colour rule** | Ember = the primary action and the dragon. Teal = interactive. Green / amber / red = status only. No per-tool or per-category colours, no gradients |
| **Data-viz palette** | Okabe-Ito (already in `charts.js`), re-ordered so the first two series are teal and ember-compatible orange; chart chrome follows the theme |
| **Type** | Display: a high-contrast serif with a true italic (Fraunces), headline pattern "plain word + *italic word*". UI text: a humanist sans. Data, file names, formulas, tree: one monospace (JetBrains Mono) with tabular figures. Pixel face (small sizes only): step counters, progress, status labels |
| **Density** | Compact, document-like. Hairline rules, 2–4 px radius, no shadows or glass. Tables are dense with right-aligned mono numbers |
| **Layout** | Explorer shell: persistent left tree of the 18 tools by category (collapsible, searchable, `/` to focus), current tool highlighted; main column is the tool. The hub becomes the explorer with a headline and the upload invitation |
| **Voice** | Terminal-style status lines (`> 1,204 rows checked, 37 duplicates`), pixel-block progress bar; plain wording for analysts |
| **Motion** | Quiet: 120–180 ms fades and slides; pixel blocks fill for progress; scales "settle" once on page load. All off under reduced motion |
| **Dragon** | Clever, composed, in profile, built from square cells (a dragon made of spreadsheet). Narrowed eye, no fire, no cartoon. One ember cell. The "scale motif" (cells dissolving) is the decorative element for empty states and section breaks |

**Mark candidates** (from `ember-ink_marks.png`, numbered left to right, top row first):
1 cell-built head · 2 dragon as the letter D · 3 eye in a table cell · 4 scales as rows and columns ·
5 tail around a table · 6 16-pixel head.
My recommendation: **1** as the mark (it scales down to a favicon as **6**), **4** as the pattern, wordmark
"Data*Dragon*" in the serif with the italic second word.

**Why this and not the alternatives**

- *Dark command-centre:* it is what the app has now, and what no successful competitor does.
- *Pure paintriver-style pixel/wash (mockup D):* memorable, but pixel type at length fails readability for
  tables and long option forms, and a full-page wash fights with data. Its best parts (explorer tree, terminal
  lines, pixel accents, the wash as a corner detail in dark) are kept.
- *Generic light SaaS (Datablist-like):* safe and forgettable.

## Decision needed

1. Go with Ember & Ink as described, or change something (palette, serif, how much pixel)?
2. Which mark concept (1–6) should be developed into the logo?
