"""Retry pending/failed Grade 1-2 notifications from the layer row (the PDF is already on the shared drive)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .bootstrap import Runtime
from .clients.store import EpochMs, Query
from .models import FeatureRecord

log = logging.getLogger(__name__)


class NotificationRetrier:
    """Re-sends e-mails whose NOTIFY_STATUS is FAILED, or PENDING for longer than `min_age_minutes`
    (so a pipeline that is still running is not raced). The PDF is already on the shared drive."""

    def __init__(self, rt: Runtime, max_age_hours: float = 6, min_age_minutes: float = 3):
        self.rt, self.max_age, self.min_age = rt, max_age_hours, min_age_minutes

    def _compose(self, a, f) -> tuple[str, str, list[Path]]:
        path = a.get(f("report_b_path"))
        attach = [Path(path)] if path and Path(path).exists() else []
        grade = a.get(f("grade"))
        subject = f"[GRADE {grade} - RETRY] Gas/Electric hazard {a.get(f('leak_id'))}"
        text = (f"RETRY: the original notification for {a.get(f('leak_id'))} was not delivered.\n"
                f"Grade {grade} ({a.get(f('grade_label'))}); dispatch {a.get(f('dispatch_code'))}; reading {a.get(f('max_lel_pct'))} % LEL\n"
                f"Nearest valves: {a.get(f('valve_ids')) or 'none found'}\n"
                f"Electric ignition assets in plume: {a.get(f('elec_ignition_count'))}; critical facilities: {a.get(f('critical_fac_count'))}\n"
                + ("" if attach else "(Risk assessment PDF not available - see the incident layer.)\n"))
        return subject, text, attach

    def run_once(self) -> int:
        svc, f = self.rt.services, self.rt.services.schema.incident_field
        now = datetime.now(timezone.utc)
        rows = svc.incidents.query(Query(time_field=f("processed_at"), time_from=now - timedelta(hours=self.max_age), return_geometry=False))
        st, sent = svc.settings, 0
        for r in rows:
            a = r.attributes
            status = a.get(f("notify_status"))
            if status not in ("FAILED", "PENDING"):
                continue
            processed = EpochMs.to_datetime(a.get(f("processed_at")))
            if status == "PENDING" and processed and now - processed < timedelta(minutes=self.min_age):
                continue
            subject, text, attach = self._compose(a, f)
            try:
                self.rt.notifier.send(subject, text, sorted(set(st.eoc + st.electric_dispatch + st.fire_liaison)), attachments=attach)
            except Exception as exc:
                log.error("Notification retry for %s failed: %s", a.get(f("leak_id")), exc)
                continue
            svc.incidents.update([FeatureRecord({f("notify_status"): "SENT"}, None, r.object_id)])
            sent += 1
        return sent
