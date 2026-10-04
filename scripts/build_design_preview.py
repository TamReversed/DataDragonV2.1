"""Render docs/redesign/design-system.html: a static specimen of the tokens, type, components and brand assets.

Usage: python scripts/build_design_preview.py     (open the file in a browser; add #dark or #light to force a theme)
"""
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parent.parent
PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DataDragon design system: Ember &amp; Ink</title>
<link rel="stylesheet" href="../../static/css/fonts.css">
<style>{{ tokens }}</style>
<style>
*{box-sizing:border-box;margin:0}
body{background:var(--dd-bg);color:var(--dd-ink);font:var(--dd-text-md)/var(--dd-leading) var(--dd-font-ui);padding:var(--dd-space-6)}
main{max-width:1180px;margin:0 auto;display:grid;gap:var(--dd-space-7)}
h1{font:400 var(--dd-text-3xl)/var(--dd-leading-tight) var(--dd-font-display);letter-spacing:-.02em}
h1 em,h2 em{font-style:italic}
h2{font:500 var(--dd-text-xl)/1.2 var(--dd-font-display);margin-bottom:var(--dd-space-4);padding-bottom:var(--dd-space-2);border-bottom:1px solid var(--dd-rule)}
.label{font:var(--dd-text-xs) var(--dd-font-pixel);color:var(--dd-ink-3);letter-spacing:.04em}
.row{display:flex;flex-wrap:wrap;gap:var(--dd-space-4);align-items:center}
.sw{width:118px}.sw i{display:block;height:56px;border:1px solid var(--dd-rule);border-radius:var(--dd-radius)}
.sw b{display:block;font:500 var(--dd-text-sm) var(--dd-font-mono);margin-top:6px}
.brand{display:flex;align-items:center;gap:12px;font:500 1.75rem/1 var(--dd-font-display)}
.brand svg{width:44px;height:auto}
.btn{font:500 var(--dd-text-md) var(--dd-font-ui);padding:9px 16px;border-radius:var(--dd-radius);border:1px solid var(--dd-line);background:var(--dd-surface);color:var(--dd-ink);cursor:pointer;transition:background var(--dd-fast) var(--dd-ease)}
.btn:hover{background:var(--dd-sunken)}
.btn-primary{background:var(--dd-primary);border-color:var(--dd-primary);color:var(--dd-on-primary)}
.btn-primary:hover{background:var(--dd-primary-hover)}
.btn-quiet{border-color:transparent;background:none;color:var(--dd-teal);text-decoration:underline;text-underline-offset:3px}
.btn[disabled]{background:var(--dd-sunken);border-color:var(--dd-rule);color:var(--dd-ink-3);cursor:not-allowed}
:focus-visible{outline:2px solid var(--dd-focus);outline-offset:2px}
input.field{font:var(--dd-text-md) var(--dd-font-ui);padding:9px 12px;border:1px solid var(--dd-line);border-radius:var(--dd-radius);background:var(--dd-surface);color:var(--dd-ink);width:260px}
input.field::placeholder{color:var(--dd-ink-3)}
.drop{border:1.5px dashed var(--dd-line);border-radius:var(--dd-radius);padding:var(--dd-space-5);text-align:center;background:var(--dd-surface);display:grid;justify-items:center;gap:6px}
.drop svg{width:220px}
.drop strong{font:500 var(--dd-text-lg) var(--dd-font-display)}
.drop span{font:var(--dd-text-sm) var(--dd-font-mono);color:var(--dd-ink-2)}
table{border-collapse:collapse;width:100%;font:var(--dd-text-sm) var(--dd-font-mono);background:var(--dd-surface);border:1px solid var(--dd-rule)}
th{background:var(--dd-sunken);text-align:left;font-weight:500;color:var(--dd-ink-2)}
th,td{padding:7px 12px;border-bottom:1px solid var(--dd-rule);white-space:nowrap}
.num{text-align:right;font-variant-numeric:tabular-nums}
tr.sel td{background:var(--dd-selected)}tr.sel td:first-child{box-shadow:inset 3px 0 0 var(--dd-teal)}
.alert{display:flex;gap:10px;padding:10px 14px;border-radius:var(--dd-radius);border:1px solid currentColor;font-size:var(--dd-text-md)}
.alert span{color:var(--dd-ink)}
.ok{color:var(--dd-ok);background:var(--dd-ok-bg)}.warn{color:var(--dd-warn);background:var(--dd-warn-bg)}
.bad{color:var(--dd-bad);background:var(--dd-bad-bg)}.info{color:var(--dd-info);background:var(--dd-info-bg)}
.say{font:var(--dd-text-sm) var(--dd-font-mono);color:var(--dd-ink-2)}.say::before{content:"> ";color:var(--dd-teal)}
.blocks{display:flex;gap:3px}.blocks i{width:12px;height:12px;background:var(--dd-rule)}.blocks i.on{background:var(--dd-teal)}.blocks i.now{background:var(--dd-ember)}
.stat b{display:block;font:400 var(--dd-text-2xl)/1 var(--dd-font-display)}
.tree{font:var(--dd-text-sm) var(--dd-font-mono);border:1px solid var(--dd-rule);background:var(--dd-surface);padding:10px;width:260px}
.tree div{padding:4px 8px;border-radius:var(--dd-radius-sm)}.tree .g{color:var(--dd-ink-3)}.tree .on{background:var(--dd-selected);box-shadow:inset 3px 0 0 var(--dd-teal)}
.ill{display:grid;grid-template-columns:repeat(4,1fr);gap:var(--dd-space-4)}
.ill figure{border:1px solid var(--dd-rule);background:var(--dd-surface);padding:var(--dd-space-4);border-radius:var(--dd-radius)}
.ill figcaption{margin-top:8px}
.bars{display:grid;gap:5px;max-width:520px}.bars i{display:block;height:16px}
.scales{color:var(--dd-ink);max-width:560px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:var(--dd-space-6)}
@media(max-width:800px){.grid2,.ill{grid-template-columns:1fr 1fr}}
</style>
</head>
<body>
<main>
<header class="row" style="justify-content:space-between">
  <div class="brand"><svg viewBox="420 460 1200 1120" role="img" aria-label="">{% include "brand/mark_paths.svg" %}</svg><span>Data<em>Dragon</em></span></div>
  <button class="btn" id="theme" type="button">Switch theme</button>
