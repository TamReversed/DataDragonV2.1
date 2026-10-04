/* Append Files: several files, a table of how their columns line up, and the stacked result.
 * Built with DOM calls only, so nothing from a file can become markup. */
(() => {
    const MAX_FILES = 10;
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
    const looseName = name => String(name).trim().replace(/\s+/g, ' ').toLowerCase();

    const sources = [];                 // [{ source, name, columns: [names] | null, renames: {column: target} }]

    function showError(message) {
        $('error').textContent = message;
        $('error').classList.add('show');
        $('error').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    async function add(source) {
        $('error').classList.remove('show');
        $('results').classList.remove('show');
        if (sources.length >= MAX_FILES) { showError('At most ' + MAX_FILES + ' files can be appended at once.'); return; }
        const entry = { source, name: source.name, columns: null, renames: {} };
        sources.push(entry);
        draw();
        try {
            const body = DD.appendFile(new FormData(), 'file', source);
            const response = await fetch('/get-columns', { method: 'POST', body });
            const data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.error || 'The file could not be read.');
            entry.columns = data.columns.map(column => column.name);
        } catch (err) {
            sources.splice(sources.indexOf(entry), 1);
            showError(source.name + ': ' + err.message);
        }
        draw();
    }

    function move(index, by) {
        const [entry] = sources.splice(index, 1);
        sources.splice(index + by, 0, entry);
        draw();
    }

    /* The name a column ends up with: its mapping if it has one, and one spelling per loose name when asked */
    function targets() {
        const loose = $('loose').checked;
        const spelling = {};
        return sources.map(entry => (entry.columns || []).map(column => {
            const target = entry.renames[column] || column;
            const key = loose ? looseName(target) : target;
            if (!(key in spelling)) spelling[key] = target;
            return spelling[key];
        }));
    }

    function draw() {
        const list = $('fileList');
        list.textContent = '';
        sources.forEach((entry, index) => {
            list.appendChild(el('div', { class: 'append-item' }, [
                el('div', {}, [
                    el('div', { class: 'cached-file-name', text: (index + 1) + '. ' + entry.name }),
                    el('div', { class: 'cached-file-meta', text: entry.columns ? entry.columns.length + ' columns' : 'reading…' }),
                ]),
                el('div', { class: 'btn-group' }, [
                    el('button', { type: 'button', class: 'btn btn-sm', text: 'Up', disabled: index === 0, 'aria-label': 'Move ' + entry.name + ' up', onclick: () => move(index, -1) }),
                    el('button', { type: 'button', class: 'btn btn-sm', text: 'Down', disabled: index === sources.length - 1, 'aria-label': 'Move ' + entry.name + ' down', onclick: () => move(index, 1) }),
                    el('button', { type: 'button', class: 'btn btn-sm', text: 'Remove', 'aria-label': 'Remove ' + entry.name, onclick: () => { sources.splice(index, 1); draw(); } }),
                ]),
            ]));
        });

        const ready = sources.length >= 2 && sources.every(entry => entry.columns);
        $('runBtn').disabled = !ready;
        $('matchSection').hidden = !ready;
        if (!ready) return;

        const named = targets();
        const union = [...new Set(named.flat())];
        const partial = union.filter(column => !named.every(columns => columns.includes(column)));
        $('matchSummary').textContent = partial.length
            ? union.length + ' columns in the result. ' + partial.length + ' of them ' + (partial.length === 1 ? 'is' : 'are') +
              ' not in every file; where one is missing, the cells stay blank. If a column has another name in one file, say what it is the same as.'
            : union.length + ' columns, and every file has all of them.';

        const table = $('matchTable');
        table.textContent = '';
        table.appendChild(el('thead', {}, el('tr', {}, [el('th', { text: 'column in the result' })].concat(
            sources.map((entry, index) => el('th', { text: (index + 1) + '. ' + entry.name }))))));
        const body = el('tbody');
        union.forEach(column => {
            const row = el('tr', {}, [el('td', { text: column })]);
            sources.forEach((entry, index) => {
                const position = named[index].indexOf(column);
                if (position < 0) { row.appendChild(el('td', { class: 'lacks', text: 'not in this file' })); return; }
                const original = entry.columns[position];
                const everywhere = named.every(columns => columns.includes(column));
                if (everywhere && !entry.renames[original]) { row.appendChild(el('td', { class: 'has', text: original === column ? 'yes' : 'yes, as "' + original + '"' })); return; }
                // a column that is not in every file can be declared the same as another column of the result
                const select = el('select', { 'aria-label': 'Column ' + original + ' of ' + entry.name });
                select.appendChild(el('option', { value: '', text: 'yes: keep "' + original + '" as it is' }));
                const current = entry.renames[original] || '';
                union.filter(other => other === current || (other !== column && !named[index].includes(other)))
                    .forEach(other => select.appendChild(el('option', { value: other, text: 'the same as "' + other + '"' })));
                select.value = current;
                select.addEventListener('change', () => {
                    if (select.value) entry.renames[original] = select.value; else delete entry.renames[original];
                    draw();
                });
                row.appendChild(el('td', { class: 'has' }, select));
            });
            body.appendChild(row);
        });
        table.appendChild(body);
    }

    /* ---------- choosing files ---------- */
    const zone = $('uploadArea'), input = $('fileInput');
    zone.addEventListener('click', event => { if (event.target !== input) input.click(); });
    input.addEventListener('change', async () => { for (const file of input.files) await add(file); input.value = ''; });
    zone.addEventListener('dragover', event => { event.preventDefault(); zone.classList.add('dragover'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
    zone.addEventListener('drop', async event => {
        event.preventDefault();
        zone.classList.remove('dragover');
        for (const file of event.dataTransfer.files) await add(file);
    });
    DD.cachedFiles({ section: $('cachedFilesSection'), list: $('cachedFilesList'), onPick: source => add(source) });
    $('loose').addEventListener('change', draw);

    /* ---------- run ---------- */
    $('runBtn').addEventListener('click', async () => {
        $('error').classList.remove('show');
        $('results').classList.remove('show');
        const button = $('runBtn');
        button.disabled = true;
        button.textContent = 'Appending…';
        try {
            const body = new FormData();
            const renames = {};
            sources.forEach((entry, index) => {
                DD.appendFile(body, 'file' + index, entry.source);
                if (Object.keys(entry.renames).length) renames[index] = entry.renames;
            });
            body.append('renames', JSON.stringify(renames));
            body.append('loose', $('loose').checked);
            body.append('source_column', $('sourceColumn').checked);
            const response = await fetch('/append-files', { method: 'POST', body });
            let data;
            try { data = await response.json(); } catch (err) { throw new Error('The server did not answer as expected. Try again.'); }
            if (!response.ok || !data.success) throw new Error(data.error || 'The request failed.');

            const summary = $('resultSummary');
            summary.textContent = '';
            const facts = data.rows_per_file.map(([name, rows], index) => [(index + 1) + '. ' + name, Number(rows).toLocaleString() + (rows === 1 ? ' row' : ' rows')]);
            facts.push(['Total', Number(data.rows).toLocaleString() + ' rows, ' + data.columns + ' columns']);
            facts.forEach(([label, value]) => { summary.appendChild(el('dt', { text: label })); summary.appendChild(el('dd', { text: value })); });
            const notes = $('resultNotes');
            notes.textContent = '';
            Object.entries(data.partial_columns).forEach(([column, files]) => notes.appendChild(
                el('p', { class: 'form-hint', text: '"' + column + '" is blank for the rows of: ' + files.join(', ') + '.' })));
            if (data.warning) notes.appendChild(el('p', { class: 'form-hint', text: data.warning }));
            $('downloadBtn').href = data.download_url;
            $('downloadBtn').setAttribute('download', data.filename);
            $('resultStats').textContent = Number(data.preview.total_rows).toLocaleString() + ' rows x ' + data.preview.columns.length + ' columns';
            const head = $('resultHead'), rows = $('resultBody');
            head.textContent = '';
            rows.textContent = '';
            head.appendChild(el('tr', {}, data.preview.columns.map(name => el('th', { text: name }))));
            data.preview.rows.forEach(row => rows.appendChild(el('tr', {}, data.preview.columns.map(name =>
                el('td', { text: row[name] === null || row[name] === undefined ? '' : String(row[name]) })))));
            $('results').classList.add('show');
            $('results').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        } catch (err) {
            showError(err.message);
        } finally {
            button.textContent = 'Append the files';
            button.disabled = !(sources.length >= 2 && sources.every(entry => entry.columns));
        }
    });

    const helpModal = $('helpModal');
    const close = () => { helpModal.classList.remove('show'); $('helpBtn').focus(); };
    $('helpBtn').addEventListener('click', () => { helpModal.classList.add('show'); $('helpClose').focus(); });
    $('helpClose').addEventListener('click', close);
    helpModal.addEventListener('click', event => { if (event.target === helpModal) close(); });
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && helpModal.classList.contains('show')) close(); });
})();
