/* DataDragon shell: theme switch, tool search in the sidebar (and the hub list), "/" shortcut, phone menu. */
(() => {
    const root = document.documentElement;

    /* ---------- theme ---------- */
    const toggle = document.getElementById('themeToggle');
    const systemDark = window.matchMedia('(prefers-color-scheme: dark)');
    const isDark = () => (root.dataset.theme ? root.dataset.theme === 'dark' : systemDark.matches);
    function labelTheme() {
        if (!toggle) return;
        toggle.textContent = isDark() ? 'light mode' : 'dark mode';
        toggle.setAttribute('aria-pressed', String(isDark()));
    }
    function announceTheme() {
        labelTheme();
        document.dispatchEvent(new CustomEvent('dd:themechange', { detail: { dark: isDark() } }));
    }
    if (toggle) {
        toggle.addEventListener('click', () => {
            const next = isDark() ? 'light' : 'dark';
            root.dataset.theme = next;
            try { localStorage.setItem('dd-theme', next); } catch (err) { /* private mode: the choice lasts for this page */ }
            announceTheme();
        });
        systemDark.addEventListener('change', announceTheme);
        labelTheme();
    }

    /* ---------- phone menu ---------- */
    const sidebar = document.getElementById('sidebar');
    const navToggle = document.getElementById('navToggle');
    if (sidebar && navToggle) {
        navToggle.addEventListener('click', () => {
            const open = sidebar.classList.toggle('open');
            navToggle.setAttribute('aria-expanded', String(open));
        });
    }

    /* ---------- tool search ---------- */
    const box = document.getElementById('toolSearch');
    if (!box) return;
    const status = document.getElementById('toolSearchStatus');
    const groups = Array.from(document.querySelectorAll('.tool-tree details'));
    const hubRows = Array.from(document.querySelectorAll('.hub-tool'));
    const hubGroups = Array.from(document.querySelectorAll('.hub-group'));
    const wasOpen = new Map(groups.map(group => [group, group.open]));

    function filter() {
        const words = box.value.toLowerCase().split(/\s+/).filter(Boolean);
        let shown = 0;
        groups.forEach(group => {
            let any = false;
            group.querySelectorAll('li').forEach(item => {
                const text = (item.textContent + ' ' + (item.dataset.keywords || '')).toLowerCase();
                const match = words.every(word => text.includes(word));
                item.hidden = !match;
                if (match) { any = true; shown += 1; }
            });
            group.hidden = !any;
            group.open = words.length ? any : wasOpen.get(group);
        });
        hubRows.forEach(row => {
            row.hidden = !words.every(word => row.textContent.toLowerCase().includes(word));
        });
        hubGroups.forEach(group => {
            group.hidden = !group.querySelector('.hub-tool:not([hidden])');
        });
        const empty = document.getElementById('hubEmpty');
        if (empty) empty.hidden = !(words.length && shown === 0);
        if (status) {
            status.textContent = words.length ? (shown ? shown + (shown === 1 ? ' tool' : ' tools') + ' found' : 'No tool matches') : '';
        }
    }

    box.addEventListener('input', filter);
    box.addEventListener('keydown', event => {
        if (event.key === 'Escape') { box.value = ''; filter(); }
        if (event.key === 'Enter') {                                       // Enter opens the first match
            const first = document.querySelector('.tool-tree li:not([hidden]) a');
            if (first && box.value.trim()) window.location.href = first.getAttribute('href');
        }
    });
    document.addEventListener('keydown', event => {
        const target = event.target;
        const modal = document.getElementById('testFileModal');
        const modalOpen = modal && modal.style.display === 'flex';
        const typing = modalOpen || (target && (target.closest('input, textarea, select, button, [role=dialog]') || target.isContentEditable));
        if (event.key === '/' && !typing && !event.ctrlKey && !event.metaKey && !event.altKey) {
            event.preventDefault();
            if (sidebar && navToggle && !sidebar.classList.contains('open') && getComputedStyle(navToggle).display !== 'none') {
                sidebar.classList.add('open');
                navToggle.setAttribute('aria-expanded', 'true');
            }
            box.focus();
            box.select();
        }
    });
})();
