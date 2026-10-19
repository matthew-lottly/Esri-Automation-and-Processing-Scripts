"""Command line: demo | check | serve | poll | reconcile | flowchart"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Settings
from .exceptions import ConfigError
from .logging_setup import setup


def _demo(args) -> int:
    from .bootstrap import build_demo
    from .clients.store import Query

    out = Path(args.out)
    rt = build_demo(out, args.config, args.sample)
    print(f"Running offline demo (no Esri account, no network). Output -> {out.resolve()}\n")
    for rec in rt.services.survey.query(Query()):
        ctx = rt.pipeline.process_record(rec)
        lid = rec.attributes["Leak_ID"]
        if ctx is None:
            print(f"  {lid}: skipped")
        elif ctx.rejected:
            print(f"  {lid}: REJECTED - {ctx.rejected}")
        else:
            print(f"  {lid}: Grade {int(ctx.grade)} ({ctx.grade.label}) dispatch={ctx.dispatch_code} "
                  f"{'DUPLICATE of ' + ctx.duplicate_of if ctx.is_duplicate else 'new incident'} "
                  f"| elec in plume={len(ctx.electric_hits)} valves={','.join(v.valve_id for v in ctx.valves)} "
                  f"| A={ctx.report_a_status} B={ctx.report_b_status} mail={ctx.notify_status}")
    from .reconcile import DailyReconciler

    rec = DailyReconciler(rt.services, rt.notifier, cutoff=time(23, 59, 59))
    summ = rec.run(datetime.now(ZoneInfo(rt.services.settings.timezone)).date(), send=True)
    print("\nDaily summary (demo window = whole day):", {"raw": summ.raw_submissions, "grades": summ.by_grade,
                                                          "discrepancies": summ.discrepancies})
    print(f"\nOpen the PDFs in {out}/A_field_tickets and {out}/B_risk_assessments, and e-mails in {out}/outbox")
    return 0


def _runtime(args):
    from .bootstrap import build_production

    return build_production(Settings.from_env(args.env_file))


def _check(args) -> int:
    from .diagnostics import run_checks

    rt = _runtime(args)
    bad = 0
    for ok, msg in run_checks(rt.services):
        print(("[ OK ] " if ok else "[FAIL] ") + msg)
        bad += not ok
    print("\nAll checks passed." if not bad else f"\n{bad} check(s) failed - fix layer fields or config/layer_schema.yaml.")
    return 1 if bad else 0


def _serve(args) -> int:
    import uvicorn

    from .receiver.app import create_app

    settings = Settings.from_env(args.env_file)
    from .bootstrap import build_production

    rt = build_production(settings)
    uvicorn.run(create_app(rt, settings), host=args.host, port=args.port, log_level=settings.log_level.lower())
    return 0


def _poll(args) -> int:
    from .poller import poll_forever, poll_once

    rt = _runtime(args)
    if args.once:
        print(f"processed {poll_once(rt, args.lookback)} submission(s)")
    else:
        poll_forever(rt, args.interval, args.lookback)
    return 0


def _reconcile(args) -> int:
    from .reconcile import DailyReconciler

    settings = Settings.from_env(args.env_file)
    tz = ZoneInfo(settings.timezone)
    day = date.fromisoformat(args.date) if args.date else None
    today = datetime.now(tz).date()
    if day is None and today.weekday() >= 5 and not args.force:
        print(f"{today} is a weekend; skipping. The next business-day run covers everything since the last report (use --force to run now).")
        return 0
    rt = _runtime(args)
    hh, mm = (int(x) for x in args.cutoff.split(":"))
    s = DailyReconciler(rt.services, rt.notifier, time(hh, mm)).run(day, send=not args.no_email)
    print(f"{s.window_local[0]:%Y-%m-%d %H:%M} -> {s.window_local[1]:%Y-%m-%d %H:%M}: submissions={s.raw_submissions} grades={s.by_grade} discrepancies={len(s.discrepancies)}")
    return 0


def _flowchart(args) -> int:
    from .bootstrap import build_demo
    from .flowchart import inject, pipeline_mermaid, reconcile_mermaid, stage_table
    from .reconcile import RECONCILE_STEPS
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        rt = build_demo(Path(td), args.config, args.sample)
        blocks = {"PIPELINE": pipeline_mermaid(rt.pipeline.stages), "STAGES": stage_table(rt.pipeline.stages), "RECONCILE": reconcile_mermaid(RECONCILE_STEPS)}
    readme = Path(args.readme)
    text = readme.read_text(encoding="utf-8")
    new = text
    for name, block in blocks.items():
        new = inject(new, name, block)
    if args.check:
        if new != text:
            print("README flow charts are STALE. Run: python -m gas_electric_response flowchart --write")
            return 1
        print("README flow charts are up to date.")
        return 0
    readme.write_text(new, encoding="utf-8")
    print("README flow charts regenerated.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gas_electric_response", description=__doc__)
    p.add_argument("--env-file", default=".env")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="offline end-to-end run on synthetic data (no Esri account needed)")
    d.add_argument("--out", default="./demo_output"); d.add_argument("--config", default="./config"); d.add_argument("--sample", default="./sample_data")
    d.set_defaults(fn=_demo)
    sub.add_parser("check", help="read-only verification of credentials, layers and fields").set_defaults(fn=_check)
    s = sub.add_parser("serve", help="run the webhook receiver"); s.add_argument("--host", default="0.0.0.0"); s.add_argument("--port", type=int, default=8080)
    s.set_defaults(fn=_serve)
    q = sub.add_parser("poll", help="poll the survey layer instead of (or alongside) webhooks")
    q.add_argument("--once", action="store_true"); q.add_argument("--interval", type=int, default=60); q.add_argument("--lookback", type=int, default=180)
    q.set_defaults(fn=_poll)
    r = sub.add_parser("reconcile", help="daily 17:00 audit + executive summary e-mail")
    r.add_argument("--date"); r.add_argument("--cutoff", default="17:00"); r.add_argument("--force", action="store_true"); r.add_argument("--no-email", action="store_true")
    r.set_defaults(fn=_reconcile)
    f = sub.add_parser("flowchart", help="(re)generate the README Mermaid flow charts from the code")
    f.add_argument("--readme", default="README.md"); f.add_argument("--config", default="./config"); f.add_argument("--sample", default="./sample_data")
    g = f.add_mutually_exclusive_group(required=True); g.add_argument("--write", action="store_true"); g.add_argument("--check", action="store_true")
    f.set_defaults(fn=_flowchart)
    args = p.parse_args(argv)
    setup(Settings.from_env(args.env_file).log_level if args.cmd not in ("demo", "flowchart") else "WARNING")
    try:
        return args.fn(args)
    except ConfigError as exc:
        print(f"CONFIGURATION ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
