"""Prepare the images of the Data Readiness Report from their masters in design/source/report/ (Higgsfield).

Usage: python scripts/build_report_assets.py
Writes report_assets/images/: cover.jpg, banner.jpg, spot-gaps.jpg, spot-keys.jpg.
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'design' / 'source' / 'report'
OUT = ROOT / 'report_assets' / 'images'


def whiten(image, threshold=236):
    """Turn the near-white background of a spot illustration into pure white, so it has no visible box on the page."""
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            if min(pixels[x, y]) >= threshold:
                pixels[x, y] = (255, 255, 255)
    return image


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cover = Image.open(SOURCE / 'cover-b.png').convert('RGB')
    cover = cover.resize((1600, round(cover.height * 1600 / cover.width)), Image.LANCZOS)   # flat artwork enlarges cleanly
    cover.save(OUT / 'cover.jpg', quality=84, optimize=True)
    banner = Image.open(SOURCE / 'cover-c.png').convert('RGB')
    banner.crop((0, 186, 1024, 486)).save(OUT / 'banner.jpg', quality=88, optimize=True)
    for source, name in (('spot-gaps.png', 'spot-gaps.jpg'), ('spot-keys-2.png', 'spot-keys.jpg')):
        spot = whiten(Image.open(SOURCE / source).convert('RGB'))
        box = Image.eval(spot.convert('L'), lambda v: 0 if v >= 250 else 255).getbbox()       # trim the empty margin
        side = max(box[2] - box[0], box[3] - box[1]) + 40
        square = Image.new('RGB', (side, side), 'white')
        square.paste(spot.crop(box), ((side - (box[2] - box[0])) // 2, (side - (box[3] - box[1])) // 2))
        spot = square.resize((512, 512), Image.LANCZOS)
        spot.save(OUT / name, quality=90, optimize=True)
    for path in sorted(OUT.iterdir()):
        print(f'{path.stat().st_size:8,d}  {path.relative_to(ROOT)}')
    print('cover background:', '#%02X%02X%02X' % cover.getpixel((10, 10)))


if __name__ == '__main__':
    main()
