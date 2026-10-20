# Adapting the pipeline: other analyses, other datasets

The gas/electric scenario is **one configuration of a general pattern**:

```
 intake  ──►  clean/validate  ──►  enrich (weather, tickets)  ──►  analyse against layers  ──►  grade/decide  ──►  write results  ──►  reports  ──►  notify
 (webhook     (Stage)              (Stage)                        (OverlayStage)               (Stage)           (Stage)           (Template)   (Template)
  or poll)
```

Everything moves through one mutable object, the **`IncidentContext`**. Each **`Stage`** reads what earlier stages put on it and adds its own results.
Each external system sits behind a small interface (**`FeatureStore`**, **`WeatherClient`**, **`ExcavationTicketClient`**, **`EmailNotifier`**), and the pipeline itself is
just an ordered list in [`bootstrap.PipelineBuilder.stages()`](../src/gas_electric_response/bootstrap.py). Change the list and the README diagram regenerates itself.

| Concept | Class | Where | You change it when... |
|---|---|---|---|
| Field names, domain values, output schema | YAML | `config/layer_schema.yaml` | your layers use different names/codes (no code needed) |
| Thresholds, multipliers, dispatch codes | YAML | `config/risk_parameters.yaml` | tuning the analysis |
| A step | `Stage` | `stages/*.py` | adding/removing/replacing a step |
| "Find features related to the incident" | `OverlayStage` | `stages/analysis.py` | a new reference layer (point, line or polygon) |
| The decision rule | `GradingPolicy` | `stages/analysis.py` | different severity/priority logic |
| The hazard footprint | `PlumeModel` | `plume.py` | a different buffer/footprint model |
| Data access | `FeatureStore` | `clients/` | a non-ArcGIS source (database, API, GeoPackage) |
| What the reports/e-mails say | Jinja2 templates | `templates/` | wording, layout, maps ([report-templates.md](report-templates.md)) |

## Recipe 1 - Same analysis, different field names (no code)

Edit the right-hand side of `config/layer_schema.yaml`: survey question names, your asset-type values and which count as `ignition_source` / `conduit` / `other`,
valve/facility fields, and the output layer fields (which `scripts/create_incident_service.py` also uses to build the service). Then `python -m gas_electric_response check`.

## Recipe 2 - The submitted feature is a LINE or POLYGON, not a point

Nothing to change in code: the pipeline treats the submission geometry generically.

| Submitted geometry | What happens |
|---|---|
| **Point** | plume = teardrop from the point; the point is the anchor |
| **Line** (e.g. an inspected pipe segment) | `report.lon/lat` = a point on the line; the plume is the whole line buffered by the model radius and swept downwind; duplicate check buffers the entire line |
| **Polygon** (e.g. an affected area) | same, using the polygon; anchor = representative point inside it |

Use a line/polygon layer for the survey (Survey123 `geotrace` / `geoshape` questions, or a Field Maps layer) and set `SURVEY_FEATURE_LAYER_URL`.
In reports, `{{ report.geometry_type }}` says which it was, and `map()` zooms to the whole feature. Tests: `tests/test_geometry_types.py`.

> Incident layer geometry is still the **anchor point**; if you want the submitted line/polygon itself written to the output layer, change the `Point(r.lon, r.lat)` in
> `stages/publish.py` to `r.geometry` and create the output layer with the matching geometry type (`scripts/create_incident_service.py` -> `geometryType`).

## Recipe 3 - A new reference layer (POINT, LINE or POLYGON)

Subclass `OverlayStage`. It hands you the search area, runs the spatial query, and you decide what a hit is. `measure()` gives **length inside the search area for lines** and
**area inside for polygons**, so line and polygon layers need no extra code.

```python
# stages/parcels.py
from gas_electric_response.models import AssetHit
from gas_electric_response.stages.analysis import OverlayStage

class ParcelStage(OverlayStage):
    name = "Find parcels in the plume"                      # shown in the README diagram and stage table
    description = "Land parcels inside the plume (polygon layer)"
    external = "ArcGIS parcels layer"
    group = "Spatial & Risk Analysis"
    store_name = "parcels"                                  # a name in Services.layers / extra_layers

    @property
    def store(self):                                        # where to read it from
        return self.svc.extra_layers["parcels"]

    def search_geometry(self, ctx, frame):                  # what to search with: here, the plume polygon
        return ctx.plume.polygon                            #   (try frame.buffer(ctx.report.geometry, 200) for "within 200 m")

    def make_hit(self, ctx, rec, frame, search):            # one reference feature -> one hit (return None to skip it)
        length_m, area_m2 = self.measure(frame, rec.geometry, search)
        return AssetHit("parcel", "parcel", rec.attributes["APN"], "parcel",
                        frame.distance(ctx.report.geometry, rec.geometry), inside_area_m2=area_m2)

    def collect(self, ctx, hits):                           # store the results where later stages/reports can find them
        ctx.parcels = hits
```

