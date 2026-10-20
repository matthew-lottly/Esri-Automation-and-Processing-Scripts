# Report & e-mail templates

Everything the pipeline sends or saves is rendered from the files in this folder. **Copy this folder, edit the files, and point `TEMPLATE_DIR` /
`REPORT_TEMPLATE_A` / `REPORT_TEMPLATE_B` in your `.env` at your copy.** No Python needed.

| File | What it is |
|---|---|
| [`field_ticket.html`](field_ticket.html) | **Report 1** - the technician's submission: readings, photos, a **map zoomed to the submitted feature**, raw attributes |
| [`risk_assessment.html`](risk_assessment.html) | **Report 2** - layer-level hazard assessment (grade, electric assets, facilities, valves, 811). No map by default |
| [`email_alert.html`](email_alert.html) | Grade 1-2 alert e-mail (inline CSS, as e-mail clients require) |
| [`email_daily_summary.html`](email_daily_summary.html) | 17:00 executive summary e-mail |
| [`report.css`](report.css) | Page size, header band, tiles, tables - shared by both reports |
| [`theme.yaml`](theme.yaml) | The colour palette (also used by report maps) |
| [`partials/macros.html`](partials/macros.html) | Reusable pieces: `kpi()`, `field()`, `callout()`, `header()`, `footer()` |

## Templates are Jinja2

```jinja
<h1>{{ report.leak_id }}</h1>                                     print a value
{{ report.lel_pct | fmt('.1f') }} % LEL                            format a number
{% if report.photos %} ... {% else %} ... {% endif %}              condition
{% for row in electric_hits %}<td>{{ row.asset_id }}</td>{% endfor %}   loop over a table
{{ map(zoom_m=140, layers=['electric', 'valves']) }}               a map zoomed to the submitted feature
{{ photo_gallery(columns=2) }}                                     the survey photos
```

## Try a change in seconds

```bash
python -m gas_electric_response preview templates/field_ticket.html --out preview.pdf   # renders PDF + HTML with the demo data
python -m gas_electric_response template-help                                            # lists every variable, table, helper and filter
```

In the offline demo, maps and photos render as **neutral placeholders** (no imagery is made up). Add `--live-media` to draw the real map from the sample layers.
The complete guide with examples is in [`docs/report-templates.md`](../docs/report-templates.md).
