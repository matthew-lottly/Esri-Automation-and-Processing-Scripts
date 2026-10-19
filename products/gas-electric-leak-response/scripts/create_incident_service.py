"""Create the hosted feature service `Active_Gas_Electric_Incidents` (layer 0 = incidents, layer 1 = plumes)
from config/layer_schema.yaml, so the fields the pipeline writes always exist.

  python scripts/create_incident_service.py            # DRY RUN: prints the definition, touches nothing
  python scripts/create_incident_service.py --create   # creates the service in your org (needs privileges)

!! Not exercised against a live org by the author (no Esri account). The dry run output is plain JSON you
!! can also paste into the ArcGIS REST "Add to Definition" form if the API call needs adjusting.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gas_electric_response.config import LayerSchema, Settings  # noqa: E402

ESRI_TYPES = {"string": "esriFieldTypeString", "int": "esriFieldTypeInteger", "double": "esriFieldTypeDouble", "datetime": "esriFieldTypeDate"}
GRADE_COLORS = {1: [209, 39, 30, 255], 2: [242, 142, 43, 255], 3: [78, 154, 81, 255]}


def fields_json(spec: dict) -> list[dict]:
    out = [{"name": "OBJECTID", "type": "esriFieldTypeOID", "alias": "OBJECTID", "editable": False, "nullable": False}]
    for v in spec.values():
        f = {"name": v["name"], "type": ESRI_TYPES[v["type"]], "alias": v["name"], "nullable": True, "editable": True}
        if v["type"] == "string":
            f["length"] = int(v.get("length", 255))
        out.append(f)
    return out


def layer_json(idx: int, name: str, geometry: str, spec: dict, grade_field: str) -> dict:
    infos = [{"value": g, "label": f"Grade {g}", "symbol": (
        {"type": "esriSMS", "style": "esriSMSCircle", "color": c, "size": 12, "outline": {"color": [255, 255, 255, 255], "width": 1}}
        if geometry == "esriGeometryPoint" else
        {"type": "esriSFS", "style": "esriSFSSolid", "color": c[:3] + [90], "outline": {"type": "esriSLS", "style": "esriSLSSolid", "color": c, "width": 1.5}})}
             for g, c in GRADE_COLORS.items()]
    return {"id": idx, "name": name, "type": "Feature Layer", "geometryType": geometry, "objectIdField": "OBJECTID",
            "displayField": spec["leak_id"]["name"], "fields": fields_json(spec), "hasAttachments": False, "capabilities": "Query,Create,Update,Editing",
            "drawingInfo": {"renderer": {"type": "uniqueValue", "field1": grade_field, "uniqueValueInfos": infos}},
            "extent": {"xmin": -180, "ymin": -85, "xmax": 180, "ymax": 85, "spatialReference": {"wkid": 4326}},
            "spatialReference": {"wkid": 4326}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--create", action="store_true", help="actually create the service (default: dry run)")
    ap.add_argument("--name", default="Active_Gas_Electric_Incidents")
    ap.add_argument("--config", default="./config")
    ap.add_argument("--env-file", default=".env")
    a = ap.parse_args()
    schema = LayerSchema.load(a.config)
    inc, plu = schema.incident_layer.fields.raw(), schema.plume_layer.fields.raw()
    layers = [layer_json(0, "Incidents", "esriGeometryPoint", inc, inc["grade"]["name"]),
              layer_json(1, "Plumes", "esriGeometryPolygon", plu, plu["grade"]["name"])]
    if not a.create:
        print(json.dumps({"layers": layers}, indent=2))
        print("\n(dry run - nothing created; re-run with --create)", file=sys.stderr)
        return 0
    from gas_electric_response.clients.arcgis_store import connect

    gis = connect(Settings.from_env(a.env_file))
    if not gis.content.is_service_name_available(a.name, "featureService"):
        print(f"The service name '{a.name}' is not available in your org (it may already exist); choose --name.", file=sys.stderr)
        return 1
    item = gis.content.create_service(a.name, service_description="Active gas/electric hazard incidents", wkid=4326,
                                      capabilities="Query,Create,Update,Editing", has_static_data=False)
    from arcgis.features import FeatureLayerCollection

    flc = FeatureLayerCollection.fromitem(item)
    flc.manager.add_to_definition({"layers": layers})
    print("Created:", item.homepage)
    print("Layer URLs (put these in .env):")
    for i in (0, 1):
        print(f"  {'INCIDENT' if i == 0 else 'PLUME'}_LAYER_URL={item.url}/{i}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
