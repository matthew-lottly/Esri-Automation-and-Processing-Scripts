"""Generate the SYNTHETIC sample data used by `python -m gas_electric_response demo` and the tests.

Everything is fictional and placed on an arbitrary point; nothing here represents real infrastructure.
There are deliberately NO images: the demo's maps and photos are neutral placeholders.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from shapely.geometry import LineString, Point, box, mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gas_electric_response.geo import LocalFrame  # noqa: E402


class SampleDataGenerator:
    OUT = Path(__file__).resolve().parents[1] / "sample_data"
    LON0, LAT0 = -96.8000, 32.7800            # arbitrary demo origin

    def __init__(self):
        self.frame = LocalFrame(self.LON0, self.LAT0)

    # ---- helpers -----------------------------------------------------------------------------
    def pt(self, x: float, y: float) -> tuple[float, float]:
        """Metres east/north of the demo origin -> (lon, lat)."""
        g = self.frame.from_local(Point(x, y))
        return g.x, g.y

    @staticmethod
    def feat(geom, **props) -> dict:
        return {"type": "Feature", "geometry": mapping(geom), "properties": props}

    def dump(self, name: str, obj) -> None:
        (self.OUT / name).write_text(json.dumps(obj, indent=1))

    @staticmethod
    def collection(feats: list[dict]) -> dict:
        return {"type": "FeatureCollection", "features": feats}

    # ---- layers ------------------------------------------------------------------------------
    def franchise(self) -> None:
        self.dump("franchise.geojson", self.collection([self.feat(box(self.LON0 - 0.06, self.LAT0 - 0.05, self.LON0 + 0.06, self.LAT0 + 0.05), NAME="Demo franchise")]))

    def electric(self) -> None:
        pt, f = self.pt, self.feat
        self.dump("electric_assets.geojson", self.collection([
            f(Point(*pt(4, 2)), ASSETID="PM-1001", ASSETTYPE="PadmountTransformer", ENERGIZED="Y"),
            f(Point(*pt(28, 15)), ASSETID="SW-2002", ASSETTYPE="PadmountSwitchgear", ENERGIZED="Y"),
            f(LineString([pt(-5, -4), pt(40, -4)]), ASSETID="UG-3003", ASSETTYPE="UndergroundConduit", ENERGIZED="Y"),
            f(LineString([pt(-100, 80), pt(100, 80)]), ASSETID="OH-4004", ASSETTYPE="OverheadConductor", ENERGIZED="Y"),
            f(Point(*pt(900, 900)), ASSETID="PM-9009", ASSETTYPE="PadmountTransformer", ENERGIZED="Y")]))

    def valves(self) -> None:
        feats = [self.feat(Point(*self.pt(x, y)), VALVEID=vid, PRESSURE_SYSTEM="LP") for vid, x, y in
                 [("GV-101", -120, 30), ("GV-102", 150, -40), ("GV-103", 60, 200), ("GV-104", -300, -300), ("GV-105", 500, 500)]]
        self.dump("gas_valves.geojson", self.collection(feats))

    def facilities(self) -> None:
        self.dump("critical_facilities.geojson", self.collection([
            self.feat(Point(*self.pt(45, 8)), NAME="Demo Elementary School", FACILITY_TYPE="school"),
            self.feat(Point(*self.pt(700, 700)), NAME="Demo Hospital", FACILITY_TYPE="hospital")]))

    def meters(self) -> None:
        pts = [(10, 12), (20, 14), (35, 10), (44, 20), (300, 300), (-60, 60)]
        self.dump("customer_meters.geojson", self.collection([self.feat(Point(*self.pt(x, y)), METERID=f"M-{i}") for i, (x, y) in enumerate(pts, 1)]))

    # ---- survey submissions (minutes_ago becomes CreationDate when loaded) ------------------------------------
    def survey(self) -> None:
        base = dict(Pipeline_Type="cast_iron", Pressure_Class="medium_distribution", Surface_Cover="asphalt")
        pt = self.pt
        rows = [
            ("A", 180, pt(0, 0), {"Leak_ID": "LK-DEMO-A", "Gas_LEL_Pct": 35, "Gas_PPM": 17500, "Crew_Badge": "T-4471",
                                  "Notes": "Strong odor at curb line next to pad-mount; reading taken at bar hole.", "Instrument_Temp_C": 21, **base}),
            ("B-duplicate", 30, pt(-10, 6), {"Leak_ID": "LK-DEMO-B", "Gas_LEL_Pct": 12, "Gas_PPM": 6000, "Crew_Badge": "T-5120",
                                             "Notes": "Second crew re-checked the same area.", **base}),
            ("C-grade3", 20, pt(3000, 2500), {"Leak_ID": "LK-DEMO-C", "Gas_LEL_Pct": 0.4, "Gas_PPM": 200, "Crew_Badge": "T-3301", "Notes": "Trace reading in open field.",
                                              "Pipeline_Type": "polyethylene", "Pressure_Class": "low_distribution", "Surface_Cover": "grass"}),
            ("D-outside", 10, (self.LON0 + 0.5, self.LAT0), {"Leak_ID": "LK-DEMO-D", "Gas_LEL_Pct": 5, "Gas_PPM": 2500, "Crew_Badge": "T-0001",
                                                              "Notes": "Coordinates outside franchise (demonstrates validation).", **base}),
        ]
        self.dump("survey_submissions.json", [{"name": n, "minutes_ago": m, "xy": xy, "attributes": a, "photos": []} for n, m, xy, a in rows])
        self.dump("weather.json", {"wind_speed_mps": 4.0, "wind_from_deg": 270.0, "air_temp_c": 18.0, "pressure_hpa": 1013.0, "station": "SAMPLE"})

    def generate(self) -> Path:
        self.OUT.mkdir(exist_ok=True)
        for step in (self.franchise, self.electric, self.valves, self.facilities, self.meters, self.survey):
            step()
        return self.OUT


if __name__ == "__main__":
    print("sample data written to", SampleDataGenerator().generate())
