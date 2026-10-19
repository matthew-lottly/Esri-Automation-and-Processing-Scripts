"""Report generation: Field Ticket -> Location A, Layer Risk Report -> Location B."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..clients.store import Query
from ..models import FeatureRecord, IncidentContext
from ..reference import Services
from ..reports.pdf import FieldTicketPDF, RiskAssessmentPDF
from ..reports.storage import ReportStore, ReportStoreError
from .base import Stage

log = logging.getLogger(__name__)


class ReportStage(Stage):
    name = "Generate dual reports"
    description = "Report 1 Field Ticket -> Shared Drive A; Report 2 Hazard Assessment -> Shared Drive B"
    external = "Shared drives A and B"
    group = "Reporting & Distribution"
    skip_when_rejected = True

    def __init__(self, svc: Services):
        self.svc = svc
        self.store_a = ReportStore(svc.settings.report_path_a, "Location A (Field Tickets)", svc.settings.timezone)
        self.store_b = ReportStore(svc.settings.report_path_b, "Location B (Risk Assessments)", svc.settings.timezone)
        self.pdf_a, self.pdf_b = FieldTicketPDF(svc.settings.timezone), RiskAssessmentPDF(svc.settings.timezone)

    def run(self, ctx: IncidentContext) -> None:
        now = datetime.now(timezone.utc)
        r = ctx.report
        try:
            p = self.store_a.write(self.pdf_a.build(ctx, now), r.leak_id, r.reported_at, "field_ticket")
            ctx.report_a_path, ctx.report_a_status = str(p), "OK"
        except Exception as exc:      # a broken report must not stop Report 2 / e-mail
            ctx.report_a_status = "FAILED"
            ctx.flags.append("report A failed")
            log.error("Report A failed for %s: %s", r.leak_id, exc)
        if ctx.report_b_required:
            try:
                p = self.store_b.write(self.pdf_b.build(ctx, now), r.leak_id, r.reported_at, "risk_assessment")
                ctx.report_b_path, ctx.report_b_status = str(p), "OK"
            except Exception as exc:
                ctx.report_b_status = "FAILED"
                ctx.flags.append("report B failed")
                log.error("Report B failed for %s: %s", r.leak_id, exc)
        try:
            self._record_status(ctx)
        except Exception as exc:
            log.error("Could not record report status for %s: %s", r.leak_id, exc)

    def _record_status(self, ctx: IncidentContext) -> None:
        f = self.svc.schema.incident_field
        upd = {f("report_a_status"): ctx.report_a_status, f("report_a_path"): ctx.report_a_path[:255],
               f("report_b_status"): ctx.report_b_status, f("report_b_path"): ctx.report_b_path[:255],
               f("quality_flags"): "; ".join(ctx.flags)[:255]}
        self.svc.incidents.update([FeatureRecord(upd, None, ctx.incident_object_id)])
