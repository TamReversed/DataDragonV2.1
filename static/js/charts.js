/* DataDragon chart system: one colour-blind-safe palette, shared defaults, readable bar charts and PNG export.
 *
 * Needs Chart.js (loaded by the page). Everything is built with DOM calls and Chart.js options, never with HTML
 * strings, so column names and values can't become markup.
 */
const DDCharts = (() => {
    // Okabe-Ito palette (distinguishable with the common kinds of colour blindness); black replaced by grey for dark pages
    const palette = ['#0072B2', '#E69F00', '#009E73', '#CC79A7', '#56B4E9', '#D55E00', '#F0E442', '#999999'];
    const TEXT = 'rgba(255, 255, 255, 0.8)';
    const GRID = 'rgba(255, 255, 255, 0.1)';
    const TOP_N = 25;

    function applyDefaults() {
        if (!window.Chart) return;
        Chart.defaults.color = TEXT;
        Chart.defaults.borderColor = GRID;
        Chart.defaults.font.family = "Inter, system-ui, sans-serif";
        Chart.defaults.plugins.tooltip.backgroundColor = 'rgba(18, 18, 26, 0.95)';
        Chart.defaults.plugins.tooltip.titleColor = '#fff';
        Chart.defaults.plugins.tooltip.bodyColor = 'rgba(255, 255, 255, 0.9)';
        Chart.defaults.plugins.tooltip.padding = 12;
        Chart.defaults.maintainAspectRatio = false;
        Chart.defaults.responsive = true;
    }

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
                             borderRadius: 3, borderSkipped: false }],
            },
            options: {
                indexAxis: 'y',
                ...(fixed ? { responsive: false, animation: false } : {}),
                plugins: {
                    legend: options.legend ? {
                        display: true,
                        labels: {
                            generateLabels: () => options.legend.map(item => ({ text: item.label, fillStyle: item.color,
                                                                              strokeStyle: item.color, fontColor: TEXT })),
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
                    x: { beginAtZero: true, max: options.max, ticks: { callback: v => format(v) }, grid: { color: GRID } },
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
                             borderColor: 'rgba(18, 18, 26, 1)', borderWidth: 2 }],
            },
            options: {
                cutout: '60%',
                plugins: {
                    legend: { display: true, position: 'right',
                              labels: { color: TEXT, font: { size: 12 }, padding: 12, usePointStyle: true, pointStyle: 'circle' } },
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

    /** A chart as a PNG data URL on the page's dark background (a transparent PNG is unreadable in most viewers). */
    function chartImage(chart) {
        const source = chart.canvas;
        const flat = document.createElement('canvas');
        flat.width = source.width;
        flat.height = source.height;
        const context = flat.getContext('2d');
        context.fillStyle = '#12121a';
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
            clone.style.color = '#cccccc';
            const image = new Image();
            image.onload = () => {
                const flat = document.createElement('canvas');
                flat.width = box.width * scale;
                flat.height = box.height * scale;
                const context = flat.getContext('2d');
                context.fillStyle = '#12121a';
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
        try {
            const chart = build(canvas);
            const image = chartImage(chart);
            const size = { width: canvas.width, height: canvas.height };
            chart.destroy();
            return { image, ...size };
        } finally {
            host.remove();
        }
    }

    return { offscreenImage, palette, applyDefaults, horizontalBars, doughnut, chartImage, svgImage, addDownloadButton, shorten };
})();
