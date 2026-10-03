"""Anonymizer: nothing original survives, blanks stay blank, mappings are lossless, verification blocks a bad save."""
import json
import os
import random
import shutil
from queue import Queue

import numpy as np
import openpyxl
import pandas as pd
import pytest

import datadragon
from helpers import make_csv, make_xlsx, output_path, post_job
from test_merge import write_rows

_counter = iter(range(10**9))


def frame(rows, columns):
    return pd.DataFrame(rows, columns=columns, dtype=object)


def blanks(series):
    return series.isna().to_numpy()


def pseudonyms_of(records, column):
    return {r["pseudonym"] for r in records if column in r["columns"]}


# ------------------------------------------------------------------ the A-07 leak
@pytest.mark.parametrize("seed", range(40))
@pytest.mark.parametrize("relationship", [False, True])
def test_nothing_original_survives_and_blanks_are_kept(seed, relationship):
    rng = random.Random(seed)
    names, emails = ["Alice", "Bob", "Carol", "Dave", None], ["a@x.io", "b@x.io", "c@x.io", None]
    rows = [(rng.choice(names), rng.choice(emails), f"id{i}") for i in range(rng.randint(1, 20))]
    df = frame(rows, ["Name", "Email", "Keep"])
    out, records = datadragon.scrub_dataframe(df, ["Name", "Email"], relationship_preserve=relationship)
    for col in ("Name", "Email"):
        assert (blanks(out[col]) == blanks(df[col])).all()                        # blanks stay blank, nothing else is
        assert set(out[col].dropna()) <= pseudonyms_of(records, col)              # every value is a pseudonym
        assert not set(out[col].dropna()) & set(df[col].dropna())                 # no original value survives
    assert (out["Keep"] == df["Keep"]).all()                                      # other columns untouched
    datadragon.verify_anonymized(df, out, ["Name", "Email"], records)             # and the guard agrees


def test_the_row_with_a_blank_partner_is_still_anonymized():
    df = frame([("Alice", "a@x"), ("Bob", None), ("Carol", "c@x")], ["Name", "Email"])
    out, _ = datadragon.scrub_dataframe(df, ["Name", "Email"], relationship_preserve=True)
    assert "Bob" not in out.to_numpy().astype(str)
    assert out["Name"].tolist() == ["Name_1", "Name_2", "Name_3"] and pd.isna(out["Email"].iloc[1])


def test_relationship_mode_pseudonyms_follow_combinations():
    df = frame([("John", "j1"), ("John", "j2"), ("John", "j1"), ("Ann", "j1"), ("John", None), ("John", None)],
               ["Name", "Email"])
    out, records = datadragon.scrub_dataframe(df, ["Name", "Email"], relationship_preserve=True)
    ids = out["Name"].tolist()
    assert ids[0] == ids[2]                                   # same pair -> same pseudonym
    assert len({ids[0], ids[1], ids[3], ids[4]}) == 4          # different pairs (incl. a blank part) differ
    assert ids[4] == ids[5]
    assert (out["Email"].dropna().to_numpy() == out["Name"][out["Email"].notna()].to_numpy()).all()  # shared id
    assert len(records) == 4                                  # one record per distinct combination


def test_all_blank_rows_stay_blank_and_use_no_pseudonym():
    df = frame([("A", "x"), (None, None), ("B", "y")], ["c1", "c2"])
    out, records = datadragon.scrub_dataframe(df, ["c1", "c2"], relationship_preserve=True)
    assert pd.isna(out.iloc[1]).all()
    assert [r["pseudonym"] for r in records] == ["c1_1", "c1_2"]       # numbering is not consumed by the blank row


# ------------------------------------------------------------------ mixed types
def test_one_true_and_text_one_are_distinguished_correctly():
    df = frame([(1,), (True,), (1.0,), ("1",), (2,), (None,)], ["Code"])
    out, records = datadragon.scrub_dataframe(df, ["Code"])
    ids = out["Code"].tolist()
    assert ids[0] == ids[2] == ids[3] == "Code_1"      # 1, 1.0 and '1' read the same
    assert ids[1] == "Code_2"                          # True is not the number 1
    assert ids[4] == "Code_3" and pd.isna(ids[5])
    assert len(records) == 3