</header>
<section>
  <p class="label">design system 01 / ember &amp; ink</p>
  <h1>Clean data, <em>calmly.</em></h1>
  <div class="scales" style="margin-top:24px">{% include "brand/scales.svg" %}</div>
</section>
<section><h2>Colour</h2>
  <div class="row">{% for name in ['bg','surface','sunken','selected','ink','ink-2','ink-3','rule','line','teal','ember','primary','ok','warn','bad'] %}
    <div class="sw"><i style="background:var(--dd-{{ name }})"></i><b>--dd-{{ name }}</b></div>{% endfor %}</div>
</section>
<section><h2>Type</h2>
  <div class="grid2">
    <div><p class="label">display / fraunces</p><p style="font:400 var(--dd-text-2xl)/1.1 var(--dd-font-display)">Duplicate <em>Finder</em></p>
      <p class="label" style="margin-top:16px">ui / instrument sans</p><p>Find rows that repeat on the columns you choose, and get a list of the ones to remove.</p></div>
    <div><p class="label">data / jetbrains mono</p><p style="font-family:var(--dd-font-mono)">customers_2026.xlsx · 12,450.00 · 2026-10-04</p>
      <p class="label" style="margin-top:16px">labels / pixelify sans (small only)</p><p style="font:var(--dd-text-sm) var(--dd-font-pixel)">step 02 / 05 · 68% · ready</p></div>
  </div>
</section>
<section><h2>Controls</h2>
  <div class="row"><button class="btn btn-primary" type="button">Find duplicates</button><button class="btn" type="button">Choose another file</button>
    <button class="btn btn-quiet" type="button">How it works</button><button class="btn" type="button" disabled>Download</button>
    <input class="field" placeholder="Search tools" aria-label="Search tools"></div>
