# DataDragon

A set of browser-based tools for cleaning, checking, comparing and reshaping tabular data (Excel and CSV files).
It is a small Flask application: you upload a file, a tool processes it in memory, and you download the result.

## The tools

| Group | Tool | What it does |
|---|---|---|
| Data pipelines | Data Readiness Pipeline | Guided workflow: analyze shape, triage gaps, discover keys, anonymize, report (PDF) |
| Quality and analysis | Column Analyzer | Profiling: statistics, semantic types, missing values, outliers, charts, PDF report |
| | Duplicate Finder | Duplicate rows on chosen columns, with a removal list |
| | Natural Key Finder | Smallest column combinations that identify every row |
| | Data Validation | Required, numeric, range, list, pattern and length rules |
| | Column Normalizer | Currency, numbers, dates, booleans and percentages to proper types |
| | Data Anonymizer | Replaces values with consistent placeholders, optionally exporting the mapping |
| Transformation | Transpose, Pivot Table Generator | Flip rows and columns; pivot tables |
| | File Splitter | Splits a big workbook into chunks (for example 40,000 rows) in a ZIP |
| | Find & Replace, Row Filter | Text replacement (with regex), AND/OR row filters |
| | Column Operations, Calculated Columns | Reorder, rename, split, merge columns; formula columns |
| Comparison and merging | Data Merge, Data Comparison, Schema Comparison | Joins; added/removed/changed rows; column structure differences |
| Documents | PDF to Word | Converts a PDF to an editable Word file |

Results of one tool can be picked as the input of the next ("earlier results" on each page) without uploading them again.

## Quick start

Python 3.12 to 3.14.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python datadragon.py
```

Then open <http://127.0.0.1:5002>. The server listens on `127.0.0.1` only, so it stays on your machine.

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `5002` | Port of `python datadragon.py` (macOS uses 5000 for AirPlay) |
| `HOST` | `127.0.0.1` | Address of `python datadragon.py`; set `0.0.0.0` only behind something you trust |
| `DATADRAGON_DATA_DIR` | the project folder | Where `uploads/` and `output/` are created |
| `DATADRAGON_SECRET_KEY` | random per start | Signs the browser session cookie; set it so sessions survive a restart |
| `DATADRAGON_HTTPS` | unset | `1` marks the session cookie `Secure` (use when served over HTTPS) |
| `DATADRAGON_OUTPUT_TTL_MIN` | `30` | Minutes a result stays downloadable before it is deleted |
| `DATADRAGON_UPLOAD_TTL_MIN` | `30` | Minutes before a stray upload (failed or abandoned request) is deleted |
| `DATADRAGON_MAX_JOBS` | `4` | Background jobs running at the same time |
| `DATADRAGON_TRUST_PROXY` | unset | `1` behind exactly one reverse proxy: believe its `X-Forwarded-For/-Host/-Proto` |
| `DATADRAGON_ALLOWED_ORIGINS` | none | Extra host names allowed to POST (comma separated), if you cannot use the setting above |
| `DATADRAGON_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

## Deploying

All job state (progress streams, results, sessions, the earlier-results list) lives in the memory of **one process**.
Run exactly **one gunicorn worker with threads**, as the `Procfile` does:

```bash
gunicorn -k gthread -w 1 --threads 8 -t 0 -b 0.0.0.0:${PORT:-5002} datadragon:app
```

- More than one worker is **not supported**: a progress stream that lands on another worker answers "Session not found".
- Use a threaded worker (`gthread`). gunicorn's default sync worker is killed at its timeout and takes running jobs with it.
- `GET /healthz` answers `{"ok": true}` for a load balancer.
- Behind a reverse proxy set `DATADRAGON_TRUST_PROXY=1` (and `DATADRAGON_HTTPS=1` when it terminates TLS).
- The app has no login. Anyone who can reach it can use it; each browser only sees its own jobs and results.

## Tests and checks

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q                      # the suite (about 90 seconds)
.venv/bin/python -m pyflakes datadragon.py datadragon_formula.py datadragon_regex.py datadragon_logging.py
.venv/bin/pip-audit -r requirements.txt            # known vulnerabilities in the dependencies
.venv/bin/python scripts/check_innerhtml.py        # file-derived text must be escaped in the page scripts
.venv/bin/python scripts/contrast.py               # WCAG contrast of the colour tokens
```

Golden snapshots (`tests/golden/`) pin the output of every tool; change them only on purpose
(`pytest tests/golden --update-golden`) and review the diff. Benchmarks: `scripts/bench.py`, results in `docs/BASELINES.md`.

## What happens to your data

This is the same statement as the Security & Privacy page of the app (`/security-info`).

- Files are processed in the server's memory and are **not** sent to any other service. The pages load some
  JavaScript libraries (Chart.js, jsPDF, html2canvas) from public CDNs, with integrity hashes. That is code only; your
  data never goes there.
- An uploaded file is deleted as soon as its data is loaded (the longer-running tools remove it when their job ends).
- Result files can be downloaded repeatedly for 30 minutes, then they are deleted whether or not you downloaded them.
- While they exist, files sit **unencrypted** on the disk of the machine running DataDragon.
- Cell values are never written to the logs on purpose. Error details are logged, and a library can put a value into
  its error text, so treat the logs as sensitive.
- Exports are written so that text that merely looks like a formula (`=...`, `+...`, `@...`) stays text.

## Project layout

```
datadragon.py            Flask app: routes, jobs, tools
datadragon_formula.py    Safe evaluator for Calculated Columns (no eval)
datadragon_regex.py      Time-limited regular expressions for Find & Replace and Validation
datadragon_logging.py    JSON-style logging with the job id
templates/               One page per tool (base.html is the shared layout)
static/                  css/ (main.css, a11y.css), js/ (common.js, charts.js), images/
tests/                   pytest suite, golden snapshots, fixtures
scripts/                 benchmarks, synthetic data, lint-style checks
docs/                    benchmarks and screenshots
design/source/           master artwork (not served)
```

Contributor and agent notes are in `CLAUDE.md`; the review that shaped the current state is in `REVIEW_PLAN.md`
with progress in `PROGRESS.md`.
