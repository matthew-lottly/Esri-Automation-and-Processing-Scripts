"""Turn a raw Survey123/Field Maps feature record into a normalized LeakReport."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from .config import LayerSchema
from .models import FeatureRecord, LeakReport
from .clients.store import ms_to_dt


def _norm(v: Any) -> str:
    return re.sub(r"[\s\-]+", "_", str(v).strip().lower()) if v not in (None, "") else ""


def _num(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class SurveyRecordParser:
    def __init__(self, schema: LayerSchema):
        self.s = schema.survey_layer

    def _choice(self, table, raw: Any) -> str:
        n = _norm(raw)
        mapping = {_norm(k): v for k, v in table.raw().items()}
        return mapping.get(n, "unknown") if n else "unknown"

    def parse(self, rec: FeatureRecord) -> LeakReport:
        a = {k.lower(): v for k, v in rec.attributes.items()}      # field-name case varies between services
        g = lambda field: a.get(str(field).lower())
        gid = str(g("globalid") or "").strip("{}").lower() or str(uuid.uuid4())
        created = ms_to_dt(g(self.s.created_field)) or datetime.now(timezone.utc)
        leak_id = str(g(self.s.leak_id) or "").strip() or f"LK-{created:%Y%m%d}-{gid[:8].upper()}"
        if rec.geometry is None or rec.geometry.is_empty:
            lon = lat = float("nan")
        else:
            lon, lat = rec.geometry.x, rec.geometry.y
        return LeakReport(
            leak_id=leak_id, source_global_id=gid, source_object_id=rec.object_id, lon=lon, lat=lat, reported_at=created,
            lel_pct=_num(g(self.s.lel_pct)), ppm=_num(g(self.s.ppm)),
            pipeline_type=self._choice(self.s.pipeline_type_values, g(self.s.pipeline_type)),
            pressure_class=self._choice(self.s.pressure_class_values, g(self.s.pressure_class)),
            surface_cover=self._choice(self.s.surface_cover_values, g(self.s.surface_cover)),
            crew_badge=str(g(self.s.crew_badge) or ""), temp_c=_num(g(self.s.get("temp_c", "temp_c"))),
            notes=str(g(self.s.notes) or ""), raw_attributes=dict(rec.attributes),
        )
