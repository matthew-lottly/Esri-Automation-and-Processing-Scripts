"""Daily 17:00 reconciliation: layer audit + shared-drive verification + executive summary e-mail."""
from __future__ import annotations

import html
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .clients.store import Query
from .notify import EmailError, EmailNotifier
from .reference import Services

log = logging.getLogger(__name__)
FT_PER_M = 3.280839895

RECONCILE_STEPS = [
    ("Query incident layer", "records processed since the previous run (rolling window; weekends and after-hours included)"),
    ("Query survey layer", "count raw field submissions in the same window"),
    ("Verify shared drives A and B", "check every recorded PDF exists; tally PDFs written today"),
    ("Compile metrics", "totals, grade breakdown, joint-utility hazards, rejections"),
    ("Detect discrepancies", "feature created but report missing/failed, submissions with no feature, failed e-mails"),
    ("Send executive summary", "Director of Gas Ops, Chief Safety Officer, GIS Systems Administrator"),
]


@dataclass
class DailySummary:
    day: date
    window_local: tuple[datetime, datetime]
    raw_submissions: int = 0
    incident_rows: int = 0
    duplicates: int = 0
    reports_a_ok: int = 0
    reports_b_ok: int = 0
    pdfs_a_written: int = 0
    pdfs_b_written: int = 0
    by_grade: dict[int, int] = field(default_factory=lambda: {1: 0, 2: 0, 3: 0})
    joint_hazards: list[str] = field(default_factory=list)
    rejections: int = 0
    escalations: list[str] = field(default_factory=list)
    ungraded: int = 0
    discrepancies: list[str] = field(default_factory=list)


