"""Jinja2 report engine: placeholders, filters, media helpers, extension points."""
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from gas_electric_response.clients.store import Query
from gas_electric_response.exceptions import ConfigError
from gas_electric_response.reports.data import ReportData, Row
from gas_electric_response.reports.emails import EmailComposer
from gas_electric_response.reports.engine import MISSING, ReportRenderer, TemplateEnvironment
from tests.conftest import ROOT

NOW = datetime.now(timezone.utc)


@pytest.fixture
def incident(runtime):
    rec = [r for r in runtime.services.survey.query(Query()) if r.attributes["Leak_ID"] == "LK-DEMO-A"][0]
    return runtime, runtime.pipeline.process_record(rec)


def tpl_dir(tmp_path, body: str, name="t.html") -> Path:
    d = tmp_path / "tpl"
    shutil.copytree(ROOT / "templates", d)
    (d / name).write_text(body, encoding="utf-8")
    return d


def renderer(rt, d: Path, name="t.html", placeholders=True, cache=None):
    st = replace(rt.services.settings, template_dir=str(d), placeholder_media=placeholders)
    return ReportRenderer(name, rt.services, st, cache_dir=cache)


def test_shipped_templates_render_to_pdf_with_placeholders(incident):
    rt, ctx = incident
    for name in ("field_ticket.html", "risk_assessment.html"):
        r = ReportRenderer(name, rt.services)
        html = r.render_html(ctx, NOW)
        assert ctx.report.leak_id in html and "LK-DEMO-A" in html
        assert r.render_pdf(ctx, NOW).startswith(b"%PDF")
    html = ReportRenderer("field_ticket.html", rt.services).render_html(ctx, NOW)
    assert "MAP PLACEHOLDER" in html.replace("MAP%20PLACEHOLDER", "MAP PLACEHOLDER") or "data:image/svg+xml" in html
    assert "image/png" not in html                      # no rendered imagery in the demo output


def test_placeholders_are_neutral_not_fake_images(incident):
    rt, ctx = incident
    html = ReportRenderer("field_ticket.html", rt.services).render_html(ctx, NOW)
    import base64, re
    uris = re.findall(r'src="data:image/svg\+xml;base64,([^"]+)"', html)
    texts = [base64.b64decode(u).decode() for u in uris]
    assert any("MAP PLACEHOLDER" in t for t in texts) and any("PHOTO PLACEHOLDER" in t for t in texts)


def test_live_media_draws_a_real_map(incident, tmp_path):
    rt, ctx = incident
    r = renderer(rt, ROOT / "templates", "field_ticket.html", placeholders=False, cache=tmp_path)
    html = r.render_html(ctx, NOW)
    assert "data:image/png;base64," in html and "No photo attachments were found" in html


def test_filters_undefined_and_escaping(incident, tmp_path):
    rt, ctx = incident
    d = tpl_dir(tmp_path, "{{ report.lel_pct | fmt('.1f') }}|{{ nothing.at.all }}|{{ report.temp_c | fmt('.2f') }}|{{ 12.3456 | round(1) }}|"
                          "{{ 100 | ft }}|{{ report.notes }}|{{ none_value }}|{{ 'x' | title_case }}")
    ctx.report.notes = "<script>alert(1)</script>"
    out = renderer(rt, d).render_html(ctx, NOW)
    parts = out.strip().split("|")
    assert parts[0] == "35.0" and parts[1] == MISSING and parts[2] == "21.00" and parts[3] == "12.3" and parts[4] == "328"
    assert "&lt;script&gt;" in parts[5] and "<script>" not in out
    assert parts[6] == MISSING and parts[7] == "X"


def test_loops_and_conditionals(incident, tmp_path):
    rt, ctx = incident
    d = tpl_dir(tmp_path, "{% for r in electric_hits %}[{{ r.asset_id }}:{{ r.in_plume }}]{% endfor %}"
                          "{% if grade.number == 1 %}CRIT{% else %}OTHER{% endif %}{% if incident.is_duplicate %}DUP{% endif %}")
    out = renderer(rt, d).render_html(ctx, NOW)
    assert "[UG-3003:45 m]" in out and "[PM-1001:point]" in out and "CRIT" in out and "DUP" not in out


def test_syntax_error_is_reported_with_line_number(incident, tmp_path):
    rt, ctx = incident
    d = tpl_dir(tmp_path, "line one\n{% for x in %}")
    with pytest.raises(ConfigError, match=r"t\.html line 2"):
        renderer(rt, d)
    with pytest.raises(ConfigError, match="not found"):
        renderer(rt, d, name="missing.html")


