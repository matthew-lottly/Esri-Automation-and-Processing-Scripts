from datetime import datetime, timezone
from pathlib import Path

import pytest
from shapely.geometry import Point

from gas_electric_response.bootstrap import DemoRuntimeFactory
from gas_electric_response.config import LayerSchema, RiskParameters
from gas_electric_response.models import FeatureRecord, IncidentContext, LeakReport

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def runtime(tmp_path):
    return DemoRuntimeFactory(tmp_path / "out", str(ROOT / "config"), str(ROOT / "sample_data")).create()


@pytest.fixture
def params():
    return RiskParameters.load(ROOT / "config")


@pytest.fixture
def schema():
    return LayerSchema.load(ROOT / "config")


def make_report(**kw) -> LeakReport:
    base = dict(leak_id="LK-T", source_global_id="g-1", source_object_id=1, lon=-96.8, lat=32.78,
                reported_at=datetime.now(timezone.utc), lel_pct=10.0, ppm=None, pipeline_type="cast_iron",
                pressure_class="medium_distribution", surface_cover="asphalt", effective_lel_pct=10.0)
    base.update(kw)
    return LeakReport(**base)


@pytest.fixture
def make_ctx():
    def _f(**kw):
        return IncidentContext(report=make_report(**kw))
    return _f
