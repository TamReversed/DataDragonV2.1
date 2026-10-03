# Performance baselines

Captured **before any fix** (commit `5489353`, T0.6). Re-run after each phase and add a column/section; raw output is in `baselines_raw.jsonl`.

- Machine: Apple M5 Pro, 24 GB RAM, macOS
- Python 3.14.5, pandas 2.3.3, openpyxl 3.1.5, Flask 3.0.0
- Data: `scripts/gen_synthetic.py` (15 mixed columns, `numpy` seed 42). CSV inputs unless noted.
- Each case runs in a fresh subprocess; `peak_rss_mb` is that process's peak RSS (about 120 MB of it is the interpreter plus imports).
- Cap per case: 900 s. No case hit it.

## Commands

```bash
D=/path/to/scratch   # any writable dir
for n in 10000 100000; do
  .venv/bin/python scripts/gen_synthetic.py $n csv  $D
  .venv/bin/python scripts/gen_synthetic.py $n xlsx $D
done
.venv/bin/python scripts/gen_synthetic.py 500000 csv $D

.venv/bin/python scripts/bench.py --timeout 900 $D/syn_$n.csv read analyze dup_lowcard dup_highcard uniqueid12 pivot validate compare1key
.venv/bin/python scripts/bench.py --timeout 900 $D/syn_$n.xlsx read write_xlsx split40k      # n = 10000, 100000
.venv/bin/python scripts/bench.py --timeout 900 $D/syn_100000.csv keyworst                   # 20 three-valued columns x 20,000 rows
.venv/bin/python scripts/bench.py --timeout 900 $D/syn_500000.csv read analyze pivot validate
```

## Results (wall seconds / peak RSS MB)

| Case | What it runs | 10k | 100k | 500k |
|---|---|---|---|---|
| `read` (csv) | `read_data_file` | 0.01 / 121 | 0.08 / 194 | 0.41 / 505 |
| `read` (xlsx) | `read_data_file` | 0.63 / 134 | 6.23 / 264 | not run |
| `write_xlsx` | `DataFrame.to_excel` | 0.65 / 178 | 6.54 / 758 | not run |
| `analyze` | `analyze_dataframe` | 0.14 / 123 | 0.57 / 208 | 2.55 / 627 |
| `dup_lowcard` | duplicates on first_name+last_name+category (about 1,080 groups) | 0.39 / 136 | 2.89 / 270 | not run |
| `dup_highcard` | duplicates on first_name+last_name+invoice_date (about 23k groups at 100k) | 0.18 / 132 | **40.03** / 246 | not run |
| `uniqueid12` | natural-key search over 12 columns | 0.75 / 183 | 7.91 / 847 | not run |
| `pivot` | region x category by status, sum of amount (a 24-cell result) | 1.64 / 242 | **18.09 / 1,417** | **89.29 / 5,990** |
| `validate` | 3 rules (required, range, numeric) | 0.80 / 188 | 8.18 / 767 | **41.34 / 3,668** |
| `compare1key` | compare two copies on `id`, comparing amount+status | 4.60 / 148 | **335.23** / 356 | not run (projected hours) |
| `split40k` | `split_excel_file`, 40,000-row chunks, xlsx input | 1.31 / 178 | 12.62 / 387 | not run |
| `keyworst` | natural-key search, 20 columns x 20,000 rows, no key exists | 10.99 / 141 (20k rows) | | |

Notes:
- Reads, analysis and splitting scale linearly. Anything that writes xlsx costs about 65 µs per row.
- `compare1key` is quadratic (335 s at 100k rows against 4.6 s at 10k: about 73x for 10x the data).
- `dup_highcard` grows with rows x groups (about 220x from 10k to 100k).
- `pivot` and `validate` use far more memory than their output needs: 5.9 GB and 3.7 GB at 500k rows.
- `keyworst` ended with "No combination ... can create unique identifiers", as designed for that input. It tests about 21.7k candidates.

## Targets (from REVIEW_PLAN.md section 7)

| Case | Baseline | Target |
|---|---|---|
| `compare1key` 100k | 335 s | < 15 s |
| `dup_highcard` 100k | 40 s | < 5 s |
| `pivot` 500k | 89 s / 5.99 GB | < 20 s / < 1.5 GB |
| `validate` 500k | 41 s / 3.67 GB | < 10 s / < 1.2 GB |
| `keyworst` 20 cols | 11 s | < 10 s or reports `truncated` |
| `write_xlsx` 100k | 6.5 s | <= 4 s |

## After fixes

Same machine, data and harness as above (`scripts/bench.py`, CSV inputs from `scripts/gen_synthetic.py`).

| Case | Baseline (before) | After | Task | Target |
|---|---|---|---|---|
| `compare1key` 100k | 335.23 s / 356 MB | **1.61 s / 376 MB** (about 208x faster) | T1.7 | < 15 s |
| `compare1key` 10k | 4.60 s / 148 MB | 0.19 s / 149 MB | T1.7 | |
| `dup_highcard` 100k | 40.03 s / 246 MB | **0.89 s / 263 MB** (45x faster) | T1.8 | < 5 s |
| `dup_lowcard` 100k | 2.89 s / 270 MB | 0.69 s / 255 MB | T1.8 | |
| `dup_highcard` 10k | 0.18 s / 132 MB | 0.06 s / 133 MB | T1.8 | |
| `write_xlsx` 100k | 6.54 s / 758 MB (pandas, openpyxl engine) | **3.37 s / 447 MB** (direct xlsxwriter in `write_excel`) | T2.3 | <= 4 s |
| `split40k` 100k (xlsx in, xlsx out) | 12.62 s / 387 MB | 9.88 s / 324 MB | T2.3 | |
