import hashlib
import hmac
import json
from datetime import datetime, time, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gas_electric_response.clients.arcgis_store import ArcGISFeatureStore
from gas_electric_response.clients.store import Query
from gas_electric_response.clients.weather import NWSObservationParser
from gas_electric_response.config import ConfigError, Settings
from gas_electric_response.flowchart import MermaidRenderer, ReadmeInjector
from gas_electric_response.ledger import ProcessedLedger
from gas_electric_response.receiver.app import WebhookApp
from gas_electric_response.receiver.payload import WebhookPayloadParser
from gas_electric_response.reconcile import DailyReconciler
from gas_electric_response.stages.transform import SchemaMapper
from tests.test_pipeline_end_to_end import run_all


def test_nws_unit_conversion_and_partial():
    p = {"timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
         "windSpeed": {"unitCode": "wmoUnit:km_h-1", "value": 18.0}, "windDirection": {"unitCode": "wmoUnit:degree_(angle)", "value": 250},
         "temperature": {"unitCode": "wmoUnit:degC", "value": 15.0}, "barometricPressure": {"unitCode": "wmoUnit:Pa", "value": 101300}}
    o = NWSObservationParser().parse(p, "KXXX")
    assert o.wind_speed_mps == pytest.approx(5.0) and o.pressure_hpa == pytest.approx(1013) and o.status == "OK"
    p["windDirection"]["value"] = None
    assert NWSObservationParser().parse(p, "KXXX").status == "PARTIAL"


def test_nws_stale_flag():
    p = {"timestamp": "2020-01-01T00:00:00Z", "windSpeed": {"unitCode": "wmoUnit:m_s-1", "value": 3}, "windDirection": {"value": 10}}
    assert NWSObservationParser().parse(p, "K").status == "STALE"


def test_payload_pointer_variants():
    P = WebhookPayloadParser()
    ptr = P.extract_pointer({"eventType": "addData", "feature": {"attributes": {"globalid": "{ABC-1}", "objectid": "7"}}})
    assert ptr.global_id == "abc-1" and ptr.object_id == 7 and P.is_new_submission(ptr.event)
    assert not P.is_new_submission("editData") and not P.is_new_submission("deleteData")
    assert P.extract_pointer({"nothing": 1}) is None and P.extract_pointer([1]) is None


def test_webhook_auth_and_queueing(runtime):
    s = Settings(webhook_token="s3cret", email_mode="file")
    body = {"eventType": "addData", "feature": {"attributes": {"GlobalID": "{00000001-0000-4000-8000-000000000001}"}}}
    with TestClient(WebhookApp(runtime, s).build()) as c:
        assert c.post("/webhooks/survey123", json=body).status_code == 401
        assert c.post("/webhooks/survey123?token=wrong", json=body).status_code == 401
        r = c.post("/webhooks/survey123?token=s3cret", json=body)
        assert r.status_code == 202 and r.json()["status"] == "queued"
        assert c.post("/webhooks/survey123?token=s3cret", json={"x": 1}).status_code == 422
        assert c.post("/webhooks/survey123?token=s3cret", json={**body, "eventType": "editData"}).json()["status"] == "ignored"
        assert c.get("/health").json()["status"] == "ok"


def test_webhook_hmac(runtime):
    s = Settings(webhook_token="t", webhook_hmac_secret="k", email_mode="file")
    raw = json.dumps({"feature": {"attributes": {"objectid": 1}}}).encode()
    good = hmac.new(b"k", raw, hashlib.sha256).hexdigest()
    with TestClient(WebhookApp(runtime, s).build()) as c:
        assert c.post("/webhooks/survey123?token=t", content=raw, headers={"X-Signature": "bad"}).status_code == 401
        assert c.post("/webhooks/survey123?token=t", content=raw, headers={"X-Signature": "sha256=" + good}).status_code == 202


def test_app_refuses_without_token(runtime):
    with pytest.raises(ConfigError):
        WebhookApp(runtime, Settings(webhook_token=""))


