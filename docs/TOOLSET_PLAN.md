# DataDragon toolset: assessment and plan

What an analyst needs from a data-wrangling tool, what DataDragon covers today, what is missing, and a phased plan
to close the gaps. Written 4 October 2026 against `main` at `8920663`.

**How this was assessed.** The current toolset was read from the code (routes, options, limits), not from the
marketing copy. The comparison with other tools (OpenRefine, Excel Power Query, Tableau Prep, Alteryx Designer,
EasyMorph, Google Dataprep) comes from knowledge of those products, not from fresh hands-on testing for this
document. Effort figures are estimates for one developer who knows the codebase, and have not been validated.

---

## 1. Where DataDragon stands

**The audience:** analysts and operations leads who receive spreadsheets (exports, vendor files, month-end
extracts) and must check, fix, combine and hand them on. Not data scientists, and not people with a database.

**What it already does well**

- **Low-friction and private.** No account, files processed on the server and deleted, results gone in 30 minutes.
  The big tools need an install, a licence or a cloud upload.
- **Honest outputs.** Every result carries a `_DataDragon_Log` sheet saying what was done; exports neutralise
  formula injection; error messages are authored, not stack traces.
- **Checks that are rare in light tools:** natural-key discovery, key-based file comparison, schema comparison,
  rule-based validation, a guided readiness pipeline with a branded report.
- **Chaining:** the result of one tool can be the input of the next ("earlier results").

**What holds it back** (detail in section 3)

1. It can describe problems better than it can fix them. Duplicates, gaps, outliers and wrong types are found, but
   the fixes are thin or missing.
2. It only matches exactly. Real files have "Acme Corp", "ACME Corp." and "Acme Corporation".
3. Nothing is repeatable. The same clean-up next month means the same clicks again.
4. Input is narrow: first sheet only, three formats, one header layout.
5. Several everyday reshaping jobs have no tool: stacking files, unpivoting, sorting, grouping, filling blanks.

---

## 2. What analysts need, and what exists

Verdict: **strong** (covers the need), **partial** (works for the simple case), **missing**.

| Stage | Need | Today | Verdict |
|---|---|---|---|
| **Get data in** | Excel, CSV | `.xlsx`, `.xls`, `.csv`; encoding fallbacks (UTF-8, cp1252, latin-1) | strong |
| | Choose a sheet; read several sheets | First sheet only; the page says when there are more | missing |
| | TSV, other delimiters, JSON, Parquet, fixed-width | None | missing |
| | Header not on row 1, junk rows above, merged headers | None | missing |
| | Files larger than memory comfortably allows | 500 MB upload limit; everything loaded into pandas | partial |
| **Understand** | Profile every column | Column Analyzer: types, statistics, missing, outliers, top values, charts, PDF | strong |
| | Find a key | Natural Key Finder (single and composite, with caps) | strong |
| | Find duplicates | Duplicate Finder: exact match on chosen columns | partial |
| | Patterns and formats (how many values look like `AA-9999`) | Semantic types only | partial |
| | Relationships between columns, drift between two versions of a file | None | missing |
| **Clean** | Trim, case, whitespace, stray characters | Normalizer has "trim whitespace"; `TRIM/UPPER/LOWER` in formulas | partial |
| | Fix types (numbers, dates, currency, booleans) | Column Normalizer, with locale options | strong |
| | Standardise categories ("NY", "N.Y.", "New York") | Find & Replace, one rule at a time | partial |
| | Near-duplicate values and rows | None | missing |
| | Fill or drop missing values | Pipeline records decisions; nothing fills | missing |
| | Remove duplicates and get the cleaned file | Gives a list of IDs to remove, not the cleaned file | partial |
| | Handle outliers | Detected in the analyzer; no action | missing |
| **Reshape** | Filter rows | Row Filter: 12 operators, AND/OR | strong |
| | Reorder, rename, delete, split, merge columns | Column Operations | strong |
| | Derived columns | Calculated Columns: 19 functions, safe evaluator | partial |
| | Pivot (long to wide summary) | Pivot Generator: six aggregations | strong |
| | Unpivot (wide to long) | None | missing |
| | Group and aggregate without a pivot layout | None | missing |
| | Sort, rank, top N, sample | None | missing |
| | Transpose | Transpose | strong |
| | Split one file into many | File Splitter: by row count only | partial |
| **Combine** | Join two files | Data Merge: one key per side, four join types | partial |
| | Join on several columns; approximate match; find non-matches | None | missing |
| | Stack files (January + February + March) | None | missing |
| | Look up and recode from a mapping table | None | missing |
| **Verify** | Rules on one column | Data Validation: required, numeric, range, list, pattern, length | strong |
| | Rules across columns, uniqueness, dates, saved rule sets | None | missing |
| | Compare two versions | Data Comparison (key-based), Schema Comparison | strong |
| | Numeric tolerance, ignore case or spacing, mapped column names | None | missing |
| | Reconcile totals; check references between files | None | missing |
| **Protect** | Anonymise | Data Anonymizer: consistent placeholders, mapping export | partial |
| | Masking, hashing, date shifting, keep the format | None | missing |
| **Repeat** | Do the same steps on next month's file | None | missing |
| | A record of what was done | Log sheet and sidecar per tool; pipeline report | partial |
| **Hand over** | Excel, CSV, zip fallbacks | Yes, with Excel-limit handling | strong |
| | Data dictionary; other formats | None | missing |

