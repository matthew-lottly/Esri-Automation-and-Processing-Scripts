"""TileBasemap: Web Mercator tile maths, stitching and the offline fallback (no network used)."""
import io

import pytest
from PIL import Image

from gas_electric_response.reports.mapping import WORLD, TileBasemap


class FakeSession:
    def __init__(self, fail=False):
        self.headers, self.calls, self.fail = {}, [], fail

    def get(self, url, timeout):
        self.calls.append(url)
        if self.fail:
            raise OSError("offline")
        buf = io.BytesIO()
        Image.new("RGB", (256, 256), (200, 210, 220)).save(buf, format="PNG")
        resp = type("R", (), {"content": buf.getvalue(), "raise_for_status": lambda self: None})()
        return resp


def test_zoom_for_scales_with_extent():
    assert TileBasemap.zoom_for(400, 1400) > TileBasemap.zoom_for(4000, 1400) > TileBasemap.zoom_for(400000, 1400)
    assert 0 <= TileBasemap.zoom_for(1e9, 1400) <= TileBasemap.zoom_for(1, 1400) <= 19


def test_mosaic_covers_requested_extent_and_limits_tiles(tmp_path):
    s = FakeSession()
    bm = TileBasemap("osm", cache_dir=tmp_path, session=s)
    minx, miny, maxx, maxy = -10776000.0, 3856000.0, -10775400.0, 3856500.0
    img, (l, r, b, t) = bm.mosaic(minx, miny, maxx, maxy, 1400)
    assert l <= minx and r >= maxx and b <= miny and t >= maxy
    assert len(s.calls) <= TileBasemap.MAX_TILES and "openstreetmap" in s.calls[0]
    assert img.size[0] % 256 == 0 and abs(r - l) / (img.size[0] / 256) == pytest.approx(abs(t - b) / (img.size[1] / 256))
    n_calls = len(s.calls)
    bm.mosaic(minx, miny, maxx, maxy, 1400)
    assert len(s.calls) == n_calls                         # second request is served from the on-disk cache


def test_esri_url_order_and_custom_template():
    assert "{z}/{y}/{x}" in TileBasemap.PROVIDERS["esri_street"][0]
    bm = TileBasemap("https://tiles.example.com/{z}/{x}/{y}.png", session=FakeSession())
    assert bm.attribution == "" and bm.mosaic(-1000, -1000, 1000, 1000, 800) is not None
    with pytest.raises(ValueError, match="Unknown basemap"):
        TileBasemap("nonsense")


def test_offline_returns_none_instead_of_raising():
    assert TileBasemap("osm", session=FakeSession(fail=True)).mosaic(0, 0, 500, 500, 800) is None


def test_whole_world_zoom_zero_extent():
    img, (l, r, b, t) = TileBasemap("osm", session=FakeSession()).mosaic(-WORLD, -WORLD, WORLD, WORLD, 256)
    assert (l, r, b, t) == pytest.approx((-WORLD, WORLD, -WORLD, WORLD))


def test_map_block_falls_back_gracefully_when_basemap_fails(runtime, tmp_path, monkeypatch):
    from gas_electric_response.clients.store import Query
    from gas_electric_response.reports.mapping import ReportMap
    from gas_electric_response.reports.theme import Theme
    rec = [r for r in runtime.services.survey.query(Query()) if r.attributes["Leak_ID"] == "LK-DEMO-A"][0]
    ctx = runtime.pipeline.process_record(rec)
    monkeypatch.setattr(TileBasemap, "mosaic", lambda self, *a, **k: None)
    png = ReportMap(runtime.services, Theme.load(None), tmp_path).render(ctx, {"basemap": "osm", "zoom_m": 120}, 7.0, 3.5)
    assert png.startswith(b"\x89PNG")
