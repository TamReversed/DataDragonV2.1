/* DataDragon chart system: one colour-blind-safe palette (from the design tokens), shared defaults, readable bar
 * charts and PNG export.
 *
 * Needs Chart.js (loaded by the page). Everything is built with DOM calls and Chart.js options, never with HTML
 * strings, so column names and values can't become markup.
 */
const DDCharts = (() => {
    // Colours come from the design tokens (static/css/tokens.css), so charts follow the light or dark theme.
    // The series hues are the Okabe-Ito colour-blind-safe set; the fallbacks are the light theme.
    const FALLBACK = ['#17605C', '#C4411B', '#0072B2', '#A8507F', '#8A6A00', '#2E7D4F', '#2B7FB8', '#6B6B6B'];
    const TOP_N = 25;

    function token(name, fallback) {
        const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
        return value || fallback;
    }

    function currentPalette() {
        return FALLBACK.map((fallback, i) => token('--dd-viz-' + (i + 1), fallback));
    }

    function themeColors() {
        return {
            text: token('--dd-viz-text', '#44524E'),
            grid: token('--dd-viz-grid', '#D9D1BF'),
            surface: token('--dd-surface', '#FFFDF8'),
            ink: token('--dd-ink', '#14211F'),
            bg: token('--dd-bg', '#F6F1E7'),
        };
    }

    // `palette` stays an array for the pages that index into it; it is refreshed when the theme changes
    const palette = currentPalette();

    function applyDefaults() {
        if (!window.Chart) return;
        const c = themeColors();
        Chart.defaults.color = c.text;
        Chart.defaults.borderColor = c.grid;
        Chart.defaults.font.family = "'Instrument Sans', system-ui, sans-serif";
        Chart.defaults.plugins.tooltip.backgroundColor = c.ink;
        Chart.defaults.plugins.tooltip.titleColor = c.bg;
        Chart.defaults.plugins.tooltip.bodyColor = c.bg;
        Chart.defaults.plugins.tooltip.padding = 10;
        Chart.defaults.plugins.tooltip.cornerRadius = 4;
        Chart.defaults.maintainAspectRatio = false;
        Chart.defaults.responsive = true;
    }

    /** Re-colour the charts already on the page after a theme switch. */
    function retheme() {
        const before = palette.slice();
        const after = currentPalette();
        after.forEach((color, i) => { palette[i] = color; });
        if (!window.Chart) return;
        applyDefaults();
        const c = themeColors();
        const swap = value => {
            const i = typeof value === 'string' ? before.indexOf(value) : -1;
            return i >= 0 ? after[i] : value;
        };
        Object.values(Chart.instances || {}).forEach(chart => {
            chart.data.datasets.forEach(dataset => {
                ['backgroundColor', 'borderColor'].forEach(key => {
                    if (Array.isArray(dataset[key])) dataset[key] = dataset[key].map(swap);
                    else if (dataset[key]) dataset[key] = swap(dataset[key]);
                });
                if (chart.config.type === 'doughnut') dataset.borderColor = c.surface;
            });
            Object.values(chart.options.scales || {}).forEach(scale => {
                if (scale.grid) scale.grid.color = c.grid;
                if (scale.ticks) scale.ticks.color = c.text;
            });
            const legend = chart.options.plugins && chart.options.plugins.legend;
            if (legend && legend.labels) legend.labels.color = c.text;
            chart.update('none');
        });
    }
    document.addEventListener('dd:themechange', retheme);

    function shorten(text, max = 28) {
        const value = String(text);
        return value.length > max ? value.slice(0, max - 1) + '…' : value;
    }

    /** Give a canvas a text alternative: the chart title plus its numbers. */
    function describe(canvas, title, labels, values, format) {
        canvas.setAttribute('role', 'img');
        const pairs = labels.slice(0, 10).map((label, i) => label + ': ' + format(values[i])).join('; ');
        canvas.setAttribute('aria-label', title + '. ' + pairs + (labels.length > 10 ? '; and more' : ''));
    }

    /**
     * Horizontal bars sorted from largest to smallest, at most `topN` of them.
     * options: { title, labels, values, datasetLabel, format(v), max, colors[], legend: [{label,color}], topN }
     * Long names are shortened on the axis and shown in full in the tooltip.
     */
    function horizontalBars(canvas, options) {
        applyDefaults();
        const format = options.format || (v => Number(v).toLocaleString());
        const rows = options.labels.map((label, i) => ({
            label: String(label), value: Number(options.values[i]), color: options.colors ? options.colors[i] : null,
        })).sort((a, b) => b.value - a.value);
        const topN = options.topN || TOP_N;
        const shown = rows.slice(0, topN);
        const height = Math.max(180, shown.length * 26 + 70);                              // room for every bar's name
        const wrapper = canvas.parentElement;
        const fixed = options.fixedSize;
        if (fixed) {                                                    // off-screen rendering (PDF): no layout needed
            canvas.width = fixed.width;
            canvas.height = fixed.height || height;
        } else if (wrapper) {
            wrapper.style.height = height + 'px';
            const container = wrapper.closest('.chart-container');
            if (container) container.style.height = 'auto';
        }

        const colors = shown.map(r => r.color || palette[0]);
        describe(canvas, options.title || options.datasetLabel || 'Chart', shown.map(r => r.label), shown.map(r => r.value), format);
        const chart = new Chart(canvas, {
            type: 'bar',
            data: {
                labels: shown.map(r => shorten(r.label)),
                datasets: [{ label: options.datasetLabel || 'Value', data: shown.map(r => r.value), backgroundColor: colors,
                             borderRadius: 1, borderSkipped: false }],
            },
            options: {
                indexAxis: 'y',
                ...(fixed ? { responsive: false, animation: false } : {}),
                plugins: {
                    legend: options.legend ? {
                        display: true,
                        labels: {
                            generateLabels: () => options.legend.map(item => ({ text: item.label, fillStyle: item.color,
                                                                              strokeStyle: item.color, fontColor: themeColors().text })),
                        },
                    } : { display: false },
                    tooltip: {
                        callbacks: {
                            title: items => shown[items[0].dataIndex].label,        // the full name
                            label: item => (options.datasetLabel || 'Value') + ': ' + format(item.parsed.x),
                        },
                    },
                },
                scales: {
                    x: { beginAtZero: true, max: options.max, ticks: { callback: v => format(v) }, grid: { color: themeColors().grid } },
                    y: { grid: { display: false }, ticks: { autoSkip: false } },
                },
            },
        });
        if (rows.length > topN && !fixed) {
            const note = document.createElement('div');
            note.className = 'chart-note';
            note.style.cssText = 'font-size:12px;color:var(--text-secondary);margin-top:4px';
            note.textContent = 'Showing the ' + topN + ' largest of ' + rows.length + '.';
            canvas.closest('.chart-container, .card, div').appendChild(note);
        }
        return chart;
    }

    /** A doughnut where colour = class; the legend is always shown. */
    function doughnut(canvas, options) {
        applyDefaults();
        const total = options.values.reduce((a, b) => a + b, 0);
        describe(canvas, options.title || 'Chart', options.labels, options.values, v => String(v));
        return new Chart(canvas, {
            type: 'doughnut',
            data: {
                labels: options.labels,
                datasets: [{ data: options.values, backgroundColor: options.labels.map((_, i) => palette[i % palette.length]),
                             borderColor: themeColors().surface, borderWidth: 2 }],
            },
            options: {
                cutout: '60%',
                plugins: {
                    legend: { display: true, position: 'right',
                              labels: { color: themeColors().text, font: { size: 12 }, padding: 12, usePointStyle: true, pointStyle: 'rect' } },
                    tooltip: {
                        callbacks: {
                            label: item => item.label + ': ' + item.raw + ' (' + (total ? (item.raw / total * 100).toFixed(1) : 0) + '%)',
                        },
                    },
                },
            },
        });
    }

    /* ---------- PNG export ---------- */
    function triggerDownload(dataUrl, filename) {
        const link = document.createElement('a');
        link.href = dataUrl;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
    }

    /** A chart as a PNG data URL on the current surface colour (a transparent PNG is unreadable in most viewers). */
    function chartImage(chart) {
        const source = chart.canvas;
        const flat = document.createElement('canvas');
        flat.width = source.width;
        flat.height = source.height;
        const context = flat.getContext('2d');
        context.fillStyle = themeColors().surface;
        context.fillRect(0, 0, flat.width, flat.height);
        context.drawImage(source, 0, 0);
        return flat.toDataURL('image/png');
    }

    function svgImage(svg) {
        return new Promise((resolve, reject) => {
            const box = svg.viewBox.baseVal;
            const scale = 2;
            const clone = svg.cloneNode(true);
            clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
            clone.setAttribute('width', box.width);
            clone.setAttribute('height', box.height);
            clone.style.color = themeColors().text;
            const image = new Image();
            image.onload = () => {
                const flat = document.createElement('canvas');
                flat.width = box.width * scale;
                flat.height = box.height * scale;
                const context = flat.getContext('2d');
                context.fillStyle = themeColors().surface;
                context.fillRect(0, 0, flat.width, flat.height);
                context.drawImage(image, 0, 0, flat.width, flat.height);
                resolve(flat.toDataURL('image/png'));
            };
            image.onerror = () => reject(new Error('Could not render the chart'));
            image.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(new XMLSerializer().serializeToString(clone));
        });
    }

    /** Add a "Download PNG" button after `anchor`. `getImage()` returns a data URL (or a promise of one). */
    function addDownloadButton(anchor, getImage, filename) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn btn-secondary chart-download';
        button.style.cssText = 'margin-top:8px;font-size:12px;padding:6px 12px';
        button.textContent = 'Download PNG';
        button.addEventListener('click', async () => {
            try {
                triggerDownload(await getImage(), filename.replace(/[^\w.-]+/g, '_') + '.png');
            } catch (err) {
                console.error(err);
            }
        });
        anchor.insertAdjacentElement('afterend', button);
        return button;
    }

    /** Render a chart off-screen and return its PNG data URL (for PDF export). `build(canvas)` creates the chart. */
    function offscreenImage(build) {
        const host = document.createElement('div');
        host.style.cssText = 'position:fixed;left:-10000px;top:0;width:1px;height:1px;overflow:hidden';
        const canvas = document.createElement('canvas');
        host.appendChild(canvas);
        document.body.appendChild(host);
        const root = document.documentElement;
        const theme = root.dataset.theme;
        root.dataset.theme = 'light';                                   // a report is printed on white paper
        const previous = palette.slice();
        currentPalette().forEach((color, i) => { palette[i] = color; });
        try {
            const chart = build(canvas);
            const image = chartImage(chart);
            const size = { width: canvas.width, height: canvas.height };
            chart.destroy();
            return { image, ...size };
        } finally {
            host.remove();
            if (theme) root.dataset.theme = theme; else delete root.dataset.theme;
            previous.forEach((color, i) => { palette[i] = color; });
        }
    }

    return { offscreenImage, palette, colors: themeColors, retheme, applyDefaults, horizontalBars, doughnut, chartImage, svgImage, addDownloadButton, shorten };
})();
