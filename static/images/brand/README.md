# Brand assets ("Ember & Ink")

| File | Use |
|---|---|
| `datadragon-mark.svg` | The mark on light backgrounds (fixed colours) |
| `datadragon-mark-dark.svg` | The mark on dark backgrounds |
| `favicon.svg` | Browser tab icon; follows the browser's light/dark preference |
| `favicon-32.png`, `apple-touch-icon.png`, `datadragon-mark-512.png` | Raster fallbacks on the paper colour |

Pages do not load these as images. Templates include `templates/brand/mark_paths.svg` inline, so the mark takes the
text colour (`currentColor`) and the ember token of the current theme. The state illustrations
(`templates/brand/empty_upload.svg`, `empty_results.svg`, `error.svg`, `done.svg`) and the scale motif (`scales.svg`)
work the same way.

The master is `design/source/brand/mark-a.svg` (generated with Higgsfield, Recraft V4.1 vector).
Rebuild after replacing it: `python scripts/build_brand.py`. The PNGs are screenshots of the SVG, resized with `sips`.
The wordmark is text, not an image: "Data" + italic "Dragon" in the display serif.
