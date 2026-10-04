# Static images

- `datadragon-logo-128.png` / `datadragon-logo-256.png` - the logo at 1x and 2x of its largest display size (64 px),
  used through `srcset` (about 6 KB and 14 KB). Generate them from the master with
  `sips -Z 128 design/source/datadragon-logo.png --out static/images/datadragon-logo-128.png` (and `-Z 256`).
- The original, 13728x8333 px and 1 MB, is kept out of the served path in `design/source/`.
- `datadragon-logo_old.svg` - the previous vector logo (unused).
- `SQUARE__11-21-2025-7-55-45.mp4` - splash video.
