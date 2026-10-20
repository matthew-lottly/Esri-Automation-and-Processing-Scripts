"""Turn a raw Survey123/Field Maps feature record into a normalized LeakReport."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from .config import LayerSchema
from .models import FeatureRecord, LeakReport
from .clients.store import EpochMs


class SurveyRecordParser:
    """Turns a raw survey feature (any geometry type) into a normalized LeakReport."""

    def __init__(self, schema: LayerSchema):
        self.s = schema.survey_layer

    @staticmethod
    def _norm(v: Any) -> str:
        return re.sub(r"[\s\-]+", "_", str(v).strip().lower()) if v not in (None, "") else ""

    @staticmethod
    def _num(v: Any) -> float | None:
        if v in (None, ""):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _choice(self, table, raw: Any) -> str:
        n = self._norm(raw)
        mapping = {self._norm(k): v for k, v in table.raw().items()}
        return mapping.get(n, "unknown") if n else "unknown"

    def parse(self, rec: FeatureRecord) -> LeakReport:
        a = {k.lower(): v for k, v in rec.attributes.items()}      # field-name case varies between services
        g = lambda field: a.get(str(field).lower())
        gid = str(g("globalid") or "").strip("{}").lower() or str(uuid.uuid4())
        created = EpochMs.to_datetime(g(self.s.created_field)) or datetime.now(timezone.utc)
        leak_id = str(g(self.s.leak_id) or "").strip() or f"LK-{created:%Y%m%d}-{gid[:8].upper()}"
        if rec.geometry is None or rec.geometry.is_empty:
            lon = lat = float("nan")
        else:
            anchor = rec.geometry.representative_point() if rec.geometry.geom_type != "Point" else rec.geometry
            lon, lat = anchor.x, anchor.y
        return LeakReport(
            leak_id=leak_id, source_global_id=gid, source_object_id=rec.object_id, lon=lon, lat=lat, reported_at=created,
            lel_pct=self._num(g(self.s.lel_pct)), ppm=self._num(g(self.s.ppm)),
            pipeline_type=self._choice(self.s.pipeline_type_values, g(self.s.pipeline_type)),
            pressure_class=self._choice(self.s.pressure_class_values, g(self.s.pressure_class)),
            surface_cover=self._choice(self.s.surface_cover_values, g(self.s.surface_cover)),
            crew_badge=str(g(self.s.crew_badge) or ""), temp_c=self._num(g(self.s.get("temp_c", "temp_c"))),
            notes=str(g(self.s.notes) or ""), raw_attributes=dict(rec.attributes),
            geometry=rec.geometry if (rec.geometry is not None and not rec.geometry.is_empty) else None,
        )
