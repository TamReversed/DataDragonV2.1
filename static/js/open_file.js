/* Open a File: show the file as stored, let the user say where the table is, and produce a clean table.
 * Built with DOM calls only, so nothing from the file can become markup. */
(() => {
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

    let file = null;

    function showError(message) {
        $('error').textContent = message;
        $('error').classList.add('show');
        $('error').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    function body(extra = {}) {
        const data = DD.appendFile(new FormData(), 'file', file);
        if (!$('sheetGroup').hidden) data.append('sheet', $('sheetSelect').value);
        if (!$('delimiterGroup').hidden) data.append('delimiter', $('delimiterSelect').value);
        Object.entries(extra).forEach(([key, value]) => data.append(key, value));
        return data;
    }

    async function post(path, data) {
        const response = await fetch(path, { method: 'POST', body: data });
        let answer;
        try { answer = await response.json(); } catch (err) { throw new Error('The server did not answer as expected. Try again.'); }
        if (!response.ok || !answer.success) throw new Error(answer.error || 'The request failed.');
        return answer;
    }

    function markHeader() {
        const header = Number($('headerRow').value) || 0;
        [...$('gridBody').rows].forEach((row, index) => {
            row.classList.toggle('is-header', index + 1 === header);
            row.classList.toggle('is-above', index + 1 < header);
            row.querySelector('.row-pick').setAttribute('aria-pressed', String(index + 1 === header));
        });
    }

    function drawGrid(answer) {
        const tbody = $('gridBody');
        tbody.textContent = '';
        answer.rows.forEach((cells, index) => {
            const pick = el('button', { type: 'button', class: 'row-pick', text: String(index + 1),
                                        'aria-label': 'Row ' + (index + 1) + ' holds the column names' });
            pick.addEventListener('click', () => { $('headerRow').value = index + 1; markHeader(); });
            const row = el('tr', {}, [el('td', { class: 'row-number' }, pick)]);
            for (let i = 0; i < answer.columns; i++) {
                const value = cells[i];
                row.appendChild(el('td', { text: value === null || value === undefined ? '' : value }));
            }
            tbody.appendChild(row);
        });
        $('gridStats').textContent = 'first ' + answer.rows.length + ' rows · ' + answer.columns + ' columns';
    }

    async function inspect(first) {
        $('error').classList.remove('show');
        $('results').classList.remove('show');
        try {
            const answer = await post('/open-file/inspect', first ? DD.appendFile(new FormData(), 'file', file) : body());
            if (first) {
                $('sheetGroup').hidden = !answer.sheets.length;
                $('sheetSelect').textContent = '';
                answer.sheets.forEach(name => $('sheetSelect').appendChild(el('option', { value: name, text: name })));
                if (answer.sheet) $('sheetSelect').value = answer.sheet;
                $('delimiterGroup').hidden = !answer.delimiter;
                if (answer.delimiter) $('delimiterSelect').value = answer.delimiter;
            }
            $('headerRow').value = answer.suggested_header_row;
            $('headerRow').max = Math.max(answer.rows.length, 1);
            drawGrid(answer);
            markHeader();
            $('settings').hidden = false;
            $('actions').hidden = false;
        } catch (err) {
            $('settings').hidden = true;
            $('actions').hidden = true;
            showError(err.message);
        }
    }

    DD.initUpload({
        zone: $('uploadArea'), input: $('fileInput'), sizeInfo: $('fileSizeInfo'),
        onFile: source => { file = source; inspect(true); },
    });
    $('sheetSelect').addEventListener('change', () => inspect(false));
    $('delimiterSelect').addEventListener('change', () => inspect(false));
    $('headerRow').addEventListener('input', markHeader);

    $('openBtn').addEventListener('click', async () => {
        $('error').classList.remove('show');
        $('results').classList.remove('show');
        const button = $('openBtn');
        button.disabled = true;
        button.textContent = 'Opening…';
        try {
            const answer = await post('/open-file', body({ header_row: $('headerRow').value || '0', drop_empty: $('dropEmpty').checked }));
            const summary = $('resultSummary');
            summary.textContent = '';
            const facts = [['Rows', Number(answer.rows).toLocaleString()], ['Columns', String(answer.columns)]];
            if (answer.sheet) facts.push(['Sheet', answer.sheet]);
            if (answer.delimiter) facts.push(['Separated by', answer.delimiter]);
            facts.forEach(([label, value]) => { summary.appendChild(el('dt', { text: label })); summary.appendChild(el('dd', { text: value })); });
            $('downloadBtn').href = answer.download_url;
            $('downloadBtn').setAttribute('download', answer.filename);
            $('resultStats').textContent = Number(answer.preview.total_rows).toLocaleString() + ' rows x ' + answer.preview.columns.length + ' columns';
            const head = $('resultHead'), rows = $('resultBody');
            head.textContent = '';
            rows.textContent = '';
            head.appendChild(el('tr', {}, answer.preview.columns.map(name => el('th', { text: name }))));
            answer.preview.rows.forEach(row => rows.appendChild(el('tr', {}, answer.preview.columns.map(name =>
                el('td', { text: row[name] === null || row[name] === undefined ? '' : String(row[name]) })))));
            $('results').classList.add('show');
            $('results').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        } catch (err) {
            showError(err.message);
        } finally {
            button.disabled = false;
            button.textContent = 'Open this table';
        }
    });

    const helpModal = $('helpModal');
    const close = () => { helpModal.classList.remove('show'); $('helpBtn').focus(); };
    $('helpBtn').addEventListener('click', () => { helpModal.classList.add('show'); $('helpClose').focus(); });
    $('helpClose').addEventListener('click', close);
    helpModal.addEventListener('click', event => { if (event.target === helpModal) close(); });
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && helpModal.classList.contains('show')) close(); });
})();
