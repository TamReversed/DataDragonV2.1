/* DataDragon shared front-end core.
 *
 * Globals: escapeHtml, formatFileSize, DD (initUpload, cachedFiles, appendFile, startJob, cancelJob, modal).
 * Every file-derived string that goes into markup must pass through escapeHtml; the helpers here build their DOM
 * with textContent so they never need it.
 */

function escapeHtml(text) {
    // Escapes quotes as well, so the result is safe inside attribute values (value="...") too
    return String(text === null || text === undefined ? '' : text).replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[ch]));
}

function formatFileSize(bytes) {
    if (!bytes) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1);
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

const DD = (() => {
    /* ---------- a file the server already has: sent as an id instead of uploading the bytes again ---------- */
    function cachedSource(info) {
        return { cacheId: info.cache_id, name: info.filename, size: undefined, isCached: true, info };
    }

    /** Add a chosen file to a FormData: the file itself, or `cache_id.<field>` for a cached result. */
    function appendFile(formData, field, source) {
        if (!source) return formData;
        if (source.isCached) {
            formData.append(field === 'file' ? 'cache_id' : 'cache_id.' + field, source.cacheId);
        } else {
            formData.append(field, source);
        }
        return formData;
    }

    /* ---------- upload zone: click, keyboard (the input is the tab stop), drag and drop, file name shown ---------- */
    function initUpload({ zone, input, sizeInfo, onFile }) {
        const controller = { selected: null };

        function show(source) {
            controller.selected = source;
            if (sizeInfo) {
                sizeInfo.textContent = source.isCached
                    ? source.name + ' (result from an earlier tool)'
                    : source.name + ' · ' + formatFileSize(source.size);
                sizeInfo.style.display = 'block';
            }
            zone.classList.add('has-file');
            if (onFile) onFile(source);
        }

        zone.addEventListener('click', event => {
            if (event.target !== input) input.click();
        });
        input.addEventListener('change', () => {
            if (input.files && input.files.length) show(input.files[0]);
        });
        zone.addEventListener('dragover', event => {
            event.preventDefault();
            zone.classList.add('dragover');
        });
        zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
        zone.addEventListener('drop', event => {
            event.preventDefault();
            zone.classList.remove('dragover');
            if (event.dataTransfer.files.length) {
                input.files = event.dataTransfer.files;
                show(event.dataTransfer.files[0]);
            }
        });

        controller.choose = source => {
            input.value = '';          // a cached result replaces any file chosen before
            show(source);
        };
        return controller;
    }

    /* ---------- results of earlier tools (owner-scoped on the server) ---------- */
    async function cachedFiles({ section, list, onPick }) {
        try {
            const response = await fetch('/get-cached-files');
            if (!response.ok) return;
            const data = await response.json();
            if (!data.files || !data.files.length) return;
            list.textContent = '';
            data.files.forEach(file => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'cached-file-item';
                const info = document.createElement('div');
                info.className = 'cached-file-info';
                const name = document.createElement('div');
                name.className = 'cached-file-name';
                name.textContent = file.filename;
                const meta = document.createElement('div');
                meta.className = 'cached-file-meta';
                meta.textContent = Number(file.rows).toLocaleString() + ' rows x ' + Number(file.cols) + ' columns';
                info.append(name, meta);
                const source = document.createElement('div');
                source.className = 'cached-file-source';
                source.textContent = file.source_tool;
                button.append(info, source);
                button.addEventListener('click', () => onPick(cachedSource(file)));
                list.appendChild(button);
            });
            section.style.display = 'block';
            section.classList.add('show');
        } catch (err) {
            console.error('Could not load earlier results:', err);
        }
    }

    /* ---------- jobs: POST, then follow the server's progress stream ---------- */
    const MAX_RECONNECTS = 5;

    /**
     * Start a job and follow it. Returns { cancel() }.
     *  onProgress(message) for every progress message, onDone(message) once with the final message,
     *  onError(text) once if the job could not start, failed, was cancelled, or the stream was lost for good.
     */
    function startJob(url, formData, { onProgress, onDone, onError }) {
        let jobId = null;
        let source = null;
        let finished = false;
        let cancelledHere = false;
        let reconnects = 0;

        function fail(text) {
            if (finished) return;
            finished = true;
            if (source) source.close();
            if (onError && !cancelledHere) onError(text);     // the page that pressed Cancel resets itself
        }

        function follow() {
            source = new EventSource('/progress/' + encodeURIComponent(jobId));
            source.onmessage = event => {
                reconnects = 0;
                let message;
                try {
                    message = JSON.parse(event.data);
                } catch (err) {
                    return;                      // keep-alive or garbled line
                }
                if (message.error) return fail(message.error);
                if (message.stage === 'error') return fail(message.message || 'The job failed');
                if (message.stage === 'done') {
                    finished = true;
                    source.close();
                    if (onDone) onDone(message);
                    return;
                }
                if (onProgress) onProgress(message);
            };
            source.onerror = () => {
                if (finished) return;
                source.close();
                reconnects += 1;
                if (reconnects > MAX_RECONNECTS) {
                    return fail('The connection to the server was lost. The job may still be running; try again in a moment.');
                }
                setTimeout(() => { if (!finished) follow(); }, 500 * reconnects);
            };
        }

        (async () => {
            try {
                let response;
                try {
                    response = await fetch(url, { method: 'POST', body: formData });
                } catch (err) {
                    return fail('Could not reach the server. Check that it is running.');
                }
                let data = null;
                try {
                    data = await response.json();
                } catch (err) {
                    // not JSON (e.g. a proxy error page)
                }
                if (!response.ok || !data || !data.success || !data.session_id) {
                    return fail((data && data.error) || 'The server could not start the job (HTTP ' + response.status + ')');
                }
                jobId = data.session_id;
                await new Promise(resolve => setTimeout(resolve, 100));   // let the job register its progress channel
                if (!finished) follow();
            } catch (err) {
                fail(err.message || 'Something went wrong');
            }
        })();

        return {
            cancel() {
                cancelledHere = true;
                if (source) source.close();
                if (jobId && !finished) fetch('/jobs/' + encodeURIComponent(jobId) + '/cancel', { method: 'POST' }).catch(() => {});
                finished = true;
            },
        };
    }

    /* ---------- a small accessible dialog (use instead of alert/confirm) ---------- */
    function modal({ title, body, actions }) {
        const previous = document.activeElement;
        const overlay = document.createElement('div');
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;align-items:center;' +
            'justify-content:center;z-index:10000;padding:16px';
        const dialog = document.createElement('div');
        dialog.setAttribute('role', 'dialog');
        dialog.setAttribute('aria-modal', 'true');
        dialog.setAttribute('aria-label', title);
        dialog.style.cssText = 'max-width:520px;width:100%;background:var(--bg-secondary,#16161d);color:var(--text-primary,#fff);' +
            'border:1px solid var(--border-subtle,#333);border-radius:12px;padding:24px;box-shadow:0 20px 60px rgba(0,0,0,.5)';
        const heading = document.createElement('h2');
        heading.textContent = title;
        heading.style.cssText = 'margin:0 0 12px;font-size:18px';
        const content = document.createElement('div');
        if (body instanceof Node) content.appendChild(body); else content.textContent = body || '';
        const bar = document.createElement('div');
        bar.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;margin-top:20px';

        function close() {
            document.removeEventListener('keydown', onKey, true);
            overlay.remove();
            if (previous && previous.focus) previous.focus();
        }
        function onKey(event) {
            if (event.key === 'Escape') { event.stopPropagation(); close(); }
            if (event.key === 'Tab') {                       // keep focus inside the dialog
                const items = dialog.querySelectorAll('button');
                if (!items.length) return;
                const first = items[0], last = items[items.length - 1];
                if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
                else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
            }
        }
        (actions && actions.length ? actions : [{ label: 'OK', primary: true }]).forEach(action => {
            const button = document.createElement('button');
            button.type = 'button';
            button.textContent = action.label;
            button.className = action.primary ? 'btn btn-primary' : 'btn btn-secondary';
            button.addEventListener('click', () => { close(); if (action.onClick) action.onClick(); });
            bar.appendChild(button);
        });
        dialog.append(heading, content, bar);
        overlay.appendChild(dialog);
        document.body.appendChild(overlay);
        document.addEventListener('keydown', onKey, true);
        overlay.addEventListener('click', event => { if (event.target === overlay) close(); });
        (bar.querySelector('button') || dialog).focus();
        return { close };
    }

    return { cachedSource, appendFile, initUpload, cachedFiles, startJob, modal };
})();
