"""FastAPI webhook receiver. Run with:  python -m gas_electric_response serve"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from ..bootstrap import Runtime
from ..config import Settings
from ..exceptions import ConfigError
from ..retry import NotificationRetrier
from .payload import WebhookPayloadParser
from .worker import SubmissionWorker

log = logging.getLogger(__name__)


class WebhookAuthenticator:
    """Token (query string or header) and optional HMAC-SHA256 body signature, all constant-time."""

    def __init__(self, settings: Settings):
        if not settings.webhook_token:
            raise ConfigError("WEBHOOK_TOKEN must be set; refusing to start an unauthenticated webhook endpoint")
        self.s = settings

    def check(self, request: Request, raw_body: bytes) -> None:
        supplied = request.query_params.get("token") or request.headers.get("X-Webhook-Token", "")
        if not secrets.compare_digest(supplied.encode(), self.s.webhook_token.encode()):
            raise HTTPException(401, "invalid token")
        if self.s.webhook_hmac_secret:
            sig = request.headers.get(self.s.webhook_hmac_header, "")
            want = hmac.new(self.s.webhook_hmac_secret.encode(), raw_body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig.removeprefix("sha256=").lower(), want):
                raise HTTPException(401, "invalid signature")


class WebhookApp:
    """Builds the FastAPI application around a Runtime."""

    MAX_BODY = 1_000_000

    def __init__(self, runtime: Runtime, settings: Settings):
        self.rt, self.settings = runtime, settings
        self.auth = WebhookAuthenticator(settings)
        self.parser = WebhookPayloadParser()
        self.worker = SubmissionWorker(runtime)
        self.retrier = NotificationRetrier(runtime)
        self._stop = threading.Event()

    def _retry_loop(self) -> None:
        while not self._stop.wait(60):
            try:
                self.retrier.run_once()
            except Exception:
                log.exception("notification retry failed")

    @asynccontextmanager
    async def _lifespan(self, _: FastAPI):
        self.worker.start()
        threading.Thread(target=self._retry_loop, name="notify-retry", daemon=True).start()
        yield
        self._stop.set()
        self.worker.stop()

    def build(self) -> FastAPI:
        app = FastAPI(title="Gas/Electric leak response webhook", docs_url=None, redoc_url=None, openapi_url=None, lifespan=self._lifespan)

        @app.get("/health")
        def health() -> dict:
            return {"status": "ok", "queued": self.worker.q.qsize()}

        @app.post("/webhooks/survey123", status_code=202)
        async def survey123(request: Request) -> dict:
            raw = await request.body()
            self.auth.check(request, raw)
            if len(raw) > self.MAX_BODY:
                raise HTTPException(413, "payload too large")
            try:
                payload = await request.json()
            except ValueError:
                raise HTTPException(400, "body is not JSON")
            ptr = self.parser.extract_pointer(payload)
            if ptr is None:
                raise HTTPException(422, "no feature GlobalID/ObjectID in payload")
            if not self.parser.is_new_submission(ptr.event):
                return {"status": "ignored", "reason": f"event '{ptr.event}' is not a new submission"}
            self.worker.submit(ptr)
            return {"status": "queued"}

        return app
