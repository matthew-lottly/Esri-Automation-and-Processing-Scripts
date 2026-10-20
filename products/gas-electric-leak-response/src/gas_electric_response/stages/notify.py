"""Distribution: Grade 1/2 risk assessment e-mailed to EOC, electric dispatch and the fire liaison."""
from __future__ import annotations

import logging
from pathlib import Path

from ..models import FeatureRecord, IncidentContext
from ..notify import EmailNotifier
from ..reference import Services
from ..reports.emails import EmailComposer
from .base import Stage

log = logging.getLogger(__name__)


class NotifyStage(Stage):
    name = "Email EOC, dispatch & fire liaison"
    description = "Grades 1-2 only: Report 2 attached, sent immediately"
    external = "SMTP / e-mail"
    group = "Reporting & Distribution"
    limits = "Grade 1-2 only. A failed e-mail is recorded and retried every 60 s until delivered. Provider acceptance is not inbox receipt."

    def __init__(self, svc: Services, notifier: EmailNotifier):
        self.svc, self.n = svc, notifier
        self.composer = EmailComposer(svc)

    def run(self, ctx: IncidentContext) -> None:
        if not ctx.notify_required:
            return
        s, r = self.svc.settings, ctx.report
        to = sorted(set(s.eoc + s.electric_dispatch + s.fire_liaison))
        elec = [h for h in ctx.electric_hits if h.category in ("ignition_source", "conduit")]
        subject = f"[GRADE {int(ctx.grade)} - {ctx.grade.label.upper()}] Gas/Electric hazard {r.leak_id}" + (" (ESCALATION)" if ctx.is_escalation else "")
        text = (f"Joint gas/electric hazard {r.leak_id}\nGrade {int(ctx.grade)} ({ctx.grade.label}); dispatch {ctx.dispatch_code}\n"
                f"Reading: {r.effective_lel_pct:.1f} % LEL at {r.lat:.5f}, {r.lon:.5f}\n"
                f"Electric assets inside plume: {len(elec)} | critical facilities: {len(ctx.facility_hits)} | meters: {ctx.meter_count}\n"
                f"Nearest shutoff valves: {', '.join(v.valve_id for v in ctx.valves) or 'none found'}\n"
                f"Why: {'; '.join(ctx.grade_reasons)}\n"
                + ("\nNOTE: " + "; ".join(ctx.flags) + "\n" if ctx.flags else "")
                + "\nScreening-level automation output - confirm with the field crew and operations.\n")
        attach = [Path(ctx.report_b_path)] if ctx.report_b_status == "OK" and ctx.report_b_path else []
        if not attach:
            text += "\n(Risk assessment PDF could not be written; see incident layer for details.)\n"
        status = "SENT"
        try:
            self.n.send(subject, text, to, html=self.composer.alert_html(ctx), attachments=attach)
        except Exception as exc:          # EmailError, OSError reading the PDF, anything: record it, never skip silently
            status = "FAILED"
            ctx.flags.append("notification failed")
            log.error("Notification failed for %s: %s", r.leak_id, exc)
        ctx.notify_status = status
        f = self.svc.schema.incident_field
        try:
            self.svc.incidents.update([FeatureRecord({f("notify_status"): status, f("quality_flags"): "; ".join(ctx.flags)[:255]}, None, ctx.incident_object_id)])
        except Exception as exc:
            log.error("Could not record notify_status for %s: %s", r.leak_id, exc)
