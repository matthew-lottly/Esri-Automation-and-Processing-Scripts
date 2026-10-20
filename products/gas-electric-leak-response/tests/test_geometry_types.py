"""The pipeline accepts POINT, LINE and POLYGON submissions and reference layers (see docs/adapting-the-pipeline.md)."""
import copy

import pytest
from shapely.geometry import LineString, Point, Polygon

from gas_electric_response.clients.store import InMemoryFeatureStore, Query
from gas_electric_response.geo import EsriGeometry, LocalFrame
from gas_electric_response.models import AssetHit, FeatureRecord
from gas_electric_response.stages.analysis import OverlayStage


def submission(runtime, geometry, leak_id="LK-GEOM", gid="{77777777-0000-4000-8000-000000000007}"):
    base = [r for r in runtime.services.survey.query(Query()) if r.attributes["Leak_ID"] == "LK-DEMO-A"][0]
    rec = copy.deepcopy(base)
    rec.attributes.update({"Leak_ID": leak_id, "GlobalID": gid})
    rec.geometry = geometry
    return rec


def frame():
    return LocalFrame(-96.8, 32.78)


def test_line_submission_gets_a_plume_around_the_whole_line(runtime):
    f = frame()
    line = f.from_local(LineString([(0, 0), (30, 0), (60, 10)]))
    ctx = runtime.pipeline.process_record(submission(runtime, line))
    assert ctx.report.geometry_type == "LineString" and not ctx.rejected
    assert ctx.plume.polygon.contains(line)                       # the entire inspected segment is inside the envelope
    assert ctx.plume.polygon.area > line.buffer(1e-5).area
    assert ctx.grade is not None and ctx.report_a_status == "OK"


def test_polygon_submission_plume_covers_the_polygon(runtime):
    f = frame()
    poly = f.from_local(Polygon([(0, 0), (40, 0), (40, 30), (0, 30)]))
    ctx = runtime.pipeline.process_record(submission(runtime, poly, "LK-POLY", "{88888888-0000-4000-8000-000000000008}"))
    assert ctx.report.geometry_type == "Polygon" and ctx.plume.polygon.contains(poly)
    assert ctx.report.lon == pytest.approx(poly.representative_point().x)   # anchor = representative point


def test_reference_line_reports_length_inside_plume(runtime):
    ctx = runtime.pipeline.process_record(submission(runtime, Point(-96.8, 32.78)))
    conduit = [h for h in ctx.electric_hits if h.asset_id == "UG-3003"][0]
    assert conduit.geometry.geom_type == "LineString" and conduit.inside_length_m and conduit.inside_length_m > 20
    assert conduit.inside_area_m2 is None


def test_custom_overlay_stage_for_a_polygon_layer(runtime):
    """Recipe from the docs: add a polygon reference layer ('parcels') with its own OverlayStage subclass."""
    f = frame()
    parcels = InMemoryFeatureStore("parcels", [FeatureRecord({"APN": "P-1"}, f.from_local(Polygon([(-20, -20), (20, -20), (20, 20), (-20, 20)]))),
                                               FeatureRecord({"APN": "P-2"}, f.from_local(Polygon([(2000, 2000), (2100, 2000), (2100, 2100), (2000, 2100)])))])
    runtime.services.extra_layers["parcels"] = parcels

    class ParcelStage(OverlayStage):
        name, description, group = "Find parcels", "Parcels inside the plume", "Spatial & Risk Analysis"
        store_name = "parcels"

        @property
        def store(self):
            return self.svc.extra_layers["parcels"]

        def search_geometry(self, ctx, frame): return ctx.plume.polygon

        def make_hit(self, ctx, rec, frame, search):
            length, area = self.measure(frame, rec.geometry, search)
            return AssetHit("parcel", "parcel", rec.attributes["APN"], "parcel", frame.distance(ctx.report.geometry, rec.geometry), inside_area_m2=area)

        def collect(self, ctx, hits): ctx.extra_hits = hits

    ctx = runtime.pipeline.process_record(submission(runtime, Point(-96.8, 32.78)))
    stage = ParcelStage(runtime.services)
    stage.run(ctx)
    assert [h.asset_id for h in ctx.extra_hits] == ["P-1"] and ctx.extra_hits[0].inside_area_m2 > 500


@pytest.mark.parametrize("geom", [Point(1, 2), LineString([(0, 0), (1, 1), (2, 0)]), Polygon([(0, 0), (4, 0), (4, 4), (0, 4)]),
                                  Polygon([(0, 0), (10, 0), (10, 10), (0, 10)], [[(2, 2), (4, 2), (4, 4), (2, 4)]])])
def test_esri_json_round_trip(geom):
    back = EsriGeometry.decode(EsriGeometry.encode(geom))
    assert back.equals(geom) or back.symmetric_difference(geom).area < 1e-9
