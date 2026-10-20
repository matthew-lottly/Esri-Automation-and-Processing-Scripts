"""Spatial and domain analysis: plume, electric/facility intersect, valves, Hazard Severity Index."""
from __future__ import annotations

import abc
import logging
from typing import Any

from ..clients.store import Query
from ..geo import LocalFrame
from ..models import AssetHit, HazardGrade, IncidentContext, ValveRef
from ..plume import PlumeModel
from ..reference import Services
from .base import Stage

log = logging.getLogger(__name__)


class TypeKey:
    """Compare asset types ignoring case, spaces, hyphens and underscores ('Pad-Mount Transformer' == 'PadmountTransformer')."""

    @staticmethod
    def of(v: Any) -> str:
        return "".join(ch for ch in str(v).casefold() if ch.isalnum()) if v is not None else ""


class PlumeStage(Stage):
    name = "Build directional plume"
    description = "Pressure, %LEL, surface cover and wind => dispersion envelope (not a circle)"
    group = "Spatial & Risk Analysis"

    def __init__(self, svc: Services):
        self.model = PlumeModel(svc.params)

    def run(self, ctx: IncidentContext) -> None:
        ctx.plume = self.model.build(ctx.report, ctx.weather)
        for n in ctx.plume.assumptions:
            if "unknown" in n or "no usable wind" in n or "calm wind with no direction" in n:
                ctx.flags.append(n)


class OverlayStage(Stage):
    """Base class for 'find reference features related to the incident' stages.

    Subclass it (or configure one of the existing subclasses) to add a new analysis. It handles POINT, LINE and
    POLYGON reference layers: every hit records its distance, plus the length (lines) or area (polygons) that
    falls inside the search area. Override `search_geometry`, `make_hit` and `collect`.
    """

    store_name: str = ""

    def __init__(self, svc: Services):
        self.svc = svc

    @property
    def store(self):
        return getattr(self.svc, self.store_name)

    @abc.abstractmethod
    def search_geometry(self, ctx: IncidentContext, frame: LocalFrame): ...

    @abc.abstractmethod
    def make_hit(self, ctx: IncidentContext, rec, frame: LocalFrame, search): ...

    @abc.abstractmethod
    def collect(self, ctx: IncidentContext, hits: list) -> None: ...

    def out_fields(self) -> list[str] | None:
        return None

    @staticmethod
    def measure(frame: LocalFrame, geom, search) -> tuple[float | None, float | None]:
        """(length inside search for lines, area inside search for polygons)."""
        inter = geom.intersection(search)
        if geom.geom_type in ("LineString", "MultiLineString"):
            return round(frame.length(inter), 1), None
        if geom.geom_type in ("Polygon", "MultiPolygon"):
            return None, round(frame.area(inter), 1)
        return None, None

    def run(self, ctx: IncidentContext) -> None:
        frame = LocalFrame(ctx.report.lon, ctx.report.lat)
        search = self.search_geometry(ctx, frame)
        recs = self.store.query(Query(geometry=search, out_fields=self.out_fields()))
        self.collect(ctx, [h for h in (self.make_hit(ctx, rec, frame, search) for rec in recs) if h is not None])


class ElectricIntersectStage(OverlayStage):
    name = "Intersect plume with electric grid"
    description = "Pad-mount transformers / switchgear (spark) and underground conduit (migration)"
    external = "ArcGIS electric assets layer"
    group = "Spatial & Risk Analysis"
    store_name = "electric"

    def __init__(self, svc: Services):
        super().__init__(svc)
        e = svc.schema.electric_assets
        self.e = e
        self.cat_lookup: dict[str, str] = {}
        for cat, values in e.categories.raw().items():
            for v in values or []:
                self.cat_lookup[TypeKey.of(v)] = cat
        self.unmapped = svc.params.grading.get("unmapped_electric_category", "other")
        self.energized_values = {TypeKey.of(v) for v in (e.get("energized_values", []) or [])}

    def out_fields(self):
        e = self.e
        return [e.asset_id_field, e.asset_type_field] + ([e.energized_field] if e.get("energized_field") else [])

    def search_geometry(self, ctx, frame):
        return ctx.plume.polygon

    def make_hit(self, ctx, rec, frame, search):
        a, e, r = rec.attributes, self.e, ctx.report
        atype = a.get(e.asset_type_field)
        cat = self.cat_lookup.get(TypeKey.of(atype))
        if cat is None:
            cat = self.unmapped
            note = f"unmapped electric asset type '{atype}' treated as {cat}"
            if note not in ctx.flags:
                ctx.flags.append(note)
        energized = (TypeKey.of(a.get(e.energized_field)) in self.energized_values) if e.get("energized_field") else None
        geom = rec.geometry if rec.geometry is not None else r.geometry
        c = geom.representative_point()
        length, area = self.measure(frame, geom, search)
        return AssetHit("electric", cat, str(a.get(e.asset_id_field, "")), str(atype), frame.distance(r.geometry, geom),
                        energized, c.x, c.y, geometry=rec.geometry, inside_length_m=length, inside_area_m2=area)

    def collect(self, ctx, hits):
        ctx.electric_hits = sorted(hits, key=lambda h: h.distance_m)


