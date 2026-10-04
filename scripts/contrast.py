"""WCAG 2.x contrast ratios for DataDragon's colour tokens (static/css/main.css).

Usage: python scripts/contrast.py        (prints the table; exit 1 if a required pair is below its minimum)
Semi-transparent text colours are composited over the page background, as a browser does.
Also checks the "Ember & Ink" tokens (static/css/tokens.css) in both themes.
"""
import re
import sys
from pathlib import Path

CSS = Path(__file__).resolve().parent.parent / 'static' / 'css' / 'main.css'


def parse_color(text):
    text = text.strip()
    m = re.fullmatch(r'#([0-9a-fA-F]{6})', text)
    if m:
        v = m.group(1)
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4)) + (1.0,)
    m = re.fullmatch(r'rgba?\(([^)]*)\)', text)
    if m:
        parts = [p.strip() for p in m.group(1).split(',')]
        return (int(parts[0]), int(parts[1]), int(parts[2]), float(parts[3]) if len(parts) > 3 else 1.0)
    raise ValueError(f'unsupported colour {text!r}')


def composite(fg, bg):
    a = fg[3]
    return tuple(round(fg[i] * a + bg[i] * (1 - a)) for i in range(3)) + (1.0,)


def luminance(rgb):
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(fg, bg):
    fg = composite(fg, bg)
    l1, l2 = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def tokens():
    css = CSS.read_text(encoding='utf-8')
    root = css[css.index(':root'):css.index('}', css.index(':root'))]
    return {name: value.strip() for name, value in re.findall(r'--([\w-]+):\s*([^;]+);', root)}


def gradient_stops(value):
    return [parse_color(c) for c in re.findall(r'#[0-9a-fA-F]{6}', value)]


def checks():
    t = tokens()
    page = parse_color(t['bg-primary'])
    card = composite(parse_color('rgba(255,255,255,0.06)'), page)       # a hovered card is the lightest surface
    rows = []
    for name in ('text-primary', 'text-secondary', 'text-muted'):
        fg = parse_color(t[name])
        rows.append((f'{name} on page', ratio(fg, page), 4.5))
        rows.append((f'{name} on card', ratio(fg, card), 4.5))
    for i, stop in enumerate(gradient_stops(t['gradient-button'])):
        rows.append((f'white on button gradient stop {i + 1}', ratio((255, 255, 255, 1.0), stop), 4.5))
    return rows


TOKENS = CSS.parent / 'tokens.css'
TEXT_TOKENS = ('ink', 'ink-2', 'ink-3', 'teal', 'ok', 'warn', 'bad')
SURFACES = ('bg', 'surface', 'sunken', 'selected')


def theme_tokens(css, selector):
    start = css.index(selector)
    block = css[css.index('{', start):css.index('}', start)]
    return {name: value.strip() for name, value in re.findall(r'--dd-([\w-]+):\s*(#[0-9a-fA-F]{6});', block)}


def token_checks():
    css = TOKENS.read_text(encoding='utf-8')
    light = theme_tokens(css, ':root {')
    themes = {'light': light, 'dark': {**light, **theme_tokens(css, ':root[data-theme="dark"]')}}
    rows = []
    for theme, t in themes.items():
        color = {name: parse_color(value) for name, value in t.items()}
        for fg in TEXT_TOKENS:
            for bg in SURFACES:
                rows.append((f'{theme}: {fg} on {bg}', ratio(color[fg], color[bg]), 4.5))
        rows.append((f'{theme}: on-primary on primary', ratio(color['on-primary'], color['primary']), 4.5))
        rows.append((f'{theme}: on-primary on primary-hover', ratio(color['on-primary'], color['primary-hover']), 4.5))
        for status in ('ok', 'warn', 'bad', 'info'):
            rows.append((f'{theme}: {status} on {status}-bg', ratio(color[status], color[f'{status}-bg']), 4.5))
            rows.append((f'{theme}: ink on {status}-bg', ratio(color['ink'], color[f'{status}-bg']), 4.5))
        for bg in ('bg', 'surface'):                                   # borders of controls, focus ring, chart marks: 3:1
            rows.append((f'{theme}: line on {bg}', ratio(color['line'], color[bg]), 3.0))
            rows.append((f'{theme}: focus on {bg}', ratio(color['focus'], color[bg]), 3.0))
        for i in range(1, 9):
            rows.append((f'{theme}: viz-{i} on surface', ratio(color[f'viz-{i}'], color['surface']), 3.0))
        rows.append((f'{theme}: viz-text on surface', ratio(color['viz-text'], color['surface']), 4.5))
    return rows


if __name__ == '__main__':
    failed = False
    for label, value, minimum in checks() + (token_checks() if TOKENS.exists() else []):
        ok = value >= minimum
        failed |= not ok
        print(f'{"ok  " if ok else "FAIL"} {value:5.2f}  (min {minimum})  {label}')
    sys.exit(1 if failed else 0)