def test_independent_columns_do_not_cascade_when_data_looks_like_pseudonyms():
    df = frame([("Vendor_2",), ("x",), ("Vendor_1",)], ["Vendor"])
    out, _ = datadragon.scrub_dataframe(df, ["Vendor"])
    assert out["Vendor"].tolist() == ["Vendor_1", "Vendor_2", "Vendor_3"]   # simultaneous, not chained


# ------------------------------------------------------------------ mapping is lossless
@pytest.mark.parametrize("relationship", [False, True])
def test_mapping_records_reverse_every_row(relationship):
    df = frame([("John", "j1"), ("John", "j2"), ("Ann", "j1"), ("Ann", None), (None, "z"), ("John", "j1")],
               ["Name", "Email"])
    out, records = datadragon.scrub_dataframe(df, ["Name", "Email"], relationship_preserve=relationship)
    for row in range(len(df)):
        for position, col in enumerate(["Name", "Email"]):
            if pd.isna(df.at[row, col]):
                assert pd.isna(out.at[row, col])
                continue
            hits = [r for r in records if r["pseudonym"] == out.at[row, col] and col in r["columns"]]
            if relationship:
                assert len(hits) == 1 and hits[0]["original"] == [None if pd.isna(v) else v for v in df.loc[row]]
            else:
                assert len(hits) == 1 and hits[0]["original"] == [df.at[row, col]]


def test_john_with_two_emails_is_fully_recorded():
    df = frame([("John", "j1"), ("John", "j2")], ["Name", "Email"])
    _, records = datadragon.scrub_dataframe(df, ["Name", "Email"], relationship_preserve=True)
    assert sorted(tuple(r["original"]) for r in records) == [("John", "j1"), ("John", "j2")]


def test_scrub_is_deterministic():
    rng = random.Random(7)
    df = frame([(rng.choice(["a", "b", None]), rng.choice(["x", "y", None])) for _ in range(30)], ["A", "B"])
    one = datadragon.scrub_dataframe(df, ["A", "B"], relationship_preserve=True)
    two = datadragon.scrub_dataframe(df, ["A", "B"], relationship_preserve=True)
    assert one[0].equals(two[0]) and one[1] == two[1]


def test_no_valid_columns_is_an_error():
    with pytest.raises(ValueError, match="No valid columns"):
        datadragon.scrub_dataframe(frame([("a",)], ["A"]), ["Nope"])


# ------------------------------------------------------------------ the tool end to end
def run_scrub(path, columns, relationship, export=True):
    q = Queue()
    session = f"scrub_test_{next(_counter)}"
    run_scrub.last_session = session
    copy = os.path.join(os.path.dirname(path), f"{session}{os.path.splitext(path)[1]}")
    shutil.copy(path, copy)
    datadragon.scrub_file_async(copy, columns, relationship, export, q, session)
    msgs = []
    while not q.empty():
        msgs.append(q.get())
    return msgs[-1]


def test_mapping_key_file_is_a_complete_record_list(tmp_path):
    path = make_xlsx(tmp_path / "s.xlsx", ["Name", "Email"], [["Alice", "a@x"], ["Bob", None], ["Alice", "a@x"]],
                     text_cols=("Name", "Email"))
    final = run_scrub(path, ["Name", "Email"], True)
    assert final["stage"] == "done", final
    mapping_path = os.path.join(datadragon.app.config["OUTPUT_FOLDER"], *final["mapping_url"].split("/")[-2:])
    data = json.load(open(mapping_path))
    assert data["relationship_preserved"] is True
    assert sorted((tuple(m["columns"]), tuple(m["original"]), m["pseudonym"]) for m in data["mappings"]) == [
        (("Name", "Email"), ("Alice", "a@x"), "Name_1"), (("Name", "Email"), ("Bob", None), "Name_2")]