class DailyReconciler:
    def __init__(self, svc: Services, notifier: EmailNotifier, cutoff: time = time(17, 0)):
        self.svc, self.n, self.cutoff = svc, notifier, cutoff
        self.tz = ZoneInfo(svc.settings.timezone)

    def window(self, day: date) -> tuple[datetime, datetime]:
        """Calendar-day window 00:00 -> cutoff (used for explicit back-fills with --date)."""
        return (datetime.combine(day, time(0, 0, 0), self.tz), datetime.combine(day, self.cutoff, self.tz))

    @property
    def _state_file(self) -> Path:
        return Path(self.svc.settings.state_db_path).parent / "reconcile_state.json"

    def rolling_window(self, now: datetime | None = None) -> tuple[datetime, datetime]:
        """From the end of the previous successful run to today's cutoff, so nothing falls between runs
        (after-hours leaks, weekends, skipped days). First run: from local midnight."""
        now = (now or datetime.now(self.tz)).astimezone(self.tz)
        end = min(datetime.combine(now.date(), self.cutoff, self.tz), now)
        start = datetime.combine(now.date(), time(0, 0, 0), self.tz)
        if self._state_file.exists():
            try:
                last = datetime.fromisoformat(json.loads(self._state_file.read_text())["last_end"]).astimezone(self.tz)
                if last < end:
                    start = last
            except (ValueError, KeyError):
                log.warning("ignoring unreadable %s", self._state_file)
        return start, end

    @staticmethod
    def _norm_gid(g) -> str:
        return str(g or "").strip("{}").lower()

    def collect(self, day: date, window: tuple[datetime, datetime] | None = None) -> DailySummary:
        s = DailySummary(day, window or self.window(day))
        start, end = s.window_local
        f, sch = self.svc.schema.incident_field, self.svc.schema
        utc = lambda d: d.astimezone(timezone.utc)
        rows = self.svc.incidents.query(Query(time_field=f("processed_at"), time_from=utc(start), time_to=utc(end), return_geometry=False))
        s.incident_rows = len(rows)
        close_m = float(self.svc.params.joint_hazard.close_distance_ft) / FT_PER_M
        incident_gids = set()
        for r in rows:
            a = r.attributes
            incident_gids.add(self._norm_gid(a.get(f("source_global_id"))))
            lid = a.get(f("leak_id"))
            is_dup = bool(a.get(f("is_duplicate")))
            escalation = is_dup and (a.get(f("report_b_status")) or "") != "NOT_REQUIRED"
            if is_dup:
                s.duplicates += 1
            if escalation:
                s.escalations.append(f"{lid} escalated {a.get(f('duplicate_of'))} (Grade {a.get(f('grade'))}, {a.get(f('lel_pct'))} % LEL)")
            if not is_dup:
                g = a.get(f("grade"))
                if g in (1, 2, 3):
                    s.by_grade[int(g)] += 1
                else:
                    s.ungraded += 1
            if not is_dup or escalation:
                d = a.get(f("elec_min_dist_m"))
                if d is not None and d <= close_m and (a.get(f("elec_ignition_count")) or 0) > 0:
                    s.joint_hazards.append(f"{lid}: gas detected {d * FT_PER_M:.0f} ft from an energized pad-mount/switchgear asset (Grade {a.get(f('grade'))})")
            self._check_reports(a, lid, s)
            if (a.get(f("notify_status")) or "") in ("FAILED", "PENDING"):
                s.discrepancies.append(f"{lid}: notification {a.get(f('notify_status'))}")
        s.pdfs_a_written = self._count_written(self.svc.settings.report_path_a, start, end)
        s.pdfs_b_written = self._count_written(self.svc.settings.report_path_b, start, end)
        sf = self.svc.schema.survey_layer
        subs = self.svc.survey.query(Query(time_field=sf.created_field, time_from=utc(start), time_to=utc(end), return_geometry=False))
        s.raw_submissions = len(subs)
        rej = self.svc.settings.rejections_log
        rejected_gids = set()
        if rej.exists():
            for line in rej.read_text().splitlines():
                try:
                    j = json.loads(line)
                    at = datetime.fromisoformat(j["at"])
                except (ValueError, KeyError):
                    continue
                if start <= at.astimezone(self.tz) <= end:
                    s.rejections += 1
                    rejected_gids.add(self._norm_gid(j.get("global_id")))
        for r in subs:
            gid = self._norm_gid(next((v for k, v in r.attributes.items() if k.lower() == "globalid"), ""))
            if gid and gid not in incident_gids and gid not in rejected_gids:
                s.discrepancies.append(f"submission {r.attributes.get(sf.leak_id) or gid}: no incident feature (webhook missed or rejected?)")
        if s.pdfs_a_written != s.reports_a_ok:
            s.discrepancies.append(f"Location A holds {s.pdfs_a_written} PDF(s) written today but the layer records {s.reports_a_ok} OK")
        if s.pdfs_b_written != s.reports_b_ok:
            s.discrepancies.append(f"Location B holds {s.pdfs_b_written} PDF(s) written today but the layer records {s.reports_b_ok} OK")
        return s

    def _check_reports(self, a, lid, s: DailySummary) -> None:
        f = self.svc.schema.incident_field
        for kind, st_key, path_key, required in (("A", "report_a_status", "report_a_path", True),
                                                 ("B", "report_b_status", "report_b_path", (a.get(f("report_b_status")) or "") != "NOT_REQUIRED")):
            if not required:
                continue
            status, path = a.get(f(st_key)), a.get(f(path_key))
            if status == "OK" and path and Path(path).exists() and Path(path).stat().st_size > 0:
                if kind == "A":
                    s.reports_a_ok += 1
                else:
                    s.reports_b_ok += 1
            else:
                s.discrepancies.append(f"{lid}: feature created but Report {kind} {'missing on drive' if status == 'OK' else (status or 'not recorded')}")

    @staticmethod
    def _count_written(root: str, start: datetime, end: datetime) -> int:
        n = 0
        for p in Path(root).glob("*/*.pdf") if root and Path(root).exists() else []:
            if start <= datetime.fromtimestamp(p.stat().st_mtime, start.tzinfo) <= end:
                n += 1
        return n

    # ---- presentation ----------------------------------------------------------------
    def render(self, s: DailySummary) -> tuple[str, str, str]:
        d = s.day.isoformat()
        subject = f"Gas/Electric daily summary {d}: {s.by_grade[1]} critical, {s.by_grade[2]} urgent, {s.by_grade[3]} monitored" + \
                  (f" - {len(s.discrepancies)} DISCREPANCY(IES)" if s.discrepancies else "")
        lines = [f"Gas/Electric Joint Hazard - Daily Executive Summary for {d}",
                 f"Window: {s.window_local[0]:%Y-%m-%d %H:%M} - {s.window_local[1]:%Y-%m-%d %H:%M} {s.window_local[1]:%Z}", "",
                 f"Raw field inspections received:          {s.raw_submissions}",
                 f"Incident rows processed (incl. {s.duplicates} duplicates): {s.incident_rows}",
                 f"Hazard assessments generated (Report 2): {s.reports_b_ok} (PDFs on drive B today: {s.pdfs_b_written})",
                 f"Field tickets generated (Report 1):      {s.reports_a_ok} (PDFs on drive A today: {s.pdfs_a_written})",
                 f"Submissions rejected by validation:      {s.rejections}", "",
                 f"Grade 1 Critical: {s.by_grade[1]}   Grade 2 Urgent: {s.by_grade[2]}   Grade 3 Monitored: {s.by_grade[3]}"
                 + (f"   (ungraded rows: {s.ungraded})" if s.ungraded else ""),
                 f"Escalations (worse duplicate readings): {len(s.escalations)}" + "".join(f"\n  - {x}" for x in s.escalations), "",
                 "Joint-utility hazards:"] + ([f"  - {j}" for j in s.joint_hazards] or ["  none"]) + ["", "Discrepancies:"] + \
                ([f"  - {x}" for x in s.discrepancies] or ["  none - all features have matching reports"])
        text = "\n".join(lines)
        rows = "".join(f"<li>{html.escape(x)}</li>" for x in s.discrepancies) or "<li>none</li>"
        jh = "".join(f"<li>{html.escape(x)}</li>" for x in s.joint_hazards) or "<li>none</li>"
        body = (f"<h2>Gas/Electric Joint Hazard - Daily Summary {d}</h2><table cellpadding=4 border=1 style='border-collapse:collapse'>"
                f"<tr><td>Raw field inspections</td><td>{s.raw_submissions}</td></tr><tr><td>Hazard assessments (Report 2)</td><td>{s.reports_b_ok}</td></tr>"
                f"<tr><td>Field tickets (Report 1)</td><td>{s.reports_a_ok}</td></tr><tr><td>Duplicates</td><td>{s.duplicates}</td></tr>"
                f"<tr><td>Rejected</td><td>{s.rejections}</td></tr><tr><td>Grade 1 / 2 / 3</td><td>{s.by_grade[1]} / {s.by_grade[2]} / {s.by_grade[3]}</td></tr></table>"
                f"<h3>Joint-utility hazards</h3><ul>{jh}</ul><h3>Discrepancies</h3><ul>{rows}</ul>")
        return subject, text, body

    def run(self, day: date | None = None, send: bool = True) -> DailySummary:
        """`day` given => calendar-day back-fill (00:00 -> cutoff), state untouched. Otherwise a rolling window from the
        previous successful run, and the new end is stored only after the e-mail was sent."""
        window = self.window(day) if day else self.rolling_window()
        s = self.collect(window[1].date(), window)
        subject, text, body = self.render(s)
        st = self.svc.settings
        out = Path(st.state_db_path).parent / "daily_summaries"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{window[1]:%Y-%m-%d}.txt").write_text(text, encoding="utf-8")
        if send:
            to = sorted(set(st.director_gas + st.chief_safety + st.gis_admin))
            try:
                self.n.send(subject, text, to, html=body)
            except EmailError as exc:
                log.error("Daily summary e-mail failed: %s", exc)
                raise
        if day is None and send:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(json.dumps({"last_end": window[1].isoformat()}))
        return s
