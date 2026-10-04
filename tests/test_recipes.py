"""Recipes: the steps behind a result are recorded, saved as a file and replayed on another file."""
import io
import json

import openpyxl
import pandas as pd
import pytest

import datadragon_tools as tools
from datadragon_tools import ToolError


def csv(df):
    buffer = io.BytesIO()
    df.to_csv(buffer, index=False)
    buffer.seek(0)
    return buffer


def table(seed):
    """A messy little table; `seed` varies the values so that the two files differ."""
    names = ["  acme corp ", "Beta  LLC", "acme corp", None, "Gamma", "beta llc ", "Delta", "  acme corp "]
    return pd.DataFrame({
        "vendor": names,
        "region": ["north", "South", None, "north", "EAST", None, "south", "north"],
        "amount": [str(v * seed) for v in (10, 20, 30, 40, 50, 60, 70, 10)],
        "note": ["ok", "ok", "check", "ok", None, "check", "ok", "ok"],
    })


# The five steps of the session, each as (url, form data); the input of every step after the first is the result before it
STEPS = [
    ("/text-cleaner", {"options": json.dumps({"columns": ["vendor", "region"], "case": "upper"})}),
    ("/fill-missing", {"options": json.dumps({"columns": ["region"], "method": "constant", "value": "UNKNOWN"})}),
    ("/find-replace", {"find_text": "LLC", "replace_text": "Ltd", "column": "vendor", "case_sensitive": "true"}),
    ("/row-filter", {"conditions": json.dumps([{"column": "note", "operator": "equals", "value": "ok"}])}),
    ("/remove-duplicates", {"options": json.dumps({"columns": ["vendor"], "keep": "first"})}),
    ("/sort-and-sample", {"options": json.dumps({"sort1": "amount", "direction1": "descending"})}),
]


def newest_cache(client):
    files = client.get("/get-cached-files").get_json()["files"]
    return max(files, key=lambda f: f["timestamp"])


def by_hand(client, df):
    """Run the session step by step, chaining through the earlier-results cache. Returns the last cache entry."""
    data = {"file": (csv(df), "month.csv")}
    for url, form in STEPS:
        response = client.post(url, data={**data, **form}, content_type="multipart/form-data")
        assert response.status_code == 200 and response.get_json()["success"], (url, response.get_json())
        data = {"cache_id": newest_cache(client)["cache_id"]}
    return newest_cache(client)


def first_sheet(client, entry_or_url):
    url = entry_or_url if isinstance(entry_or_url, str) else f"/download-cached-file/{entry_or_url['cache_id']}"
    return pd.read_excel(io.BytesIO(client.get(url).data), dtype=object)


def test_a_six_step_session_replays_on_a_second_file_to_the_same_result_as_doing_it_by_hand(client):
    last = by_hand(client, table(1))
    assert last["recipe_steps"] == ["Text Cleaner", "Fill Missing", "Find & Replace", "Row Filter", "Remove Duplicates",
                                    "Sort, Rank & Sample"]
    exported = client.get(f"/recipes/export/{last['cache_id']}")
    assert exported.status_code == 200 and "attachment" in exported.headers["Content-Disposition"]
    recipe = exported.get_json()
    assert recipe["datadragon_recipe"] == 1 and len(recipe["steps"]) == 6
    assert recipe["steps"][1]["options"]["value"] == "UNKNOWN"            # a recipe keeps what was typed (the log does not)

    second = table(3)
    expected = first_sheet(client, by_hand(client, second))               # the same steps, by hand, on the second file
    replayed = client.post("/recipes/run", data={"file": (csv(second), "next.csv"),
                                                 "recipe": (io.BytesIO(exported.data), "steps.recipe.json")},
                           content_type="multipart/form-data").get_json()
    assert replayed["success"] and len(replayed["reports"]) == 6
    got = first_sheet(client, replayed["download_url"])
    pd.testing.assert_frame_equal(got.reset_index(drop=True), expected.reset_index(drop=True))
    assert [r["tool"] for r in replayed["reports"]] == last["recipe_steps"]
    assert replayed["reports"][0]["rows_in"] == 8 and replayed["reports"][-1]["rows_out"] == len(expected)

    workbook = openpyxl.load_workbook(io.BytesIO(client.get(replayed["download_url"]).data))
    assert workbook.sheetnames[:2] == ["Result", "Recipe steps"] and "Step 5 Removed rows" in workbook.sheetnames
    log = {row[0].value: row[1].value for row in workbook["_DataDragon_Log"].iter_rows()}
    assert log["Tool"] == "Recipe" and "UNKNOWN" not in json.dumps(log)   # typed values stay out of the log