def test_photo_gallery_embeds_real_photos(incident, tmp_path):
    rt, ctx = incident
    img = tmp_path / "1_site.png"
    Image.new("RGB", (40, 30), (120, 130, 140)).save(img)
    ctx.report.photos = [str(img)]
    html = renderer(rt, tpl_dir(tmp_path, "{{ photo_gallery(columns=1) }}")).render_html(ctx, NOW)
    assert "figure" in html and "site.png" in html and "PLACEHOLDER" not in html


def test_extend_namespace_and_tables_via_subclass(incident, tmp_path):
    rt, ctx = incident
    from types import SimpleNamespace

    class MyData(ReportData):
        def extend_namespace(self, ns):
            return {"utility": SimpleNamespace(name="Acme Gas & Power")}

        def extend_sources(self):
            return {"parcels": lambda: [Row(apn="123-4", owner="A. Customer")]}

    d = tpl_dir(tmp_path, "{{ utility.name }}|{% for p in parcels %}{{ p.apn }}/{{ p.owner }}{% endfor %}")
    st = replace(rt.services.settings, template_dir=str(d))
    out = ReportRenderer("t.html", rt.services, st, data_cls=MyData).render_html(ctx, NOW)
    assert "Acme Gas &amp; Power|123-4/A. Customer" in out


def test_template_env_filters_directly():
    assert TemplateEnvironment.fmt(None) == MISSING and TemplateEnvironment.fmt("abc", ".2f") == "abc"
    assert TemplateEnvironment.metres_to_feet(1, 2) == "3.28"
    assert TemplateEnvironment.local_time(datetime(2026, 1, 1, 12, tzinfo=timezone.utc), "America/Chicago", "%H:%M") == "06:00"


def test_alert_and_daily_email_html(incident):
    rt, ctx = incident
    c = EmailComposer(rt.services)
    html = c.alert_html(ctx)
    assert "Grade 1" in html and "LK-DEMO-A" in html and "GV-101" in html
    from datetime import time
    from gas_electric_response.reconcile import DailyReconciler
    rec = DailyReconciler(rt.services, rt.notifier, cutoff=time(23, 59, 59))
    s = rec.collect(datetime.now(rec.tz).date())
    assert "Daily executive summary" in c.daily_html(s, "subj")


def test_real_photo_is_embedded_in_the_pdf(incident, tmp_path):
    rt, ctx = incident
    img = tmp_path / "1_site.png"
    Image.new("RGB", (80, 60), (30, 90, 160)).save(img)
    ctx.report.photos = [str(img)]
    r = renderer(rt, ROOT / "templates", "field_ticket.html")
    with_photo = r.render_pdf(ctx, NOW)
    ctx.report.photos = []
    without = r.render_pdf(ctx, NOW)
    assert with_photo.startswith(b"%PDF") and len(with_photo) != len(without)
    assert b"/Subtype /Image" in with_photo


def test_map_failure_degrades_to_placeholder_not_a_failed_report(incident, tmp_path, monkeypatch):
    rt, ctx = incident
    from gas_electric_response.reports.mapping import ReportMap
    monkeypatch.setattr(ReportMap, "render", lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("tiles exploded")))
    r = renderer(rt, ROOT / "templates", "field_ticket.html", placeholders=False, cache=tmp_path)
    html = r.render_html(ctx, NOW)
    import base64, re
    texts = [base64.b64decode(u).decode() for u in re.findall(r'data:image/svg\+xml;base64,([^"]+)', html)]
    assert any("MAP UNAVAILABLE" in t for t in texts) and r.render_pdf(ctx, NOW).startswith(b"%PDF")


def test_templates_are_sandboxed(incident, tmp_path):
    rt, ctx = incident
    d = tpl_dir(tmp_path, "{{ report.__class__.__mro__ }}")
    out = renderer(rt, d).render_html(ctx, NOW)
    assert "object" not in out and "class" not in out          # unsafe attribute access renders as undefined (an em dash)


def test_report_data_class_hook(runtime):
    from gas_electric_response.stages.reporting import ReportComposer
    assert ReportComposer.data_class("") is ReportData
    assert ReportComposer.data_class("gas_electric_response.reports.data:ReportData") is ReportData
    with pytest.raises(ConfigError, match="could not be imported"):
        ReportComposer.data_class("no_such_module:Thing")
