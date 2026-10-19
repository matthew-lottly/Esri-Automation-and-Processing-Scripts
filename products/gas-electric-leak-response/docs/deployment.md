# Deployment

Two long-running/scheduled pieces: the **receiver or poller** (continuous) and the **17:00 reconcile** job (scheduled).

## Receiver (webhook mode)
```bash
python -m gas_electric_response serve --host 127.0.0.1 --port 8080
```
Put it behind a reverse proxy that terminates HTTPS (nginx, IIS ARR, Caddy, a cloud load balancer) and forward only `/webhooks/survey123` and `/health`.
Run it under a service manager: **systemd** (Linux), **NSSM** or Task Scheduler "at startup" (Windows). Use a single instance: the in-process queue serialises
submissions so the duplicate check cannot race. State (ledger, rejections log, downloaded photos) lives under `STATE_DB_PATH`'s folder - keep it on persistent disk.

## Poller (no inbound connection)
```bash
python -m gas_electric_response poll --interval 60 --lookback 180
```

## Reconcile at 17:00 local on business days
The report window is **rolling**: from the end of the previous successful run to today's 17:00 (`LOCAL_TIMEZONE`, DST-safe), stored in `state/reconcile_state.json`.
After-hours leaks, weekends and skipped days are therefore always covered by the next run. Weekend runs are skipped (holidays are not detected, but nothing is lost).
`--date YYYY-MM-DD` runs a calendar-day back-fill (00:00 to cutoff) without touching that state.
* **Windows Task Scheduler**: action `python.exe -m gas_electric_response reconcile`, start in the project folder, trigger weekly Mon-Fri 17:00, run whether user is logged on or not.
* **cron**: `0 17 * * 1-5 cd /opt/gas-electric && .venv/bin/python -m gas_electric_response reconcile >> reconcile.log 2>&1` (server time zone must match, or run with `TZ=`).
* **systemd timer**: `OnCalendar=Mon..Fri 17:00`.
The service account must be able to read both report folders; the reconcile job reads (never writes) them.

## Shared drives
Windows hosts can use UNC paths in `.env` (`\\\\server\\share\\...`). Linux/macOS hosts must mount the share and use the mount path. Reports are write-once with a `.sha256`
sidecar; enforce true immutability with share/NTFS permissions (give the service account *create* but not *modify/delete*).

## Containers
A `Dockerfile` is provided as a starting point (not tested by the author): mount `.env`, `config/`, the state folder and the report shares.

## Monitoring
* `GET /health` for liveness. Failed pipelines e-mail GIS admin + EOC ("Automation FAILED ... manual triage required"); a failure *after* the incident row was published is reported as "PARTIALLY FAILED".
* Failed or stuck Grade 1-2 notifications are **retried every 60 s** (while `serve` or `poll` runs) from the layer row, using the PDF already on the share.
* The daily reconcile e-mail is your drift detector: missing PDFs, failed e-mails and submissions with no feature are listed explicitly.
