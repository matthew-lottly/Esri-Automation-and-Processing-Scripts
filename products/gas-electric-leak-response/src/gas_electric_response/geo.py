"""Geometry helpers: local metric projection, distance, buffers, Esri JSON conversion."""
from __future__ import annotations

import math
from functools import lru_cache

from pyproj import Transformer
from shapely.geometry import Point, Polygon, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

FT_PER_M = 3.280839895


def local_transformers(lon: float, lat: float) -> tuple[Transformer, Transformer]:
    """(wgs84->local metres, local->wgs84) using an azimuthal-equidistant projection centred on the point."""
    crs = f"+proj=aeqd +lat_0={lat:.8f} +lon_0={lon:.8f} +datum=WGS84 +units=m +no_defs"
    return (Transformer.from_crs("EPSG:4326", crs, always_xy=True),
            Transformer.from_crs(crs, "EPSG:4326", always_xy=True))


def to_local(geom: BaseGeometry, lon: float, lat: float) -> BaseGeometry:
    fwd, _ = local_transformers(lon, lat)
    return transform(fwd.transform, geom)


def from_local(geom: BaseGeometry, lon: float, lat: float) -> BaseGeometry:
    _, inv = local_transformers(lon, lat)
    return transform(inv.transform, geom)


def buffer_m(lon: float, lat: float, radius_m: float) -> Polygon:
    """Geodesically-sensible circular buffer (as WGS84 polygon) around a point."""
    return from_local(Point(0, 0).buffer(radius_m, 64), lon, lat)


def distance_m(lon: float, lat: float, geom: BaseGeometry) -> float:
    """Shortest distance (metres) from a WGS84 point to a WGS84 geometry."""
    return float(Point(0, 0).distance(to_local(geom, lon, lat)))


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def to_esri_json(geom: BaseGeometry, wkid: int = 4326) -> dict:
    """Shapely (WGS84) -> Esri geometry JSON for points/polygons."""
    gm = mapping(geom)
    sr = {"wkid": wkid}
    if gm["type"] == "Point":
        return {"x": gm["coordinates"][0], "y": gm["coordinates"][1], "spatialReference": sr}
    if gm["type"] == "Polygon":
        return {"rings": [list(map(list, r)) for r in gm["coordinates"]], "spatialReference": sr}
    if gm["type"] == "MultiPolygon":
        return {"rings": [list(map(list, r)) for p in gm["coordinates"] for r in p], "spatialReference": sr}
    raise ValueError(f"Unsupported geometry type for Esri JSON: {gm['type']}")


def from_esri_json(js: dict) -> BaseGeometry:
    """Esri geometry JSON (points / polygons / polylines) -> shapely."""
    from shapely.geometry import LineString, MultiLineString, MultiPoint
    from shapely.ops import unary_union

    if "x" in js and js["x"] is not None:
        return Point(js["x"], js["y"])
    if "points" in js:
        return MultiPoint(js["points"])
    if "paths" in js:
        lines = [LineString(p) for p in js["paths"]]
        return lines[0] if len(lines) == 1 else MultiLineString(lines)
    if "rings" in js:
        # Even-odd fill: a point is inside if it is within an odd number of rings. Independent of ring
        # orientation and correct for holes and islands-inside-holes.
        shape = Polygon()
        for ring in js["rings"]:
            shape = shape.symmetric_difference(Polygon(ring).buffer(0))
        return shape
    raise ValueError("Unrecognized Esri geometry JSON")
