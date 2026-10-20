"""Jinja2 report engine.

A report template is an ordinary HTML file with Jinja2 placeholders:

    <h1>{{ report.leak_id }}</h1>
    {% for row in electric_hits %}<tr><td>{{ row.asset_id }}</td></tr>{% endfor %}
    {{ map(zoom_m=140, layers=['electric', 'valves']) }}

`ReportRenderer` renders the template with data from the pipeline, then a `PdfEngine` turns the HTML into a PDF.
"""
from __future__ import annotations

import abc
import base64
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import jinja2
from jinja2.sandbox import SandboxedEnvironment
from markupsafe import Markup, escape

from ..config import Settings
from ..exceptions import ConfigError
from ..models import IncidentContext
from ..reference import Services
from .data import ReportData
from .mapping import ReportMap
from .theme import Theme

log = logging.getLogger(__name__)
MISSING = "—"


class PdfEngine(abc.ABC):
    @abc.abstractmethod
    def render(self, html: str, base_url: str) -> bytes: ...


class WeasyPrintEngine(PdfEngine):
    """HTML + CSS -> PDF with WeasyPrint (pure pip install on Linux/macOS; Windows needs the GTK runtime, see docs)."""

    def render(self, html: str, base_url: str) -> bytes:
        try:
            from weasyprint import HTML
        except (ImportError, OSError) as exc:
            raise ConfigError("WeasyPrint is not available (pip install weasyprint; on Windows also install the GTK runtime - "
                              f"see docs/report-templates.md). Original error: {exc}") from exc
        return HTML(string=html, base_url=base_url).write_pdf()


class _Undefined(jinja2.ChainableUndefined):
    """Missing variables render as an em dash instead of raising, so one typo never kills a compliance report."""

    def __str__(self) -> str:
        return MISSING


class Placeholders:
    """Neutral stand-ins for media, used by the offline demo (and `preview`) so example reports never show fake imagery."""

    @staticmethod
    def svg(label: str, hint: str, w: int, h: int) -> str:
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
               f'<rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="10" fill="#F6F8FA" stroke="#B8C2CD" stroke-width="1.6" stroke-dasharray="7 6"/>'
               f'<text x="{w / 2}" y="{h / 2 - 4}" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="15" font-weight="700" fill="#5F6C7B" letter-spacing="1.5">{label}</text>'
               f'<text x="{w / 2}" y="{h / 2 + 18}" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="11" fill="#93A0AE">{hint}</text></svg>')
        return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


class MediaHelper:
    """The template-callable helpers: map(...), photo_gallery(...). Bound to one incident per render."""

    def __init__(self, ctx: IncidentContext, svc: Services, theme: Theme, cache_dir: Path | None, placeholders: bool):
        self.ctx, self.svc, self.placeholders = ctx, svc, placeholders
        self.maps = ReportMap(svc, theme, cache_dir)

    def map_uri(self, width_in: float = 7.2, height_in: float = 3.6, **spec: Any) -> str:
        """Data-URI of a map zoomed to the submitted feature. Options are documented in docs/report-templates.md."""
        size = (int(width_in * 96), int(height_in * 96))
        if self.placeholders:
            return Placeholders.svg("MAP PLACEHOLDER", "zoomed to the submitted feature when run on live data", *size)
        try:
            return "data:image/png;base64," + base64.b64encode(self.maps.render(self.ctx, spec, width_in, height_in)).decode()
        except Exception as exc:        # a broken map must never prevent the compliance record from being written
            log.error("map rendering failed for %s: %s", self.ctx.report.leak_id, exc)
            return Placeholders.svg("MAP UNAVAILABLE", "the map could not be rendered - see the application log", *size)

    def map(self, width_in: float = 7.2, height_in: float = 3.6, css_class: str = "map", **spec: Any) -> Markup:
        return Markup(f'<img class="{css_class}" alt="Map of the submitted feature" src="{self.map_uri(width_in, height_in, **spec)}"/>')

    def photo_gallery(self, columns: int = 2, empty_text: str = "No photo attachments were found on the submission.") -> Markup:
        photos = [p for p in self.ctx.report.photos if Path(p).exists()]
        if photos:
            cells = "".join(f'<figure class="photo"><img src="{Path(p).resolve().as_uri()}"/><figcaption>{escape(Path(p).name.split("_", 1)[-1])}</figcaption></figure>' for p in photos)
        elif self.placeholders:
            uri = Placeholders.svg("PHOTO PLACEHOLDER", "survey photo attachments appear here", 330, 210)
            cells = "".join(f'<figure class="photo"><img src="{uri}"/></figure>' for _ in range(columns))
        else:
            return Markup(f'<p class="muted">{escape(empty_text)}</p>')
        return Markup(f'<div class="photos cols-{columns}">{cells}</div>')


