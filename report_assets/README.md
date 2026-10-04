# Report assets

What the Data Readiness Report (`datadragon_report.py`) needs at run time. Not served to browsers.

| Path | What | Source |
|---|---|---|
| `fonts/*.ttf` | Fraunces, Instrument Sans and JetBrains Mono as TrueType, for embedding in the PDF | Google Fonts, SIL Open Font License 1.1 (downloaded 2026-10-04) |
| `images/cover.jpg` | Cover illustration | Higgsfield (GPT Image 2.5, with the mark as reference); master `design/source/report/cover-b.png` |
| `images/banner.jpg` | Band on the summary page | Higgsfield; master `cover-c.png` |
| `images/spot-gaps.jpg`, `spot-keys.jpg` | Small section illustrations | Higgsfield; masters `spot-gaps.png`, `spot-keys-2.png` |

Rebuild the images from their masters with `python scripts/build_report_assets.py`.
A missing font or image never fails a report: the fonts fall back to built-in ones and the image is left out.
