"""FastAPI webhook receiver. Run with:  python -m gas_electric_response serve"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from ..bootstrap import Runtime
from ..config import Settings
from ..exceptions import ConfigError
from .payload import extract_pointer, is_new_submission
from .worker import SubmissionWorker

log = logging.getLogger(__name__)
MAX_BODY = 1_000_000


def create_app(runtime: Runtime, settings: Settings) -> FastAPI:
    if not settings.webhook_token:
        raise ConfigError("WEBHOOK_TOKEN must be set; refusing to start an unauthenticated webhook endpoint")
    worker = SubmissionWorker(runtime)

    import threading

    stop = threading.Event()

    def _retry_loop() -> None:
        from ..retry import retry_notifications

        while not stop.wait(60):
            try:
                retry_notifications(runtime)
            except Exception:
                log.exception("notification retry failed")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        worker.start()
        threading.Thread(target=_retry_loop, name="notify-retry", daemon=True).start()
        yield
        stop.set()
        worker.stop()

    app = FastAPI(title="Gas/Electric leak response webhook", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "queued": worker.q.qsize()}

    @app.post("/webhooks/survey123", status_code=202)
    async def survey123(request: Request) -> dict:
        supplied = request.query_params.get("token") or request.headers.get("X-Webhook-Token", "")
        if not secrets.compare_digest(supplied.encode(), settings.webhook_token.encode()):
            raise HTTPException(401, "invalid token")
        raw = await request.body()
        if len(raw) > MAX_BODY:
            raise HTTPException(413, "payload too large")
        if settings.webhook_hmac_secret:
            sig = request.headers.get(settings.webhook_hmac_header, "")
            want = hmac.new(settings.webhook_hmac_secret.encode(), raw, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig.removeprefix("sha256=").lower(), want):
                raise HTTPException(401, "invalid signature")
        try:
            payload = await request.json()
        except ValueError:
            raise HTTPException(400, "body is not JSON")
        ptr = extract_pointer(payload)
        if ptr is None:
            raise HTTPException(422, "no feature GlobalID/ObjectID in payload")
        if not is_new_submission(ptr.event):
            return {"status": "ignored", "reason": f"event '{ptr.event}' is not a new submission"}
        worker.submit(ptr)
        return {"status": "queued"}

    return app