def test_a_missing_column_stops_the_replay_and_names_the_step(client):
    last = by_hand(client, table(1))
    recipe = client.get(f"/recipes/export/{last['cache_id']}").data
    without_region = table(2).drop(columns=["region"])
    files = lambda: {"file": (csv(without_region), "next.csv"), "recipe": (io.BytesIO(recipe), "steps.recipe.json")}
    check = client.post("/recipes/check", data=files(), content_type="multipart/form-data").get_json()
    assert check["success"] and check["fits"] is False
    assert check["problem"] == 'Step 1 (Text Cleaner): Column "region" is not in this file.'
    run = client.post("/recipes/run", data=files(), content_type="multipart/form-data")
    assert run.status_code == 400 and "Step 1 (Text Cleaner)" in run.get_json()["error"]
    fits = client.post("/recipes/check", data={"file": (csv(table(2)), "next.csv"), "recipe": (io.BytesIO(recipe), "r.json")},
                       content_type="multipart/form-data").get_json()
    assert fits["fits"] is True and len(fits["reports"]) == 6 and fits["rows_in"] == 8


def test_a_result_that_passed_through_another_tool_cannot_be_saved_as_a_recipe(client):
    cleaned = client.post("/text-cleaner", data={"file": (csv(table(1)), "m.csv"),
                                                 "options": json.dumps({"columns": ["vendor"]})},
                          content_type="multipart/form-data").get_json()
    assert cleaned["recipe_steps"] == ["Text Cleaner"] and cleaned["recipe_url"]
    renamed = client.post("/column-operations", data={"cache_id": newest_cache(client)["cache_id"], "operation": "rename",
                                                      "renames": json.dumps({"note": "remark"})},
                          content_type="multipart/form-data")
    assert renamed.status_code == 200
    broken = newest_cache(client)
    assert broken["source_tool"] != "Text Cleaner" and broken["recipe_steps"] is None
    assert client.get(f"/recipes/export/{broken['cache_id']}").status_code == 400
    after = client.post("/fill-missing", data={"cache_id": broken["cache_id"],
                                               "options": json.dumps({"columns": ["region"], "method": "mode"})},
                        content_type="multipart/form-data").get_json()
    assert after["success"] and after["recipe_steps"] is None and after["recipe_url"] is None   # the gap is not papered over


def test_recipes_of_other_browsers_cannot_be_exported(client, app=None):
    import datadragon
    cleaned = client.post("/text-cleaner", data={"file": (csv(table(1)), "m.csv"), "options": json.dumps({"columns": ["vendor"]})},
                          content_type="multipart/form-data").get_json()
    stranger = datadragon.app.test_client()
    assert stranger.get(cleaned["recipe_url"]).status_code == 400
    assert client.get("/recipes/export/doesnotexist").status_code == 400


@pytest.mark.parametrize("recipe,message", [
    ({"steps": []}, "not a DataDragon recipe"),
    ({"datadragon_recipe": 99, "steps": [{"tool": "text-cleaner", "options": {}}]}, "newer version"),
    ({"datadragon_recipe": 1, "steps": []}, "no steps"),
    ({"datadragon_recipe": 1, "steps": [{"tool": "launch-missiles", "options": {}}]}, "cannot replay"),
    ({"datadragon_recipe": 1, "steps": [{"tool": "text-cleaner"}]}, "not readable"),
    ({"datadragon_recipe": 1, "steps": [{"tool": "text-cleaner", "options": {}}] * 51}, "51 steps"),
])
def test_bad_recipe_files_are_refused_with_a_reason(recipe, message):
    with pytest.raises(ToolError, match=message):
        tools.check_recipe(recipe)


def test_unreadable_recipe_uploads_are_a_400_and_the_page_is_in_the_hub(client):
    for body in (b"not json at all", b'{"steps": 1}', b"x" * 1_100_000):
        response = client.post("/recipes/run", data={"file": (csv(table(1)), "m.csv"), "recipe": (io.BytesIO(body), "r.json")},
                               content_type="multipart/form-data")
        assert response.status_code == 400 and response.get_json()["error"]
    missing = client.post("/recipes/run", data={"file": (csv(table(1)), "m.csv")}, content_type="multipart/form-data")
    assert missing.status_code == 400 and "recipe file" in missing.get_json()["error"]
    page = client.get("/recipes").get_data(as_text=True)
    assert "js/recipes.js" in page and 'href="/recipes" aria-current="page"' in page


def test_replay_does_not_half_apply_and_reports_each_step():
    df = table(1)
    steps = [{"tool": "text-cleaner", "options": {"columns": ["vendor"], "case": "upper"}},
             {"tool": "fill-missing", "options": {"columns": ["nope"], "method": "mode"}}]
    before = df.copy()
    with pytest.raises(ToolError, match=r'Step 2 \(Fill Missing\): Column "nope" is not in this file'):
        tools.replay(steps, df)
    pd.testing.assert_frame_equal(df, before)                             # the input is untouched
    result, reports, side = tools.replay(steps[:1], df)
    assert reports == [{"step": 1, "tool": "Text Cleaner", "rows_in": 8, "rows_out": 8, "cells_changed": reports[0]["cells_changed"],
                        "columns_added": [], "columns_removed": [], "summary": reports[0]["summary"]}]
    assert reports[0]["cells_changed"] == 7 and side == []
