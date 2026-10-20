"""Cleaning Stage 2: dispatch/work-order tagging and schema alignment to the enterprise layer."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..clients.store import EpochMs
from ..exceptions import ConfigError
from ..models import HazardGrade, IncidentContext
from ..reference import Services
from .base import Stage


class SchemaMapper:
    """Maps internal keys -> your field names, enforcing declared types/lengths (single source: layer_schema.yaml)."""

    def __init__(self, fields_cfg: dict[str, dict[str, Any]]):
        self.cfg = fields_cfg

    def coerce(self, key: str, value: Any) -> Any:
        spec = self.cfg[key]
        if value is None:
            return None
        t = spec["type"]
        if t == "string":
            return str(value)[: int(spec.get("length", 255))]
        if t == "int":
            return int(value)
        if t == "double":
            return round(float(value), 4)
        if t == "datetime":
            return EpochMs.from_datetime(value) if isinstance(value, datetime) else int(value)
        raise ConfigError(f"Unknown field type '{t}' for {key}")

    def to_attributes(self, values: dict[str, Any]) -> dict[str, Any]:
        unknown = set(values) - set(self.cfg)
        if unknown:
            raise ConfigError(f"Pipeline produced keys not declared in layer_schema.yaml: {sorted(unknown)}")
        return {self.cfg[k]["name"]: self.coerce(k, v) for k, v in values.items()}


class DispatchTagStage(Stage):
    name = "Dispatch & work-order tagging"
    description = "Dispatch code, three nearest shutoff valves, status Active_Triage"
    group = "Cleaning Stage 2"

    def __init__(self, svc: Services):
        self.d = svc.params.dispatch

    def run(self, ctx: IncidentContext) -> None:
        elec = any(h.category in ("ignition_source", "conduit") for h in ctx.electric_hits)
        if ctx.grade == HazardGrade.CRITICAL:
            ctx.dispatch_code = self.d.grade_1
        elif ctx.grade == HazardGrade.URGENT:
            ctx.dispatch_code = self.d.grade_2_with_electric if elec else self.d.grade_2
        else:
            ctx.dispatch_code = self.d.grade_3
        # Report 2 + e-mail are for new incidents and escalations; plain duplicates only get Report 1.
        ctx.report_b_required = (not ctx.is_duplicate) or ctx.is_escalation
        ctx.notify_required = ctx.report_b_required and int(ctx.grade) <= 2


class SchemaTransformStage(Stage):
    name = "Align to enterprise schema"
    description = "Strip survey metadata; apply field names, types and integer status codes"
    group = "Cleaning Stage 2"

    def __init__(self, svc: Services):
        self.svc = svc
        self.mapper = SchemaMapper(svc.schema.incident_layer.fields.raw())
        self.status_codes = svc.schema.incident_layer.status_codes.raw()
        self.close_m = float(svc.params.joint_hazard.close_distance_ft) / 3.280839895

    def run(self, ctx: IncidentContext) -> None:
        r, w = ctx.report, ctx.weather
        ign = [h for h in ctx.electric_hits if h.category == "ignition_source"]
        cnd = [h for h in ctx.electric_hits if h.category == "conduit"]
        # closest energized (or unknown-status) pad-mount/switchgear: drives the 'within 20 ft' joint-hazard metric
        min_elec = min((h.distance_m for h in ign if h.energized is not False), default=None)
        values = dict(
            leak_id=r.leak_id, source_global_id=r.source_global_id, source_object_id=r.source_object_id,
            reported_at=r.reported_at, processed_at=datetime.now(timezone.utc), crew_badge=r.crew_badge,
            lel_pct=r.effective_lel_pct if r.lel_pct is None else r.lel_pct, ppm=r.ppm,
            pipeline_type=r.pipeline_type, pressure_class=r.pressure_class, surface_cover=r.surface_cover,
            wind_speed_mps=w.wind_speed_mps if w else None, wind_from_deg=w.wind_from_deg if w else None,
            air_temp_c=w.air_temp_c if w else None, pressure_hpa=w.pressure_hpa if w else None,
            weather_status=w.status if w else "UNAVAILABLE", plume_length_m=ctx.plume.length_m,
            grade=int(ctx.grade), grade_label=ctx.grade.label, status_code=self.status_codes["Active_Triage"],
            status_text="Active_Triage", dispatch_code=ctx.dispatch_code, valve_ids=",".join(v.valve_id for v in ctx.valves),
            elec_ignition_count=len(ign), elec_conduit_count=len(cnd), elec_min_dist_m=min_elec,
            critical_fac_count=len(ctx.facility_hits), critical_fac_names="; ".join(h.name for h in ctx.facility_hits),
            meters_affected=ctx.meter_count, excavation_tickets=len(ctx.tickets),
            is_duplicate=1 if ctx.is_duplicate else 0, duplicate_of=ctx.duplicate_of,
            reading_count=ctx.parent_reading_count + 1 if ctx.is_duplicate else 1,
            max_lel_pct=max(ctx.parent_max_lel or 0.0, r.effective_lel_pct or 0.0) if ctx.is_duplicate else r.effective_lel_pct,
            quality_flags="; ".join(ctx.flags), report_a_status="PENDING", report_b_status="PENDING" if ctx.report_b_required else "NOT_REQUIRED",
            notify_status="PENDING" if ctx.notify_required else "NOT_REQUIRED",
        )
        ctx.incident_attrs = self.mapper.to_attributes(values)
