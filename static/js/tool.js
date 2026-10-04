/* The page of a tool built on the scaffold (templates/tool.html, datadragon_tools.py).
 *
 * Reads the tool's description from #toolConfig, draws its options (including the shared column picker), and runs
 * the two requests: "preview the changes" and "run". Everything is built with DOM calls, never with HTML strings,
 * so column names and cell values cannot become markup.
 */
(() => {
    const config = JSON.parse(document.getElementById('toolConfig').textContent);
    const $ = id => document.getElementById(id);
    const el = (tag, props = {}, children = []) => {
        const node = document.createElement(tag);
        Object.entries(props).forEach(([key, value]) => {
            if (key === 'class') node.className = value;
            else if (key === 'text') node.textContent = value;
            else if (key in node) node[key] = value;
            else node.setAttribute(key, value);
        });
        [].concat(children).forEach(child => child && node.appendChild(child));
        return node;
    };

    let selectedFile = null;
    let columns = [];                       // [{name, dtype, sample_values}]
    const controls = {};                    // option id -> { get(), row }

    /* ---------- column kinds, for the picker ---------- */
    // Files are read without guessing types (a CSV cell is always text), so the kind comes from the sample values
    function kindOf(column) {
        const dtype = column.dtype || '';
        if (/int|float/.test(dtype)) return 'number';
        if (/datetime/.test(dtype)) return 'date';
        const samples = (column.sample_values || []).filter(value => value !== '');
        if (!samples.length) return 'text';
        if (samples.every(value => /^\s*[-+]?(\d[\d,]*(\.\d*)?|\.\d+)\s*%?\s*$/.test(value))) return 'number';
        if (samples.every(value => /^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$/.test(value))) return 'date';
        return 'text';
    }

    /* ---------- the shared column picker: search, select by kind, name + kind + sample ---------- */
    function columnPicker(option) {
        const chosen = new Set();
        const count = el('span', { class: 'pixel-label', 'aria-live': 'polite' });
        const search = el('input', { type: 'search', class: 'form-input picker-search', placeholder: 'Find a column',
                                     'aria-label': 'Find a column in ' + option.label });
        const list = el('div', { class: 'picker-list', role: 'group', 'aria-label': option.label });
        const boxes = [];

        function update() {
            count.textContent = chosen.size + ' of ' + columns.length + ' chosen';
            form.dispatchEvent(new Event('change'));
        }
        function setAll(predicate) {
            boxes.forEach(({ box, column, row }) => {
                if (row.hidden) return;                         // "all" means all that the search shows
                box.checked = predicate(column);
                if (box.checked) chosen.add(column.name); else chosen.delete(column.name);
            });
            update();
        }
        const quick = el('div', { class: 'picker-quick' }, [
            el('button', { type: 'button', class: 'btn btn-sm', text: 'All', onclick: () => setAll(() => true) }),
            el('button', { type: 'button', class: 'btn btn-sm', text: 'None', onclick: () => setAll(() => false) }),
            el('button', { type: 'button', class: 'btn btn-sm', text: 'Text columns', onclick: () => setAll(c => kindOf(c) === 'text') }),
            el('button', { type: 'button', class: 'btn btn-sm', text: 'Number columns', onclick: () => setAll(c => kindOf(c) === 'number') }),
            count,
        ]);

        columns.forEach((column, index) => {
            const id = option.id + '_col_' + index;
            const box = el('input', { type: 'checkbox', id });
            box.addEventListener('change', () => {
                if (box.checked) chosen.add(column.name); else chosen.delete(column.name);
                update();
            });
            const sample = (column.sample_values || []).slice(0, 3).join(', ');
            const row = el('label', { class: 'picker-item', for: id }, [
                box,
                el('span', { class: 'picker-name', text: column.name }),
                el('span', { class: 'badge', text: kindOf(column) }),
                el('span', { class: 'picker-sample', text: sample }),
            ]);
            boxes.push({ box, column, row });
            list.appendChild(row);
        });
        search.addEventListener('input', () => {
            const words = search.value.toLowerCase().split(/\s+/).filter(Boolean);
            boxes.forEach(({ column, row }) => {
                row.hidden = !words.every(word => column.name.toLowerCase().includes(word));
            });
        });
        update();
        return { node: el('div', { class: 'picker' }, [search, quick, list]), get: () => columns.map(c => c.name).filter(name => chosen.has(name)) };
    }

    /* ---------- the options form ---------- */
    const form = $('toolOptions');
    const fields = $('optionFields');

    function buildOptions() {
        fields.textContent = '';
        Object.keys(controls).forEach(key => delete controls[key]);
        config.options.forEach(option => {
            const id = 'opt_' + option.id;
            const row = el('div', { class: 'form-group tool-option' });
            const help = option.help ? el('p', { class: 'form-hint', id: id + '_help', text: option.help }) : null;
            let get;
            if (option.kind === 'columns') {
                row.appendChild(el('div', { class: 'form-label', text: option.label + (option.required ? ' *' : '') }));
                if (help) row.appendChild(help);
                const picker = columnPicker(option);
                row.appendChild(picker.node);
                get = picker.get;
            } else if (option.kind === 'checkbox') {
                const box = el('input', { type: 'checkbox', id, checked: option.default === true });
                row.appendChild(el('label', { class: 'form-checkbox', for: id }, [box, el('span', { text: option.label })]));
                if (help) row.appendChild(help);
                get = () => box.checked;
            } else {
                row.appendChild(el('label', { class: 'form-label', for: id, text: option.label + (option.required ? ' *' : '') }));
                let input;
                if (option.kind === 'column' || option.kind === 'select') {
                    input = el('select', { id, class: 'form-select' });
                    if (option.kind === 'column') {
                        if (option.optional_blank) input.appendChild(el('option', { value: '', text: option.optional_blank }));
                        else input.appendChild(el('option', { value: '', text: 'Choose a column' }));
                        columns.forEach(column => input.appendChild(el('option', { value: column.name, text: column.name })));
                    } else {
                        option.choices.forEach(([value, label]) => input.appendChild(el('option', { value, text: label })));
                        input.value = option.default;
                    }
                } else {
                    input = el('input', { id, class: 'form-input', type: option.kind === 'number' ? 'number' : 'text' });
                    if (option.default !== null && option.default !== undefined) input.value = option.default;
                    if (option.minimum !== null && option.minimum !== undefined) input.min = option.minimum;
                    if (option.maximum !== null && option.maximum !== undefined) input.max = option.maximum;
                    input.addEventListener('input', () => form.dispatchEvent(new Event('change')));
                }
                if (help) input.setAttribute('aria-describedby', id + '_help');
                row.appendChild(input);
                if (help) row.appendChild(help);
                get = () => input.value;
            }
            controls[option.id] = { get, row, option };
            fields.appendChild(row);
        });
        applyVisibility();
    }

    function currentValue(id) {
        const control = controls[id];
        if (!control) return undefined;
        const value = control.get();
        return control.option.kind === 'checkbox' ? value : value;
    }

    function applyVisibility() {
        config.options.forEach(option => {
            if (!controls[option.id]) return;                  // the form is still being built
            const conditions = Object.entries(option.show_if || {});
            const visible = conditions.every(([other, allowed]) => allowed.includes(currentValue(other)));
            controls[option.id].row.hidden = !visible;
        });
    }

    function collectOptions() {
        const values = {};
        config.options.forEach(option => {
            if (controls[option.id].row.hidden) return;        // a hidden option keeps its default on the server
            values[option.id] = controls[option.id].get();
        });
        return values;
    }

    form.addEventListener('change', () => {
        applyVisibility();
        $('changePreview').hidden = true;                       // the preview no longer matches the options
    });
    form.addEventListener('submit', event => event.preventDefault());

    /* ---------- tables ---------- */
    function fillTable(head, body, cols, rows) {
        head.textContent = '';
        body.textContent = '';
        head.appendChild(el('tr', {}, cols.map(name => el('th', { text: name }))));
        rows.forEach(row => body.appendChild(el('tr', {}, cols.map(name => {
            const value = row[name];
            return el('td', { text: value === null || value === undefined ? '' : String(value) });
        }))));
    }

    /* ---------- messages ---------- */
    const error = $('error');
    function showError(message) {
        error.textContent = message;
        error.classList.add('show');
        error.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
    function clearMessages() {
        error.classList.remove('show');
        $('results').classList.remove('show');
        $('changePreview').hidden = true;
    }

    /* ---------- the chosen file ---------- */
    const upload = DD.initUpload({
        zone: $('uploadArea'), input: $('fileInput'), sizeInfo: $('fileSizeInfo'),
        onFile: async source => {
            selectedFile = source;
            clearMessages();
            try {
                const body = DD.appendFile(new FormData(), 'file', source);
                const response = await fetch('/get-columns-with-samples', { method: 'POST', body });
                const data = await response.json();
                if (!response.ok || !data.success) throw new Error(data.error || 'The file could not be read.');
                columns = data.columns;
                buildOptions();
                form.hidden = false;
                $('toolActions').hidden = false;
                const previewBody = DD.appendFile(new FormData(), 'file', source);
                const preview = await (await fetch('/preview-data', { method: 'POST', body: previewBody })).json();
                if (preview.success) {
                    $('previewStats').textContent = Number(preview.total_rows).toLocaleString() + ' rows x ' + preview.columns.length + ' columns';
                    fillTable($('previewTableHead'), $('previewTableBody'), preview.columns, preview.rows);
                    $('previewMoreIndicator').textContent = preview.total_rows > preview.rows.length
                        ? 'Showing the first ' + preview.rows.length + ' of ' + Number(preview.total_rows).toLocaleString() + ' rows' : '';
                    $('dataPreviewSection').classList.add('show');
                }
            } catch (err) {
                form.hidden = true;
                $('toolActions').hidden = true;
                showError(err.message);
            }
        },
    });
    DD.cachedFiles({ section: $('cachedFilesSection'), list: $('cachedFilesList'), onPick: source => upload.choose(source) });

    /* ---------- requests ---------- */
    async function send(path) {
        const body = DD.appendFile(new FormData(), 'file', selectedFile);
        body.append('options', JSON.stringify(collectOptions()));
        const response = await fetch(path, { method: 'POST', body });
        let data;
        try { data = await response.json(); } catch (err) { throw new Error('The server did not answer as expected. Try again.'); }
        if (!response.ok || !data.success) throw new Error(data.error || 'The request failed.');
        return data;
    }

    function busy(button, text) {
        const original = button.textContent;
        button.disabled = true;
        button.textContent = text;
        return () => { button.disabled = false; button.textContent = original; };
    }

    const number = value => (typeof value === 'number' ? value.toLocaleString() : String(value));

    function summaryList(target, pairs) {
        target.textContent = '';
        pairs.forEach(([label, value]) => {
            target.appendChild(el('dt', { text: label }));
            target.appendChild(el('dd', { text: number(value) }));
        });
    }

    /* ---------- preview the changes ---------- */
    $('previewBtn').addEventListener('click', async () => {
        if (!selectedFile) return;
        error.classList.remove('show');
        const done = busy($('previewBtn'), 'Working…');
        try {
            const data = await send('/' + config.slug + '/preview');
            const change = data.change;
            const body = $('changeBody');
            body.textContent = '';
            const facts = [['Rows now', change.rows_before], ['Rows after', change.rows_after]];
            if (change.cells_changed !== null) facts.push(['Cells that change', change.cells_changed], ['Rows that change', change.rows_changed]);
            if (change.columns_added.length) facts.push(['Columns added', change.columns_added.join(', ')]);
            if (change.columns_removed.length) facts.push(['Columns removed', change.columns_removed.join(', ')]);
            if (change.reordered) facts.push(['Order', 'The rows are put in a different order']);
            const list = el('dl', { class: 'tool-summary' });
            summaryList(list, facts.concat(data.summary.filter(([label]) => !/^Rows (in|out|kept)$/.test(label))));
            body.appendChild(list);
            const nothing = change.rows_before === change.rows_after && change.cells_changed === 0 &&
                !change.columns_added.length && !change.columns_removed.length && !change.reordered;
            if (nothing) {
                DD.showState(body.appendChild(el('div')), 'done', 'Nothing would change',
                             'With these options the result is the same as the file you gave.');
            } else if (change.sample.length) {
                body.appendChild(el('p', { class: 'form-hint', text: 'A few of the changes (row numbers as in a spreadsheet, with the header on row 1):' }));
                const table = el('table', { class: 'preview-table' });
                table.appendChild(el('thead', {}, el('tr', {}, ['row', 'column', 'before', 'after'].map(text => el('th', { text })))));
                const rows = el('tbody');
                change.sample.forEach(item => item.cells.slice(0, 4).forEach(cell => {
                    rows.appendChild(el('tr', {}, [
                        el('td', { text: String(item.row) }), el('td', { text: cell.column }),
                        el('td', { text: cell.before === null ? '(blank)' : cell.before, class: cell.before === null ? 'is-blank' : '' }),
                        el('td', { text: cell.after === null ? '(blank)' : cell.after, class: cell.after === null ? 'is-blank' : '' }),
                    ]));
                }));
                table.appendChild(rows);
                body.appendChild(el('div', { class: 'preview-table-wrapper' }, table));
            }
            (data.notes || []).forEach(note => body.appendChild(el('p', { class: 'form-hint', text: note })));
            $('changePreview').hidden = false;
            $('changePreview').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        } catch (err) {
            showError(err.message);
        } finally {
            done();
        }
    });

    /* ---------- run ---------- */
    $('runBtn').addEventListener('click', async () => {
        if (!selectedFile) return;
        clearMessages();
        const done = busy($('runBtn'), 'Working…');
        try {
            const data = await send('/' + config.slug);
            $('resultTitle').textContent = config.name + ': done';
            summaryList($('resultSummary'), data.summary);
            const notes = $('resultNotes');
            notes.textContent = '';
            (data.notes || []).concat(data.warning ? [data.warning] : []).forEach(note => notes.appendChild(el('p', { class: 'form-hint', text: note })));
            if ((data.extra_sheets || []).length) {
                notes.appendChild(el('p', { class: 'form-hint', text: 'The file also has: ' + data.extra_sheets.join(', ') + '.' }));
            }
            $('downloadBtn').href = data.download_url;
            $('downloadBtn').setAttribute('download', data.filename);
            $('resultStats').textContent = Number(data.preview.total_rows).toLocaleString() + ' rows x ' + data.preview.columns.length + ' columns';
            if (data.preview.total_rows) {
                $('resultPreviewCard').hidden = false;
                fillTable($('resultHead'), $('resultBody'), data.preview.columns, data.preview.rows);
            } else {
                $('resultPreviewCard').hidden = true;
            }
            $('results').classList.add('show');
            $('results').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        } catch (err) {
            showError(err.message);
        } finally {
            done();
        }
    });

    /* ---------- "How it works" ---------- */
    const helpModal = $('helpModal');
    if (helpModal) {
        const close = () => { helpModal.classList.remove('show'); $('helpBtn').focus(); };
        $('helpBtn').addEventListener('click', () => { helpModal.classList.add('show'); $('helpClose').focus(); });
        $('helpClose').addEventListener('click', close);
        helpModal.addEventListener('click', event => { if (event.target === helpModal) close(); });
        document.addEventListener('keydown', event => { if (event.key === 'Escape' && helpModal.classList.contains('show')) close(); });
    }
})();
