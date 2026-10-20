# Limitations and verification checklist

## Modelling
1. Plume = heuristic envelope, **not** a dispersion model; coefficients are placeholders (see hazard-grading.md).
2. Valves = nearest, **not** traced upstream.
3. "Structures" are approximated by facilities and customer meters; there is no building-footprint analysis, and no model of gas entering conduits or basements beyond intersecting mapped conduits.
4. Weather comes from the nearest NWS reporting station, which can be far away or up to ~1 h old; it is flagged when stale/partial and the plume then falls back to a conservative circle.
5. Surface cover / pipeline material only scale the radius; they do not model soil permeability, frost, or main depth.
6. 811 ticket data is vendor-specific; the REST client must be adapted to your provider (docs/excavation-811.md).

## Reports
- PDFs use WeasyPrint (needs Pango; Windows needs the GTK runtime) - not verified on Windows.
- Report maps zoom to the submitted feature using YOUR registered layers; online basemaps (OSM/Esri tiles) are best-effort, were not fetched live by the author, and carry their own usage terms.
- The demo and the committed `examples/` show maps and photos as neutral placeholders by design.
- A report is generated once (write-once store). Changing a template does not rewrite past reports.

## Operations
7. Holidays are not detected by the 17:00 job (its window is rolling, so nothing is lost). 8. Run one receiver instance per state folder; `serve` and `poll` may share it.
9. A forced re-run needs a new GlobalID or a cleared ledger row. 10. The reconcile window counts rows by `PROCESSED_AT`, not by edit tracking.
11. The webhook queue is in memory: a restart loses queued items - run `poll` alongside as the durable safety net. 12. Report 1/2 PDFs are not regenerated after a partial failure
(only the notification is retried); the daily summary lists the missing report so it can be regenerated manually.
13. Grade 1 is never triggered by customer meters alone, and an electric asset's `ENERGIZED` flag only affects the "within 20 ft" metric, not the grade (conservative).

## Not verified against real systems (author had no Esri account)
- [ ] `check` passes against your org (sign-in, every layer opens, all fields present)
- [ ] `create_incident_service.py --create` produces both layers with the expected fields (or create them manually)
- [ ] A test Survey123 submission produces an incident row, a plume polygon, two PDFs, and (Grade 1-2) an e-mail
- [ ] Photo attachments appear in Report 1
- [ ] Hosted-layer SQL timestamp filters (`FIELD >= timestamp 'YYYY-MM-DD HH:MM:SS'`) behave on your platform/time zone (the reconcile job depends on them)
- [ ] Webhook delivery and the exact event name Survey123 sends; token/HMAC settings
- [ ] SMTP delivery, UNC share write permissions, 17:00 scheduled run
- [ ] Safety/engineering sign-off on `risk_parameters.yaml` and the grading rules