Count: 11 strong, 11 partial, 19 missing, out of 41 needs.

---

## 3. The gaps, ranked

Ranked by how often the need comes up for this audience and how badly its absence sends people back to Excel.

| # | Gap | Why it matters |
|---|---|---|
| 1 | **Repeatable recipes** | Month-end work repeats. Without replay, DataDragon saves time once; with it, every month. This is what separates a tool people try from one they keep. |
| 2 | **Fixing, not only finding** (fill blanks, remove duplicates, treat outliers, bulk text clean-up) | The analyzer and pipeline raise issues the app cannot then resolve. |
| 3 | **Approximate matching** (cluster near-duplicate values, fuzzy join) | OpenRefine's best-known feature; the commonest reason clean-up takes hours. |
| 4 | **Stack and unpivot** | Combining periodic files and turning month-columns into rows are weekly jobs. |
| 5 | **Input flexibility** (sheet choice, header row, more formats) | A tool that cannot open the file is not used at all. |
| 6 | **Stronger joins and comparisons** (multi-key, anti-join, tolerance) | The current join covers the textbook case only. |
| 7 | **Reconciliation and cross-file checks** | The audience is finance-adjacent (GL, vendors, invoices appear throughout the copy). |
| 8 | **Richer formulas and rules** | 19 functions run out quickly; validation cannot express "end date after start date". |
| 9 | **Scale** | Fine to a few hundred thousand rows; one process and in-memory pandas set the ceiling. |

---

## 4. What to build

### 4.1 Foundations (make every later tool cheaper and better)

| ID | Item | What it is |
|---|---|---|
| F1 | **Input layer** | One shared "open a file" step: sheet picker for workbooks, header-row choice with a raw preview, delimiter and encoding override, skip-rows. Add `.tsv`, `.txt`, `.json` (records), `.parquet` in; `.csv`, `.xlsx`, `.parquet`, `.json` out. |
| F2 | **Tool scaffold** | A shared page partial and server helper so a simple tool is a config plus one function: upload, preview, options, run, result, download, log, earlier results. Most pages repeat this today. |
| F3 | **Step log as data** | Extend the existing transformation log into a machine-readable step (`tool`, `options`, `input columns`). Every tool writes one. This is the prerequisite for recipes. |
| F4 | **Preview before apply** | Every transforming tool shows "before / after" on a sample and the count of rows and cells that will change, before the full run. Some tools do; make it universal. |
| F5 | **Column picker** | One shared control showing name, detected type and a sample, with search and select-by-type. Replaces five different checkbox grids. |

### 4.2 New tools

