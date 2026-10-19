"""IncidentPipeline: runs the ordered stages, with fail-safe handling so a gas leak is never silently dropped."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .clients.store import Query
from .exceptions import StageRejected
from .ledger import ProcessedLedger
from .models import FeatureRecord, IncidentContext
from .notify import EmailError, EmailNotifier
from .reference import Services
from .stages.base import Stage
from .survey import SurveyRecordParser

log = logging.getLogger(__name__)


class IncidentPipeline:
    def __init__(self, svc: Services, stages: list[Stage], ledger: ProcessedLedger, notifier: EmailNotifier,
                 rejections_log: Path | None = None):
        self.svc, self.stages, self.ledger, self.notifier = svc, stages, ledger, notifier
        self.parser = SurveyRecordParser(svc.schema)
        self.rejections_log = rejections_log or svc.settings.rejections_log

    # ---- fetching the authoritative record ------------------------------------------------
    def fetch_survey_record(self, global_id: str | None = None, object_id: int | None = None) -> FeatureRecord | None:
        """Never trust webhook content: re-read the submission from the survey layer with our own credentials.
        Hosted and enterprise services format GlobalIDs differently, so several spellings are tried, then the ObjectID."""
        survey = self.svc.survey
        if global_id:
            g = global_id.strip("{}")
            for cand in ("{" + g.upper() + "}", g.lower(), g.upper(), "{" + g.lower() + "}"):
                hits = survey.query(Query(equals={"GlobalID": cand}))
                if hits:
                    return hits[0]
        if object_id is not None:
            hits = survey.query(Query(equals={survey.oid_field: object_id}))
            if hits:
                return hits[0]
        return None

    # ---- main entry ----------------------------------------------------------------------
    def process_record(self, rec: FeatureRecord) -> IncidentContext | None:
        try:
            report = self.parser.parse(rec)
        except Exception as exc:
            log.exception("Could not parse submission OID %s", rec.object_id)
            self.send_alert("[URGENT] A gas-leak submission could not be parsed - manual triage required",
                       f"Survey record OID {rec.object_id} could not be parsed: {exc!r}. Check it manually.",
                       sorted(set(self.svc.settings.gis_admin + self.svc.settings.eoc)))
            raise
        if not self.ledger.claim(report.source_global_id, report.leak_id):
            log.info("Skipping %s: finished or being processed by another worker", report.leak_id)
            return None
        ctx = IncidentContext(report=report)
        try:
            f = self.svc.schema.incident_field
            if self.svc.incidents.count(Query(equals={f("source_global_id"): report.source_global_id})):
                log.info("Skipping %s: incident row already exists", report.leak_id)
                self.ledger.mark(report.source_global_id, report.leak_id, "DONE", "found in incident layer")
                return None
            for st in self.stages:
                log.debug("stage: %s", st.name)
                st.run(ctx)
        except StageRejected as exc:
            ctx.rejected = str(exc)
            self._reject(ctx)
            self.ledger.mark(report.source_global_id, report.leak_id, "REJECTED", ctx.rejected)
            return ctx
        except Exception as exc:        # unexpected: fail SAFE and loud
            log.exception("Pipeline failure for %s", report.leak_id)
            if ctx.published:
                # The incident row exists, so a re-run would skip it. Finish as PARTIAL: alert people, and let
                # retry.py re-send any pending notification from the layer row.
                self.ledger.mark(report.source_global_id, report.leak_id, "DONE", f"PARTIAL after publish: {exc!r}")
                self._alert_failure(ctx, exc, partial=True)
                return ctx
            self.ledger.mark(report.source_global_id, report.leak_id, "ERROR", repr(exc))
            self._alert_failure(ctx, exc)
            raise
        self.ledger.mark(report.source_global_id, report.leak_id, "DONE", f"grade {int(ctx.grade)}")
        return ctx

    # ---- failure handling ----------------------------------------------------------------
    def _reject(self, ctx: IncidentContext) -> None:
        r = ctx.report
        self.rejections_log.parent.mkdir(parents=True, exist_ok=True)
        with open(self.rejections_log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "leak_id": r.leak_id, "global_id": r.source_global_id,
                                 "reason": ctx.rejected, "lel": r.lel_pct, "ppm": r.ppm, "lat": r.lat, "lon": r.lon}) + "\n")
        self.send_alert(f"[Gas/Electric] Submission {r.leak_id} REJECTED by validation",
                   f"Submission {r.leak_id} (crew {r.crew_badge or 'n/a'}) was rejected:\n  {ctx.rejected}\n\n"
                   "It was NOT graded or dispatched by automation. TREAT THE REPORTED LEAK AS LIVE per normal procedure, "
                   "and contact the crew to correct/resubmit.\n", sorted(set(self.svc.settings.gis_admin + self.svc.settings.eoc)))

    def _alert_failure(self, ctx: IncidentContext, exc: Exception, partial: bool = False) -> None:
        r, s = ctx.report, self.svc.settings
        tail = ("The incident row WAS published; some later step (reports/notification) failed - check the layer row's status fields. "
                "Pending notifications are retried automatically." if partial else "No hazard grade was produced. Triage manually.")
        self.send_alert(f"[URGENT] Automation {'PARTIALLY ' if partial else ''}FAILED for gas leak {r.leak_id} - manual triage required",
                   f"The pipeline raised an error while processing leak {r.leak_id} at {r.lat:.5f}, {r.lon:.5f} "
                   f"({r.effective_lel_pct or r.lel_pct} % LEL, crew {r.crew_badge or 'n/a'}).\nError: {exc!r}\n\n{tail}\n",
                   sorted(set(s.gis_admin + s.eoc)))

    def send_alert(self, subject: str, text: str, to: list[str]) -> None:
        try:
            self.notifier.send(subject, text, to)
        except EmailError as exc:
            log.error("Could not send alert '%s': %s", subject, exc)
