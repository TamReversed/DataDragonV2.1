# PROGRESS

Source of truth for task order and verification: `REVIEW_PLAN.md` section 6. Update this file after every task.
Owner decisions D1–D11: plan defaults apply (nothing was answered inline). Local commits only; never push (D9).

Status: `todo` · `doing` · `done` · `blocked`

## Phase 0 — Safety net & baselines
| Task | Status | Verified by |
|---|---|---|
| T0.0 Commit REVIEW_PLAN.md | done | `git log` (f8caa29) |
| T0.1 Pin pandas<3, xlrd, dev requirements | done | fresh `.venv` installs; `pandas 2.3.3`, `import xlrd` OK |
| T0.2 `DATADRAGON_DATA_DIR` | done | import with temp dir creates `uploads/`+`output/` there |
| T0.3 Test harness + smoke tests | done | 22 smoke tests pass |
| T0.4 Golden fixtures | done | generator idempotent (`git diff --exit-code tests/fixtures` clean on rerun) |
| T0.5 Golden snapshots + strict xfails | done | `pytest tests`: 59 passed, 1 skipped, 29 xfailed, 0 XPASS; golden run twice with identical results |
| T0.6 Perf baselines | done | `docs/BASELINES.md` + `docs/baselines_raw.jsonl`; all 27 cases ran, none hit the 900 s cap (commit 65c5dec) |
| T0.7 CI + agent docs | done | CI YAML parses (ruby YAML); locally: pytest green (59 passed, 1 skipped, 28 xfailed with slow excluded), pyflakes 34 warnings / 1 undefined name, pip-audit reports Flask/Werkzeug CVEs (both non-gating for now) |

## Phase 1 — P0 fixes
T1.1 todo · T1.2 todo · T1.3 todo · T1.4 todo · T1.5 todo · T1.6 todo · T1.7 todo · T1.8 todo · T1.9 todo · T1.10 todo · T1.11 todo · T1.12 todo

## Phase 2 — P1
T2.1 todo · T2.2 todo · T2.3 todo · T2.4 todo · T2.5 todo · T2.6 todo · T2.7 todo · T2.8 todo · T2.9 todo · T2.10 todo · T2.11 todo · T2.12 todo · T2.13 todo · T2.14 todo

## Phase 3 — P2
T3.1 todo · T3.2 todo · T3.3 todo · T3.4 todo · T3.5 todo · T3.6 todo · T3.7 todo · T3.8 todo · T3.9 todo · T3.10 todo · T3.11 todo

## Phase 4 — P3
T4.1 todo · T4.2 todo · T4.3 todo

## Phase 5 — Optional media (needs owner approval, spends credits)
M-1 todo (not approved) · M-2 todo (not approved)

---

## Notes, deviations and discoveries (Phase 0)

**Deviations from the plan**
- Golden tests are grouped in 3 files (`test_async_tools.py`, `test_sync_tools.py`, `test_pipeline.py`) instead of one file per tool; each tool still has its own snapshot(s) in `tests/golden/expected/`.
- Tools are driven through their HTTP routes (`post_job` / `post_form` in `tests/helpers.py`) rather than calling `*_async` functions directly, except `analyze_dataframe`, which is called directly.
- CI: the pyflakes and pip-audit steps are `continue-on-error` for now (repo has 1 undefined name and 14 CVEs, so gating would be red from day one). Flip to gating in T2.6/T4.1 (pyflakes) and T2.1 (pip-audit).
- Extra step T0.0 (committing the plan on its own).
- `.venv/` was already ignored as `.venv`; added `.venv/` as well.

**Audit findings that did NOT reproduce exactly as reported (audit ran on pandas 3.0.6; repo is now pinned to 2.3.3)**
- A-04 single-key int/float mismatch: on the single-key path raw values go into a set, so `1 == 1.0` matches. The bug is on the **composite-key** path (2+ key columns, `str(1)` vs `str(1.0)`); the known-bug test uses two key columns. Null-key and duplicate-key compare bugs reproduce.
- A-11 "date detection is dead" and A-12 transpose crash / CONCAT-with-NaN errors are pandas-3 behaviours (moot while pinned `<3`). The `ID` → Postal Code misdetection **does** reproduce on 2.x. Transpose crash was not re-verified on 2.x; `transpose` golden snapshot passes on 2.3.3.
- Reproduced on 2.3.3 (so real regardless of pin): A-01, A-02, A-03, A-04 (composite, null, duplicate), A-05, A-06 (both), A-07, A-08 (decimal + dates), A-09 (key uniqueness), A-10 (range + pattern), A-13 (alternation + literal "nan"), A-19, A-21, G-01, G-02, G-03, G-04, G-06, G-07, H-01, H-04, H-05.

**Other discoveries**
- A normalizer date test initially passed vacuously: unparseable dates become blank cells that are trimmed from the sheet, so row count shrinks (2 rows in, 1 out). Test now asserts the row count.
- The pivot "Source Data" sheet already writes the hostile `Notes` text as a live formula (G-06 affects every export path, snapshots record `f:` cell types).
- Normalizer snapshot shows `BigId` coming out as `#VALUE!` (error cell) and `EuroNum` as 25. Locked in as current behaviour.
- `tests/golden/snapshot.py` once dropped any key named `id` (case-insensitive), which silently deleted columns named `ID` from snapshots. Fixed before the commit.
- Known-bug test for `.xls` is skipped (no `xlwt`; not adding it as a dependency, per plan).
- Test suite takes ~45 s, of which ~21 s is the ReDoS test (marked `slow`; CI excludes it).