| ID | Tool | What it does | Gap |
|---|---|---|---|
| N1 | **Text Cleaner** | In bulk, on chosen columns: trim, collapse spaces, case, remove non-printing characters and accents, strip punctuation, pad, extract by pattern. Shows a count of cells changed per rule. | 2 |
| N2 | **Fill Missing** | Per column: constant, previous value (fill down), next value, mean, median, most common; or drop rows or columns above a threshold. Adds an optional "was filled" flag column. | 2 |
| N3 | **Remove Duplicates** | The action that Duplicate Finder lacks: keep first, last, or the most complete row; output the cleaned file plus the removed rows on a second sheet. | 2 |
| N4 | **Cluster & Standardise** | Groups values that are probably the same (fingerprint, n-gram and edit-distance methods), lets the user pick the spelling to keep per cluster, applies it, and exports the mapping. | 3 |
| N5 | **Append Files** | Stack two or more files or sheets. Matches columns by name with a mapping screen for mismatches; adds a source-file column. | 4 |
| N6 | **Unpivot** | Wide to long: choose identifier columns, the rest become name/value rows. The inverse of the pivot tool. | 4 |
| N7 | **Group & Summarise** | Group by columns with several aggregations at once (sum, count, distinct, min, max, mean, first, last); flat output. | 4 |
| N8 | **Sort, Rank & Sample** | Multi-column sort; rank within groups; top or bottom N; random or every-nth sample. | 4 |
| N9 | **Lookup & Recode** | Replace values using a mapping table (uploaded, pasted, or exported from N4); reports unmapped values. | 3, 6 |
| N10 | **Reconcile** | Two files, a key and amount columns: totals by key side by side, differences, a tolerance, and unmatched keys. | 7 |
| N11 | **Reference Check** | Do all values of column A in file 1 exist in column B of file 2? Lists orphans in both directions. | 7 |
| N12 | **Outlier Review** | For numeric columns: show flagged values in context; flag, cap at a percentile, or remove, with the rule recorded. | 2 |
| N13 | **Data Dictionary** | Export a document of the file: columns, types, example values, completeness, key, and room for descriptions. Reuses the analyzer and the report module. | hand over |
| N14 | **Recipes** | Record the steps of a session; save as a file; replay on a new file with a check that the columns still match; show what each step changed. | 1 |

### 4.3 Upgrades to existing tools

| ID | Tool | Upgrade |
|---|---|---|
| U1 | Data Merge | Multi-column keys; "only in left / only in right" (anti-join) outputs; approximate match on the key with a similarity score; match-rate summary. |
| U2 | Data Comparison | Numeric tolerance; ignore case and surrounding spaces; map differently named columns; summary of which columns change most. |
| U3 | Calculated Columns | More functions: date difference and add, text extract and position, regular-expression match, `CASE`/`SWITCH`, `MIN`/`MAX` across columns, rounding variants, safe number parsing. A function picker with examples. |
| U4 | Data Validation | Uniqueness, cross-column rules, date rules, "matches another file" (uses N11); save and load rule sets; errors exported per rule. |
| U5 | Row Filter | Date-aware comparisons, "in list", "between", regular expressions; keep or remove; save the rejected rows too. |
| U6 | Find & Replace | Many rules at once from a table; whole-word option; report of hits per rule. |
| U7 | File Splitter | Split by the values of a column (one file per region) and by file size, not only by row count. |
| U8 | Pivot Generator | Several aggregations together; percentage of row, column and total; group dates by month, quarter, year. |
| U9 | Data Anonymizer | Masking (keep last four), salted hashing, date shifting that preserves intervals, format-preserving replacement for emails and phones. |
| U10 | Column Analyzer | Value-pattern frequencies (`AA-9999`), histograms, correlation between numeric columns, and a two-file profile comparison (drift). |
| U11 | Readiness Pipeline | Offer the fixes, not only the findings: fill (N2), deduplicate (N3), convert flagged types, standardise (N4); each as a recorded step in the report. |
| U12 | Duplicate Finder | Option to match ignoring case, spacing and punctuation; hand over to N3 and N4. |

---

## 5. Plan

Five phases. Each ends with something an analyst can use; none depends on a later one.
Sizes: **S** about a day, **M** two to four days, **L** one to two weeks.

### Phase A — Foundations and the most-missed fixes