class TemplateEnvironment:
    """Builds the Jinja2 environment: autoescape, em-dash for empty values, and the report filters."""

    def __init__(self, template_dir: Path):
        self.dir = Path(template_dir)
        self.env = SandboxedEnvironment(      # templates may come from anywhere: no access to Python internals
            loader=jinja2.FileSystemLoader(str(self.dir)), autoescape=jinja2.select_autoescape(["html", "xml"]),
            undefined=_Undefined, finalize=lambda v: MISSING if v is None or v == "" else v, trim_blocks=True, lstrip_blocks=True)
        self.env.filters.update(fmt=self.fmt, ft=self.metres_to_feet, local=self.local_time, title_case=lambda s: str(s).replace("_", " ").title())

    @staticmethod
    def fmt(value: Any, spec: str = "") -> str:
        """`{{ report.lel_pct | fmt('.1f') }}` - Python format spec; empty values give an em dash."""
        if value is None or value == "":
            return MISSING
        try:
            return format(value, spec)
        except (ValueError, TypeError):
            return str(value)

    @staticmethod
    def metres_to_feet(value: Any, nd: int = 0) -> str:
        return MISSING if value is None else f"{float(value) * 3.280839895:.{nd}f}"

    @staticmethod
    def local_time(value: datetime | None, tz: str = "UTC", pattern: str = "%Y-%m-%d %H:%M:%S %Z") -> str:
        return MISSING if value is None else value.astimezone(ZoneInfo(tz)).strftime(pattern)

    def load(self, name: str) -> jinja2.Template:
        try:
            return self.env.get_template(name)
        except jinja2.TemplateNotFound as exc:
            raise ConfigError(f"Report template not found: {self.dir / name}") from exc
        except jinja2.TemplateSyntaxError as exc:
            raise ConfigError(f"{name} line {exc.lineno}: template syntax error - {exc.message}") from exc


class ReportRenderer:
    """template name + incident -> HTML or PDF."""

    def __init__(self, template_name: str, svc: Services, settings: Settings | None = None, pdf_engine: PdfEngine | None = None,
                 data_cls: type[ReportData] = ReportData, cache_dir: Path | None = None):
        self.name, self.svc = template_name, svc
        self.settings = settings or svc.settings
        self.dir = Path(self.settings.template_dir)
        self.theme = Theme.load(self.dir / "theme.yaml")
        self.tenv = TemplateEnvironment(self.dir)
        self.template = self.tenv.load(template_name)           # fail at start-up, not when a leak happens
        self.pdf, self.data_cls, self.cache_dir = pdf_engine or WeasyPrintEngine(), data_cls, cache_dir

    def context(self, ctx: IncidentContext, generated_at: datetime) -> dict[str, Any]:
        data = self.data_cls(ctx, self.svc, generated_at)
        ns = data.namespace()
        media = MediaHelper(ctx, self.svc, self.theme, self.cache_dir, self.settings.placeholder_media)
        tables = {k: fn() for k, fn in data.sources().items()}
        return {**ns, **tables, "map": media.map, "map_uri": media.map_uri, "photo_gallery": media.photo_gallery,
                "theme_css": Markup(self.theme.css_variables()), "tz": self.settings.timezone, "demo_mode": self.settings.placeholder_media,
                "layers": sorted(self.svc.layers)}

    def render_html(self, ctx: IncidentContext, generated_at: datetime) -> str:
        return self.template.render(**self.context(ctx, generated_at))

    def render_pdf(self, ctx: IncidentContext, generated_at: datetime) -> bytes:
        return self.pdf.render(self.render_html(ctx, generated_at), self.dir.resolve().as_uri() + "/")