def test_schema_mapper_types_and_unknown_keys(schema):
    m = SchemaMapper(schema.incident_layer.fields.raw())
    out = m.to_attributes({"leak_id": "x" * 500, "grade": "2", "lel_pct": 1.23456789, "reported_at": datetime(2026, 1, 1, tzinfo=timezone.utc)})
    assert len(out["LEAK_ID"]) == 64 and out["HSI_GRADE"] == 2 and out["LEL_PCT"] == 1.2346 and out["REPORTED_AT"] == 1767225600000
    with pytest.raises(ConfigError):
        m.to_attributes({"not_a_key": 1})


def test_where_clause_builder():
    from datetime import datetime, timezone
    q = Query(equals={"A": "o'brien", "B": 3}, time_field="T", time_from=datetime(2026, 1, 1, 6, tzinfo=timezone.utc))
    w = ArcGISFeatureStore.build_where(q)
    assert w == "A = 'o''brien' AND B = 3 AND T >= timestamp '2026-01-01 06:00:00'"
    assert ArcGISFeatureStore.build_where(Query()) == "1=1"


def test_settings_validation_lists_everything():
    with pytest.raises(ConfigError) as e:
        Settings().validate_for_production()
    msg = str(e.value)
    for needle in ("ARCGIS_USERNAME", "INCIDENT_LAYER_URL", "REPORT_PATH_A", "NWS_USER_AGENT", "SMTP_HOST"):
        assert needle in msg


def test_ledger_terminal_and_attempts():
    L = ProcessedLedger(":memory:")
    L.mark("g", "L", "ERROR")
    assert not L.is_terminal("g") and L.get("g") == ("ERROR", 1)
    L.mark("g", "L", "DONE")
    assert L.is_terminal("g") and L.get("g")[1] == 2


def test_daily_reconcile(runtime):
    run_all(runtime)
    rec = DailyReconciler(runtime.services, runtime.notifier, cutoff=time(23, 59, 59))
    from datetime import date
    s = rec.collect(datetime.now(rec.tz).date())
    assert s.raw_submissions == 4 and s.by_grade[1] == 1 and s.by_grade[3] == 1 and s.duplicates == 1 and s.rejections == 1
    assert s.escalations == []
    assert s.reports_a_ok == 3 and s.reports_b_ok == 2 and s.discrepancies == []
    assert s.joint_hazards and "LK-DEMO-A" in s.joint_hazards[0]


def test_daily_reconcile_detects_missing_pdf(runtime):
    run_all(runtime)
    f = runtime.services.schema.incident_field
    victim = [r for r in runtime.services.incidents.query(Query()) if r.attributes[f("leak_id")] == "LK-DEMO-C"][0]
    p = Path(victim.attributes[f("report_b_path")])
    p.chmod(0o644); p.unlink()
    rec = DailyReconciler(runtime.services, runtime.notifier, cutoff=time(23, 59, 59))
    s = rec.collect(datetime.now(rec.tz).date())
    assert any("LK-DEMO-C" in d and "Report B" in d for d in s.discrepancies)
    subject, text, html = rec.render(s)
    assert "DISCREPANCY" in subject


def test_flowchart_is_generated_from_stage_list(runtime):
    mer = MermaidRenderer()
    m = mer.pipeline(runtime.pipeline.stages)
    assert m.startswith("```mermaid") and all(mer.esc(st.name) in m for st in runtime.pipeline.stages)
    text = "a\n<!-- FLOWCHART:X:START -->\nold\n<!-- FLOWCHART:X:END -->\nb"
    inj = ReadmeInjector()
    assert "NEW" in inj.inject(text, "X", "NEW") and "old" not in inj.inject(text, "X", "NEW")


def test_diagnostics_on_demo(runtime):
    from gas_electric_response.diagnostics import SetupDiagnostics
    res = SetupDiagnostics(runtime.services).run()
    failing = [r.message for r in res if not r.ok]
    # in-memory incident/plume stores are empty so only those two may report missing fields
    assert all(m.startswith(("incidents", "plumes")) for m in failing), failing
