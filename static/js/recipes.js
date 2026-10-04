/* The Recipes page: save the steps behind an earlier result, and replay a recipe file on another file.
 * Built with DOM calls only, so nothing from a file or a recipe can become markup. */
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
    const count = value => (value === null || value === undefined ? '–' : Number(value).toLocaleString());

    let dataFile = null;
    let recipeFile = null;

    /* ---------- results of this session that can be saved as a recipe ---------- */
    fetch('/get-cached-files').then(r => r.json()).then(data => {
        const savable = (data.files || []).filter(file => file.recipe_steps && file.recipe_steps.length);
        if (!savable.length) return;
        savable.forEach(file => {
            $('savableList').appendChild(el('div', { class: 'recipe-savable-item' }, [
                el('div', {}, [
                    el('div', { class: 'cached-file-name', text: file.filename }),
                    el('div', { class: 'cached-file-meta', text: file.recipe_steps.map((name, i) => (i + 1) + '. ' + name).join('   ') }),
                ]),
                el('a', { class: 'btn btn-sm', href: '/recipes/export/' + encodeURIComponent(file.cache_id),
                          text: 'Save recipe (' + file.recipe_steps.length + (file.recipe_steps.length === 1 ? ' step)' : ' steps)') }),
            ]));
        });
        $('savableSection').hidden = false;
    }).catch(() => {});

    /* ---------- the two files ---------- */
    function ready() {
        $('runBtn').disabled = $('checkBtn').disabled = !(dataFile && recipeFile);
    }
    function reset() {
        $('error').classList.remove('show');
        $('checkResult').hidden = true;
        $('results').classList.remove('show');
    }
    const dataUpload = DD.initUpload({
        zone: $('dataZone'), input: $('dataInput'), sizeInfo: $('dataInfo'),
        onFile: source => { dataFile = source; reset(); ready(); },
    });
    DD.cachedFiles({ section: $('cachedFilesSection'), list: $('cachedFilesList'), onPick: source => dataUpload.choose(source) });

    function describeOptions(options) {
        return Object.entries(options || {}).filter(([, value]) => value !== null && value !== '' && value !== false &&
            !(Array.isArray(value) && !value.length)).map(([key, value]) => {
            let shown = value;
            if (Array.isArray(value)) shown = value.length && typeof value[0] === 'object' ? value.length + ' item(s)' : value.join(', ');
            else if (value === true) shown = 'yes';
            return key.replace(/_/g, ' ') + ': ' + shown;
        }).join(' · ');
    }

    DD.initUpload({
        zone: $('recipeZone'), input: $('recipeInput'), sizeInfo: $('recipeInfo'),
        onFile: async source => {
            reset();
            recipeFile = null;
            $('recipeContents').hidden = true;
            try {
                const recipe = JSON.parse(await source.text());
                if (!recipe || !recipe.datadragon_recipe || !Array.isArray(recipe.steps)) throw new Error('not a recipe');
                const list = $('recipeStepList');
                list.textContent = '';
                recipe.steps.forEach(step => list.appendChild(el('li', {}, [
                    el('strong', { text: step.name || step.tool }),
                    el('div', { class: 'cached-file-meta', text: describeOptions(step.options) }),
                ])));
                $('recipeContents').hidden = false;
                recipeFile = source;
            } catch (err) {
                showError('That file is not a DataDragon recipe. Recipes are saved from a result and end in .recipe.json.');
            }
            ready();
        },
    });

    function showError(message) {
        $('error').textContent = message;
        $('error').classList.add('show');
        $('error').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    async function send(path) {
        const body = DD.appendFile(new FormData(), 'file', dataFile);
        body.append('recipe', recipeFile);
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
        return () => { button.textContent = original; ready(); };
    }

    function stepTable(reports) {
        const table = el('table', { class: 'preview-table' });
        table.appendChild(el('thead', {}, el('tr', {}, ['step', 'tool', 'rows in', 'rows out', 'cells changed', 'columns added or removed'].map(text => el('th', { text })))));
        const body = el('tbody');
        reports.forEach(report => {
            const columns = [].concat(report.columns_added.map(name => '+ ' + name), report.columns_removed.map(name => '− ' + name)).join(', ');
            body.appendChild(el('tr', {}, [
                el('td', { text: String(report.step) }), el('td', { text: report.tool }),
                el('td', { text: count(report.rows_in), class: 'num' }), el('td', { text: count(report.rows_out), class: 'num' }),
                el('td', { text: count(report.cells_changed), class: 'num' }), el('td', { text: columns }),
            ]));
        });
        table.appendChild(body);
        return el('div', { class: 'preview-table-wrapper' }, table);
    }

    $('checkBtn').addEventListener('click', async () => {
        reset();
        const done = busy($('checkBtn'), 'Checking…');
        try {
            const data = await send('/recipes/check');
            const body = $('checkBody');
            body.textContent = '';
            if (data.fits) {
                $('checkTitle').textContent = 'The recipe fits this file';
                body.appendChild(el('p', { class: 'form-hint', text: count(data.rows_in) + ' rows in, ' + count(data.rows_out) + ' rows out. Nothing has been written yet.' }));
                body.appendChild(stepTable(data.reports));
            } else {
                $('checkTitle').textContent = 'The recipe does not fit this file';
                DD.showState(body, 'error', 'A step cannot run on this file', data.problem);
            }
            $('checkResult').hidden = false;
            $('checkResult').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        } catch (err) {
            showError(err.message);
        } finally {
            done();
        }
    });

    $('runBtn').addEventListener('click', async () => {
        reset();
        const done = busy($('runBtn'), 'Replaying…');
        try {
            const data = await send('/recipes/run');
            const body = $('resultBody');
            body.textContent = '';
            body.appendChild(el('p', { class: 'form-hint', text: count(data.rows_in) + ' rows in, ' + count(data.rows_out) + ' rows out, ' + data.reports.length + (data.reports.length === 1 ? ' step.' : ' steps.') }));
            body.appendChild(stepTable(data.reports));
            if (data.warning) body.appendChild(el('p', { class: 'form-hint', text: data.warning }));
            $('downloadBtn').href = data.download_url;
            $('downloadBtn').setAttribute('download', data.filename);
            $('resultStats').textContent = count(data.preview.total_rows) + ' rows x ' + data.preview.columns.length + ' columns';
            const head = $('resultHead'), rows = $('resultBody2');
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
            done();
        }
    });

    /* ---------- "How it works" ---------- */
    const helpModal = $('helpModal');
    const close = () => { helpModal.classList.remove('show'); $('helpBtn').focus(); };
    $('helpBtn').addEventListener('click', () => { helpModal.classList.add('show'); $('helpClose').focus(); });
    $('helpClose').addEventListener('click', close);
    helpModal.addEventListener('click', event => { if (event.target === helpModal) close(); });
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && helpModal.classList.contains('show')) close(); });
})();
