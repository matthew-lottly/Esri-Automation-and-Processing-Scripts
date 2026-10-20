"""Report generation: Field Ticket -> Location A, Layer Risk Report -> Location B. Layouts come from templates/*.yaml."""
from __future__ import annotations

import importlib
import logging
from datetime import datetime, timezone
from pathlib import Path

from ..models import FeatureRecord, IncidentContext
from ..reference import Services
from ..exceptions import ConfigError
from ..reports.data import ReportData
from ..reports.engine import ReportRenderer
from ..reports.storage import ReportStore
from .base import Stage

log = logging.getLogger(__name__)


class ReportComposer:
    """Owns the two ReportRenderers (A and B), their Jinja2 templates and their write-once stores."""

    @staticmethod
    def data_class(path: str) -> type[ReportData]:
        """'mypkg.reports:MyReportData' -> the class (so users can add template variables without editing this project)."""
        if not path:
            return ReportData
        module, _, cls = path.partition(":")
        try:
            return getattr(importlib.import_module(module), cls)
        except (ImportError, AttributeError) as exc:
            raise ConfigError(f"REPORT_DATA_CLASS '{path}' could not be imported: {exc}") from exc

    def __init__(self, svc: Services):
        st = svc.settings
        cache, data_cls = Path(st.state_db_path).parent, self.data_class(st.report_data_class)
        self.builder_a = ReportRenderer(st.report_template_a, svc, data_cls=data_cls, cache_dir=cache)
        self.builder_b = ReportRenderer(st.report_template_b, svc, data_cls=data_cls, cache_dir=cache)
        self.store_a = ReportStore(st.report_path_a, "Location A (Field Tickets)", st.timezone)
        self.store_b = ReportStore(st.report_path_b, "Location B (Risk Assessments)", st.timezone)


class ReportStage(Stage):
    name = "Generate dual reports"
    description = "Report 1 Field Ticket (with zoomed map) -> Shared Drive A; Report 2 Hazard Assessment -> Shared Drive B"
    external = "Shared drives A and B"
    group = "Reporting & Distribution"

    def __init__(self, svc: Services):
        self.svc = svc
        self.composer = ReportComposer(svc)

    def run(self, ctx: IncidentContext) -> None:
        now, r, c = datetime.now(timezone.utc), ctx.report, self.composer
        try:
            p = c.store_a.write(c.builder_a.render_pdf(ctx, now), r.leak_id, r.reported_at, "field_ticket")
            ctx.report_a_path, ctx.report_a_status = str(p), "OK"
        except Exception as exc:      # a broken report must not stop Report 2 / e-mail
            ctx.report_a_status = "FAILED"
            ctx.flags.append("report A failed")
            log.exception("Report A failed for %s: %s", r.leak_id, exc)
        if ctx.report_b_required:
            try:
                p = c.store_b.write(c.builder_b.render_pdf(ctx, now), r.leak_id, r.reported_at, "risk_assessment")
                ctx.report_b_path, ctx.report_b_status = str(p), "OK"
            except Exception as exc:
                ctx.report_b_status = "FAILED"
                ctx.flags.append("report B failed")
                log.exception("Report B failed for %s: %s", r.leak_id, exc)
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
