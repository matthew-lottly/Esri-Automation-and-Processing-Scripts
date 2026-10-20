"""Command line. Every command is a class; `CommandLineApp` wires them to argparse."""
from __future__ import annotations

import abc
import argparse
import sys
import tempfile
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from .bootstrap import DemoRuntimeFactory, ProductionRuntimeFactory, Runtime
from .clients.store import Query
from .config import Settings
from .console import ConsoleUI
from .exceptions import ConfigError
from .logging_setup import LoggingConfigurator


class Command(abc.ABC):
    name: str = ""
    help: str = ""

    def __init__(self, ui: ConsoleUI):
        self.ui = ui

    def add_arguments(self, p: argparse.ArgumentParser) -> None:
        pass

    @abc.abstractmethod
    def run(self, args: argparse.Namespace) -> int: ...

    @staticmethod
    def production_runtime(args) -> Runtime:
        return ProductionRuntimeFactory(Settings.from_env(args.env_file)).create()


class DemoCommand(Command):
    name, help = "demo", "offline end-to-end run on synthetic data (no Esri account needed)"

    def add_arguments(self, p):
        p.add_argument("--out", default="./demo_output")
        p.add_argument("--config", default="./config")
        p.add_argument("--sample", default="./sample_data")

    def run(self, args) -> int:
        from .reconcile import DailyReconciler

        out = Path(args.out)
        rt = DemoRuntimeFactory(out, args.config, args.sample).create()
        self.ui.banner("Gas / Electric Joint Hazard Response", "offline demo • synthetic data • no Esri account or network required")
        rows = []
        with self.ui.c.status("Running submissions through the pipeline…"):
            for rec in rt.services.survey.query(Query()):
                rows.append((str(rec.attributes["Leak_ID"]), rt.pipeline.process_record(rec)))
        self.ui.results(rows)
        recon = DailyReconciler(rt.services, rt.notifier, cutoff=time(23, 59, 59))
        s = recon.run(datetime.now(ZoneInfo(rt.services.settings.timezone)).date(), send=True)
        self.ui.ok(f"Daily summary: {s.raw_submissions} submissions, grades {s.by_grade}, "
                   f"{len(s.discrepancies)} discrepancies")
        self.ui.info(f"Open the PDFs in [bold]{out}/A_field_tickets[/bold] and [bold]{out}/B_risk_assessments[/bold]; e-mails are in [bold]{out}/outbox[/bold]")
        return 0


class CheckCommand(Command):
    name, help = "check", "read-only verification of credentials, layers and fields"

    def run(self, args) -> int:
        from .diagnostics import SetupDiagnostics

        bad = self.ui.checks(SetupDiagnostics(self.production_runtime(args).services).run())
        (self.ui.fail if bad else self.ui.ok)(f"{bad} check(s) failed - fix layer fields or config/layer_schema.yaml." if bad else "All checks passed.")
        return 1 if bad else 0


class ServeCommand(Command):
    name, help = "serve", "run the webhook receiver"

    def add_arguments(self, p):
        p.add_argument("--host", default="0.0.0.0")
        p.add_argument("--port", type=int, default=8080)

    def run(self, args) -> int:
        import uvicorn

        from .receiver.app import WebhookApp

        settings = Settings.from_env(args.env_file)
        rt = ProductionRuntimeFactory(settings).create()
        uvicorn.run(WebhookApp(rt, settings).build(), host=args.host, port=args.port, log_level=settings.log_level.lower())
        return 0


class PollCommand(Command):
    name, help = "poll", "poll the survey layer instead of (or alongside) webhooks"

    def add_arguments(self, p):
        p.add_argument("--once", action="store_true")
        p.add_argument("--interval", type=int, default=60)
        p.add_argument("--lookback", type=int, default=180)

    def run(self, args) -> int:
        from .poller import SubmissionPoller

        poller = SubmissionPoller(self.production_runtime(args), args.lookback)
        if args.once:
            self.ui.ok(f"processed {poller.poll_once()} submission(s)")
        else:
            poller.run_forever(args.interval)
        return 0


class ReconcileCommand(Command):
    name, help = "reconcile", "daily 17:00 audit + executive summary e-mail (rolling window)"

    def add_arguments(self, p):
        p.add_argument("--date", help="calendar-day back-fill YYYY-MM-DD (does not move the rolling window)")
        p.add_argument("--cutoff", default="17:00")
        p.add_argument("--force", action="store_true")
        p.add_argument("--no-email", action="store_true")

    def run(self, args) -> int:
        from datetime import date

        from .reconcile import DailyReconciler

        settings = Settings.from_env(args.env_file)
        today = datetime.now(ZoneInfo(settings.timezone)).date()
        day = date.fromisoformat(args.date) if args.date else None
        if day is None and today.weekday() >= 5 and not args.force:
            self.ui.info(f"{today} is a weekend; skipping. The next business-day run covers everything since the last report (use --force to run now).")
            return 0
        rt = self.production_runtime(args)
        hh, mm = (int(x) for x in args.cutoff.split(":"))
        s = DailyReconciler(rt.services, rt.notifier, time(hh, mm)).run(day, send=not args.no_email)
        self.ui.ok(f"{s.window_local[0]:%Y-%m-%d %H:%M} → {s.window_local[1]:%Y-%m-%d %H:%M}: submissions={s.raw_submissions} "
                   f"grades={s.by_grade} discrepancies={len(s.discrepancies)}")
        return 0


