"""Screening-level directional gas migration envelope.

THIS IS A HEURISTIC, NOT A DISPERSION MODEL. It stretches the incident geometry downwind and widens it for higher
pressure, higher %LEL and sealed surfaces, using multipliers in config/risk_parameters.yaml. The multipliers are
placeholders to be reviewed and calibrated by the utility's gas engineering/safety group. Its purpose is to make the
intersect analysis directional instead of a static circle - nothing more.

Works for a POINT leak (teardrop) and for LINE / POLYGON submissions (the feature is buffered, then swept downwind).
"""
from __future__ import annotations

from shapely.affinity import rotate, scale, translate
from shapely.geometry import Point
from shapely.ops import unary_union

from .config import RiskParameters
from .geo import LocalFrame
from .models import LeakReport, PlumeResult, WeatherObservation


class PlumeModel:
    name = "heuristic-teardrop-v1"

    def __init__(self, params: RiskParameters):
        self.p = params.plume

    # ---- radius -----------------------------------------------------------------
    def radius(self, report: LeakReport) -> tuple[float, list[str]]:
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

    # ---- wind handling ------------------------------------------------------------
    @staticmethod
    def wind_state(weather: WeatherObservation | None, calm_mps: float) -> str:
        """'directional' | 'calm' | 'stale' | 'missing'"""
        if weather is None or weather.wind_speed_mps is None:
            return "missing"
        if weather.status == "STALE":
            return "stale"
        if weather.wind_speed_mps < calm_mps:
            return "calm"
        return "directional" if weather.wind_from_deg is not None else "missing"

    # ---- build --------------------------------------------------------------------
    def build(self, report: LeakReport, weather: WeatherObservation | None) -> PlumeResult:
        p = self.p
        r, notes = self.radius(report)
        frame = LocalFrame(report.lon, report.lat)
        geom = frame.to_local(report.geometry)                       # Point(0,0) for a point leak
        state = self.wind_state(weather, float(p.calm_wind_mps))
        wind_from = None
        if state in ("missing", "stale"):
            r = min(r * float(p.missing_weather_radius_factor), float(p.max_radius_m))
            notes.append("weather observation is stale - not used for plume direction" if state == "stale"
                         else "no usable wind data - conservative omnidirectional envelope")
        if state != "directional":
            if state == "calm":
                notes.append("calm wind - omnidirectional envelope")
            local = geom.buffer(r, 64)
            model = self.name + "/circle"
        else:
            u = min(float(weather.wind_speed_mps), float(p.wind_cap_mps))
            down = min(r * (1 + float(p.wind_stretch_per_mps) * u), float(p.max_length_m))
            up = max(r * float(p.upwind_fraction), 1.0)
            half = r * max(float(p.min_crosswind_factor), 1 - float(p.crosswind_narrowing) * u / float(p.wind_cap_mps))
            wind_from = float(weather.wind_from_deg) % 360
            bearing = (wind_from + 180.0) % 360                      # met convention: wind FROM `wind_from`
            if report.geometry.geom_type == "Point":
                ell = translate(scale(Point(0, 0).buffer(1.0, 64), half, down / 2), 0, down / 2)   # +y = north
                shape = unary_union([ell, Point(0, 0).buffer(up, 64)])
                local = rotate(shape, -bearing, origin=(0, 0))          # compass bearing is clockwise from north
            else:
                import math
                dx, dy = math.sin(math.radians(bearing)) * down, math.cos(math.radians(bearing)) * down
                local = unary_union([geom.buffer(up, 32), translate(geom, dx, dy).buffer(half, 32)]).convex_hull
                local = unary_union([local, geom.buffer(up, 32)])
            model = self.name
            notes.append(f"downwind bearing {bearing:.0f} deg (wind from {wind_from:.0f} deg at {weather.wind_speed_mps:.1f} m/s)")
        minx, miny, maxx, maxy = local.bounds
        length = max(maxx - minx, maxy - miny)
        width = min(maxx - minx, maxy - miny)
        return PlumeResult(frame.from_local(local), round(length, 1), round(width, 1), model, wind_from, notes)
