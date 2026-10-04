"""Build the DataDragon mark from its master (design/source/brand/mark-a.svg, generated with Higgsfield / Recraft V4.1).

Usage: python scripts/build_brand.py
Writes:
  templates/brand/mark_paths.svg         paths only, themeable (currentColor + --dd-ember); included inline by templates
  static/images/brand/datadragon-mark.svg        fixed colours for light backgrounds
  static/images/brand/datadragon-mark-dark.svg   fixed colours for dark backgrounds
  static/images/brand/favicon.svg                follows the browser's light/dark preference
The master's grid-coloured under-layer is dropped, so the gaps between cells are transparent: the dragon is made of cells.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / 'design' / 'source' / 'brand' / 'mark-a.svg'
VIEWBOX = '420 460 1200 1120'
PAPER, UNDERLAY, EMBER = 'rgb(246,241,231)', {'rgb(51,90,77)', 'rgb(58,101,85)', 'rgb(53,101,76)'}, 'rgb(228,87,46)'


def round_path(d):
    return re.sub(r'-?\d+\.\d+', lambda m: str(round(float(m.group(0)))), d)


def layers():
    svg = re.sub(r'<metadata>.*?</metadata>', '', MASTER.read_text(encoding='utf-8'), flags=re.S)
    ink, eye, ember = [], [], []
    for tag in re.findall(r'<path[^>]*/>', svg):
        fill = re.search(r'fill="([^"]+)"', tag).group(1)
        d = round_path(re.search(r' d="([^"]+)"', tag).group(1))
        if fill == PAPER:
            if not d.startswith('M 0 0'):
                eye.append(d)
        elif fill in UNDERLAY:
            continue
        elif fill == EMBER:
            ember.append(d)
        else:
            ink.append(d)
    return ' '.join(ink), ' '.join(eye), ' '.join(ember)


def paths(ink_fill, eye_fill, ember_fill):
    ink, eye, ember = layers()
    return (f'<path fill="{ink_fill}" d="{ink}"/><path fill="{eye_fill}" d="{eye}"/>'
            f'<path fill="{ember_fill}" d="{ember}"/>')


def standalone(body, extra=''):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{VIEWBOX}">{extra}{body}</svg>\n'


def main():
    brand = ROOT / 'static' / 'images' / 'brand'
    brand.mkdir(parents=True, exist_ok=True)
    (ROOT / 'templates' / 'brand').mkdir(exist_ok=True)
    (ROOT / 'templates' / 'brand' / 'mark_paths.svg').write_text(
        paths('currentColor', 'var(--dd-mark-eye, var(--dd-bg, #F6F1E7))', 'var(--dd-ember, #E4572E)') + '\n', encoding='utf-8')
    (brand / 'datadragon-mark.svg').write_text(standalone(paths('#14211F', '#F6F1E7', '#E4572E')), encoding='utf-8')
    (brand / 'datadragon-mark-dark.svg').write_text(standalone(paths('#F2ECDD', '#0F1B1E', '#FF7A45')), encoding='utf-8')
    style = ('<style>.i{fill:#14211F}.e{fill:#F6F1E7}.m{fill:#E4572E}'
             '@media (prefers-color-scheme:dark){.i{fill:#F2ECDD}.e{fill:#0F1B1E}.m{fill:#FF7A45}}</style>')
    ink, eye, ember = layers()
    (brand / 'favicon.svg').write_text(standalone(
        f'<path class="i" d="{ink}"/><path class="e" d="{eye}"/><path class="m" d="{ember}"/>', style), encoding='utf-8')
    for f in sorted(brand.glob('*.svg')) + [ROOT / 'templates' / 'brand' / 'mark_paths.svg']:
        print(f'{f.stat().st_size:7,d}  {f.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