class FlowchartCommand(Command):
    name, help = "flowchart", "(re)generate the animated README diagrams from the code"

    def add_arguments(self, p):
        p.add_argument("--readme", default="README.md")
        p.add_argument("--config", default="./config")
        p.add_argument("--sample", default="./sample_data")
        g = p.add_mutually_exclusive_group(required=True)
        g.add_argument("--write", action="store_true")
        g.add_argument("--check", action="store_true")

    def run(self, args) -> int:
        from .flowchart import DiagramPublisher

        with tempfile.TemporaryDirectory() as td:
            rt = DemoRuntimeFactory(Path(td), args.config, args.sample).create()
            pub = DiagramPublisher(rt.pipeline.stages, Path(args.readme))
            if args.check:
                stale = pub.stale()
                if stale:
                    self.ui.fail("Generated diagrams are STALE: " + ", ".join(stale) + "\n  Run: python -m gas_electric_response flowchart --write")
                    return 1
                self.ui.ok("README diagrams are up to date.")
                return 0
            pub.write()
        self.ui.ok("Regenerated docs/workflow.html and the README diagram block. Re-record the GIF with: python scripts/build_workflow_gif.py")
        return 0


class TemplateHelpCommand(Command):
    name, help = "template-help", "list every variable, table, helper and filter available inside report templates"

    def add_arguments(self, p):
        p.add_argument("--config", default="./config")
        p.add_argument("--sample", default="./sample_data")

    def run(self, args) -> int:
        from datetime import timezone

        from .reports.data import ReportData

        with tempfile.TemporaryDirectory() as td:
            rt = DemoRuntimeFactory(Path(td), args.config, args.sample).create()
            ctx = rt.pipeline.process_record(rt.services.survey.query(Query())[0])
            data = ReportData(ctx, rt.services, datetime.now(timezone.utc))
            self.ui.listing("Variables  (use like  {{ report.leak_id }}  or  {{ report.lel_pct | fmt('.1f') }})", data.describe())
            self.ui.info("Tables you can loop over with {% for row in NAME %}:  " + ", ".join(sorted(data.sources())))
            self.ui.info("Helpers: map(zoom_m=, padding=, basemap=, layers=, height_in=)  photo_gallery(columns=)  map_uri(...)")
            self.ui.info("Filters: fmt('.1f')  ft  local(tz)  title_case   (plus every built-in Jinja2 filter: round, default, join, length ...)")
            self.ui.info("Map layers registered in Services.layers: " + ", ".join(sorted(rt.services.layers)))
        return 0


class PreviewCommand(Command):
    name, help = "preview", "render a report template to PDF (and HTML) using the demo data - the template authoring loop"

    def add_arguments(self, p):
        p.add_argument("template", help="template file name or path, e.g. templates/field_ticket.html")
        p.add_argument("--out", default="preview.pdf")
        p.add_argument("--leak", default="LK-DEMO-A", help="which demo submission to render")
        p.add_argument("--config", default="./config")
        p.add_argument("--sample", default="./sample_data")
        p.add_argument("--live-media", action="store_true", help="draw real maps from the sample layers instead of placeholders")

    def run(self, args) -> int:
        from datetime import timezone
        from dataclasses import replace

        from .reports.engine import ReportRenderer

        tpl = Path(args.template)
        with tempfile.TemporaryDirectory() as td:
            rt = DemoRuntimeFactory(Path(td), args.config, args.sample).create()
            rec = next(r for r in rt.services.survey.query(Query()) if r.attributes["Leak_ID"] == args.leak)
            ctx = rt.pipeline.process_record(rec)
            if ctx is None or ctx.rejected:
                self.ui.fail(f"{args.leak} did not produce a graded incident; pick another with --leak")
                return 1
            st = replace(rt.services.settings, template_dir=str(tpl.parent) if tpl.parent != Path(".") else rt.services.settings.template_dir,
                         placeholder_media=not args.live_media)
            r = ReportRenderer(tpl.name, rt.services, st, cache_dir=Path(td))
            now = datetime.now(timezone.utc)
            html, pdf = r.render_html(ctx, now), r.render_pdf(ctx, now)
        out = Path(args.out)
        out.write_bytes(pdf)
        out.with_suffix(".html").write_text(html, encoding="utf-8")
        self.ui.ok(f"Wrote {out} ({len(pdf) / 1024:.0f} KB) and {out.with_suffix('.html').name} from {tpl.name}")
        return 0


class CommandLineApp:
    COMMANDS = (DemoCommand, CheckCommand, ServeCommand, PollCommand, ReconcileCommand, FlowchartCommand, TemplateHelpCommand, PreviewCommand)

    def __init__(self, ui: ConsoleUI | None = None):
        self.ui = ui or ConsoleUI()
        self.commands = {c.name: c(self.ui) for c in self.COMMANDS}

    def parser(self) -> argparse.ArgumentParser:
        p = argparse.ArgumentParser(prog="gas_electric_response", description="Gas leak / electric arcing risk automation for ArcGIS")
        p.add_argument("--env-file", default=".env")
        sub = p.add_subparsers(dest="cmd", required=True)
        for cmd in self.commands.values():
            cmd.add_arguments(sub.add_parser(cmd.name, help=cmd.help))
        return p

    def main(self, argv: list[str] | None = None) -> int:
        args = self.parser().parse_args(argv)
        quiet = args.cmd in ("demo", "flowchart", "template-help", "preview")
        LoggingConfigurator.setup("WARNING" if quiet else Settings.from_env(args.env_file).log_level)
        try:
            return self.commands[args.cmd].run(args)
        except ConfigError as exc:
            print(f"CONFIGURATION ERROR: {exc}", file=sys.stderr)
            return 2
