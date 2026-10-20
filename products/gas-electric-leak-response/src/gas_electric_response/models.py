"""Plain data objects passed between pipeline stages."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from shapely.geometry.base import BaseGeometry


class HazardGrade(enum.IntEnum):
    CRITICAL = 1   # Grade 1
    URGENT = 2     # Grade 2
    MONITORED = 3  # Grade 3

    @property
    def label(self) -> str:
        return {1: "Critical", 2: "Urgent", 3: "Monitored"}[int(self)]


class CIDict(dict):
    """dict with case-insensitive string-key lookup (ArcGIS returns the layer's own field-name casing)."""

    def _find(self, key):
        if isinstance(key, str) and not dict.__contains__(self, key):
            low = key.lower()
            for k in self:
                if isinstance(k, str) and k.lower() == low:
                    return k
        return key

    def __getitem__(self, key):
        return dict.__getitem__(self, self._find(key))

    def get(self, key, default=None):
        return dict.get(self, self._find(key), default)

    def __contains__(self, key):
        return dict.__contains__(self, self._find(key))

    def __setitem__(self, key, value):
        dict.__setitem__(self, self._find(key), value)

    def update(self, *a, **kw):
        for k, v in dict(*a, **kw).items():
            self[k] = v


@dataclass
class FeatureRecord:
    """A feature as exchanged with a FeatureStore. Geometry is always WGS84 (EPSG:4326)."""

    attributes: dict[str, Any]
    geometry: BaseGeometry | None = None
    object_id: int | None = None

    def __post_init__(self):
        if not isinstance(self.attributes, CIDict):
            self.attributes = CIDict(self.attributes)


@dataclass
class LeakReport:
    """Normalized field submission (what the technician entered)."""

    leak_id: str
    source_global_id: str
    source_object_id: int | None
    lon: float
    lat: float
    reported_at: datetime            # timezone-aware, UTC
    lel_pct: float | None
    ppm: float | None
    pipeline_type: str               # normalized category or "unknown"
    pressure_class: str
    surface_cover: str
    crew_badge: str = ""
    temp_c: float | None = None
    effective_lel_pct: float | None = None   # set by validation: max(LEL, ppm-derived LEL)
    notes: str = ""
    raw_attributes: dict[str, Any] = field(default_factory=dict)
    photos: list[str] = field(default_factory=list)   # local paths of downloaded attachments
    geometry: BaseGeometry | None = None              # the submitted feature: point, line OR polygon (WGS84)

    def __post_init__(self):
        """`lon`/`lat` are the ANCHOR point (the point itself, or a line/polygon's representative point)."""
        if self.geometry is None and self.lon == self.lon and self.lat == self.lat:   # not NaN
            from shapely.geometry import Point
            self.geometry = Point(self.lon, self.lat)

    @property
    def geometry_type(self) -> str:
        return self.geometry.geom_type if self.geometry is not None else "None"


@dataclass
class WeatherObservation:
    wind_speed_mps: float | None
    wind_from_deg: float | None
    air_temp_c: float | None
    pressure_hpa: float | None
    station: str = ""
    observed_at: datetime | None = None
    status: str = "OK"               # OK | STALE | PARTIAL | UNAVAILABLE


@dataclass
class ExcavationTicket:
    number: str
    status: str = ""
    work_type: str = ""
    distance_m: float | None = None
    expires: str = ""


@dataclass
class AssetHit:
    """A reference-layer feature found inside the plume."""

    layer: str                       # electric | facility | meter
    category: str                    # ignition_source | conduit | other | facility | meter
    asset_id: str
    asset_type: str
    distance_m: float
    energized: bool | None = None
    lon: float = 0.0
    lat: float = 0.0
    name: str = ""
    geometry: BaseGeometry | None = None       # kept so reports can draw lines (conduit) as lines
    inside_length_m: float | None = None       # line features: length inside the search area
    inside_area_m2: float | None = None        # polygon features: area inside the search area


@dataclass
class ValveRef:
    valve_id: str
    distance_m: float
    lon: float
    lat: float


@dataclass
class PlumeResult:
    polygon: BaseGeometry            # WGS84
    length_m: float
    max_width_m: float
    model: str
    wind_from_deg: float | None
    assumptions: list[str] = field(default_factory=list)


@dataclass
class IncidentContext:
    """Mutable bag that travels through the stages. Each stage fills in its own part."""

    report: LeakReport
    flags: list[str] = field(default_factory=list)            # data-quality / processing notes
    duplicate_of: str | None = None
    parent_grade: int | None = None
    parent_max_lel: float | None = None
    parent_reading_count: int = 1
    parent_object_id: int | None = None
    weather: WeatherObservation | None = None
    tickets: list[ExcavationTicket] = field(default_factory=list)
    plume: PlumeResult | None = None
    electric_hits: list[AssetHit] = field(default_factory=list)
    facility_hits: list[AssetHit] = field(default_factory=list)
    meter_count: int = 0
    valves: list[ValveRef] = field(default_factory=list)
    grade: HazardGrade | None = None
    grade_reasons: list[str] = field(default_factory=list)
    dispatch_code: str = ""
    incident_attrs: dict[str, Any] = field(default_factory=dict)   # schema-mapped attributes
    incident_object_id: int | None = None
    published: bool = False
    report_a_path: str = ""
    report_b_path: str = ""
    report_a_status: str = "PENDING"
    report_b_status: str = "NOT_REQUIRED"
    notify_status: str = "NOT_REQUIRED"
    notify_required: bool = False
    report_b_required: bool = True
    rejected: str | None = None

    @property
    def is_duplicate(self) -> bool:
        return self.duplicate_of is not None

    escalation_lel_delta: float = 10.0       # set from config by DedupeStage

    @property
    def is_escalation(self) -> bool:
        """A duplicate reading that is worse than the incident it duplicates: a worse grade, OR (while Grade 1-2)
        an effective %LEL at least `escalation_lel_delta` points above the highest reading so far."""
        if not self.is_duplicate or self.grade is None:
            return False
        if self.parent_grade is not None and int(self.grade) < int(self.parent_grade):
            return True
        lel = self.report.effective_lel_pct or 0.0
        return int(self.grade) <= 2 and self.parent_max_lel is not None and lel >= float(self.parent_max_lel) + self.escalation_lel_delta
