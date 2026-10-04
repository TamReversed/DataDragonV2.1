# DataDragon — agent notes

## Setup & commands
- Python 3.12–3.14. `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`
- Run: `DATADRAGON_DATA_DIR=$(mktemp -d) .venv/bin/python datadragon.py` → http://127.0.0.1:5002 (port 5002, not 5000: macOS AirPlay owns 5000)
- Tests: `.venv/bin/python -m pytest -q` (slow perf tests: `-m slow`)
- Regenerate golden snapshots ONLY with justification: `.venv/bin/python -m pytest tests/golden --update-golden`
- Lint: `.venv/bin/python -m pyflakes datadragon.py datadragon_formula.py datadragon_regex.py datadragon_logging.py` (must print nothing)
- Audit deps: `.venv/bin/pip-audit -r requirements.txt`
- Benchmarks: `.venv/bin/python scripts/gen_synthetic.py 100000 csv /tmp/dd && .venv/bin/python scripts/bench.py /tmp/dd/syn_100000.csv <case>`

## Working rules
- Execute `REVIEW_PLAN.md` in order; track status in `PROGRESS.md` after every task.
- One task per commit, using the plan's commit message. Run the task's verification + full test suite first.
- Never weaken, skip or delete a test to make it pass. Golden snapshots change only via `--update-golden` in a commit that lists each changed snapshot and why.
- Ask the owner before: any push/remote/PR, deleting files or features, major dependency upgrades, spending money (Higgsfield), deploying.

## Gotchas
- Repo path contains spaces — quote it.
- `import datadragon` creates `uploads/` and `output/` under `DATADRAGON_DATA_DIR` (default: repo dir) and starts a daemon cleanup thread.
- `.gitignore` ignores `*.xlsx`/`*.csv`; fixtures are whitelisted under `tests/fixtures/`.
- pandas is pinned `<3` — several code paths break on pandas 3.
- All job state is in-process: run ONE gunicorn worker (`-k gthread -w 1 --threads 8`). Multi-worker is unsupported.
- Read files only via `read_data_file(path, mode='lossless'|'infer')`; write xlsx only via `excel_writer()` (formula-injection safe).
- Never use `eval`/`exec`; formulas go through `datadragon_formula.py`.
- Every job/cache/download is owner-scoped via the signed session cookie; new endpoints must check ownership.
- Never print or log cell values (PII). Use `log` (from `datadragon_logging`, JSON lines with the job id), never `print`.
- Errors: raise `UserError("message for the user")` for bad input; any other exception reaches the browser only as `Processing failed (ref ...)` (details go to the log). Routes use `@api_errors`; background jobs use `@job_worker(...)` (see `datadragon.py` near `start_job`).
- Each tool page is a Jinja template in `templates/` extending `base.html` (the shell: sidebar tree, theme switch); shared JS in `static/js/common.js`, charts in `static/js/charts.js`, shell behaviour in `static/js/shell.js`.
- New simple tools go on the scaffold: describe the tool and write one function `run(df, options) -> ToolResult` in `datadragon_tools.py`; the page (`templates/tool.html`, `static/js/tool.js`), the preview-the-changes endpoint, the output, the log and the cache come for free. Add the tool to `templates/_tools.html`, a reference test to `tests/test_scaffold_tools.py` and a golden case to `tests/golden/test_sync_tools.py`. The roadmap is `docs/TOOLSET_PLAN.md`.
- Recipes: every scaffold tool is replayable for free. A tool with its own page becomes replayable by (1) putting its logic in a function, (2) registering a `Tool(..., page=False)` for it at the bottom of `datadragon.py`, and (3) passing `steps=extend_steps(input_steps(), {...full options...})` to `cache_session_file`. The log sheet never holds typed values; the recipe file does.
- Design system "Ember & Ink": tokens in `static/css/tokens.css` (`--dd-*`, light and dark), components in `static/css/main.css`. Use the tokens, never literal colours; ember is only for the primary action and the mark. See `docs/redesign/design-system.md`. The tool list for the sidebar and hub is `templates/_tools.html`.
- Do not push: `origin` is TamReversed/DataDragonV2.1; ask the owner.
- Some notes above describe the target state (helpers introduced in Phases 1–3); check `PROGRESS.md` for what exists yet.
