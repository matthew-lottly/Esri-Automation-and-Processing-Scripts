"""Regression tests for defects found in the independent code review."""
import threading
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pytest
from shapely.geometry import Point, Polygon

from gas_electric_response.clients.store import Query
from gas_electric_response.geo import from_esri_json
from gas_electric_response.ledger import ProcessedLedger
from gas_electric_response.models import CIDict, FeatureRecord, WeatherObservation
from gas_electric_response.plume import PlumeModel
from gas_electric_response.reconcile import DailyReconciler
from gas_electric_response.retry import retry_notifications
from tests.conftest import make_report
from tests.test_pipeline_end_to_end import run_all


def test_cidict_case_insensitive():
    d = CIDict({"AssetType": 1})
    assert d["ASSETTYPE"] == 1 and d.get("assettype") == 1 and "assettype" in d and d.get("nope", 7) == 7


def test_unmapped_and_spaced_asset_types(runtime):
    from gas_electric_response.stages.analysis import ElectricIntersectStage
    from gas_electric_response.models import IncidentContext
    from gas_electric_response.plume import PlumeModel
    ctx = IncidentContext(report=make_report())
    ctx.plume = PlumeModel(runtime.services.params).build(ctx.report, None)
    e = runtime.services.schema.electric_assets
    runtime.services.electric.add([
        FeatureRecord({e.asset_id_field: "X1", e.asset_type_field: "Pad-Mount Transformer", e.energized_field: "Y"}, Point(-96.8, 32.78)),
        FeatureRecord({e.asset_id_field: "X2", e.asset_type_field: 999, e.energized_field: "Y"}, Point(-96.8, 32.78001))])
    ElectricIntersectStage(runtime.services).run(ctx)
    cats = {h.asset_id: h.category for h in ctx.electric_hits}
    assert cats["X1"] == "ignition_source"            # spacing/hyphen/case tolerant
    assert cats["X2"] == "other" and any("unmapped electric asset type '999'" in f for f in ctx.flags)


def test_stale_weather_is_not_used_for_direction(params):
    r = make_report()
    stale = WeatherObservation(6.0, 270.0, 20, 1013, status="STALE")
    res = PlumeModel(params).build(r, stale)
    assert res.model.endswith("circle") and any("stale" in n for n in res.assumptions)


def test_calm_null_direction_is_valid_circle_without_conservative_inflation(params):
    r = make_report()
    calm = PlumeModel(params).build(r, WeatherObservation(0.0, None, 20, 1013))
    none = PlumeModel(params).build(r, None)
    assert calm.model.endswith("circle") and calm.length_m < none.length_m


def test_even_odd_polygon_handles_hole_and_island():
    outer = [[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]
    hole = [[2, 2], [8, 2], [8, 8], [2, 8], [2, 2]]
    island = [[4, 4], [4, 6], [6, 6], [6, 4], [4, 4]]
    g = from_esri_json({"rings": [outer, hole, island]})
    assert g.area == pytest.approx(100 - 36 + 4)
    assert g.contains(Point(5, 5)) and not g.contains(Point(3, 3))
    g2 = from_esri_json({"rings": [outer[::-1]]})        # opposite orientation still a filled polygon
    assert g2.area == pytest.approx(100)


def test_ledger_claim_is_exclusive_and_leased():
    L = ProcessedLedger(":memory:")
    assert L.claim("g", "L") and not L.claim("g", "L")            # second worker is refused
    L.mark("g", "L", "ERROR")
    assert L.claim("g", "L")                                       # failed work can be re-claimed
    L.mark("g", "L", "DONE")
    assert not L.claim("g", "L")
    # a crashed worker's lease expires
    L.claim("h", "L")
    L._db.execute("UPDATE processed SET updated_at=? WHERE global_id='h'", ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),))
    assert L.claim("h", "L")