class FacilitiesStage(OverlayStage):
    name = "Find critical facilities at risk"
    description = "Hospitals, schools, nursing homes inside the plume"
    external = "ArcGIS facilities layer"
    group = "Spatial & Risk Analysis"
    store_name = "facilities"

    def out_fields(self):
        cf = self.svc.schema.critical_facilities
        return [cf.name_field, cf.type_field]

    def search_geometry(self, ctx, frame):
        return ctx.plume.polygon

    def make_hit(self, ctx, rec, frame, search):
        cf, r = self.svc.schema.critical_facilities, ctx.report
        geom = rec.geometry if rec.geometry is not None else r.geometry
        c = geom.representative_point()
        length, area = self.measure(frame, geom, search)
        return AssetHit("facility", "facility", str(rec.object_id), str(rec.attributes.get(cf.type_field, "")),
                        frame.distance(r.geometry, geom), None, c.x, c.y, str(rec.attributes.get(cf.name_field, "")),
                        geometry=rec.geometry, inside_length_m=length, inside_area_m2=area)

    def collect(self, ctx, hits):
        ctx.facility_hits = sorted(hits, key=lambda h: h.distance_m)


class MetersStage(OverlayStage):
    name = "Count customer meters at risk"
    description = "Customer meters inside the plume (proxy for occupied structures)"
    external = "ArcGIS customer meters layer"
    group = "Spatial & Risk Analysis"
    store_name = "meters"

    def run(self, ctx: IncidentContext) -> None:
        if self.svc.meters is None:
            return
        ctx.meter_count = self.svc.meters.count(Query(geometry=ctx.plume.polygon))

    def search_geometry(self, ctx, frame): return ctx.plume.polygon
    def make_hit(self, ctx, rec, frame, search): return None
    def collect(self, ctx, hits): pass


class ValveStage(OverlayStage):
    name = "Locate nearest gas shutoff valves"
    description = "Three closest valves (true upstream tracing needs a Utility Network trace)"
    external = "ArcGIS gas valves layer"
    group = "Spatial & Risk Analysis"
    store_name = "valves"

    def search_geometry(self, ctx, frame):
        return frame.buffer(ctx.report.geometry, float(self.svc.params.valves.search_radius_m))

    def make_hit(self, ctx, rec, frame, search):
        gv = self.svc.schema.gas_valves
        usable = {TypeKey.of(x) for x in (gv.get("usable_values", []) or [])}
        if gv.get("status_field") and usable and TypeKey.of(rec.attributes.get(gv.status_field)) not in usable:
            return None
        if rec.geometry is None:
            return None
        c = rec.geometry.representative_point()
        return ValveRef(str(rec.attributes.get(gv.valve_id_field, rec.object_id)), frame.distance(ctx.report.geometry, rec.geometry), c.x, c.y)

    def collect(self, ctx, hits):
        vp = self.svc.params.valves
        ctx.valves = sorted(hits, key=lambda v: v.distance_m)[: int(vp.count)]
        if not ctx.valves:
            ctx.flags.append(f"no gas valves found within {vp.search_radius_m} m")
        elif ctx.valves[0].distance_m > float(vp.get("far_valve_flag_m", 500)):
            ctx.flags.append(f"nearest valve is {ctx.valves[0].distance_m:.0f} m away")


class GradingPolicy:
    """Pure function of the analysis results -> HazardGrade (unit-tested; see docs/hazard-grading.md)."""

    def __init__(self, params):
        self.g = params.grading

    def grade(self, ctx: IncidentContext) -> tuple[HazardGrade, list[str]]:
        g = self.g
        lel = ctx.report.effective_lel_pct or 0.0
        g1_cats = set(g.grade1_electric_categories)
        elec_hits = [h for h in ctx.electric_hits if h.category in g1_cats]
        elec_any = bool(ctx.electric_hits)
        fac = bool(ctx.facility_hits)
        structures = fac or ctx.meter_count > 0
        reasons = [f"effective reading {lel:.1f} %LEL"]
        if elec_hits:
            reasons.append(f"{len(elec_hits)} electric ignition/conduit asset(s) inside plume")
        if fac:
            reasons.append(f"{len(ctx.facility_hits)} critical facility(ies) inside plume")
        if lel > float(g.critical_lel_pct) and (elec_hits or (fac and g.critical_facility_makes_grade1)):
            return HazardGrade.CRITICAL, reasons + [f"> {g.critical_lel_pct}% LEL overlapping ignition source/critical facility"]
        if lel >= float(g.monitored_max_lel_pct):
            return HazardGrade.URGENT, reasons + [f">= {g.monitored_max_lel_pct}% LEL: significant gas migration"]
        if lel >= float(g.measurable_lel_pct) and (elec_any or (g.structure_in_plume_escalates and structures)):
            return HazardGrade.URGENT, reasons + ["measurable gas with electric asset/structure inside plume"]
        if ctx.tickets and g.excavation_ticket_escalates and lel >= float(g.measurable_lel_pct):
            return HazardGrade.URGENT, reasons + ["active 811 excavation ticket within 500 ft"]
        return HazardGrade.MONITORED, reasons + ["low reading and nothing at risk inside plume"]


class GradeStage(Stage):
    name = "Hazard Severity Index"
    description = "Grade 1 Critical / Grade 2 Urgent / Grade 3 Monitored"
    group = "Spatial & Risk Analysis"

    def __init__(self, svc: Services):
        self.policy = GradingPolicy(svc.params)

    def run(self, ctx: IncidentContext) -> None:
        ctx.grade, ctx.grade_reasons = self.policy.grade(ctx)
