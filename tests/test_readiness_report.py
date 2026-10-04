"""The Data Readiness Report: it builds for full, sparse and empty sessions, and says what the data says."""
import types

import pytest

import datadragon_report as report


def make_state(**overrides):
    analysis = {
        "overview": {"shape": {"rows": 200, "columns": 3}, "duplicate_rows": 2, "memory_usage_mb": 0.5,
                     "detected_types_count": {"Text": 2, "Float": 1}},
        "columns": {
            "id": {"detected_type": "Text", "null_percentage": 0, "null_count": 0, "unique_count": 198},
            "amount": {"detected_type": "Float", "null_percentage": 10, "null_count": 20, "unique_count": 150, "is_numeric": True,
                       "statistics": {"min": 1, "median": 50.5, "mean": 49.25, "max": 12000}, "outliers": {"count": 3}},
            "<b>note</b> & more": {"detected_type": "Text", "null_percentage": 40, "null_count": 80, "unique_count": 5},
        },
        "gap_summary": [{"column": "<b>note</b> & more", "null_percentage": 40, "null_count": 80},
                        {"column": "amount", "null_percentage": 10, "null_count": 20}],
    }
    state = types.SimpleNamespace(
        filename="sales <2026> & co.xlsx",
        stage_data={1: analysis, 3: {"minimal_combinations_after_dedup": [["id"], ["amount", "id"]], "user_selected_key": ["id"],
                                     "duplicate_rows": 2, "excluded_null_columns": ["amount"]}},
        user_decisions={2: {"amount": "acceptable", "<b>note</b> & more": "needs_attention"},
                        4: {"anonymization": {"enabled": True, "columns": ["id"]}}})
    for key, value in overrides.items():
        setattr(state, key, value)
    return state


LOG = [{"type": "anonymization", "columns": ["id"], "status": "applied", "note": "placeholders <like> this"}]


def page_count(path):
    data = open(path, "rb").read()
    assert data.startswith(b"%PDF-")
    return data.count(b"/Type /Page\n") or data.count(b"/Type /Page ")


def test_a_full_session_builds_a_multi_page_branded_report(tmp_path):
    out = tmp_path / "report.pdf"
    report.generate_readiness_report(str(out), make_state(), LOG)
    assert page_count(str(out)) >= 5                    # cover, summary, shape, missing data, keys and transformations
    data = out.read_bytes()
    assert b"/FontFile2" in data                        # the brand fonts are embedded (TrueType)
    assert b"/DCTDecode" in data                        # the cover artwork


@pytest.mark.parametrize("state", [
    make_state(stage_data={1: None, 3: None}, user_decisions={}),                                   # nothing was run
    make_state(stage_data={1: {"overview": {"shape": {"rows": 0, "columns": 0}}, "columns": {}}, 3: {}}, user_decisions={}),
    make_state(filename="x" * 400 + "<i>.csv"),                                                     # a very long, hostile name
])
def test_sparse_and_hostile_sessions_still_build(tmp_path, state):
    out = tmp_path / "report.pdf"
    report.generate_readiness_report(str(out), state, None)
    assert out.stat().st_size > 10_000


def test_facts_and_findings_restate_the_analysis():
    facts = report.report_facts(make_state(), LOG)
    assert facts["rows"] == 200 and facts["column_count"] == 3 and facts["duplicates"] == 2
    assert facts["completeness"] == pytest.approx(100 - 100 / 600 * 100)       # 100 missing cells of 600
    assert facts["selected_key"] == ["id"]
    text = " ".join(report.findings(facts))
    assert "200 rows" in text and "3 columns" in text
    assert "2 columns" in text and "40.0%" in text                              # the gaps and the largest one
    assert "&lt;b&gt;note&lt;/b&gt; &amp; more" in text                         # file-derived text is escaped
    assert "2 rows" in text and "once the duplicate rows are removed" in text
    assert "1 transformation" in text


def test_missing_assets_do_not_fail_the_report(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "ASSETS", str(tmp_path / "nowhere"))
    monkeypatch.setattr(report, "MARK_SVG", str(tmp_path / "no-mark.svg"))
    monkeypatch.setattr(report, "_font_names", {})
    monkeypatch.setattr(report, "_mark_layers", None)
    out = tmp_path / "report.pdf"
    report.generate_readiness_report(str(out), make_state(), LOG)
    assert out.stat().st_size > 3_000 and b"/DCTDecode" not in out.read_bytes()


def test_the_mark_is_read_from_the_shared_svg():
    layers = dict(report.mark_layers())
    assert set(layers) == {"ink", "eye", "ember"} and layers["ink"][0][0] == "M"
