"""Ingestion and Cleaning Stage 1: validate, de-duplicate, fetch photos, add weather and 811 context."""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shapely.geometry import Point
from shapely.prepared import prep

from ..clients.store import Query
from ..exceptions import StageRejected, StoreError
from ..geo import LocalFrame, Units
from ..models import IncidentContext
from ..reference import Services
from .base import Stage

log = logging.getLogger(__name__)


class ValidateStage(Stage):
    name = "Validate coordinates & sensor values"
    description = "Coordinates inside franchise area; reject impossible readings; flag suspicious ones"
    group = "Cleaning Stage 1"
    limits = "Franchise area and sensor ranges come from your config. Negative readings are rejected; over-range readings are clamped and flagged, never dropped."

    def __init__(self, svc: Services):
        self.v = svc.params.validation
        poly = svc.franchise_polygon
        self._franchise = prep(poly) if poly is not None else None
        self._require = svc.settings.require_franchise_check

    def run(self, ctx: IncidentContext) -> None:
        r, v = ctx.report, self.v
        if not (math.isfinite(r.lon) and math.isfinite(r.lat)) or not (-180 <= r.lon <= 180 and -90 <= r.lat <= 90):
            raise StageRejected(self.name, f"missing or impossible coordinates ({r.lon}, {r.lat})")
        if r.lel_pct is None and r.ppm is None:
            raise StageRejected(self.name, "no gas reading (neither %LEL nor ppm) - cannot grade")
        # Negative values are impossible. Values ABOVE range are an over-ranged/saturated instrument: that is a very
        # dangerous atmosphere, so clamp, flag, and keep grading (never reject the worst leaks).
        if r.lel_pct is not None and r.lel_pct < float(v.lel_pct_min):
            raise StageRejected(self.name, f"%LEL {r.lel_pct} is below {v.lel_pct_min} (impossible)")
        if r.ppm is not None and r.ppm < float(v.ppm_min):
            raise StageRejected(self.name, f"ppm {r.ppm} is below {v.ppm_min} (impossible)")
        if r.lel_pct is not None and r.lel_pct > float(v.lel_pct_max):
            ctx.flags.append(f"%LEL {r.lel_pct:g} above sensor range - clamped to {v.lel_pct_max}")
            r.lel_pct = float(v.lel_pct_max)
        if r.ppm is not None and r.ppm > float(v.ppm_max):
            ctx.flags.append(f"ppm {r.ppm:g} above sensor range - clamped to {v.ppm_max}")
            r.ppm = float(v.ppm_max)
        if r.temp_c is not None and not (float(v.temperature_c_min) <= r.temp_c <= float(v.temperature_c_max)):
            raise StageRejected(self.name, f"temperature {r.temp_c} C outside sensor limits")
        # franchise area
        if self._franchise is not None:
            if not self._franchise.contains(Point(r.lon, r.lat)):
                raise StageRejected(self.name, "location is outside the licensed franchise area")
        elif self._require:
            raise StageRejected(self.name, "franchise check required but no franchise polygon is loaded")
        else:
            ctx.flags.append("franchise check skipped (disabled)")
        # effective LEL: use the more conservative of the two readings
        ppm_lel = r.ppm / float(v.ppm_per_lel_pct) if r.ppm is not None else None
        cands = [x for x in (r.lel_pct, ppm_lel) if x is not None]
        r.effective_lel_pct = min(max(cands), 100.0)
        if r.lel_pct is None:
            ctx.flags.append("lel derived from ppm")
        if r.lel_pct is not None and ppm_lel is not None:
            hi, lo = max(r.lel_pct, ppm_lel), min(r.lel_pct, ppm_lel)
            if hi > 2 and (lo == 0 or hi / lo > float(v.lel_ppm_mismatch_ratio)):
                ctx.flags.append("lel/ppm readings inconsistent")
        now = datetime.now(timezone.utc)
        if r.reported_at > now + timedelta(minutes=5):
            ctx.flags.append("report timestamp in the future")
        elif now - r.reported_at > timedelta(hours=24):
            ctx.flags.append("report older than 24h (offline sync?)")
        for label, val in (("pipeline_type", r.pipeline_type), ("pressure_class", r.pressure_class), ("surface_cover", r.surface_cover)):
            if val == "unknown":
                ctx.flags.append(f"{label} missing/unrecognized")