def test_no_mapping_file_unless_requested(tmp_path):
    path = write_rows(tmp_path / "s.csv", ["Name"], [["Alice"]])
    assert run_scrub(path, ["Name"], False, export=False)["mapping_url"] is None


def test_csv_in_csv_out_keeps_other_columns_and_blanks(tmp_path):
    path = write_rows(tmp_path / "s.csv", ["Name", "Note"], [["Alice", "007"], [None, "x"], ["Alice", None]])
    final = run_scrub(path, ["Name"], False)
    assert final["download_url"].endswith(".csv")
    saved = pd.read_csv(output_path(final), dtype=str, keep_default_na=False, na_values=[""])
    assert saved["Name"].tolist()[0] == saved["Name"].tolist()[2] == "Name_1" and pd.isna(saved["Name"].iloc[1])
    assert saved["Note"].tolist()[0] == "007" and pd.isna(saved["Note"].iloc[2])


def test_a_failed_pre_save_check_saves_nothing(tmp_path, monkeypatch):
    path = write_rows(tmp_path / "s.csv", ["Name"], [["Alice"], ["Bob"]])
    monkeypatch.setattr(datadragon, "scrub_dataframe", lambda df, cols, rel, q, sid: (df.copy(), [
        {"columns": ["Name"], "original": ["Alice"], "pseudonym": "Name_1"}]))  # a broken scrubber that leaks
    final = run_scrub(path, ["Name"], False)
    assert final["stage"] == "error" and "verification failed" in final["message"]
    assert "Alice" not in final["message"] and "Bob" not in final["message"]
    assert "download_url" not in final


def test_a_failed_post_save_check_removes_the_saved_file(tmp_path, monkeypatch):
    path = write_rows(tmp_path / "s.csv", ["Name"], [["Alice"]])
    real, state = datadragon.verify_anonymized, {"calls": 0, "existed": None}

    def saved_files():
        folder = datadragon.job_dir(run_scrub.last_session, create=False)
        return [f for f in os.listdir(folder) if f.startswith("anonymized_")] if os.path.isdir(folder) else []

    def flaky(*args):
        state["calls"] += 1
        if state["calls"] == 2:                       # the read-back check, after the file was written
            state["existed"] = bool(saved_files())
            raise ValueError("Anonymization verification failed: simulated read-back problem. Nothing was saved.")
        return real(*args)

    monkeypatch.setattr(datadragon, "verify_anonymized", flaky)
    final = run_scrub(path, ["Name"], False)
    assert final["stage"] == "error" and state["calls"] == 2
    assert state["existed"] is True                   # there really was a saved file at that moment ...
    assert saved_files() == []                        # ... and it is gone afterwards
    assert "download_url" not in final


def test_cell_values_are_never_written_to_the_log(tmp_path, capsys):
    secrets_in_data = ["Zebediah Quux", "q.uux@secret.example", "Xanthippe Fnord"]
    path = write_rows(tmp_path / "s.csv", ["Name", "Email"],
                      [[secrets_in_data[0], secrets_in_data[1]], [secrets_in_data[2], None]])
    for relationship in (False, True):
        run_scrub(path, ["Name", "Email"], relationship)
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert not any(secret in text for secret in secrets_in_data)
    assert "Anonymization complete" in text  # it still logs counts


def test_route_runs_both_modes(client, tmp_path):
    path = make_xlsx(tmp_path / "s.xlsx", ["Name", "Email"], [["Alice", "a@x"], ["Bob", None]],
                     text_cols=("Name", "Email"))
    for relationship in ("false", "true"):
        final, _ = post_job(client, "/scrub-data", {"file": path},
                            {"columns[]": ["Name", "Email"], "relationship_preserve": relationship,
                             "export_mapping": "true"})
        assert final["stage"] == "done", final
        values = {c.value for row in openpyxl.load_workbook(output_path(final)).active.iter_rows(min_row=2) for c in row}
        assert not values & {"Alice", "Bob", "a@x"}
