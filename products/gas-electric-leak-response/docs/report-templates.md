# Report templates

Reports and e-mails are **Jinja2 templates**: ordinary HTML files with `{{ placeholders }}` that the pipeline fills in. They live in the
[`templates/`](../templates) folder - copy the folder, edit, and point `TEMPLATE_DIR` / `REPORT_TEMPLATE_A` / `REPORT_TEMPLATE_B` (in `.env`) at your copy.

```
templates/
  field_ticket.html        Report 1  (submission record, with a map zoomed to the submitted feature)
  risk_assessment.html     Report 2  (layer-level hazard assessment, no map)
  email_alert.html         alert e-mail           email_daily_summary.html   17:00 summary e-mail
  report.css               look & layout          theme.yaml                 colour palette
  partials/macros.html     reusable pieces: kpi() field() callout() header() footer()
```

## 1. The 60-second tour

```jinja
{% from "partials/macros.html" import header, footer, kpi, field, callout %}   {# import the reusable pieces #}
<!doctype html><html><head>
  <style>{{ theme_css }}</style>                       {# palette as CSS variables: var(--accent), var(--grade-1) ... #}
  <link rel="stylesheet" href="report.css"/>
</head><body>
  {{ header("Gas Leak Field Ticket", report.leak_id) }}      {# dark band at the top of every page #}

  <div class="kpis">
    {{ kpi("Peak reading", report.effective_lel_pct | fmt('.1f'), "% LEL", grade.color) }}
    {{ kpi("Meters in plume", counts.meters) }}
  </div>

  <h2>Location of submitted feature</h2>
  {{ map(height_in=3.6, zoom_m=140) }}                 {# <- the map placeholder: filled with a real map at run time #}
</body></html>
```

