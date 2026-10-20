"""Geometry toolkit (all classes; no loose functions).

* `Units`           - unit constants
* `LocalFrame`      - a metric (azimuthal-equidistant) frame centred on a lon/lat; project, buffer, measure
* `EsriGeometry`    - encode/decode Esri geometry JSON for points, lines and polygons
* `GeoMath`         - spherical helpers
"""
from __future__ import annotations

import math

from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString, MultiPoint, Point, Polygon, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform


class Units:
    FT_PER_M = 3.280839895
    M_PER_FT = 1 / FT_PER_M
    SQM_PER_ACRE = 4046.8564224

    @classmethod
    def ft_to_m(cls, ft: float) -> float:
        return ft * cls.M_PER_FT

    @classmethod
    def m_to_ft(cls, m: float) -> float:
        return m * cls.FT_PER_M


class LocalFrame:
    """Metric working frame centred on (lon, lat). Everything is WGS84 in/out; maths happens in metres."""

    def __init__(self, lon: float, lat: float):
        self.lon, self.lat = lon, lat
        crs = f"+proj=aeqd +lat_0={lat:.8f} +lon_0={lon:.8f} +datum=WGS84 +units=m +no_defs"
        self._fwd = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        self._inv = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)

    @classmethod
    def around(cls, geom: BaseGeometry) -> "LocalFrame":
        """Frame centred on any geometry's representative point (points, lines or polygons)."""
        p = geom.representative_point()
        return cls(p.x, p.y)

    def to_local(self, geom: BaseGeometry) -> BaseGeometry:
        return transform(self._fwd.transform, geom)

    def from_local(self, geom: BaseGeometry) -> BaseGeometry:
        return transform(self._inv.transform, geom)

    def xy(self, lon: float, lat: float) -> tuple[float, float]:
        return self._fwd.transform(lon, lat)

    def buffer(self, geom: BaseGeometry, radius_m: float) -> BaseGeometry:
        """Buffer ANY geometry by a distance in metres; returns WGS84."""
        return self.from_local(self.to_local(geom).buffer(radius_m, 32))

    def circle(self, radius_m: float) -> Polygon:
        return self.from_local(Point(0, 0).buffer(radius_m, 64))

    def distance(self, a: BaseGeometry, b: BaseGeometry) -> float:
        """Shortest distance in metres between two WGS84 geometries (0 if they touch/overlap)."""
        return float(self.to_local(a).distance(self.to_local(b)))

    def length(self, geom: BaseGeometry) -> float:
        return float(self.to_local(geom).length)

    def area(self, geom: BaseGeometry) -> float:
        return float(self.to_local(geom).area)

    @classmethod
    def metres_between(cls, a: BaseGeometry, b: BaseGeometry) -> float:
        return cls.around(a).distance(a, b)


class GeoMath:
    EARTH_R_M = 6371008.8

    @classmethod
    def haversine_m(cls, lon1: float, lat1: float, lon2: float, lat2: float) -> float:
        p1, p2 = math.radians(lat1), math.radians(lat2)
        a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
        return 2 * cls.EARTH_R_M * math.asin(math.sqrt(a))


class EsriGeometry:
    """Esri geometry JSON <-> shapely, for points, polylines and polygons."""

    @staticmethod
    def encode(geom: BaseGeometry, wkid: int = 4326) -> dict:
        gm, sr = mapping(geom), {"wkid": wkid}
        t, c = gm["type"], gm["coordinates"]
        if t == "Point":
            return {"x": c[0], "y": c[1], "spatialReference": sr}
        if t == "MultiPoint":
            return {"points": [list(p) for p in c], "spatialReference": sr}
        if t == "LineString":
            return {"paths": [list(map(list, c))], "spatialReference": sr}
        if t == "MultiLineString":
            return {"paths": [list(map(list, p)) for p in c], "spatialReference": sr}
        if t == "Polygon":
            return {"rings": [list(map(list, r)) for r in c], "spatialReference": sr}
        if t == "MultiPolygon":
            return {"rings": [list(map(list, r)) for p in c for r in p], "spatialReference": sr}
        raise ValueError(f"Unsupported geometry type for Esri JSON: {t}")

    @staticmethod
    def decode(js: dict) -> BaseGeometry:
        if "x" in js and js["x"] is not None:
            return Point(js["x"], js["y"])
        if "points" in js:
            return MultiPoint(js["points"])
        if "paths" in js:
            lines = [LineString(p) for p in js["paths"]]
            return lines[0] if len(lines) == 1 else MultiLineString(lines)
        if "rings" in js:
            # Even-odd fill: orientation-independent; correct for holes and islands inside holes.
            shape = Polygon()
            for ring in js["rings"]:
                shape = shape.symmetric_difference(Polygon(ring).buffer(0))
            return shape
        raise ValueError("Unrecognized Esri geometry JSON")