| Order | Item | Size | Depends on | Done when |
|---|---|---|---|---|
| A1 | F2 tool scaffold | M | — | One existing simple tool (Transpose) runs on it with unchanged output (golden snapshot identical) |
| A2 | F5 column picker | S | A1 | Used by the scaffold; keyboard and screen-reader checks pass |
| A3 | F3 step log as data | S | — | Every existing tool writes a structured step; the log sheet is unchanged for readers |
| A4 | N1 Text Cleaner | M | A1 | Each rule matches a row-by-row reference implementation on the fixtures |
| A5 | N2 Fill Missing | M | A1 | Each method checked against pandas reference; flag column optional; nothing filled without being counted |
| A6 | N3 Remove Duplicates | S | A1 | Row counts reconcile: kept + removed = original; agrees with Duplicate Finder on the same choice |
| A7 | N8 Sort, Rank & Sample | S | A1 | Stable sort; sample is reproducible with a shown seed |
| A8 | F4 preview before apply | M | A1 | Every transforming tool reports rows and cells that will change before the run |

### Phase B — Combine and reshape

| Order | Item | Size | Depends on | Done when |
|---|---|---|---|---|
| B1 | F1 input layer: sheet picker, header row, delimiter | L | — | A workbook's second sheet, a file with three junk rows, and a semicolon CSV all open in every tool |
| B2 | N5 Append Files | M | B1 | Mismatched columns are shown and mapped; row count equals the sum of inputs |
| B3 | N6 Unpivot | S | A1 | Unpivot then pivot returns the original table |
| B4 | N7 Group & Summarise | M | A1 | Matches pandas `groupby` on the fixtures, including blanks in the group columns |
| B5 | U1 Merge: multi-key and anti-join | M | — | Existing single-key snapshots unchanged; new outputs reconcile to the inputs |
| B6 | U7 Splitter by column value | S | — | One file per value; row total preserved |
| B7 | U8 Pivot upgrades | M | — | Existing snapshots unchanged |

### Phase C — Approximate matching

| Order | Item | Size | Depends on | Done when |
|---|---|---|---|---|
| C1 | Matching library: fingerprint, n-gram, edit distance, with caps on comparisons and time | M | — | Results match a brute-force reference on small inputs; a 100,000-value column finishes inside the stated budget or reports truncation |
| C2 | N4 Cluster & Standardise | L | C1 | The user's choices are applied exactly; the mapping export replays to the same result |
| C3 | N9 Lookup & Recode | S | — | Unmapped values listed; mapping from C2 loads |
| C4 | U1 Merge: approximate key match | M | C1, B5 | Each fuzzy match shows its score; nothing below the threshold is joined silently |
| C5 | U12 Duplicate Finder: normalised matching | S | C1 | Exact mode unchanged |

### Phase D — Verify and reconcile

| Order | Item | Size | Depends on | Done when |
|---|---|---|---|---|
| D1 | U4 Validation: uniqueness, cross-column, dates, saved rule sets | L | — | A rule set saved from one file loads on another; existing rule snapshots unchanged |
| D2 | N11 Reference Check | S | — | Orphans in both directions reconcile to the distinct counts |
| D3 | N10 Reconcile | M | B5 | Totals tie to independent sums; tolerance behaves at the boundary |
| D4 | U2 Comparison: tolerance, ignore case and spaces, column mapping | M | — | Default behaviour unchanged |
| D5 | N12 Outlier Review | M | — | The rule and the count of values treated are in the log |
| D6 | U3 Calculated Columns: more functions | M | — | Each function has reference tests, including blanks and wrong types; the evaluator stays without `eval` |
| D7 | U5, U6 Row Filter and Find & Replace upgrades | M | — | Existing snapshots unchanged |

### Phase E — Repeat, document, scale

