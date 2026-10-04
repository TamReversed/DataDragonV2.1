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
- Each tool page is a Jinja template in `templates/` extending `base.html`; shared JS in `static/js/common.js`, charts in `static/js/charts.js`.
- Do not push: `origin` is TamReversed/DataDragonV2.1; ask the owner.
- Some notes above describe the target state (helpers introduced in Phases 1–3); check `PROGRESS.md` for what exists yet.
