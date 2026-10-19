import math

from shapely.geometry import Point

from gas_electric_response.geo import to_local
from gas_electric_response.models import WeatherObservation
from gas_electric_response.plume import PlumeModel
from tests.conftest import make_report


def centroid_xy(res, r):
    c = to_local(res.polygon, r.lon, r.lat).centroid
    return c.x, c.y


def test_wind_from_west_pushes_plume_east(params):
    r = make_report()
    res = PlumeModel(params).build(r, WeatherObservation(5.0, 270.0, 20, 1013))
    x, y = centroid_xy(res, r)
    assert x > 5 and abs(y) < 2, (x, y)


def test_wind_from_north_pushes_plume_south(params):
    r = make_report()
    res = PlumeModel(params).build(r, WeatherObservation(5.0, 0.0, 20, 1013))
    x, y = centroid_xy(res, r)
    assert y < -5 and abs(x) < 2


def test_wind_from_southwest_pushes_plume_northeast(params):
    r = make_report()
    res = PlumeModel(params).build(r, WeatherObservation(5.0, 225.0, 20, 1013))
    x, y = centroid_xy(res, r)
    assert x > 3 and y > 3 and math.isclose(x, y, rel_tol=0.1)


def test_calm_wind_is_circle(params):
    r = make_report()
    res = PlumeModel(params).build(r, WeatherObservation(0.2, 90.0, 20, 1013))
    x, y = centroid_xy(res, r)
    assert abs(x) < 0.5 and abs(y) < 0.5 and res.model.endswith("circle")


def test_missing_weather_is_conservative_circle_and_noted(params):
    r = make_report()
    m = PlumeModel(params)
    with_wind = m.build(r, WeatherObservation(0.0, None, 20, 1013))
    none = m.build(r, None)
    assert none.model.endswith("circle") and any("no usable wind" in n for n in none.assumptions)
    assert none.length_m > with_wind.length_m * 0.99


def test_sealed_surface_and_pressure_and_lel_widen_plume(params):
    m, w = PlumeModel(params), WeatherObservation(3.0, 270.0, 20, 1013)
    base = m.build(make_report(surface_cover="soil", pressure_class="low_distribution", effective_lel_pct=1.0), w)
    asphalt = m.build(make_report(surface_cover="asphalt", pressure_class="low_distribution", effective_lel_pct=1.0), w)
    high = m.build(make_report(surface_cover="soil", pressure_class="high_transmission", effective_lel_pct=1.0), w)
    hot = m.build(make_report(surface_cover="soil", pressure_class="low_distribution", effective_lel_pct=80.0), w)
    assert asphalt.polygon.area > base.polygon.area
    assert high.polygon.area > base.polygon.area
    assert hot.polygon.area > base.polygon.area


def test_plume_contains_leak_point_and_is_capped(params):
    r = make_report(pressure_class="high_transmission", effective_lel_pct=100.0)
    res = PlumeModel(params).build(r, WeatherObservation(20.0, 90.0, 20, 1013))
    assert res.polygon.contains(Point(r.lon, r.lat))
    assert res.length_m <= float(params.plume.max_length_m) + float(params.plume.max_radius_m)
