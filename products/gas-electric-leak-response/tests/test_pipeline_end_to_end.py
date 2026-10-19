from pathlib import Path

from gas_electric_response.clients.store import Query


def run_all(rt):
    out = {}
    for rec in rt.services.survey.query(Query()):
        out[rec.attributes["Leak_ID"]] = rt.pipeline.process_record(rec)
    return out


def test_demo_scenario_outcomes(runtime):
    res = run_all(runtime)
    a, b, c, d = (res[k] for k in ("LK-DEMO-A", "LK-DEMO-B", "LK-DEMO-C", "LK-DEMO-D"))
    assert int(a.grade) == 1 and a.dispatch_code == "DISPATCH_JOINT_GAS_ELECTRIC_CREW" and a.notify_status == "SENT"
    assert [v.valve_id for v in a.valves] == ["GV-101", "GV-102", "GV-103"]
    assert b.duplicate_of == "LK-DEMO-A" and b.report_b_status == "NOT_REQUIRED" and b.notify_status == "NOT_REQUIRED" and not b.is_escalation
    assert int(c.grade) == 3 and c.notify_status == "NOT_REQUIRED" and c.report_b_status == "OK"
    assert d.rejected and "franchise" in d.rejected


def test_layers_and_reports_written(runtime):
    run_all(runtime)
    sc = runtime.services.schema
    f = sc.incident_field
    rows = runtime.services.incidents.query(Query())
    assert len(rows) == 3                                           # A, B(dup), C ; D rejected
    parent = [r for r in rows if r.attributes[f("leak_id")] == "LK-DEMO-A"][0]
    assert parent.attributes[f("reading_count")] == 2 and parent.attributes[f("status_code")] == 1
    assert parent.attributes[f("report_a_status")] == "OK" and Path(parent.attributes[f("report_a_path")]).exists()
    assert Path(parent.attributes[f("report_b_path")]).exists()
    assert len(runtime.services.plumes.query(Query())) == 2         # A and C; the duplicate adds no polygon
    outbox = list(Path(runtime.services.settings.email_outbox_dir).glob("*.eml"))
    assert len(outbox) == 2 and any("GRADE 1" in p.read_text() for p in outbox)   # Grade-1 mail + rejection alert


def test_idempotent_second_pass(runtime):
    run_all(runtime)
    second = run_all(runtime)
    assert all(v is None or v.rejected is None for v in second.values())
    assert len(runtime.services.incidents.query(Query())) == 3     # no new rows


def test_reports_are_write_once_and_hashed(runtime, tmp_path):
    run_all(runtime)
    pdfs = list(Path(runtime.services.settings.report_path_a).glob("*/*.pdf"))
    assert pdfs and all(p.with_suffix(".pdf.sha256").exists() for p in pdfs)
    import hashlib
    from datetime import datetime, timezone
    import pytest
    from gas_electric_response.reports.storage import ReportStore, ReportStoreError
    st, when = ReportStore(tmp_path / "x", "x", "UTC"), datetime(2026, 3, 31, 23, 30, tzinfo=timezone.utc)
    data = b"%PDF-1.4 test"
    p = st.write(data, "LK 1/2", when, "field_ticket")
    assert p.parent.name == "2026-03" and p.name.startswith("LK_1_2_20260331T233000")
    assert p.with_suffix(".pdf.sha256").read_text().split()[0] == hashlib.sha256(data).hexdigest()
    with pytest.raises(ReportStoreError, match="write-once"):
        st.write(data, "LK 1/2", when, "field_ticket")
    with pytest.raises(ReportStoreError, match="non-PDF"):
        st.write(b"nope", "x", when, "field_ticket")


def test_month_folder_uses_local_time(tmp_path):
    from datetime import datetime, timezone
    from gas_electric_response.reports.storage import ReportStore
    st = ReportStore(tmp_path, "x", "America/Chicago")
    assert st.month_dir(datetime(2026, 4, 1, 3, 0, tzinfo=timezone.utc)).name == "2026-03"     # still March in Chicago


def test_unexpected_failure_alerts_and_reraises(runtime, monkeypatch):
    import pytest
    from gas_electric_response.stages.analysis import ElectricIntersectStage
    monkeypatch.setattr(ElectricIntersectStage, "run", lambda self, ctx: (_ for _ in ()).throw(RuntimeError("layer down")))
    rec = runtime.services.survey.query(Query())[0]
    with pytest.raises(RuntimeError):
        runtime.pipeline.process_record(rec)
    mails = [p.read_text() for p in Path(runtime.services.settings.email_outbox_dir).glob("*.eml")]
    assert any("Automation FAILED" in m and "eoc@example.com" in m for m in mails)
    assert runtime.ledger.get(rec.attributes["GlobalID"].strip("{}").lower())[0] == "ERROR"
    assert not runtime.services.incidents.query(Query())            # nothing half-published


def test_escalating_duplicate_updates_parent_and_notifies(runtime):
    # first a low-grade parent, then a worse reading 5 m away
    sv = runtime.services.survey
    recs = sv.query(Query())
    import copy
    a_low = copy.deepcopy([r for r in recs if r.attributes["Leak_ID"] == "LK-DEMO-A"][0])
    a_low.attributes.update({"Leak_ID": "LK-LOW", "Gas_LEL_Pct": 3, "Gas_PPM": 1500, "GlobalID": "{99999999-0000-4000-8000-000000000001}"})
    a_low.attributes["CreationDate"] -= 3_600_000
    low = runtime.pipeline.process_record(a_low)
    assert low.grade is not None and int(low.grade) == 2
    hot = runtime.pipeline.process_record([r for r in recs if r.attributes["Leak_ID"] == "LK-DEMO-A"][0])
    assert hot.is_escalation and hot.notify_required and hot.report_b_required
    f = runtime.services.schema.incident_field
    parent = [r for r in runtime.services.incidents.query(Query()) if r.attributes[f("leak_id")] == "LK-LOW"][0]
    assert parent.attributes[f("grade")] == 1 and parent.attributes[f("max_lel_pct")] >= 35
    assert len(runtime.services.plumes.query(Query())) == 1        # parent's plume REPLACED, not stacked
