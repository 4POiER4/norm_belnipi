from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_web_pipeline_does_not_start_tnpa_lookup():
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "refine_and_check_normative_refs_tnpa" not in app_source
    assert 'request.form.get("search_tnpa")' not in app_source


def test_tnpa_toggle_is_removed_from_web_ui():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert 'id="search-tnpa"' not in html
    assert "fd.append('search_tnpa'" not in js
