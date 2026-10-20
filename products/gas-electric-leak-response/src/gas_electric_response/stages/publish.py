"""Commit results to the enterprise feature service (incident point + plume polygon)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from shapely.geometry import Point

from ..clients.store import Query
from ..models import FeatureRecord, IncidentContext
from ..reference import Services
from .base import Stage

log = logging.getLogger(__name__)


class PublishStage(Stage):
    name = "Update ArcGIS feature layers"
    description = "Incident point, plume polygon, grade and valve references (dashboards/Field Maps update live)"
    external = "ArcGIS incident + plume layers"
    group = "Enterprise GIS Update"
    limits = "Writes the incident point and the plume polygon; escalations replace the parent plume. Hosted-layer writes are not yet verified against a live org."

    def __init__(self, svc: Services):
        self.svc = svc

    def run(self, ctx: IncidentContext) -> None:
        r, f = ctx.report, self.svc.schema.incident_field
        pf = lambda k: self.svc.schema.plume_layer.fields.raw()[k]["name"]
        attrs = ctx.incident_attrs
        # 1. Always add this submission as its own row (traceable; reconcile counts it).
        ctx.incident_object_id = self.svc.incidents.add([FeatureRecord(attrs, Point(r.lon, r.lat))])[0]
        # 2. Plume polygon for new incidents and for escalations.
        escalated = ctx.is_escalation
        if not ctx.is_duplicate or escalated:
            plume_attrs = {pf("leak_id"): ctx.duplicate_of or r.leak_id, pf("grade"): int(ctx.grade),
                           pf("length_m"): ctx.plume.length_m, pf("max_width_m"): ctx.plume.max_width_m,
                           pf("model"): ctx.plume.model[:64],
                           pf("created_at"): int(datetime.now(timezone.utc).timestamp() * 1000)}
            if escalated:   # replace the parent's plume instead of stacking polygons
                old = self.svc.plumes.query(Query(equals={pf("leak_id"): ctx.duplicate_of}, return_geometry=False))
                for o in old:
                    self.svc.plumes.update([FeatureRecord(plume_attrs, ctx.plume.polygon, o.object_id)])
                if not old:
                    self.svc.plumes.add([FeatureRecord(plume_attrs, ctx.plume.polygon)])
            else:
                self.svc.plumes.add([FeatureRecord(plume_attrs, ctx.plume.polygon)])
        # 3. Keep the parent's counters current; copy new grade/dispatch on escalation.
        if ctx.is_duplicate and ctx.parent_object_id is not None:
            upd = {f("reading_count"): attrs[f("reading_count")], f("max_lel_pct"): attrs[f("max_lel_pct")]}
            if escalated:
                for k in ("grade", "grade_label", "dispatch_code", "valve_ids", "elec_ignition_count", "elec_conduit_count",
                          "elec_min_dist_m", "critical_fac_count", "critical_fac_names", "meters_affected", "plume_length_m"):
                    upd[f(k)] = attrs[f(k)]
                upd[f("quality_flags")] = f"escalated by {r.leak_id}"[:255]
            self.svc.incidents.update([FeatureRecord(upd, None, ctx.parent_object_id)])
        ctx.published = True
        log.info("Published %s (OID %s, grade %s)%s", r.leak_id, ctx.incident_object_id, int(ctx.grade), " [escalation]" if escalated else "")
