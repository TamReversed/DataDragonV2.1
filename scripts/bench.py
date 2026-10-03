"""Benchmark harness. Each case runs in a fresh subprocess so peak RSS is per case.

Usage:  python scripts/bench.py FILE CASE [CASE ...] [--timeout SECONDS]
Cases:  read write_xlsx analyze dup_lowcard dup_highcard uniqueid12 pivot validate compare1key split40k keyworst
Output: one JSON line per case (wall_s, peak_rss_mb) on stdout. Uploads/outputs go to a temp dir.
keyworst ignores FILE's contents: it builds 20 three-valued columns x ROWS rows (env BENCH_KEYWORST_ROWS, default 20000).
"""
import contextlib
import io
import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from queue import Queue

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = ['read', 'write_xlsx', 'analyze', 'dup_lowcard', 'dup_highcard', 'uniqueid12', 'pivot', 'validate',
         'compare1key', 'split40k', 'keyworst']


def peak_mb():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / 1048576 if sys.platform == 'darwin' else r / 1024  # macOS reports bytes, Linux KB


def worker(case, src):
    os.environ['DATADRAGON_DATA_DIR'] = tempfile.mkdtemp(prefix='dd_bench_')
    sys.path.insert(0, ROOT)
    import warnings
    warnings.filterwarnings('ignore')
    with contextlib.redirect_stdout(io.StringIO()):
        import datadragon as d
        import numpy as np
        import pandas as pd
    up = d.app.config['UPLOAD_FOLDER']

    def cp(tag=''):
        dst = os.path.join(up, f'x_y_{tag}' + os.path.basename(src))
        shutil.copy(src, dst)
        return dst

    q, info = Queue(), {}
    with contextlib.redirect_stdout(io.StringIO()):
        if case == 'keyworst':
            n = int(os.environ.get('BENCH_KEYWORST_ROWS', 20000))
            rng = np.random.default_rng(1)
            df = pd.DataFrame({f'c{i}': rng.integers(0, 3, n) for i in range(20)})
            p = os.path.join(up, 'w_x_worst.csv')
            df.to_csv(p, index=False)
            t = time.perf_counter()
            d.find_unique_identifier_async(p, list(df.columns), q, 's')
        elif case == 'read':
            t = time.perf_counter()
            info['rows'] = len(d.read_data_file(src))
        elif case == 'write_xlsx':
            df = d.read_data_file(src)
            out = os.path.join(up, 'write_bench.xlsx')
            t = time.perf_counter()
            d.write_excel(df, out)
        elif case == 'analyze':
            df = d.read_data_file(src)
            t = time.perf_counter()
            d.analyze_dataframe(df)
        elif case == 'dup_lowcard':
            t = time.perf_counter()
            d.find_duplicates_async(cp(), 'id', ['first_name', 'last_name', 'category'], q, 's')
        elif case == 'dup_highcard':
            t = time.perf_counter()
            d.find_duplicates_async(cp(), 'id', ['first_name', 'last_name', 'invoice_date'], q, 's')
        elif case == 'uniqueid12':
            cols = [c for c in d.read_data_file(src).columns if c not in ('id', 'email', 'phone')]
            t = time.perf_counter()
            d.find_unique_identifier_async(cp(), cols, q, 's')
        elif case == 'pivot':
            t = time.perf_counter()
            d.generate_pivot_async(cp(), ['region', 'category'], ['status'], ['amount'], 'sum', None, q, 's')
        elif case == 'validate':
            rules = [{'column': 'email', 'type': 'required'},
                     {'column': 'amount', 'type': 'range', 'value': {'min': 0, 'max': 40000}},
                     {'column': 'qty', 'type': 'numeric'}]
            t = time.perf_counter()
            d.validate_data_async(cp(), json.dumps(rules), q, 's')
        elif case == 'compare1key':
            t = time.perf_counter()
            d.compare_files_async(cp('a'), cp('b'), ['id'], ['amount', 'status'], q, 's')
        elif case == 'split40k':
            t = time.perf_counter()
            d.split_excel_file(src, os.path.join(d.app.config['OUTPUT_FOLDER'], 'split_bench'), 40000)
        else:
            raise SystemExit(f'unknown case {case}')
    wall = time.perf_counter() - t
    msgs = []
    while not q.empty():
        msgs.append(q.get())
    last = msgs[-1] if msgs else {}
    if last.get('stage') == 'error':
        info['error'] = str(last.get('message'))[:120]
    if 'minimal_columns' in last:
        info['key'] = last['minimal_columns']
    print(json.dumps({'case': case, 'file': os.path.basename(src), 'wall_s': round(wall, 2),
                      'peak_rss_mb': round(peak_mb()), **info}, default=str))


def main():
    args = sys.argv[1:]
    if args and args[0] == '--worker':
        return worker(args[1], args[2])
    timeout = 900
    if '--timeout' in args:
        i = args.index('--timeout')
        timeout = int(args[i + 1])
        del args[i:i + 2]
    src, cases = args[0], args[1:]
    for case in cases:
        try:
            r = subprocess.run([sys.executable, __file__, '--worker', case, src], capture_output=True, text=True,
                               timeout=timeout)
            lines = [ln for ln in r.stdout.strip().splitlines() if ln.startswith('{')]
            print(lines[-1] if lines else json.dumps({'case': case, 'file': os.path.basename(src),
                                                      'failed': (r.stderr or 'no output').strip()[-200:]}), flush=True)
        except subprocess.TimeoutExpired:
            print(json.dumps({'case': case, 'file': os.path.basename(src), 'timeout_s': timeout}), flush=True)


if __name__ == '__main__':
    main()
