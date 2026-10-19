# Esri account requirements

These products automate Esri workflows, so most of them need access to an Esri environment. Exact licensing terms change; confirm with your Esri account manager or administrator.

| You want to... | You need |
|---|---|
| Run an offline demo / unit tests | Nothing from Esri (Python + `pip install -r requirements.txt`) |
| Sign in with the ArcGIS API for Python and edit hosted feature layers | An **ArcGIS Online organization** (or **ArcGIS Enterprise** portal) with a named user allowed to create/edit hosted feature layers; a free personal/developer account is generally not sufficient for org workflows |
| Collect data with Survey123 / Field Maps | Survey123 and Field Maps are available to ArcGIS Online named users (check your user type); the author's products ship an XLSForm for Survey123 Connect |
| Use enterprise reference layers (assets, valves, facilities) | Read access to those feature services (sharing with the service account's group) |
| Publish/own services created by scripts | A user type/role with publish + create-content privileges |
| Receive Survey123 webhooks | A reachable HTTPS endpoint, or use the polling mode these products provide |

## Recommended practice
* Run automation as a dedicated **service account** with least privilege (edit rights on output layers, read-only elsewhere). Use username/password or an OAuth app (client id/secret).
* Keep credentials in `.env` only. Never commit it. Rotate on staff changes.
* Start in a **non-production org or a copy of your layers**, run each product's `check` command, then test with sample submissions.
* ArcGIS Online and API behaviour changes over time; the products pin a minimum version in `requirements.txt` and say which calls were not verified live.
