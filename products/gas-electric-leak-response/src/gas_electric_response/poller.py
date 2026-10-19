"""Polling fallback: use it when Esri cannot reach your server (no public HTTPS endpoint) or with Field Maps layers."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from .bootstrap import Runtime
from .clients.store import Query
from .retry import retry_notifications

log = logging.getLogger(__name__)


MAX_ATTEMPTS = 6


def poll_once(rt: Runtime, lookback_minutes: int = 180) -> int:
    """Process every recent survey record not yet handled, then retry pending notifications.
    Records that keep failing are escalated to people (GAVE_UP) instead of being skipped silently."""
    sch = rt.services.schema.survey_layer
    now = datetime.now(timezone.utc)
    recs = rt.services.survey.query(Query(time_field=sch.created_field, time_from=now - timedelta(minutes=lookback_minutes),
                                          time_to=now, order_by=f"{sch.created_field} ASC"))
    done = 0
    for rec in recs:
        gid = str(next((v for k, v in rec.attributes.items() if k.lower() == "globalid"), "")).strip("{}").lower()
        led = rt.ledger.get(gid)
        if led and led[0] in rt.ledger.TERMINAL:
            continue
        if led and led[0] == "ERROR" and led[1] >= MAX_ATTEMPTS:
            rt.ledger.mark(gid, str(rec.attributes.get(rt.services.schema.survey_layer.leak_id, gid)), "GAVE_UP", "too many failures")
            st = rt.services.settings
            rt.pipeline.send_alert(f"[URGENT] Automation GAVE UP on gas-leak submission {gid}",
                                   f"Submission {gid} failed {led[1]} times and will not be retried automatically. Triage it manually.",
                                   sorted(set(st.gis_admin + st.eoc)))
            continue
        try:
            if rt.pipeline.process_record(rec) is not None:
                done += 1
        except Exception:
            log.exception("poll: record %s failed (will retry next cycle, max 4 attempts)", gid)
    try:
        retry_notifications(rt)
    except Exception:
        log.exception("notification retry failed")
    return done


def poll_forever(rt: Runtime, interval_s: int = 60, lookback_minutes: int = 180) -> None:
    log.info("Polling every %ss (lookback %s min)", interval_s, lookback_minutes)
    while True:
        try:
            n = poll_once(rt, lookback_minutes)
            if n:
                log.info("processed %d submission(s)", n)
        except Exception:
            log.exception("poll cycle failed")
        time.sleep(interval_s)