Register it:

```python
# bootstrap.py -> PipelineBuilder.stages(): add the stage in the list, e.g. right after FacilitiesStage(svc)
# and give Services the layer (ProductionRuntimeFactory.create):
svc.extra_layers["parcels"] = store(st.parcels_layer_url, "parcels")      # add PARCELS_LAYER_URL to Settings/.env
```

Use it in a template (after exposing the results with a `ReportData` subclass, see [report-templates.md](report-templates.md#4-calling-out-fields-photos-and-other-layer-info)):
`{% for p in parcels %}{{ p.apn }} - {{ p.area | fmt(',.0f') }} m²{% endfor %}`, and draw it on a map: `{{ map(layers=['electric', {'layer': 'parcels', 'color': '#2F9E5B', 'fill_alpha': 0.15}]) }}`.

Test pattern: `tests/test_geometry_types.py::test_custom_overlay_stage_for_a_polygon_layer`.

## Recipe 4 - Different decision rule

Subclass `GradingPolicy` (or edit `config/risk_parameters.yaml -> grading`) and swap it in `GradeStage`:

```python
class MyPolicy(GradingPolicy):
    def grade(self, ctx):
        if ctx.parcels and ctx.report.effective_lel_pct > 10:
            return HazardGrade.CRITICAL, ["gas reading above 10 % with parcels in the plume"]
        return super().grade(ctx)
```

## Recipe 5 - A different hazard footprint

`PlumeModel.build(report, weather) -> PlumeResult(polygon, length_m, max_width_m, model, wind_from_deg, assumptions)`. Replace the class (or its `radius()`/`wind_state()`) with your
approved method - e.g. a fixed isolation distance, a dispersion model call, or a drive-time polygon. Everything downstream (intersects, grade, maps, reports) only uses the `polygon`.

## Recipe 6 - A whole different use case

Keep the plumbing, replace the domain. For example, a **sewer overflow response**:

| Keep as is | Replace |
|---|---|
| intake (webhook/poller), validation skeleton, duplicate detection, ledger, retries, alerts | `ValidateStage` ranges (flow, level), `survey_layer` mapping |
| `OverlayStage` machinery, `FeatureStore`, publish, report engine, e-mail, reconcile | `WeatherStage` -> rainfall gauge client; `PlumeStage` -> overflow footprint; overlays -> manholes (points), pipes (lines), flood zones (polygons), schools (points) |
| templates + CSS + maps | wording in the templates; `GradingPolicy` rules; `layer_schema.yaml` output fields |

| Your dataset | Typical analysis | How |
|---|---|---|
| Points (hydrants, poles, meters) | "nearest N", "count within X", "inside the footprint" | `OverlayStage` + `frame.distance` / count |
| Lines (mains, roads, conduit) | "length inside the footprint", "nearest segment" | `OverlayStage`, `inside_length_m` |
| Polygons (parcels, flood zones, districts) | "area inside", "which polygon contains the incident" | `OverlayStage`, `inside_area_m2` / `search_geometry` = the incident point |
| Non-ArcGIS data (PostGIS, GeoPackage, REST) | any of the above | implement `FeatureStore` (`query`, `add`, `update`, attachments); the pipeline only uses that interface |

## Recipe 7 - Another notification channel, another intake

* **Channel** (Teams, SMS, PagerDuty): write a class with `send(subject, text, to, html=None, attachments=None)` like `EmailNotifier` and pass it to `NotifyStage`.
* **Intake** other than Survey123: anything that can hand `process_record()` a `FeatureRecord` works - a new poller, a message-queue consumer, an Azure Function. Implement a `FeatureStore` for the source.

## Removing or reordering steps

`PipelineBuilder.stages()` is a plain list. Delete `ExcavationStage` if you have no 811 data; move `WeatherStage` later; add stages anywhere. Stages that depend on others
read their results from the context (e.g. grading needs `ctx.plume`), so keep dependencies earlier in the list. Run `python -m gas_electric_response flowchart --write` to refresh the README diagrams.

## Checklist when you adapt it

1. Offline first: add your layers to `sample_data/` (GeoJSON) and run `demo` - everything works without Esri.
2. Write/adjust tests next to `tests/test_geometry_types.py`.
3. Update `layer_schema.yaml`, run `scripts/create_incident_service.py` (dry run) to see the output schema, then `check` against your org.
4. Review the report templates and send yourself the e-mails (`EMAIL_MODE=file`).
