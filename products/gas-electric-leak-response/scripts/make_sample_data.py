"""Generate the SYNTHETIC sample data used by `python -m gas_electric_response demo` and the tests.

Everything is fictional and placed on an arbitrary point; nothing here represents real infrastructure.
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from shapely.geometry import LineString, Point, box, mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gas_electric_response.geo import from_local  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "sample_data"
LON0, LAT0 = -96.8000, 32.7800            # arbitrary demo origin
OUT.mkdir(exist_ok=True)
(OUT / "photos").mkdir(exist_ok=True)


def pt(x, y, lon=LON0, lat=LAT0):
    g = from_local(Point(x, y), lon, lat)
    return g.x, g.y


def feat(geom, **props):
    return {"type": "Feature", "geometry": mapping(geom), "properties": props}


def fc(feats):
    return {"type": "FeatureCollection", "features": feats}


def dump(name, obj):
    (OUT / name).write_text(json.dumps(obj, indent=1))


# Franchise area: ~10 km box around the origin
dump("franchise.geojson", fc([feat(box(LON0 - 0.06, LAT0 - 0.05, LON0 + 0.06, LAT0 + 0.05), NAME="Demo franchise")]))

elec = [feat(Point(*pt(4, 2)), ASSETID="PM-1001", ASSETTYPE="PadmountTransformer", ENERGIZED="Y"),
        feat(Point(*pt(28, 15)), ASSETID="SW-2002", ASSETTYPE="PadmountSwitchgear", ENERGIZED="Y"),
        feat(LineString([pt(-5, -4), pt(40, -4)]), ASSETID="UG-3003", ASSETTYPE="UndergroundConduit", ENERGIZED="Y"),
        feat(LineString([pt(-100, 80), pt(100, 80)]), ASSETID="OH-4004", ASSETTYPE="OverheadConductor", ENERGIZED="Y"),
        feat(Point(*pt(900, 900)), ASSETID="PM-9009", ASSETTYPE="PadmountTransformer", ENERGIZED="Y")]
dump("electric_assets.geojson", fc(elec))
valves = [feat(Point(*pt(x, y)), VALVEID=vid, PRESSURE_SYSTEM="LP") for vid, x, y in
          [("GV-101", -120, 30), ("GV-102", 150, -40), ("GV-103", 60, 200), ("GV-104", -300, -300), ("GV-105", 500, 500)]]
dump("gas_valves.geojson", fc(valves))
dump("critical_facilities.geojson", fc([feat(Point(*pt(45, 8)), NAME="Demo Elementary School", FACILITY_TYPE="school"),
                                         feat(Point(*pt(700, 700)), NAME="Demo Hospital", FACILITY_TYPE="hospital")]))
dump("customer_meters.geojson", fc([feat(Point(*pt(x, y)), METERID=f"M-{i}") for i, (x, y) in
                                     enumerate([(10, 12), (20, 14), (35, 10), (44, 20), (300, 300), (-60, 60)], 1)]))

# Survey submissions (minutes_ago is converted to CreationDate when loaded). Leak A is the main incident.
base = dict(Pipeline_Type="cast_iron", Pressure_Class="medium_distribution", Surface_Cover="asphalt")
survey = [
    {"name": "A", "minutes_ago": 180, "xy": pt(0, 0), "attributes": {"Leak_ID": "LK-DEMO-A", "Gas_LEL_Pct": 35, "Gas_PPM": 17500, "Crew_Badge": "T-4471",
     "Notes": "Strong odor at curb line next to pad-mount; reading taken at bar hole.", "Instrument_Temp_C": 21, **base}, "photos": ["leak_a_1.png", "leak_a_2.png"]},
    {"name": "B-duplicate", "minutes_ago": 30, "xy": pt(-10, 6), "attributes": {"Leak_ID": "LK-DEMO-B", "Gas_LEL_Pct": 12, "Gas_PPM": 6000, "Crew_Badge": "T-5120",
     "Notes": "Second crew re-checked the same area.", **base}, "photos": []},
    {"name": "C-grade3", "minutes_ago": 20, "xy": pt(3000, 2500), "attributes": {"Leak_ID": "LK-DEMO-C", "Gas_LEL_Pct": 0.4, "Gas_PPM": 200, "Crew_Badge": "T-3301",
     "Notes": "Trace reading in open field.", "Pipeline_Type": "polyethylene", "Pressure_Class": "low_distribution", "Surface_Cover": "grass"}, "photos": []},
    {"name": "D-outside", "minutes_ago": 10, "xy": (LON0 + 0.5, LAT0), "attributes": {"Leak_ID": "LK-DEMO-D", "Gas_LEL_Pct": 5, "Gas_PPM": 2500, "Crew_Badge": "T-0001",
     "Notes": "Coordinates outside franchise (demonstrates validation).", **base}, "photos": []},
]
dump("survey_submissions.json", survey)
dump("weather.json", {"wind_speed_mps": 4.0, "wind_from_deg": 270.0, "air_temp_c": 18.0, "pressure_hpa": 1013.0, "station": "SAMPLE"})

for name, text in (("leak_a_1.png", "SAMPLE photo: leak site"), ("leak_a_2.png", "SAMPLE: CGI screenshot 35 %LEL")):
    im = Image.new("RGB", (640, 420), (60, 70, 80))
    d = ImageDraw.Draw(im)
    d.rectangle([20, 20, 620, 400], outline=(230, 230, 230), width=3)
    d.text((40, 180), text + "  (synthetic image)", fill=(240, 240, 240))
    im.save(OUT / "photos" / name)
print("sample data written to", OUT)
