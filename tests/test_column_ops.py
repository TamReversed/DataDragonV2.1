"""T2.10 / A-13: Column Operations keep blanks blank, treat the split delimiter literally, refuse rename
collisions and keep unlisted columns when reordering."""
import json

import openpyxl

from helpers import make_csv, post_form, sync_output


def sheets(path):
    wb = openpyxl.load_workbook(path)
    return {ws.title: [[c.value for c in row] for row in ws.iter_rows()] for ws in wb.worksheets}


def op(client, tmp_path, text, operation, **fields):
    path = make_csv(tmp_path / "c.csv", text)
    resp, payload = post_form(client, "/column-operations", {"file": path}, {"operation": operation, **fields})
    return resp, payload


def rows(payload):
    return sheets(sync_output(payload))["Sheet1"]


def test_split_delimiter_is_literal_and_blank_stays_blank(client, tmp_path):
    resp, payload = op(client, tmp_path, 'k,v\n1,a | b\n2,\n3,c | d\n', "split", column_to_split="v", delimiter=" | ")
    assert resp.status_code == 200, payload
    assert rows(payload) == [["k", "v_part1", "v_part2"], ["1", "a", "b"], ["2", None, None], ["3", "c", "d"]]


def test_split_regex_characters_are_literal(client, tmp_path):
    resp, payload = op(client, tmp_path, 'v\na.b\nc.d\n', "split", column_to_split="v", delimiter=".")
    assert [r for r in rows(payload)[1:]] == [["a", "b"], ["c", "d"]]


def test_merge_blank_parts_are_empty_text(client, tmp_path):
    resp, payload = op(client, tmp_path, 'a,b\nx,y\n,y\nx,\n', "merge", columns_to_merge=json.dumps(["a", "b"]),
                       separator="-", new_column_name="m")
    assert resp.status_code == 200, payload
    assert [r[2] for r in rows(payload)[1:]] == ["x-y", "-y", "x-"]


def test_rename_collision_with_existing_column_is_refused(client, tmp_path):
    resp, payload = op(client, tmp_path, 'a,b\n1,2\n', "rename", renames=json.dumps({"a": "b"}))
    assert resp.status_code == 400 and "b" in payload["error"]


def test_rename_two_columns_to_the_same_name_is_refused(client, tmp_path):
    resp, payload = op(client, tmp_path, 'a,b,c\n1,2,3\n', "rename", renames=json.dumps({"a": "z", "b": "z"}))
    assert resp.status_code == 400


def test_rename_swap_is_allowed(client, tmp_path):
    resp, payload = op(client, tmp_path, 'a,b\n1,2\n', "rename", renames=json.dumps({"a": "b", "b": "a"}))
    assert resp.status_code == 200 and rows(payload)[0] == ["b", "a"]


def test_reorder_keeps_unlisted_columns(client, tmp_path):
    resp, payload = op(client, tmp_path, 'a,b,c,d\n1,2,3,4\n', "reorder", column_order=json.dumps(["c", "a"]))
    assert resp.status_code == 200 and rows(payload)[0] == ["c", "a", "b", "d"]


def test_reorder_duplicate_names_are_used_once(client, tmp_path):
    resp, payload = op(client, tmp_path, 'a,b\n1,2\n', "reorder", column_order=json.dumps(["b", "b"]))
    assert resp.status_code == 200 and rows(payload)[0] == ["b", "a"]
