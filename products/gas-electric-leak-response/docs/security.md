# Security notes

* **Secrets** only in `.env` (gitignored). `.env.example` contains placeholders only. Rotate anything you ever commit by mistake.
* **Webhook authentication**: `WEBHOOK_TOKEN` (constant-time compare; app refuses to start without it) plus optional HMAC-SHA256 of the raw body.
  Survey123's own signing mechanism was **not verified** by the author - confirm what your tenant sends and set `WEBHOOK_HMAC_HEADER` accordingly.
* **Never trust payload content**: the receiver takes only an ID from the body and re-reads the submission from your survey layer with service-account credentials,
  so a forged request cannot inject readings. It can still trigger a re-read, so keep the token secret and rate-limit at the proxy.
* **Least privilege**: the service account needs edit rights on the two output layers and read-only on everything else.
* **PII**: crew badge numbers and incident locations are stored in layers and PDFs; apply your records-retention and access policies to those shares.
* **Outbound**: only your ArcGIS portal, `api.weather.gov`, your SMTP server and (optionally) your 811 provider.