</section>
<section class="grid2">
  <div><h2>Navigation tree</h2><div class="tree"><div class="g">▾ quality &amp; analysis</div><div>&nbsp;&nbsp;column analyzer</div><div class="on">&nbsp;&nbsp;duplicate finder</div><div>&nbsp;&nbsp;natural key finder</div><div class="g">▸ transformation</div><div class="g">▸ comparison &amp; merging</div></div></div>
  <div><h2>Progress and status</h2>
    <p class="say">1,204 rows checked, 37 duplicates</p>
    <div class="row" style="margin:10px 0 18px"><div class="blocks">{% for i in range(24) %}<i class="{{ 'on' if i < 15 else ('now' if i == 15 else '') }}"></i>{% endfor %}</div><span class="label">64%</span></div>
    <div class="row" style="gap:40px"><div class="stat"><b>1,204</b><span class="label">rows</span></div><div class="stat"><b>37</b><span class="label">duplicates</span></div><div class="stat"><b>3.07%</b><span class="label">rate</span></div></div></div>
</section>
<section><h2>Table</h2>
  <table><thead><tr><th>#</th><th>customer_id</th><th>name</th><th>region</th><th class="num">revenue</th><th>join_date</th></tr></thead>
  <tbody><tr><td>1</td><td>C00123</td><td>Acme Co</td><td>North</td><td class="num">12,450.00</td><td>2024-01-15</td></tr>
  <tr class="sel"><td>2</td><td>C00456</td><td>Globex Ltd</td><td>West</td><td class="num">9,820.50</td><td>2024-02-03</td></tr>
  <tr><td>3</td><td>C00789</td><td>Umbrella Corp</td><td>East</td><td class="num">17,630.00</td><td>2024-02-17</td></tr></tbody></table>
</section>
<section><h2>Messages</h2>
  <div style="display:grid;gap:8px">
    <div class="alert ok">✓ <span>37 duplicate rows removed. The result is ready to download.</span></div>
    <div class="alert info">i <span>Results stay available for 30 minutes.</span></div>
    <div class="alert warn">! <span>Column “amount” has 12 values that are not numbers.</span></div>
    <div class="alert bad">× <span>This file could not be read. Is it a real .xlsx file?</span></div>
  </div>
</section>
<section><h2>States</h2>
  <div class="ill">
    <figure>{% include "brand/empty_upload.svg" %}<figcaption class="label">no file yet</figcaption></figure>
    <figure>{% include "brand/empty_results.svg" %}<figcaption class="label">nothing found</figcaption></figure>
    <figure>{% include "brand/error.svg" %}<figcaption class="label">something went wrong</figcaption></figure>
    <figure>{% include "brand/done.svg" %}<figcaption class="label">finished</figcaption></figure>
  </div>
  <div class="drop" style="margin-top:24px">{% include "brand/empty_upload.svg" %}<strong>Drop a spreadsheet here</strong><span>or choose a file · .xlsx .xls .csv</span></div>
</section>
<section><h2>Chart colours</h2>
  <div class="bars">{% for i in range(1, 9) %}<i style="background:var(--dd-viz-{{ i }});width:{{ 100 - i * 9 }}%"></i>{% endfor %}</div>
</section>
<section><h2>Mark</h2>
  <div class="row" style="align-items:flex-end;gap:32px">{% for w in [160, 64, 32, 20] %}<svg viewBox="420 460 1200 1120" width="{{ w }}" role="img" aria-label="DataDragon mark at {{ w }} pixels">{% include "brand/mark_paths.svg" %}</svg>{% endfor %}</div>
</section>
</main>
<script>
(function () {
  var root = document.documentElement;
  if (location.hash === '#dark' || location.hash === '#light') root.dataset.theme = location.hash.slice(1);
  document.getElementById('theme').addEventListener('click', function () {
    var dark = root.dataset.theme ? root.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    root.dataset.theme = dark ? 'light' : 'dark';
  });
})();
</script>
</body>
</html>
"""


def main():
    env = Environment(loader=FileSystemLoader(ROOT / 'templates'), autoescape=False)
    html = env.from_string(PAGE).render(tokens=(ROOT / 'static' / 'css' / 'tokens.css').read_text(encoding='utf-8'))
    out = ROOT / 'docs' / 'redesign' / 'design-system.html'
    out.write_text(html, encoding='utf-8')
    print(f'{out.stat().st_size:,d} bytes  {out.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