| You write | You get |
|---|---|
| `{{ report.leak_id }}` | the value (empty/missing values print as an em dash `—`, never an error) |
| `{{ report.lel_pct \| fmt('.1f') }}` | formatted with a [Python format spec](https://docs.python.org/3/library/string.html#formatspec): `'.1f'`, `',.0f'`, `'05d'` |
| `{% if grade.number <= 2 %}...{% endif %}` | conditional content |
| `{% for row in electric_hits %}...{% endfor %}` | a table row per item |
| `{{ map(...) }}` | a map image zoomed to the submitted feature |
| `{{ photo_gallery(columns=2) }}` | the survey's photo attachments in a grid |

Preview any change instantly (demo data, no Esri needed):

```bash
python -m gas_electric_response preview templates/field_ticket.html --out preview.pdf    # also writes preview.html
python -m gas_electric_response template-help                                             # every variable, table, helper, filter
```

> In the demo, `map()` and `photo_gallery()` draw **neutral placeholders** - nothing is faked. Add `--live-media` to `preview` to draw the real map from the sample layers.
> In production they render the real map and the real photos. Set `REPORT_PLACEHOLDER_MEDIA=true` while designing a template if you want placeholders there too.

## 2. Variables you can use

Run `template-help` for the live list. The main namespaces:

| Namespace | Examples | Notes |
|---|---|---|
| `report` | `leak_id` `lel_pct` `ppm` `effective_lel_pct` `pipeline_type` `pressure_class` `surface_cover` `crew_badge` `temp_c` `notes` `anchor` `geometry_type` `reported_local` `reported_utc` `source_global_id` `photos` | the normalized field submission |
| `raw` | `{{ raw['Gas_LEL_Pct'] }}` | any raw survey field by its **layer field name** (case matters) |
| `grade` | `number` `label` `title` `color` `reasons` `dispatch` | Hazard Severity Index result |
| `weather` | `summary` `wind_speed_mps` `wind_from_deg` `wind_from_text` `air_temp_c` `pressure_hpa` `station` `observed_local` `status` | |
| `plume` | `length_m` `max_width_m` `model` `assumptions` | |
| `counts` | `electric_total` `electric_ignition` `electric_conduit` `facilities` `meters` `valves` `tickets` `flags` `photos` | ready-made numbers for KPI tiles |
| `nearest_valve` | `id` `distance_m` | |
| `incident` | `is_duplicate` `duplicate_of` `is_escalation` `dispatch_code` `valve_ids` `max_lel_pct` ... | what was written to the incident layer, by **internal key** (not your field name) |
| `generated` | `local` `utc` | report generation time |
| `flags` | one string of data-quality notes | `{% if flags %}` |

### Tables (loop with `{% for row in NAME %}`)

| Table | Row fields |
|---|---|
| `electric_hits` | `asset_id` `asset_type` `category` `energized` `distance_m` `in_plume` (e.g. `45 m` for a line, `1,200 m²` for a polygon, `point`) `length_in_plume_m` `area_in_plume_m2` `geometry_type` |
| `facility_hits` | `name` `facility_type` `distance_m` |
| `valves` | `rank` `valve_id` `distance_m` |
| `tickets` | `number` `status` `work_type` `distance_m` `expires` |
| `raw_attributes` | `name` `value` |
| `flag_list`, `reasons` | `text` |

```jinja
<table class="data">
  <thead><tr><th>Asset</th><th>Type</th><th class="num">Distance (ft)</th></tr></thead>
  <tbody>
  {% for row in electric_hits if row.category == "ignition source" %}
    <tr><td>{{ row.asset_id }}</td><td>{{ row.asset_type }}</td><td class="num">{{ row.distance_m | ft }}</td></tr>
  {% else %}
    <tr><td colspan="3">No ignition sources in the plume.</td></tr>
  {% endfor %}
  </tbody>
</table>
```

### Filters

`fmt('.1f')` format any value (empty → `—`) · `ft` metres → feet (`{{ row.distance_m | ft }}`, `ft(1)` for one decimal) · `local('America/Chicago')` UTC datetime → local text ·
`title_case` (`medium_distribution` → `Medium Distribution`) · plus every [built-in Jinja2 filter](https://jinja.palletsprojects.com/en/stable/templates/#builtin-filters): `round` `default` `join` `length` `upper` `sort` ...

## 3. Calling out maps

`{{ map(...) }}` draws a map **zoomed to the feature the technician submitted** (a point, line or polygon), with scale bar, north arrow and legend.

```jinja
{{ map() }}                                                  {# defaults: fit the feature + plume, all layers, no basemap #}
{{ map(height_in=4, zoom_m=100) }}                            {# fixed zoom: 100 m each side of the feature #}
{{ map(padding=0.8) }}                                        {# fit the feature with 80 % extra room around it #}
{{ map(layers=['electric', 'valves']) }}                      {# only these layers #}
{{ map(layers=[]) }}                                          {# no reference layers - just the feature and plume #}
{{ map(basemap='esri_imagery') }}                             {# online basemap (needs internet) #}
{{ map(show_plume=false, legend=false, north_arrow=false) }}  {# strip it down #}
```

| Option | Default | Meaning |
|---|---|---|
| `width_in`, `height_in` | `7.2`, `3.6` | size on the page |
| `zoom_m` | – | fixed half-width around the feature, in metres (overrides `padding`) |
| `padding` | `0.35` | margin around the feature(+plume) as a fraction of its size |
| `min_half_width_m` | `35` | never zoom in tighter than this |
| `include_plume` | `true` | include the plume when fitting the extent |
| `show_plume` | `true` | draw the modeled gas migration envelope |
| `basemap` | `none` | `none` · `osm` · `esri_street` · `esri_imagery` · `esri_gray` · or a tile URL template with `{z}/{x}/{y}` |
| `layers` | all default layers | list of layer names (strings) or style dicts - see below |
| `scale_bar`, `north_arrow`, `legend` | `true` | map furniture |

**Layers** are looked up by name in `Services.layers` (`electric`, `valves`, `facilities`, `meters`, `survey`, `incidents`, `plumes`, plus anything you add to `Services.extra_layers`).
Each can be styled inline - points, **lines and polygons** are all drawn correctly:

```jinja
{{ map(layers=[
     {'layer': 'electric',   'label': 'Electric assets', 'color': '#E8590C', 'symbol': 's', 'size': 46, 'linewidth': 2.6, 'label_field': 'ASSETID'},
     {'layer': 'facilities', 'label': 'Schools / hospitals', 'color': '#7048E8', 'symbol': 'P', 'size': 70, 'label_field': 'NAME'},
     {'layer': 'parcels',    'label': 'Parcels', 'color': '#2F9E5B', 'fill_alpha': 0.15},
     {'layer': 'electric', 'color_by': {'field': 'ASSETTYPE', 'colors': {'PadmountTransformer': '#D1271E', 'UndergroundConduit': '#E8890C'}, 'default': '#868E96'}}
   ]) }}
```

Style keys: `label` `color` `symbol` (`o s ^ v D P *`) `size` `linewidth` `alpha` `fill_alpha` `label_field` `color_by` `max_features`.

**Basemap notes.** `none` is fully offline and uses a neutral background. The online options fetch standard XYZ tiles (best-effort: if offline you get a neutral map with a note).
OpenStreetMap and Esri tiles have their own usage terms - check them for your volume and attribution requirements (attribution is drawn on the map) before enabling in production.

**Other map placements.** `{{ map_uri(...) }}` returns the image as a data URI if you want to build the `<img>` yourself:
`<img src="{{ map_uri(zoom_m=80) }}" style="width:50%">`. Put two maps side by side with CSS (`display:flex`).

## 4. Calling out fields, photos and other layer info

```jinja
{{ field("Technician badge", report.crew_badge) }}                            {# label/value pair inside <div class="fields"> #}
{{ field("Raw reading", raw['Gas_LEL_Pct'] ~ " % LEL") }}                      {# any raw survey field by its name; ~ joins text #}
{{ kpi("Nearest valve", nearest_valve.distance_m | fmt('.0f'), "m") }}          {# big-number tile #}
{{ callout(grade.reasons, "Why this grade", grade.color) }}                    {# highlighted note with a coloured edge #}
{% if counts.facilities %}{{ callout("Critical facility in plume", "", "var(--grade-1)") }}{% endif %}
{{ photo_gallery(columns=3) }}                                                {# survey photos; placeholders in the demo #}
```

**Other layer info** - anything the pipeline found is a table you can loop over (section 2). To show data from a layer the pipeline does *not* analyse yet
(e.g. parcels), add an analysis stage ([adapting-the-pipeline.md](adapting-the-pipeline.md)) and expose its results as a table with a `ReportData` subclass:

```python
# mypackage/reports.py
from gas_electric_response.reports.data import ReportData, Row

class MyReportData(ReportData):
    def extend_namespace(self, ns):            # new variables
        from types import SimpleNamespace
        return {"utility": SimpleNamespace(name="Acme Gas & Power", phone="555-0100")}

    def extend_sources(self):                  # new tables
        return {"parcels": lambda: [Row(apn=h.asset_id, area=h.inside_area_m2) for h in getattr(self.ctx, "extra_hits", [])]}
```

Set `REPORT_DATA_CLASS=mypackage.reports:MyReportData` and use `{{ utility.name }}` / `{% for p in parcels %}` in your templates.

## 5. Styling

* **Colours**: edit [`theme.yaml`](../templates/theme.yaml); they become CSS variables (`var(--accent)`) and are used by maps too.
* **Layout/typography**: edit [`report.css`](../templates/report.css) (standard CSS; [WeasyPrint](https://doc.courtbouillon.org/weasyprint/stable/) also supports `@page`, flexbox and grid).
* **Page size/margins**: the `@page { size: Letter; margin: ... }` rule at the top of `report.css` (`size: A4` for A4).
* **Logo**: put `logo.png` in the templates folder and add `<img src="logo.png" style="height:10mm">` to the `header` macro in `partials/macros.html`.
* **A new report** (e.g. a one-page summary): copy a template, change the body, add it to `ReportStage`/`ReportComposer` in `stages/reporting.py`, or just point `REPORT_TEMPLATE_B` at it.

## 6. E-mail templates

`email_alert.html` and `email_daily_summary.html` use the same variables (the daily summary gets `s`, a `DailySummary`, see `reconcile.py`). Use **inline CSS** - e-mail clients ignore `<style>` blocks.
A plain-text version is always sent as well. If an e-mail template file is missing the plain-text body is used.

## 7. PDF engine requirements

PDFs are produced by [WeasyPrint](https://weasyprint.org) (`pip install weasyprint`, included in `requirements.txt`).
* **Linux/macOS**: works out of the box on most systems (needs Pango; `apt install libpango-1.0-0 libpangoft2-1.0-0` / `brew install pango`).
* **Windows**: install the GTK runtime first - see the [WeasyPrint install guide](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#windows).
* Don't want WeasyPrint? Implement `PdfEngine.render(html, base_url) -> bytes` (e.g. with headless Chromium or wkhtmltopdf) and pass it to `ReportRenderer(pdf_engine=...)` - everything else stays the same.

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `template syntax error` with a line number | an unclosed `{% if %}`/`{% for %}`, or a missing `}}` - the message names the file and line |
| a value shows `—` | the variable is empty or misspelled; run `template-help` to see the exact names |
| `raw['Field']` shows `—` | field names are case-sensitive and must match the survey layer |
| header band missing on later pages | keep the `header()` call near the top of `<body>` (it is `position: fixed`) |
| `WeasyPrint is not available` | see section 7 |
