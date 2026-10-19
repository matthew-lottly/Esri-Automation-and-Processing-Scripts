"""Spatial and domain analysis: plume, electric/facility intersect, valves, Hazard Severity Index."""
from __future__ import annotations

import logging
from typing import Any

from shapely.geometry import Point

from ..clients.store import Query
from ..geo import buffer_m, distance_m
from ..models import AssetHit, HazardGrade, IncidentContext, ValveRef
from ..plume import PlumeModel
from ..reference import Services
from .base import Stage

log = logging.getLogger(__name__)


def _key(v: Any) -> str:
    """Compare asset types ignoring case, spaces, hyphens and underscores ('Pad-Mount Transformer' == 'PadmountTransformer')."""
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


class ElectricIntersectStage(Stage):
    name = "Intersect plume with electric grid"
    description = "Pad-mount transformers / switchgear (spark) and underground conduit (migration)"
    external = "ArcGIS electric assets layer"
    group = "Spatial & Risk Analysis"

    def __init__(self, svc: Services):
        self.svc = svc
        e = svc.schema.electric_assets
        self.e = e
        self.cat_lookup: dict[str, str] = {}
        for cat, values in e.categories.raw().items():
            for v in values or []:
                self.cat_lookup[_key(v)] = cat
        self.unmapped = svc.params.grading.get("unmapped_electric_category", "other")
        en = e.get("energized_values", []) or []
        self.energized_values = {_key(v) for v in en}

    def run(self, ctx: IncidentContext) -> None:
        r = ctx.report
        e = self.e
        fields = [e.asset_id_field, e.asset_type_field] + ([e.energized_field] if e.get("energized_field") else [])
        recs = self.svc.electric.query(Query(geometry=ctx.plume.polygon, out_fields=fields))
        for rec in recs:
            a = rec.attributes
            atype = a.get(e.asset_type_field)
            cat = self.cat_lookup.get(_key(atype))
            if cat is None:
                cat = self.unmapped
                note = f"unmapped electric asset type '{atype}' treated as {cat}"
                if note not in ctx.flags:
                    ctx.flags.append(note)
            energized = None
            if e.get("energized_field"):
                energized = _key(a.get(e.energized_field)) in self.energized_values
            c = rec.geometry.centroid if rec.geometry is not None else Point(r.lon, r.lat)
            ctx.electric_hits.append(AssetHit("electric", cat, str(a.get(e.asset_id_field, "")), str(atype),
                                              distance_m(r.lon, r.lat, rec.geometry) if rec.geometry is not None else 0.0,
                                              energized, c.x, c.y, geometry=rec.geometry))
        ctx.electric_hits.sort(key=lambda h: h.distance_m)


class FacilitiesAndMetersStage(Stage):
    name = "Find critical facilities & meters at risk"
    description = "Hospitals, schools, nursing homes inside the plume; customer meters counted"
    external = "ArcGIS facilities / meters layers"
    group = "Spatial & Risk Analysis"

    def __init__(self, svc: Services):
        self.svc = svc

    def run(self, ctx: IncidentContext) -> None:
        r, cf = ctx.report, self.svc.schema.critical_facilities
        for rec in self.svc.facilities.query(Query(geometry=ctx.plume.polygon, out_fields=[cf.name_field, cf.type_field])):
            c = rec.geometry.centroid if rec.geometry is not None else Point(r.lon, r.lat)
            ctx.facility_hits.append(AssetHit("facility", "facility", str(rec.object_id), str(rec.attributes.get(cf.type_field, "")),
                                              distance_m(r.lon, r.lat, rec.geometry) if rec.geometry is not None else 0.0,
                                              None, c.x, c.y, str(rec.attributes.get(cf.name_field, ""))))
        if self.svc.meters is not None:
            ctx.meter_count = self.svc.meters.count(Query(geometry=ctx.plume.polygon))


class ValveStage(Stage):
    name = "Locate nearest gas shutoff valves"
    description = "Three closest valves (true upstream tracing needs a Utility Network trace)"
    external = "ArcGIS gas valves layer"
    group = "Spatial & Risk Analysis"

    def __init__(self, svc: Services):
        self.svc = svc

    def run(self, ctx: IncidentContext) -> None:
        r, gv, vp = ctx.report, self.svc.schema.gas_valves, self.svc.params.valves
        recs = self.svc.valves.query(Query(geometry=buffer_m(r.lon, r.lat, float(vp.search_radius_m))))
        usable = {_key(x) for x in (gv.get("usable_values", []) or [])}
        found = []
        for rec in recs:
            if gv.get("status_field") and usable and _key(rec.attributes.get(gv.status_field)) not in usable:
                continue
            if rec.geometry is None:
                continue
            c = rec.geometry.centroid
            found.append(ValveRef(str(rec.attributes.get(gv.valve_id_field, rec.object_id)), distance_m(r.lon, r.lat, rec.geometry), c.x, c.y))
        found.sort(key=lambda v: v.distance_m)
        ctx.valves = found[: int(vp.count)]
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