def test_two_threads_cannot_both_process_one_submission(runtime):
    rec = runtime.services.survey.query(Query())[0]
    results = []
    ts = [threading.Thread(target=lambda: results.append(runtime.pipeline.process_record(rec))) for _ in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sum(r is not None for r in results) == 1
    assert len([r for r in runtime.services.incidents.query(Query())]) == 1


def test_failure_after_publish_is_partial_alerted_not_lost(runtime, monkeypatch):
    from gas_electric_response.stages.notify import NotifyStage
    monkeypatch.setattr(NotifyStage, "run", lambda self, ctx: (_ for _ in ()).throw(RuntimeError("boom after publish")))
    rec = [r for r in runtime.services.survey.query(Query()) if r.attributes["Leak_ID"] == "LK-DEMO-A"][0]
    ctx = runtime.pipeline.process_record(rec)                      # does NOT raise
    assert ctx.published
    mails = "\n".join(p.read_text() for p in Path(runtime.services.settings.email_outbox_dir).glob("*.eml"))
    assert "PARTIALLY FAILED" in mails and "eoc@example.com" in mails
    assert runtime.ledger.get(rec.attributes["GlobalID"].strip("{}").lower())[0] == "DONE"


def test_failed_notification_is_retried_from_the_layer_row(runtime, monkeypatch):
    from gas_electric_response.notify import EmailError, EmailNotifier
    real = EmailNotifier.send
    monkeypatch.setattr(EmailNotifier, "send", lambda self, *a, **k: (_ for _ in ()).throw(EmailError("smtp down")))
    rec = [r for r in runtime.services.survey.query(Query()) if r.attributes["Leak_ID"] == "LK-DEMO-A"][0]
    ctx = runtime.pipeline.process_record(rec)
    f = runtime.services.schema.incident_field
    row = lambda: [r for r in runtime.services.incidents.query(Query()) if r.attributes[f("leak_id")] == "LK-DEMO-A"][0]
    assert ctx.notify_status == "FAILED" and row().attributes[f("notify_status")] == "FAILED"
    monkeypatch.setattr(EmailNotifier, "send", real)                # SMTP recovers
    assert retry_notifications(runtime) == 1
    assert row().attributes[f("notify_status")] == "SENT"
    assert any("RETRY" in p.read_text() for p in Path(runtime.services.settings.email_outbox_dir).glob("*.eml"))
    assert retry_notifications(runtime) == 0


def test_rejection_alert_reaches_eoc_with_treat_as_live_wording(runtime):
    run_all(runtime)
    import email
    from email import policy
    msgs = [email.message_from_bytes(p.read_bytes(), policy=policy.default) for p in Path(runtime.services.settings.email_outbox_dir).glob("*.eml")]
    rej = [m for m in msgs if "REJECTED" in m["Subject"]]
    assert rej and "eoc@example.com" in rej[0]["To"]
    assert "TREAT THE REPORTED LEAK AS LIVE" in rej[0].get_content()


def test_rolling_window_covers_after_hours_and_weekend(runtime, tmp_path):
    rec = DailyReconciler(runtime.services, runtime.notifier, cutoff=time(17, 0))
    tz = rec.tz
    fri = datetime(2026, 10, 16, 17, 0, tzinfo=tz)
    mon = datetime(2026, 10, 19, 17, 0, tzinfo=tz)
    rec._state_file.parent.mkdir(parents=True, exist_ok=True)
    rec._state_file.write_text('{"last_end": "%s"}' % fri.isoformat())
    start, end = rec.rolling_window(now=mon)
    assert start == fri and end == mon                                 # Friday 17:00 -> Monday 17:00, nothing skipped


def test_worker_retry_does_not_block_queue(runtime):
    from gas_electric_response.receiver.payload import Pointer
    from gas_electric_response.receiver.worker import SubmissionWorker
    w = SubmissionWorker(runtime, retry_delays=(30,))
    w.start()
    try:
        w.submit(Pointer("does-not-exist", None, "addData"))           # fails -> scheduled for later, not slept on
        good = runtime.services.survey.query(Query())[0]
        w.submit(Pointer(good.attributes["GlobalID"].strip("{}").lower(), None, "addData"))
        import time as _t
        for _ in range(100):
            if runtime.services.incidents.query(Query()):
                break
            _t.sleep(0.1)
        assert runtime.services.incidents.query(Query())               # processed while the bad one waits on its timer
    finally:
        w.stop()


def test_globalid_lookup_tries_several_spellings(runtime):
    p = runtime.pipeline
    gid = "00000002-0000-4000-8000-000000000002"
    assert p.fetch_survey_record(gid) is not None
    assert p.fetch_survey_record("nope", object_id=2) is not None      # OID fallback
    assert p.fetch_survey_record("nope") is None
