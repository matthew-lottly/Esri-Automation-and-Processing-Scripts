# Esri setup (step by step)

You need an **ArcGIS Online organization** (or ArcGIS Enterprise). None of the steps below can be done with a free personal account.

## 1. A service account
Create a named user (e.g. `svc_gas_electric`) with a role that can **create content, edit hosted feature layers, and read your reference
layers**. Do not use a personal admin login. Put its credentials in `.env` (`ARCGIS_USERNAME` / `ARCGIS_PASSWORD`) or register an OAuth
application and use `ARCGIS_CLIENT_ID` / `ARCGIS_CLIENT_SECRET` instead. Share the survey layer, electric assets, valves, facilities
(and meters / franchise layers if used) with this user's group.

## 2. Output layers
```bash
PYTHONPATH=src python scripts/create_incident_service.py            # dry run: prints JSON
PYTHONPATH=src python scripts/create_incident_service.py --create   # creates Active_Gas_Electric_Incidents (layers 0 and 1)
```
Copy the printed layer URLs into `.env`. Layer 0 = incidents (points), layer 1 = plumes (polygons). Build your dashboard / Field Maps
map on these; filter `IS_DUPLICATE = 0` for map symbols and symbolise by `HSI_GRADE`.
If your org prefers to own the schema, edit `config/layer_schema.yaml`'s `incident_layer` / `plume_layer` sections and create the layers yourself.

## 3. The survey
`python scripts/build_xlsform.py` regenerates `survey123/gas_leak_form.xlsx`. Open it in **Survey123 Connect**, review constraints, publish.
Question names (`Leak_ID`, `Gas_LEL_Pct`, ...) become layer field names and must match `survey_layer:` in `config/layer_schema.yaml`.
If you already have a survey, change the right-hand names in that YAML section instead. The `Leak_ID` auto-ID calculation uses `uuid()`
and `substr()`; if Connect rejects it, replace it with your own ID scheme.

## 4. Your reference layers
Edit `electric_assets`, `gas_valves`, `critical_facilities` in `config/layer_schema.yaml`: field names, and which **asset-type values** count as
`ignition_source` (pad-mount transformers, switchgear), `conduit` (underground), or `other`. Values may be text or integer coded values.
If your enterprise data is a Utility Network, use the asset-type codes/names your feature classes expose.

## 5. Verify (read-only)
```bash
PYTHONPATH=src python -m gas_electric_response check
```
It signs in, opens every layer and confirms the expected fields exist. Fix anything marked `[FAIL]`.

## 6. Receive submissions
* **Webhook** - Survey123 website -> your survey -> Settings -> Webhooks -> add `https://<host>/webhooks/survey123?token=<WEBHOOK_TOKEN>`
  for the *new submission* event. Needs a public HTTPS URL (reverse proxy; see deployment.md). The receiver only reads the submission's ID
  from the body and then re-reads the record from your layer with its own credentials.
* **Polling** - `python -m gas_electric_response poll` queries the survey layer every minute. Works behind a firewall and with Field Maps layers.
  You can run both (recommended: polling is the safety net for a missed/lost webhook); an atomic claim in the SQLite ledger prevents double processing,
  provided both processes share the same `STATE_DB_PATH` on one machine.

## What the author could not verify
Sign-in, layer queries/edits, attachments download, service creation and real webhook traffic have **not** been run against a live org.
Expect to adjust small things (field names, SQL timestamp syntax on your platform, the webhook event name). `check` and the dry-run outputs are built to make those fixes quick.
