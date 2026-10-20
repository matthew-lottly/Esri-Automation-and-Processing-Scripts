"""Regenerate the example outputs shown in the README (examples/ and docs/assets/*.png) from the SYNTHETIC demo data.

Maps and photos are neutral placeholders, so the examples never contain made-up imagery.
Needs `pip install pypdfium2` (dev only) for the PNG previews.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gas_electric_response.bootstrap import DemoRuntimeFactory  # noqa: E402
from gas_electric_response.clients.store import Query  # noqa: E402
from gas_electric_response.reconcile import DailyReconciler  # noqa: E402
from gas_electric_response.reports.emails import EmailComposer  # noqa: E402
from gas_electric_response.reports.engine import ReportRenderer  # noqa: E402


class ExampleBuilder:
    def __init__(self, out_dir: Path = ROOT / "examples", assets: Path = ROOT / "docs" / "assets"):
        self.out, self.assets = out_dir, assets

    def png(self, pdf_bytes: bytes, dest: Path, scale: float = 1.7) -> None:
        import pypdfium2 as pdfium

        page = pdfium.PdfDocument(pdf_bytes)[0]
        page.render(scale=scale).to_pil().save(dest, optimize=True)

    def build(self) -> list[Path]:
        self.out.mkdir(exist_ok=True)
        self.assets.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        with tempfile.TemporaryDirectory() as td:
            rt = DemoRuntimeFactory(Path(td), str(ROOT / "config"), str(ROOT / "sample_data")).create()
            ctx = None
            for rec in rt.services.survey.query(Query()):
                c = rt.pipeline.process_record(rec)
                ctx = c if rec.attributes["Leak_ID"] == "LK-DEMO-A" else ctx
            now = datetime.now(timezone.utc)
            for tpl, stem in (("field_ticket.html", "field_ticket"), ("risk_assessment.html", "risk_assessment")):
                pdf = ReportRenderer(tpl, rt.services).render_pdf(ctx, now)
                (self.out / f"{stem}_example.pdf").write_bytes(pdf)
                self.png(pdf, self.assets / f"example_{stem}.png")
                written += [self.out / f"{stem}_example.pdf", self.assets / f"example_{stem}.png"]
            composer = EmailComposer(rt.services)
            (self.out / "email_alert_example.html").write_text(composer.alert_html(ctx), encoding="utf-8")
            summary = DailyReconciler(rt.services, rt.notifier, cutoff=time(23, 59, 59)).collect(now.astimezone(ZoneInfo(rt.services.settings.timezone)).date())
            (self.out / "email_daily_summary_example.html").write_text(composer.daily_html(summary, "Daily summary"), encoding="utf-8")
            written += [self.out / "email_alert_example.html", self.out / "email_daily_summary_example.html"]
        return written


if __name__ == "__main__":
    for p in ExampleBuilder().build():
        print("wrote", p.relative_to(ROOT))
