# Static images

- `datadragon-logo-128.png` / `datadragon-logo-256.png` - the logo at 1x and 2x of its largest display size (64 px),
  used through `srcset` (about 6 KB and 14 KB). Generate them from the master with
  `sips -Z 128 design/source/datadragon-logo.png --out static/images/datadragon-logo-128.png` (and `-Z 256`).
- The original master (13728x8333 px, 1 MB) is kept out of the served path in `design/source/`.
