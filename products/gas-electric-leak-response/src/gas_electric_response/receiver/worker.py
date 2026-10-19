"""Single-threaded work queue: serialises submissions so concurrent webhooks cannot race the duplicate check."""
from __future__ import annotations

import logging
import queue
import threading

from ..bootstrap import Runtime
from .payload import Pointer

log = logging.getLogger(__name__)


class _Retry:
    def __init__(self, ptr: Pointer, attempt: int):
        self.ptr, self.attempt = ptr, attempt


class SubmissionWorker:
    def __init__(self, runtime: Runtime, retry_delays: tuple[float, ...] = (5, 15, 45)):
        self.rt, self.delays = runtime, retry_delays
        self.q: "queue.Queue" = queue.Queue()
        self.thread = threading.Thread(target=self._loop, name="submission-worker", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.q.put(None)
        self.thread.join(timeout=10)

    def submit(self, ptr: Pointer) -> None:
        self.q.put(ptr)

    def _loop(self) -> None:
        while True:
            item = self.q.get()
            if item is None:
                return
            if isinstance(item, _Retry):
                self.handle(item.ptr, item.attempt)
            else:
                self.handle(item)

    def handle(self, ptr: Pointer, attempt: int = 0) -> None:
        """One attempt. A failure schedules a later retry on a timer so other submissions are never blocked."""
        try:
            rec = self.rt.pipeline.fetch_survey_record(ptr.global_id, ptr.object_id)
            if rec is None:        # the layer may lag the webhook by a few seconds
                raise LookupError(f"submission {ptr.global_id or ptr.object_id} not found in survey layer yet")
            self.rt.pipeline.process_record(rec)
        except Exception as exc:
            log.warning("attempt %d failed for %s: %s", attempt + 1, ptr.global_id or ptr.object_id, exc)
            if attempt < len(self.delays):
                t = threading.Timer(self.delays[attempt], lambda: self.q.put(_Retry(ptr, attempt + 1)))
                t.daemon = True
                t.start()
            else:
                log.error("giving up on %s after %d attempts; run `poll` as a safety net (it re-reads the layer)",
                          ptr.global_id or ptr.object_id, attempt + 1)
                st = self.rt.services.settings
                self.rt.pipeline.send_alert(f"[URGENT] Webhook submission {ptr.global_id or ptr.object_id} could not be processed",
                                            f"Gave up after {attempt + 1} attempts: {exc!r}. Check the survey layer and process it manually.",
                                            sorted(set(st.gis_admin + st.eoc)))
