"""The chart helpers run (in node, against a stand-in for Chart.js) and take their colours from the theme tokens."""
import json
import shutil
import subprocess

import pytest

SCRIPT = r"""
const fs = require('fs');
const vm = require('vm');
const tokens = { '--dd-viz-1': '#111111', '--dd-viz-2': '#222222', '--dd-viz-text': '#333333', '--dd-viz-grid': '#444444',
                 '--dd-surface': '#555555' };
const created = [];
function Chart(canvas, config) { this.config = config; this.data = config.data; this.options = config.options; created.push(config); }
Chart.defaults = { font: {}, plugins: { tooltip: {} } };
Chart.instances = {};
const element = () => ({ setAttribute() {}, style: {}, parentElement: null, closest: () => null, appendChild() {} });
const listeners = {};
const sandbox = {
    window: { Chart }, Chart,
    document: { documentElement: { dataset: {} }, addEventListener: (name, fn) => { listeners[name] = fn; }, createElement: element },
    getComputedStyle: () => ({ getPropertyValue: name => tokens[name] || '' }),
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('static/js/charts.js', 'utf8') + '\nthis.DDCharts = DDCharts;', sandbox);
const D = sandbox.DDCharts;
D.horizontalBars(element(), { title: 't', labels: ['a', 'b'], values: [1, 2], legend: [{ label: 'x', color: D.palette[0] }] });
D.doughnut(element(), { title: 'd', labels: ['a', 'b'], values: [1, 2] });
// a canvas whose parent is itself the .chart-container (the pipeline's uniqueness chart)
const box = element(); box.closest = () => box; box.insertAdjacentElement = () => {};
const boxed = element(); boxed.parentElement = box;
D.horizontalBars(boxed, { title: 'u', labels: ['a', 'b'], values: [1, 2] });
const bars = created[0], ring = created[1];
console.log(JSON.stringify({
    palette: D.palette.slice(0, 3), barColor: bars.data.datasets[0].backgroundColor[0], grid: bars.options.scales.x.grid.color,
    ringColors: ring.data.datasets[0].backgroundColor, ringBorder: ring.data.datasets[0].borderColor,
    boxHeight: box.style.height, legendColor: ring.options.plugins.legend.labels.color, text: Chart.defaults.color, listens: Object.keys(listeners),
}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_bar_and_doughnut_charts_build_with_token_colours():
    out = subprocess.run(["node", "-e", SCRIPT], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout)
    assert result["palette"] == ["#111111", "#222222", "#0072B2"]          # tokens first, the fallback where a token is missing
    assert result["barColor"] == "#111111" and result["grid"] == "#444444"
    assert result["ringColors"] == ["#111111", "#222222"] and result["ringBorder"] == "#555555"
    assert result["legendColor"] == "#333333" and result["text"] == "#333333"
    assert "dd:themechange" in result["listens"]
    # the box around a bar chart keeps a fixed height; with `auto` a responsive chart grows without end
    assert result["boxHeight"] == "180px"
