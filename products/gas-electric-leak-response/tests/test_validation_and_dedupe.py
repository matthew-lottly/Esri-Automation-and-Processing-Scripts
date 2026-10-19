from datetime import datetime, timedelta, timezone

import pytest

from gas_electric_response.exceptions import StageRejected
from gas_electric_response.models import FeatureRecord
from gas_electric_response.stages.ingest import DedupeStage, ValidateStage
from tests.conftest import make_report
from gas_electric_response.models import IncidentContext
from shapely.geometry import Point


def vc(**kw):
    return IncidentContext(report=make_report(**kw))


@pytest.mark.parametrize("kw,msg", [
    (dict(lel_pct=-1.0), "impossible"),
    (dict(lel_pct=None, ppm=None), "no gas reading"),
    (dict(ppm=-5.0), "impossible"),
    (dict(temp_c=99.0), "temperature"),
    (dict(lat=float("nan")), "coordinates"),
    (dict(lon=-96.0 + 0.5), "outside the licensed franchise"),
])
def test_rejections(runtime, kw, msg):
    with pytest.raises(StageRejected, match=msg):
        ValidateStage(runtime.services).run(vc(**kw))


def test_ppm_only_derives_lel_and_flags(runtime):
    c = vc(lel_pct=None, ppm=10000.0, effective_lel_pct=None)
    ValidateStage(runtime.services).run(c)
    assert c.report.effective_lel_pct == pytest.approx(20.0) and "lel derived from ppm" in c.flags


def test_effective_lel_uses_more_conservative_reading_and_flags_mismatch(runtime):
    c = vc(lel_pct=2.0, ppm=20000.0, effective_lel_pct=None)     # ppm implies 40 %LEL
    ValidateStage(runtime.services).run(c)
    assert c.report.effective_lel_pct == pytest.approx(40.0) and "lel/ppm readings inconsistent" in c.flags


def test_franchise_required_but_missing_fails_closed(runtime):
    runtime.services.franchise_polygon = None
    with pytest.raises(StageRejected, match="no franchise polygon"):
        ValidateStage(runtime.services).run(vc())


def _incident(rt, leak_id, dx_m, hours_ago, dup=0):
    from gas_electric_response.geo import from_local
    g = from_local(Point(dx_m, 0), -96.8, 32.78)
    f = rt.services.schema.incident_field
    rt.services.incidents.add([FeatureRecord({f("leak_id"): leak_id, f("is_duplicate"): dup, f("grade"): 2, f("max_lel_pct"): 5.0,
        f("reading_count"): 1, f("reported_at"): int((datetime.now(timezone.utc) - timedelta(hours=hours_ago)).timestamp() * 1000)}, g)])


def test_duplicate_within_50ft_and_12h(runtime):
    _incident(runtime, "OLD", 10, 3)
    c = vc(leak_id="NEW")
    DedupeStage(runtime.services).run(c)
    assert c.duplicate_of == "OLD" and c.parent_grade == 2 and c.parent_object_id is not None


@pytest.mark.parametrize("dx,hours", [(30, 3), (10, 13)])       # too far / too old
def test_not_duplicate(runtime, dx, hours):
    _incident(runtime, "OLD", dx, hours)
    c = vc(leak_id="NEW")
    DedupeStage(runtime.services).run(c)
    assert c.duplicate_of is None


def test_duplicate_rows_are_not_used_as_parent(runtime):
    _incident(runtime, "DUPROW", 5, 2, dup=1)
    c = vc(leak_id="NEW")
    DedupeStage(runtime.services).run(c)
    assert c.duplicate_of is None


def test_over_range_readings_are_clamped_not_rejected(runtime):
    c = vc(lel_pct=140.0, ppm=2_000_000.0)
    ValidateStage(runtime.services).run(c)
    assert c.report.lel_pct == 100.0 and c.report.ppm == 1_000_000.0 and c.report.effective_lel_pct == 100.0
    assert sum("above sensor range" in f for f in c.flags) == 2


def test_escalation_by_lel_jump_even_when_grade_unchanged(runtime):
    c = vc(leak_id="NEW", effective_lel_pct=45.0)
    c.duplicate_of, c.parent_grade, c.parent_max_lel = "OLD", 1, 30.0
    from gas_electric_response.models import HazardGrade
    c.grade = HazardGrade.CRITICAL
    assert c.is_escalation                      # 45 >= 30 + 10 even though the grade is not worse
    c.report.effective_lel_pct = 35.0
    assert not c.is_escalation
    c.grade = HazardGrade.MONITORED; c.parent_grade = 3; c.report.effective_lel_pct = 90.0
    assert not c.is_escalation                  # Grade 3 never escalates by LEL alone
