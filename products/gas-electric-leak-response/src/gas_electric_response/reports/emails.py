"""HTML e-mail bodies, rendered from the same kind of Jinja2 templates as the PDF reports (templates/email_*.html)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..models import IncidentContext
from ..reference import Services
from .data import ReportData
from .engine import TemplateEnvironment


class EmailComposer:
    """Renders the alert e-mail and the daily executive summary. Missing templates simply mean 'plain text only'."""

    ALERT, DAILY = "email_alert.html", "email_daily_summary.html"

    def __init__(self, svc: Services):
        self.svc = svc
        self.tenv = TemplateEnvironment(Path(svc.settings.template_dir))

    def _has(self, name: str) -> bool:
        return (self.tenv.dir / name).exists()

    def alert_html(self, ctx: IncidentContext) -> str | None:
        if not self._has(self.ALERT):
            return None
        data = ReportData(ctx, self.svc, datetime.now(timezone.utc))
        variables = {**data.namespace(), **{k: fn() for k, fn in data.sources().items()}, "escalation": ctx.is_escalation}
        return self.tenv.load(self.ALERT).render(**variables)

    def daily_html(self, summary, subject: str) -> str | None:
        if not self._has(self.DAILY):
            return None
        return self.tenv.load(self.DAILY).render(s=summary, subject=subject, tz=self.svc.settings.timezone)
