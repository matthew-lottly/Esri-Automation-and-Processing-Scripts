"""Create the hosted feature service `Active_Gas_Electric_Incidents` (layer 0 = incidents, layer 1 = plumes)
from config/layer_schema.yaml, so the fields the pipeline writes always exist.

  python scripts/create_incident_service.py            # DRY RUN: prints the definition, touches nothing
  python scripts/create_incident_service.py --create   # creates the service in your org (needs privileges)

!! Not exercised against a live org by the author (no Esri account). The dry-run output is plain JSON you
!! can also paste into the ArcGIS REST "Add to Definition" form if the API call needs adjusting.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gas_electric_response.config import LayerSchema, Settings  # noqa: E402


class LayerDefinitionBuilder:
    """Turns the `incident_layer` / `plume_layer` sections of layer_schema.yaml into ArcGIS layer definitions."""

    ESRI_TYPES = {"string": "esriFieldTypeString", "int": "esriFieldTypeInteger", "double": "esriFieldTypeDouble", "datetime": "esriFieldTypeDate"}
    GRADE_COLORS = {1: [209, 39, 30, 255], 2: [242, 142, 43, 255], 3: [78, 154, 81, 255]}

    def __init__(self, schema: LayerSchema):
        self.inc, self.plu = schema.incident_layer.fields.raw(), schema.plume_layer.fields.raw()

    def fields(self, spec: dict) -> list[dict]:
        out = [{"name": "OBJECTID", "type": "esriFieldTypeOID", "alias": "OBJECTID", "editable": False, "nullable": False}]
        for v in spec.values():
            f = {"name": v["name"], "type": self.ESRI_TYPES[v["type"]], "alias": v["name"], "nullable": True, "editable": True}
            if v["type"] == "string":
                f["length"] = int(v.get("length", 255))
            out.append(f)
        return out

    def renderer(self, geometry: str, grade_field: str) -> dict:
        def symbol(c):
            if geometry == "esriGeometryPoint":
                return {"type": "esriSMS", "style": "esriSMSCircle", "color": c, "size": 12, "outline": {"color": [255, 255, 255, 255], "width": 1}}
            return {"type": "esriSFS", "style": "esriSFSSolid", "color": c[:3] + [90], "outline": {"type": "esriSLS", "style": "esriSLSSolid", "color": c, "width": 1.5}}
        infos = [{"value": g, "label": f"Grade {g}", "symbol": symbol(c)} for g, c in self.GRADE_COLORS.items()]
        return {"renderer": {"type": "uniqueValue", "field1": grade_field, "uniqueValueInfos": infos}}

    def layer(self, idx: int, name: str, geometry: str, spec: dict) -> dict:
        return {"id": idx, "name": name, "type": "Feature Layer", "geometryType": geometry, "objectIdField": "OBJECTID",
                "displayField": spec["leak_id"]["name"], "fields": self.fields(spec), "hasAttachments": False,
                "capabilities": "Query,Create,Update,Editing", "drawingInfo": self.renderer(geometry, spec["grade"]["name"]),
                "extent": {"xmin": -180, "ymin": -85, "xmax": 180, "ymax": 85, "spatialReference": {"wkid": 4326}},
                "spatialReference": {"wkid": 4326}}

    def layers(self) -> list[dict]:
        return [self.layer(0, "Incidents", "esriGeometryPoint", self.inc), self.layer(1, "Plumes", "esriGeometryPolygon", self.plu)]


class ServicePublisher:
    """Creates the hosted feature service (the only step that needs an Esri login)."""

    def __init__(self, settings: Settings, definition: LayerDefinitionBuilder):
        self.settings, self.definition = settings, definition

    def create(self, name: str) -> int:
        from gas_electric_response.clients.arcgis_store import ArcGISConnection

        gis = ArcGISConnection(self.settings).open()
        if not gis.content.is_service_name_available(name, "featureService"):
            print(f"The service name '{name}' is not available in your org (it may already exist); choose --name.", file=sys.stderr)
            return 1
        item = gis.content.create_service(name, service_description="Active gas/electric hazard incidents", wkid=4326,
                                          capabilities="Query,Create,Update,Editing", has_static_data=False)
        from arcgis.features import FeatureLayerCollection

        FeatureLayerCollection.fromitem(item).manager.add_to_definition({"layers": self.definition.layers()})
        print("Created:", item.homepage)
        print("Layer URLs (put these in .env):")
        for i, key in enumerate(("INCIDENT", "PLUME")):
            print(f"  {key}_LAYER_URL={item.url}/{i}")
        return 0


class CreateServiceApp:
    def run(self, argv: list[str] | None = None) -> int:
        ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
        ap.add_argument("--create", action="store_true", help="actually create the service (default: dry run)")
        ap.add_argument("--name", default="Active_Gas_Electric_Incidents")
        ap.add_argument("--config", default="./config")
        ap.add_argument("--env-file", default=".env")
        a = ap.parse_args(argv)
        definition = LayerDefinitionBuilder(LayerSchema.load(a.config))
        if not a.create:
            print(json.dumps({"layers": definition.layers()}, indent=2))
            print("\n(dry run - nothing created; re-run with --create)", file=sys.stderr)
            return 0
        return ServicePublisher(Settings.from_env(a.env_file), definition).create(a.name)


if __name__ == "__main__":
    raise SystemExit(CreateServiceApp().run())
