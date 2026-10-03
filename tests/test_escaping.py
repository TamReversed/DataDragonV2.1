"""Output escaping: HTML in the page scripts, and markup in generated PDFs."""
import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from queue import Queue

import pytest

import datadragon
from helpers import fixture_path, output_path, pdf_text, post_job
from test_merge import write_rows

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = sorted(glob.glob(os.path.join(ROOT, "templates", "*.html")))
NODE = shutil.which("node")
HOSTILE = ["<img src=x onerror=alert(1)>", '" onmouseover="alert(1)', "' onclick='alert(1)", "a&b", "</script><script>x",
           "`${alert(1)}`", "x y", "&lt;already&gt;"]
ESCAPE_FUNCTION = re.compile(r"function escapeHtml\(text\) \{.*?\n    \}", re.S)


def escape_source(path):
    match = ESCAPE_FUNCTION.search(open(path, encoding="utf-8").read())
    return match.group(0) if match else None


# ------------------------------------------------------------------ HTML
def templates_with_escape():
    return [t for t in TEMPLATES if escape_source(t)]


def test_every_copy_of_escapehtml_is_the_same_quote_escaping_function():
    sources = {os.path.basename(t): escape_source(t) for t in templates_with_escape()}
    assert len(sources) >= 17
    assert len(set(sources.values())) == 1, "escapeHtml differs between templates"
    only = next(iter(sources.values()))
    assert "&quot;" in only and "&#39;" in only and "createElement" not in only


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_escapehtml_output_is_inert_in_text_and_in_attributes():
    source = escape_source(templates_with_escape()[0])
    script = source + "\nconst out = " + json.dumps(HOSTILE) + ".map(escapeHtml); " \
        "out.push(escapeHtml(null), escapeHtml(undefined), escapeHtml(0), escapeHtml(false), escapeHtml(12.5));" \
        "console.log(JSON.stringify(out));"
    result = json.loads(subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True).stdout)
    hostile_results, extras = result[:len(HOSTILE)], result[len(HOSTILE):]
    for text in hostile_results:
        assert not re.search(r"[<>\"']", text), text      # nothing that can open a tag or leave an attribute value
    assert hostile_results[3] == "a&amp;b" and hostile_results[7] == "&amp;lt;already&amp;gt;"
    assert extras == ["", "", "0", "false", "12.5"]


def test_the_template_checker_passes_on_the_real_templates():
    run = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "check_innerhtml.py")], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout


def checker():
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import check_innerhtml
    return check_innerhtml


@pytest.mark.parametrize("js,unsafe", [
    ("const h = `<b>${value}</b>`;", ["value"]),
    ("const h = `<b>${escapeHtml(value)}</b>`;", []),
    ("const h = `<b>${count.toLocaleString()} ${items.length}</b>`;", []),
    ("const h = `<b>${a ? 'x' : 'y'}</b>`;", []),
    ("const h = `<b>${name /* safe: counter */}</b>`;", []),
    ("const h = `<b>${rows.map(r => `<i>${r.name}</i>`).join('')}</b>`;", ["r.name"]),       # nested template is inspected
    ("const h = `<b>${rows.map(r => `<i>${escapeHtml(r.name)}</i>`).join('')}</b>`;", []),
    ("const h = `<input value=\"${cell}\">`;", ["cell"]),
    ("const h = `<b>${col.name}</b>${safeName}`;", ["col.name"]),
    ("const h = `<b>${x.y.z}</b>`; const k = `${n}`;", ["x.y.z"]),
    ("const plain = 'no template here ${value}';", []),
])
def test_template_checker_rules(tmp_path, js, unsafe):
    module = checker()
    found = [expr for _, expr, after in module.interpolations(js) if not module.is_safe(expr, after)]
    assert found == unsafe


# ------------------------------------------------------------------ PDF
HOSTILE_COLUMNS = ["id", "a<b", "x & y", "<b>bold</b>"]


@pytest.fixture
def hostile_csv(tmp_path):
    return write_rows(tmp_path / "h.csv", HOSTILE_COLUMNS, [["1", "p", "q", "r"], ["2", "s", "t", "u"]])


def pdfs_in(zip_path):
    zf = zipfile.ZipFile(zip_path)
    return [zf.read(n) for n in zf.namelist() if n.endswith(".pdf")]


def test_natural_key_report_shows_column_names_literally(client, hostile_csv):
    final, _ = post_job(client, "/find-unique-identifier", {"file": hostile_csv},
                        {"selected_columns[]": HOSTILE_COLUMNS})
    assert final["stage"] == "done", final
    (pdf,) = pdfs_in(output_path(final))
    squashed = re.sub(r"\s+", "", pdf_text(pdf))
    assert "a<b" in squashed and "x&y" in squashed and "<b>bold</b>" in squashed


def start_pipeline(client, path):
    with open(path, "rb") as fh:
        return client.post("/pipeline/start", data={"file": (fh, os.path.basename(path))},
                           content_type="multipart/form-data").get_json()["session_id"]


def test_pipeline_report_shows_keys_and_transformations_literally(client, hostile_csv):
    from helpers import drain_progress
    datadragon.rate_limit_store.clear()
    sid = start_pipeline(client, hostile_csv)
    client.post(f"/pipeline/{sid}/analyze")
    drain_progress(client, sid)
    client.post(f"/pipeline/{sid}/keys", json={"selected_columns": HOSTILE_COLUMNS})
    drain_progress(client, sid)
    client.post(f"/pipeline/{sid}/keys/confirm", json={"selected_key": ["a<b", "x & y"]})
    client.post(f"/pipeline/{sid}/transformations/select",
                json={"<i>evil</i>": {"enabled": True, "columns": ["a<b"]}, "anonymization": {"enabled": True, "columns": ["a<b"]}})
    client.post(f"/pipeline/{sid}/execute")
    final = next(m for m in drain_progress(client, sid) if m.get("stage") in ("done", "error"))
    assert final["stage"] == "done", final
    (pdf,) = pdfs_in(output_path(final))
    squashed = re.sub(r"\s+", "", pdf_text(pdf))        # glyph runs come back space-separated
    assert "a<b+x&y" in squashed                         # the selected key, shown literally
    assert "<I>Evil</I>" in squashed                     # a transformation name with markup in it is text, not markup