| Order | Item | Size | Depends on | Done when |
|---|---|---|---|---|
| E1 | N14 Recipes: record and replay | L | A3 | A five-step session replays on a second file to the same result as doing it by hand; a missing column stops the replay with a clear message |
| E2 | U11 Pipeline applies fixes | L | A5, A6, C2 | Each fix is a recorded step and appears in the report |
| E3 | N13 Data Dictionary | M | — | Built from the analysis; uses the report module |
| E4 | U10 Analyzer: patterns, histograms, drift | L | — | Existing analysis snapshots unchanged except for added keys |
| E5 | U9 Anonymizer upgrades | M | — | Same input and salt give the same output; mapping export still round-trips |
| E6 | F1 formats: Parquet and JSON in and out | M | B1 | Round-trip preserves types on the fixtures |
| E7 | Scale: chunked CSV reading for the streaming-friendly tools, and benchmarks for every new tool in `docs/BASELINES.md` | L | — | A 2-million-row CSV filters and splits without loading whole |

**Rough total:** 9 small, 18 medium and 7 large items, which is about 16 to 30 developer-weeks if everything is
built. Phases A and B alone (about 6 to 10 weeks) close most of the everyday gaps; Phase C is the feature that most distinguishes the product; Phase E1 (recipes) is
the one most likely to make people come back.

**Suggested order if time is short:** A, then E1 (recipes) pulled forward, then C, B, D.

---

## 6. Rules every new tool follows

These already hold for the existing tools and are what "done" means here.

- **Server:** `@api_errors`, `@job_worker`, `UserError` for anything the user can cause; no file-derived text in logs.
- **Output:** written through `save_table` or `write_sheets` with a log (structure, not cell values), Excel-limit
  fallbacks, formula-injection neutralised.
- **Limits stated, not hidden:** caps on rows, comparisons or time are shown in the result when they bite.
- **Tests:** a golden snapshot of the output, and a brute-force or pandas reference test for the logic.
- **Page:** built on the scaffold and the design system; both themes; keyboard operable; one primary action;
  designed empty, loading and error states; a "How it works" panel in plain words.
- **Hub:** added to `templates/_tools.html` with a one-sentence description an analyst would recognise.
- **Performance:** a benchmark row in `docs/BASELINES.md`.

---

## 7. What not to build, and why

| Not building | Reason |
|---|---|
| Dashboards and chart building | A different product (BI). DataDragon's charts explain data quality; they are not for presenting results. |
| Database, API and cloud-storage connectors | They require stored credentials and accounts, which contradicts the "nothing kept" promise. Revisit only with a deliberate decision on accounts. |
| Accounts, sharing and collaboration | Same reason. Recipes are files the user keeps, not server-side objects. |
| A chat assistant that edits data | Sends data to a model provider unless self-hosted; conflicts with the privacy statement. A local, rule-based "suggest fixes" (U11) gives most of the value. |
| A free-form SQL tool | Powerful, but it is for a different user. Group, filter, join and sort tools cover the same ground for this audience. |

---

## 8. Decisions

Made on 4 October 2026:

1. **Scope:** all five phases.
2. **Recipes early:** yes. E1 moves to right after Phase A.
3. **Matching library:** RapidFuzz (added when Phase C starts).
4. **Hub grouping:** "Transformation" is split into Clean, Reshape, and Combine & compare (done with Phase A).

Still open:

5. **Formats:** is Parquet wanted for this audience, or only delimited text and JSON?
6. **Scale target:** comfortable at one million rows, or are a few hundred thousand enough?

## 9. Progress

| Item | Status |
|---|---|
| A1 tool scaffold | Done: `datadragon_tools.py`, `templates/tool.html`, `static/js/tool.js`, generic routes. Proven by four new tools. **Not done:** moving an existing tool (Transpose) onto it, which the plan named as the acceptance check; the existing pages were left alone to avoid risk to working tools. |
| A2 column picker | Done, inside the scaffold: search, all / none / text / number, name with kind and sample values. The existing pages still use their own checkbox grids. |
| A3 step log as data | Done for scaffold tools: the log sheet has a `Step` row (tool and settings; typed literals recorded by length only). **Not done:** the 18 existing tools do not write a step yet; that moves into the recipes work (E1), which needs it. |
| A4 Text Cleaner | Done |
| A5 Fill Missing | Done |
| A6 Remove Duplicates | Done |
| A7 Sort, Rank & Sample | Done |
| A8 preview before apply | Done for scaffold tools ("Preview the changes": counts and a few changed cells, before and after). **Not done:** the existing transforming tools. |
