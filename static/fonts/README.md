# Fonts

Served by the app itself, so no page asks another server for a font.

| Family | Use | Licence |
|---|---|---|
| Fraunces (variable, roman and italic) | Display: titles, headings, big numbers | SIL Open Font License 1.1 |
| Instrument Sans (variable) | Interface text | SIL Open Font License 1.1 |
| JetBrains Mono (variable) | Data, file names, the tool tree | SIL Open Font License 1.1 |
| Pixelify Sans (variable) | Small labels only | SIL Open Font License 1.1 |

The files are the Latin and Latin Extended subsets as distributed by Google Fonts (downloaded 2026-10-04).
`static/css/fonts.css` declares them with `unicode-range`, so a browser fetches the Latin Extended file only when a
page needs it.
