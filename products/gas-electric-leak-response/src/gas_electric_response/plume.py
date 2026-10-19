"""Screening-level directional gas migration envelope.

THIS IS A HEURISTIC, NOT A DISPERSION MODEL. It stretches a circle downwind and widens it for higher
pressure, higher %LEL and sealed surfaces, using multipliers in config/risk_parameters.yaml. The
multipliers are placeholders to be reviewed and calibrated by the utility's gas engineering/safety group.
Its purpose is to make the intersect analysis directional instead of a static circle - nothing more.
"""
from __future__ import annotations

import math

from shapely.affinity import rotate, scale, translate
from shapely.geometry import Point
from shapely.ops import unary_union

from .config import RiskParameters
from .geo import from_local
from .models import LeakReport, PlumeResult, WeatherObservation


class PlumeModel:
    name = "heuristic-teardrop-v1"

    def __init__(self, params: RiskParameters):
        self.p = params.plume

    def _radius(self, report: LeakReport) -> tuple[float, list[str]]:
        p, notes = self.p, []
        mult = lambda table, key: table.get(key, table.get("unknown", 1.0))
        r = float(p.base_radius_m)
        r *= mult(p.pressure_class_multiplier, report.pressure_class)
        r *= mult(p.material_multiplier, report.pipeline_type)
        r *= mult(p.surface_cover_multiplier, report.surface_cover)
        lel = report.effective_lel_pct if report.effective_lel_pct is not None else (report.lel_pct or 0.0)
        r *= 1.0 + float(p.lel_gain) * min(max(lel, 0.0), 100.0) / 100.0
        for label, val in (("pressure class", report.pressure_class), ("pipeline type", report.pipeline_type),
                           ("surface cover", report.surface_cover)):
            if val == "unknown":
                notes.append(f"{label} unknown - conservative multiplier used")
        return min(r, float(p.max_radius_m)), notes

    def build(self, report: LeakReport, weather: WeatherObservation | None) -> PlumeResult:
        p = self.p
        r, notes = self._radius(report)
        fresh = weather is not None and weather.status in ("OK", "PARTIAL") and weather.status != "STALE"
        calm = (fresh and weather.wind_speed_mps is not None and weather.wind_speed_mps < float(p.calm_wind_mps))
        usable = fresh and weather.wind_speed_mps is not None and (weather.wind_from_deg is not None or calm)
        if weather is not None and weather.status == "STALE":
            notes.append("weather observation is stale - not used for plume direction")
        if not usable and not calm:
            r = min(r * float(p.missing_weather_radius_factor), float(p.max_radius_m))
            notes.append("no usable wind data - conservative omnidirectional envelope")
        if calm or not usable:
            if calm and weather.wind_from_deg is None:
                notes.append("calm wind - omnidirectional envelope")
            local = Point(0, 0).buffer(r, 64)
            length, width, model = r * 2, r * 2, self.name + "/circle"
            wind_from = None
        else:
            u = min(float(weather.wind_speed_mps), float(p.wind_cap_mps))
            down = min(r * (1 + float(p.wind_stretch_per_mps) * u), float(p.max_length_m))
            up = max(r * float(p.upwind_fraction), 1.0)
            half = r * max(float(p.min_crosswind_factor), 1 - float(p.crosswind_narrowing) * u / float(p.wind_cap_mps))
            # Ellipse spanning origin -> `down` metres along +y, then rotated to the downwind bearing.
            ell = scale(Point(0, 0).buffer(1.0, 64), half, down / 2)
            ell = translate(ell, 0, down / 2)
            circ = Point(0, 0).buffer(up, 64)
            wind_from = float(weather.wind_from_deg) % 360
            downwind_bearing = (wind_from + 180.0) % 360          # met convention: wind FROM `wind_from`
            # shapely rotates counter-clockwise from +x; a compass bearing is clockwise from +y (north).
            shape = unary_union([ell, circ])
            local = rotate(shape, -downwind_bearing, origin=(0, 0))
            length, width, model = up + down, 2 * half, self.name
            notes.append(f"downwind bearing {downwind_bearing:.0f} deg (wind from {wind_from:.0f} deg at {weather.wind_speed_mps:.1f} m/s)")
        poly = from_local(local, report.lon, report.lat)
        return PlumeResult(poly, round(length, 1), round(width, 1), model, wind_from, notes)