class FetchPhotosStage(Stage):
    name = "Fetch inspection photos"
    description = "Download photo attachments from the survey layer for the field ticket"
    external = "ArcGIS survey attachments"
    group = "Cleaning Stage 1"
    limits = "Photo download uses the ArcGIS attachments API - not yet verified against a live org. A failed download only flags the report."

    def __init__(self, svc: Services, work_dir: Path):
        self.svc, self.work_dir = svc, work_dir

    def run(self, ctx: IncidentContext) -> None:
        r = ctx.report
        if r.source_object_id is None:
            return
        dest = self.work_dir / re.sub(r"[^A-Za-z0-9_.-]", "_", r.leak_id)
        try:
            for att in self.svc.survey.list_attachments(r.source_object_id):
                name = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{att['id']}_{att.get('name', 'photo')}")
                r.photos.append(str(self.svc.survey.download_attachment(r.source_object_id, att["id"], dest, name)))
        except StoreError as exc:
            ctx.flags.append("photos unavailable")
            log.warning("Photo download failed for %s: %s", r.leak_id, exc)


class DedupeStage(Stage):
    name = "Duplicate detection"
    description = "Another leak within 50 ft in the past 12 h => flag as duplicate reading"
    external = "ArcGIS incident layer"
    group = "Cleaning Stage 1"
    limits = "50 ft / 12 h come from risk_parameters.yaml. A worse duplicate is an escalation (re-analysed and re-notified), never silently merged."

    def __init__(self, svc: Services):
        self.svc = svc
        self.radius_m = Units.ft_to_m(float(svc.params.dedupe.radius_ft))
        self.window = timedelta(hours=float(svc.params.dedupe.window_hours))
        self.lel_delta = float(svc.params.dedupe.get("escalation_lel_delta_pct", 10.0))

    def run(self, ctx: IncidentContext) -> None:
        r, sch = ctx.report, self.svc.schema
        f = sch.incident_field
        q = Query(equals={f("is_duplicate"): 0}, time_field=f("reported_at"), time_from=r.reported_at - self.window,
                  time_to=r.reported_at, geometry=LocalFrame(r.lon, r.lat).buffer(r.geometry, self.radius_m), order_by=f"{f('reported_at')} ASC")
        parents = [p for p in self.svc.incidents.query(q) if p.attributes.get(f("leak_id")) != r.leak_id]
        if not parents:
            return
        p = parents[0]
        ctx.duplicate_of = p.attributes[f("leak_id")]
        ctx.parent_grade = p.attributes.get(f("grade"))
        ctx.parent_max_lel = p.attributes.get(f("max_lel_pct"))
        ctx.parent_reading_count = int(p.attributes.get(f("reading_count")) or 1)
        ctx.escalation_lel_delta = self.lel_delta
        ctx.flags.append(f"duplicate of {ctx.duplicate_of}")
        ctx.parent_object_id = p.object_id


class WeatherStage(Stage):
    name = "Ingest live weather"
    description = "Wind speed/direction, temperature and pressure for the leak location"
    external = "NWS weather API"
    group = "Cleaning Stage 1"
    limits = "Nearest NWS station can be far away or stale; stale or missing wind produces a conservative omnidirectional envelope. The live API has not been called from the dev environment."

    def __init__(self, svc: Services):
        self.svc = svc

    def run(self, ctx: IncidentContext) -> None:
        r = ctx.report
        obs = self.svc.weather.get_observation(r.lat, r.lon)
        ctx.weather = obs
        if obs is None:
            ctx.flags.append("weather unavailable")
        elif obs.status != "OK":
            ctx.flags.append(f"weather {obs.status.lower()}")


class ExcavationStage(Stage):
    name = "Check 811 excavation tickets"
    description = "Active third-party dig tickets within 500 ft"
    external = "811 ticket service"
    group = "Cleaning Stage 1"
    limits = "There is no national 811 API - the REST client is a placeholder contract you adapt to your provider. Informational unless you enable escalation."

    def __init__(self, svc: Services):
        self.svc = svc

    def run(self, ctx: IncidentContext) -> None:
        res = self.svc.tickets.find_tickets(ctx.report.lat, ctx.report.lon)
        if res is None:
            ctx.flags.append("811 lookup failed")
        else:
            ctx.tickets = res
