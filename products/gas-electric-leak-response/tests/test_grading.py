import pytest

from gas_electric_response.models import AssetHit, ExcavationTicket, HazardGrade
from gas_electric_response.stages.analysis import GradingPolicy
from tests.conftest import make_report
from gas_electric_response.models import IncidentContext


def ctx(lel, elec=(), fac=0, meters=0, tickets=0):
    c = IncidentContext(report=make_report(effective_lel_pct=lel, lel_pct=lel))
    c.electric_hits = [AssetHit("electric", cat, "A", "t", 3.0) for cat in elec]
    c.facility_hits = [AssetHit("facility", "facility", "F", "school", 10.0) for _ in range(fac)]
    c.meter_count = meters
    c.tickets = [ExcavationTicket("T1")] * tickets
    return c


@pytest.mark.parametrize("lel,elec,fac,meters,expected", [
    (35, ["ignition_source"], 0, 0, HazardGrade.CRITICAL),
    (35, ["conduit"], 0, 0, HazardGrade.CRITICAL),
    (21, [], 1, 0, HazardGrade.CRITICAL),                  # critical facility overlap
    (20, ["ignition_source"], 0, 0, HazardGrade.URGENT),   # must EXCEED 20
    (35, ["other"], 0, 0, HazardGrade.URGENT),             # overhead conductor alone does not escalate to Grade 1
    (35, [], 0, 0, HazardGrade.URGENT),                    # high gas, no overlap
    (12, [], 0, 0, HazardGrade.URGENT),                    # >= monitored_max
    (3, ["ignition_source"], 0, 0, HazardGrade.URGENT),    # measurable + electric
    (3, [], 0, 2, HazardGrade.URGENT),                     # measurable + meters (structures)
    (3, [], 0, 0, HazardGrade.MONITORED),
    (0.4, ["ignition_source"], 1, 5, HazardGrade.MONITORED),  # below measurable threshold
])
def test_grade_table(params, lel, elec, fac, meters, expected):
    g, reasons = GradingPolicy(params).grade(ctx(lel, elec, fac, meters))
    assert g == expected and reasons


def test_excavation_escalation_is_opt_in(params):
    assert GradingPolicy(params).grade(ctx(3, tickets=2))[0] == HazardGrade.MONITORED
