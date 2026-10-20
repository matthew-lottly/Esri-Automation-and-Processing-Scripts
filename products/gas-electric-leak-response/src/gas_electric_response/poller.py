"""Polling fallback: use it when Esri cannot reach your server (no public HTTPS endpoint) or with Field Maps layers."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from .bootstrap import Runtime
from .clients.store import Query
from .retry import NotificationRetrier

log = logging.getLogger(__name__)


class SubmissionPoller:
    """Processes recent survey records that no worker has finished, then retries pending notifications.
    Records that keep failing are escalated to people (GAVE_UP) instead of being skipped silently."""

    MAX_ATTEMPTS = 6

    def __init__(self, rt: Runtime, lookback_minutes: int = 180):
        self.rt, self.lookback = rt, lookback_minutes
        self.retrier = NotificationRetrier(rt)

    @staticmethod
    def _global_id(rec) -> str:
        return str(next((v for k, v in rec.attributes.items() if k.lower() == "globalid"), "")).strip("{}").lower()

    def _give_up(self, gid: str, rec, attempts: int) -> None:
        rt = self.rt
        rt.ledger.mark(gid, str(rec.attributes.get(rt.services.schema.survey_layer.leak_id, gid)), "GAVE_UP", "too many failures")
        st = rt.services.settings
        rt.pipeline.send_alert(f"[URGENT] Automation GAVE UP on gas-leak submission {gid}",
                               f"Submission {gid} failed {attempts} times and will not be retried automatically. Triage it manually.",
                               sorted(set(st.gis_admin + st.eoc)))

    def poll_once(self) -> int:
        rt, sch = self.rt, self.rt.services.schema.survey_layer
        now = datetime.now(timezone.utc)
        recs = rt.services.survey.query(Query(time_field=sch.created_field, time_from=now - timedelta(minutes=self.lookback),
                                              time_to=now, order_by=f"{sch.created_field} ASC"))
        done = 0
        for rec in recs:
            gid = self._global_id(rec)
            led = rt.ledger.get(gid)
            if led and led[0] in rt.ledger.TERMINAL:
                continue
            if led and led[0] == "ERROR" and led[1] >= self.MAX_ATTEMPTS:
                self._give_up(gid, rec, led[1])
                continue
            try:
                if rt.pipeline.process_record(rec) is not None:
                    done += 1
            except Exception:
                log.exception("poll: record %s failed (will retry next cycle)", gid)
        try:
            self.retrier.run_once()
        except Exception:
            log.exception("notification retry failed")
        return done

    def run_forever(self, interval_s: int = 60) -> None:
        log.info("Polling every %ss (lookback %s min)", interval_s, self.lookback)
        while True:
            try:
                n = self.poll_once()
                if n:
                    log.info("processed %d submission(s)", n)
            except Exception:
                log.exception("poll cycle failed")
            time.sleep(interval_s)
