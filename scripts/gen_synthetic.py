"""Generate deterministic synthetic data (15 mixed columns) for benchmarks.

Usage: python scripts/gen_synthetic.py N {csv|xlsx} OUTDIR   ->  OUTDIR/syn_N.csv|xlsx
"""
import os
import sys
import time

import numpy as np
import pandas as pd


def make_frame(n, seed=42):
    rng = np.random.default_rng(seed)
    first = np.array(['James', 'Mary', 'John', 'Linda', 'Robert', 'Patricia', 'Michael', 'Susan', 'David',
                      'Karen', 'Wei', 'Aisha', 'Carlos', 'Priya', 'Olga'])
    last = np.array(['Smith', 'Jones', 'Brown', 'Lee', 'Garcia', 'Khan', 'Nguyen', 'Patel', 'Silva', 'Muller',
                     'Kim', 'Rossi'])
    f, l = rng.choice(first, n), rng.choice(last, n)
    return pd.DataFrame({
        'id': [f'PR-{i:07d}' for i in range(n)],
        'first_name': f,
        'last_name': l,
        'email': [f'{a.lower()}.{b.lower()}{i}@example.com' for i, (a, b) in enumerate(zip(f, l))],
        'phone': [f'555-{a}-{b}' for a, b in zip(rng.integers(100, 999, n), rng.integers(1000, 9999, n))],
        'invoice_date': (pd.Timestamp('2023-01-01') + pd.to_timedelta(rng.integers(0, 730, n), unit='D')).strftime('%Y-%m-%d'),
        'amount': np.round(rng.uniform(10, 50000, n), 2),
        'qty': rng.integers(1, 50, n),
        'category': rng.choice(np.array(['Hardware', 'Software', 'Services', 'Consulting', 'Travel', 'Office']), n),
        'region': rng.choice(np.array(['NA', 'EMEA', 'APAC', 'LATAM']), n),
        'status': rng.choice(np.array(['Pending', 'Approved', 'Paid', 'Rejected']), n),
        'zip': [f'{z:05d}' for z in rng.integers(1000, 99999, n)],
        'discount': np.where(rng.random(n) < 0.3, np.nan, np.round(rng.random(n) * 0.3, 3)),
        'notes': np.where(rng.random(n) < 0.6, None, rng.choice(np.array(['rush', 'backorder', 'gift', 'net30']), n)),
        'vendor_code': [f'V{v:04d}' for v in rng.integers(0, 500, n)],
    })


def main():
    n, fmt, outdir = int(sys.argv[1]), sys.argv[2], sys.argv[3]
    os.makedirs(outdir, exist_ok=True)
    df = make_frame(n)
    out = os.path.join(outdir, f'syn_{n}.{fmt}')
    t = time.perf_counter()
    df.to_csv(out, index=False) if fmt == 'csv' else df.to_excel(out, index=False)
    print(f'{out}  write {time.perf_counter() - t:.2f}s')


if __name__ == '__main__':
    main()
