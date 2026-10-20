"""ReportData: the namespace every template placeholder, table and map reads from.

Add your own values or tables by subclassing `ReportData` (override `extend_namespace` / `extend_sources`)
and passing it to the ReportBuilder. See docs/report-templates.md.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any, Callable
from zoneinfo import ZoneInfo

from ..models import IncidentContext
from ..reference import Services


class Compass:
    POINTS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

    @classmethod
    def label(cls, degrees: float | None) -> str | None:
        return None if degrees is None else cls.POINTS[int((degrees % 360) / 22.5 + 0.5) % 16]


class Row(SimpleNamespace):
    """A table row: attribute access in templates ({row.asset_id}); also behaves like a dict for {row[asset_id]}."""

    def __getitem__(self, k):
        return getattr(self, k)


class ReportData:
    def __init__(self, ctx: IncidentContext, svc: Services, generated_at: datetime):
        self.ctx, self.svc, self.generated_at = ctx, svc, generated_at
        self.tz = ZoneInfo(svc.settings.timezone)

    # ---- helpers -----------------------------------------------------------------
    def local(self, dt: datetime | None) -> str | None:
        return None if dt is None else dt.astimezone(self.tz).strftime("%Y-%m-%d %H:%M:%S %Z")

    def _incident_values(self) -> dict[str, Any]:
        """Incident-layer attributes keyed by INTERNAL key (grade, dispatch_code, valve_ids...), not your field names."""
        names = {v["name"].lower(): k for k, v in self.svc.schema.incident_layer.fields.raw().items()}
        return {names[k.lower()]: v for k, v in self.ctx.incident_attrs.items() if k.lower() in names}

    # ---- namespace -----------------------------------------------------------------
    def namespace(self) -> dict[str, Any]:
        c, r, w, p = self.ctx, self.ctx.report, self.ctx.weather, self.ctx.plume
        grade_n = int(c.grade) if c.grade is not None else None
        ns: dict[str, Any] = {
            "report": SimpleNamespace(**{**r.__dict__, "reported_local": self.local(r.reported_at),
                                         "reported_utc": r.reported_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
                                         "geometry_type": r.geometry_type, "anchor": f"{r.lat:.6f}, {r.lon:.6f}"}),
            "raw": dict(r.raw_attributes),
            "incident": SimpleNamespace(**{**self._incident_values(), "is_duplicate": c.is_duplicate, "duplicate_of": c.duplicate_of,
                                           "is_escalation": c.is_escalation, "object_id": c.incident_object_id}),
            "grade": SimpleNamespace(number=grade_n, label=c.grade.label if c.grade else None, color=self.svc_theme_hex(grade_n),
                                     reasons="; ".join(c.grade_reasons), dispatch=c.dispatch_code,
                                     title=f"Grade {grade_n} – {c.grade.label.upper()}" if c.grade else None),
            "weather": SimpleNamespace(
                wind_speed_mps=w.wind_speed_mps if w else None, wind_from_deg=w.wind_from_deg if w else None,
                wind_from_text=Compass.label(w.wind_from_deg) if w else None, air_temp_c=w.air_temp_c if w else None,
                pressure_hpa=w.pressure_hpa if w else None, station=w.station if w else None, status=w.status if w else "UNAVAILABLE",
                observed_local=self.local(w.observed_at) if w else None,
                summary=("Weather unavailable – conservative omnidirectional envelope used" if not w else
                         f"{w.wind_speed_mps if w.wind_speed_mps is not None else 'n/a'} m/s from {Compass.label(w.wind_from_deg) or 'n/a'}"
                         f" ({w.wind_from_deg if w.wind_from_deg is not None else 'n/a'}°)")),
            "plume": SimpleNamespace(length_m=p.length_m if p else None, max_width_m=p.max_width_m if p else None,
                                     model=p.model if p else None, assumptions="; ".join(p.assumptions) if p else ""),
            "counts": SimpleNamespace(
                electric_total=len(c.electric_hits), electric_ignition=sum(h.category == "ignition_source" for h in c.electric_hits),
                electric_conduit=sum(h.category == "conduit" for h in c.electric_hits), facilities=len(c.facility_hits),
                meters=c.meter_count, valves=len(c.valves), tickets=len(c.tickets), flags=len(c.flags), photos=len(r.photos)),
            "nearest_valve": SimpleNamespace(id=c.valves[0].valve_id if c.valves else None,
                                             distance_m=c.valves[0].distance_m if c.valves else None),
            "generated": SimpleNamespace(local=self.local(self.generated_at), utc=self.generated_at.strftime("%Y-%m-%d %H:%M:%S UTC")),
            "flags": "; ".join(c.flags),
            "photos": list(r.photos),
        }
        ns.update(self.extend_namespace(ns))
        return ns

    def svc_theme_hex(self, grade_n: int | None) -> str:
        return {1: "#D1271E", 2: "#E8890C", 3: "#2F9E5B"}.get(grade_n, "#5F6C7B")

    def _raw_value(self, key: str, value: Any) -> Any:
        """Show the survey's creation-time epoch as a readable local timestamp."""
        if key.lower() == str(self.svc.schema.survey_layer.created_field).lower() and isinstance(value, (int, float)):
            from ..clients.store import EpochMs

            return self.local(EpochMs.to_datetime(value))
        return value

    def extend_namespace(self, ns: dict[str, Any]) -> dict[str, Any]:
        """Override in a subclass to add your own placeholders, e.g. {"utility": SimpleNamespace(name="Acme Gas & Power")}."""
        return {}

    # ---- table sources ---------------------------------------------------------------
    def sources(self) -> dict[str, Callable[[], list[Row]]]:
        c = self.ctx
        src: dict[str, Callable[[], list[Row]]] = {
            "electric_hits": lambda: [Row(asset_id=h.asset_id, asset_type=h.asset_type, category=h.category.replace("_", " "),
                                          distance_m=h.distance_m, length_in_plume_m=h.inside_length_m, area_in_plume_m2=h.inside_area_m2,
                                          in_plume=(f"{h.inside_length_m:.0f} m" if h.inside_length_m is not None else
                                                    f"{h.inside_area_m2:,.0f} m\u00b2" if h.inside_area_m2 is not None else "point"),
                                          energized=("n/a" if h.energized is None else "yes" if h.energized else "no"),
                                          geometry_type=h.geometry.geom_type if h.geometry is not None else "Point") for h in c.electric_hits],
            "facility_hits": lambda: [Row(name=h.name or h.asset_id, facility_type=h.asset_type, distance_m=h.distance_m) for h in c.facility_hits],
            "valves": lambda: [Row(rank=i, valve_id=v.valve_id, distance_m=v.distance_m) for i, v in enumerate(c.valves, 1)],
            "tickets": lambda: [Row(number=t.number, status=t.status, work_type=t.work_type, distance_m=t.distance_m, expires=t.expires) for t in c.tickets],
            "flag_list": lambda: [Row(text=f) for f in c.flags],
            "reasons": lambda: [Row(text=t) for t in c.grade_reasons],
            "raw_attributes": lambda: [Row(name=k, value=self._raw_value(k, v)) for k, v in sorted(c.report.raw_attributes.items())],
        }
        src.update(self.extend_sources())
        return src

    def extend_sources(self) -> dict[str, Callable[[], list[Row]]]:
        """Override to add tables: return {"parcels": lambda: [Row(apn=..., owner=...), ...]}."""
        return {}

    # ---- self-documentation (used by `template-help`) --------------------------------------
    def describe(self) -> dict[str, list[str]]:
        ns = self.namespace()
        out: dict[str, list[str]] = {}
        for k, v in ns.items():
            if isinstance(v, SimpleNamespace):
                out[k] = sorted(vars(v))
            elif isinstance(v, dict):
                out[k] = sorted(map(str, v))[:60]
            else:
                out[k] = []
        return out
