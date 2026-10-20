"""Report maps: a clean, zoomed-in cartographic map of the feature of interest.

`ReportMap` draws the submitted feature (point, line OR polygon), the modeled plume, and any registered feature
layers around it, with scale bar, north arrow and legend. `TileBasemap` optionally adds an online basemap
(OpenStreetMap / Esri tiles); with `basemap: none` the map is fully offline and uses a neutral background.
"""
from __future__ import annotations

import hashlib
import io
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import requests
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Polygon as MplPolygon
from PIL import Image
from pyproj import Transformer
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform, unary_union

from ..clients.store import Query
from ..models import IncidentContext
from ..reference import Services
from .theme import Theme

log = logging.getLogger(__name__)
WORLD = 20037508.342789244          # half-width of the Web Mercator world (metres)
TILE = 256


class TileBasemap:
    """Fetches and stitches XYZ tiles in Web Mercator. Best-effort: returns None (never raises) when offline."""

    PROVIDERS = {
        "osm": ("https://tile.openstreetmap.org/{z}/{x}/{y}.png", "© OpenStreetMap contributors"),
        "esri_street": ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}", "Esri, HERE, Garmin, OpenStreetMap contributors"),
        "esri_imagery": ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", "Esri, Maxar, Earthstar Geographics"),
        "esri_gray": ("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}", "Esri, HERE, Garmin"),
    }
    MAX_TILES = 36

    def __init__(self, provider: str, cache_dir: Path | None = None, session: requests.Session | None = None,
                 user_agent: str = "gas-electric-response-report/0.2 (+report map basemap)", timeout: int = 10):
        if provider in self.PROVIDERS:
            self.url, self.attribution = self.PROVIDERS[provider]
        elif "{z}" in provider:
            self.url, self.attribution = provider, ""
        else:
            raise ValueError(f"Unknown basemap '{provider}'. Use one of {sorted(self.PROVIDERS)} or a URL template with {{z}}/{{x}}/{{y}}.")
        self.cache = Path(cache_dir) if cache_dir else None
        self.s = session or requests.Session()
        self.s.headers.setdefault("User-Agent", user_agent)
        self.timeout = timeout

    @staticmethod
    def zoom_for(width_m: float, width_px: int) -> int:
        res_needed = max(width_m / max(width_px, 1), 1e-6)
        return int(min(19, max(0, math.ceil(math.log2(156543.03392804097 / res_needed)))))

    def _fetch(self, z: int, x: int, y: int) -> Image.Image | None:
        key = hashlib.sha1(self.url.encode()).hexdigest()[:10] + f"_{z}_{x}_{y}.png"
        if self.cache and (self.cache / key).exists():
            return Image.open(self.cache / key).convert("RGB")
        try:
            r = self.s.get(self.url.format(z=z, x=x, y=y), timeout=self.timeout)
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception as exc:
            log.warning("basemap tile %s/%s/%s unavailable: %s", z, x, y, exc)
            return None
        if self.cache:
            self.cache.mkdir(parents=True, exist_ok=True)
            img.save(self.cache / key)
        return img

    def mosaic(self, minx: float, miny: float, maxx: float, maxy: float, width_px: int = 1400):
        """(RGB image, (left, right, bottom, top) in EPSG:3857) covering the box, or None."""
        z = self.zoom_for(maxx - minx, width_px)
        n = 2 ** z
        size = 2 * WORLD / n
        while True:
            x0, x1 = int((minx + WORLD) // size), int((maxx + WORLD) // size)
            y0, y1 = int((WORLD - maxy) // size), int((WORLD - miny) // size)
            if (x1 - x0 + 1) * (y1 - y0 + 1) <= self.MAX_TILES or z == 0:
                break
            z -= 1; n = 2 ** z; size = 2 * WORLD / n
        x0, y0 = max(x0, 0), max(y0, 0)
        x1, y1 = min(x1, n - 1), min(y1, n - 1)
        canvas = Image.new("RGB", ((x1 - x0 + 1) * TILE, (y1 - y0 + 1) * TILE), (240, 242, 245))
        got = 0
        for ix in range(x0, x1 + 1):
            for iy in range(y0, y1 + 1):
                t = self._fetch(z, ix, iy)
                if t is not None:
                    canvas.paste(t, ((ix - x0) * TILE, (iy - y0) * TILE)); got += 1
        if not got:
            return None
        return canvas, (x0 * size - WORLD, (x1 + 1) * size - WORLD, WORLD - (y1 + 1) * size, WORLD - y0 * size)


DEFAULT_LAYER_STYLES: dict[str, dict[str, Any]] = {
    "electric": {"label": "Electric assets", "color": "#E8590C", "symbol": "s", "size": 46, "linewidth": 2.6, "label_field": "ASSETID"},
    "valves": {"label": "Gas valves", "color": "#1C7ED6", "symbol": "v", "size": 52, "label_field": "VALVEID"},
    "facilities": {"label": "Critical facilities", "color": "#7048E8", "symbol": "P", "size": 70, "label_field": "NAME"},
    "meters": {"label": "Customer meters", "color": "#868E96", "symbol": "o", "size": 16},
}


@dataclass
class LayerStyle:
    """How one registered layer is drawn. Every key is settable from the template YAML."""

    layer: str
    label: str = ""
    color: str = "#6B7785"
    size: float = 38
    symbol: str = "o"                  # matplotlib marker for points: o s ^ v D P *
    linewidth: float = 2.2
    alpha: float = 0.9
    fill_alpha: float = 0.25
    label_field: str | None = None
    color_by: dict[str, Any] | None = None        # {field: ASSETTYPE, colors: {PadmountTransformer: "#d00"}, default: "#999"}
    max_features: int = 400

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LayerStyle":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def color_for(self, attrs: dict[str, Any]) -> str:
        if self.color_by:
            return self.color_by.get("colors", {}).get(attrs.get(self.color_by["field"]), self.color_by.get("default", self.color))
        return self.color


class ReportMap:
    """Renders a map PNG from a template `map` block spec (see docs/report-templates.md)."""

    def __init__(self, svc: Services, theme: Theme, cache_dir: Path | None = None):
        self.svc, self.theme, self.cache_dir = svc, theme, cache_dir
        self._to_merc = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        self._to_wgs = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)

    # ---- geometry helpers ----------------------------------------------------------------
    def merc(self, g: BaseGeometry) -> BaseGeometry:
        return transform(self._to_merc.transform, g)

    def extent(self, ctx: IncidentContext, spec: dict[str, Any], aspect: float, lat: float):
        """Mercator extent (minx, miny, maxx, maxy) with the figure's aspect ratio, centred on the feature of interest."""
        focus = [self.merc(ctx.report.geometry)]
        if spec.get("include_plume", True) and ctx.plume is not None and spec.get("focus", "submission") != "submission_only":
            focus.append(self.merc(ctx.plume.polygon))
        minx, miny, maxx, maxy = unary_union(focus).bounds
        k = 1 / math.cos(math.radians(lat))               # ground metres -> mercator units
        if spec.get("zoom_m"):
            half = float(spec["zoom_m"]) * k
            cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
            hw = hh = half
        else:
            pad = float(spec.get("padding", 0.35))
            cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
            hw = max((maxx - minx) / 2 * (1 + pad), float(spec.get("min_half_width_m", 35)) * k)
            hh = max((maxy - miny) / 2 * (1 + pad), float(spec.get("min_half_width_m", 35)) * k)
        if hw / hh < aspect:
            hw = hh * aspect
        else:
            hh = hw / aspect
        return cx - hw, cy - hh, cx + hw, cy + hh

    # ---- drawing ------------------------------------------------------------------------------
    def _draw_geom(self, ax, g: BaseGeometry, color: str, style: LayerStyle, zorder: int, halo: bool = False) -> None:
        for part in getattr(g, "geoms", [g]):
            t = part.geom_type
            if t == "Point":
                if halo:
                    ax.scatter(part.x, part.y, s=style.size * 5, facecolors="none", edgecolors=color, linewidths=1.6, zorder=zorder - 1)
                ax.scatter(part.x, part.y, s=style.size, marker=style.symbol, color=color, edgecolor="white", linewidths=0.9, zorder=zorder, alpha=style.alpha)
            elif t in ("LineString", "LinearRing"):
                x, y = part.xy
                ax.plot(x, y, color="white", lw=style.linewidth + 1.6, solid_capstyle="round", zorder=zorder - 1)
                ax.plot(x, y, color=color, lw=style.linewidth, solid_capstyle="round", zorder=zorder, alpha=style.alpha)
            elif t == "Polygon":
                ax.add_patch(MplPolygon(list(part.exterior.coords), closed=True, facecolor=color, alpha=style.fill_alpha, edgecolor="none", zorder=zorder - 1))
                ax.add_patch(MplPolygon(list(part.exterior.coords), closed=True, fill=False, edgecolor=color, lw=style.linewidth * 0.8, zorder=zorder))

    def _scale_bar(self, ax, ext, lat: float) -> None:
        minx, miny, maxx, maxy = ext
        ground_w = (maxx - minx) * math.cos(math.radians(lat))
        length = max(v for v in (5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000) if v <= ground_w * 0.28) if ground_w * 0.28 >= 5 else 5
        merc_len = length / math.cos(math.radians(lat))
        x0, y0 = minx + (maxx - minx) * 0.035, miny + (maxy - miny) * 0.045
        ink = self.theme.hex("ink")
        ax.plot([x0, x0 + merc_len], [y0, y0], color=ink, lw=2.4, solid_capstyle="butt", zorder=30)
        for xx in (x0, x0 + merc_len):
            ax.plot([xx, xx], [y0 - (maxy - miny) * 0.008, y0 + (maxy - miny) * 0.008], color=ink, lw=1.6, zorder=30)
        ax.text(x0 + merc_len / 2, y0 + (maxy - miny) * 0.016, f"{length:g} m" if length < 1000 else f"{length / 1000:g} km",
                ha="center", va="bottom", fontsize=6.5, color=ink, zorder=30,
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.75))

    def _north_arrow(self, ax, ext) -> None:
        minx, miny, maxx, maxy = ext
        cx, cy = maxx - (maxx - minx) * 0.05, maxy - (maxy - miny) * 0.07
        h = (maxy - miny) * 0.07
        ax.add_patch(MplPolygon([(cx, cy + h / 2), (cx - h * 0.28, cy - h / 2), (cx, cy - h * 0.22), (cx + h * 0.28, cy - h / 2)],
                                closed=True, fc=self.theme.hex("ink"), ec="white", lw=0.6, zorder=30))
        ax.text(cx, cy - h * 0.72, "N", ha="center", va="top", fontsize=7, weight="bold", color=self.theme.hex("ink"), zorder=30)

    def render(self, ctx: IncidentContext, spec: dict[str, Any], width_in: float, height_in: float, dpi: int = 220) -> bytes:
        th, r = self.theme, ctx.report
        fig, ax = plt.subplots(figsize=(width_in, height_in), dpi=dpi)
        fig.subplots_adjust(0, 0, 1, 1)
        ext = self.extent(ctx, spec, width_in / height_in, r.lat)
        minx, miny, maxx, maxy = ext
        ax.set_xlim(minx, maxx); ax.set_ylim(miny, maxy); ax.set_aspect("equal"); ax.set_axis_off()
        ax.add_patch(plt.Rectangle((minx, miny), maxx - minx, maxy - miny, fc="#EEF1F4", ec="none", zorder=0))
        # light grid for a clean, technical look when there is no basemap
        basemap_name = str(spec.get("basemap", "none"))
        attribution = ""
        if basemap_name != "none":
            try:
                bm = TileBasemap(basemap_name, self.cache_dir / "tiles" if self.cache_dir else None)
                mosaic = bm.mosaic(minx, miny, maxx, maxy, int(width_in * dpi * 0.7))
            except ValueError:
                raise
            if mosaic:
                img, (l, rt, b, t) = mosaic
                ax.imshow(np.asarray(img), extent=(l, rt, b, t), origin="upper", zorder=1, interpolation="bilinear")
                attribution = bm.attribution
            else:
                ax.text(minx + (maxx - minx) * 0.5, miny + (maxy - miny) * 0.5, "Basemap unavailable (offline)", ha="center", color=th.hex("faint"), fontsize=7, zorder=2)
        if basemap_name == "none" or not attribution:
            for gx in np.linspace(minx, maxx, 9)[1:-1]:
                ax.plot([gx, gx], [miny, maxy], color="white", lw=0.8, zorder=1)
            for gy in np.linspace(miny, maxy, 7)[1:-1]:
                ax.plot([minx, maxx], [gy, gy], color="white", lw=0.8, zorder=1)
        handles: list[Any] = []
        sub_geom_m = self.merc(r.geometry)
        # ---- registered feature layers (point / line / polygon, any store) ----
        wgs_box = box(*self._to_wgs.transform(minx, miny), *self._to_wgs.transform(maxx, maxy))
        layer_specs = spec.get("layers", list(DEFAULT_LAYER_STYLES))
        for i, ld in enumerate(layer_specs):
            ld = {"layer": ld} if isinstance(ld, str) else dict(ld)          # 'electric'  or  {layer: electric, color: ...}
            st = LayerStyle.from_dict({**DEFAULT_LAYER_STYLES.get(ld["layer"], {}), **ld})
            store = self.svc.layers.get(st.layer)
            if store is None:
                log.warning("map layer '%s' is not registered (available: %s)", st.layer, sorted(self.svc.layers))
                continue
            fields = [f for f in (st.label_field, (st.color_by or {}).get("field")) if f]
            try:
                recs = store.query(Query(geometry=wgs_box, out_fields=fields or None))[: st.max_features]
            except Exception as exc:
                log.warning("map layer '%s' query failed: %s", st.layer, exc)
                continue
            for rec in recs:
                if rec.geometry is None:
                    continue
                g = self.merc(rec.geometry)
                self._draw_geom(ax, g, st.color_for(rec.attributes), st, 5 + i)
                if st.label_field and rec.attributes.get(st.label_field) not in (None, ""):
                    c = g.representative_point()
                    if c.distance(sub_geom_m) < (maxx - minx) * 0.035:      # would collide with the submitted-feature marker
                        continue
                    ax.annotate(str(rec.attributes[st.label_field]), (c.x, c.y), xytext=(5, 5), textcoords="offset points", fontsize=6,
                                color=th.hex("ink"), zorder=20, bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.8))
            gt = recs[0].geometry.geom_type if recs and recs[0].geometry is not None else "Point"
            lbl = st.label or st.layer.title()
            handles.append(Line2D([0], [0], color=st.color, lw=3) if "Line" in gt else Patch(fc=st.color, ec=st.color, alpha=0.5) if "Polygon" in gt
                           else Line2D([0], [0], marker=st.symbol, color="none", markerfacecolor=st.color, markeredgecolor="white", markersize=7, label=lbl))
            handles[-1].set_label(lbl)
        # ---- plume + the submitted feature (always on top) ----
        if spec.get("show_plume", True) and ctx.plume is not None:
            pc = th.grade_hex(int(ctx.grade) if ctx.grade else None)
            ps = LayerStyle("plume", color=pc, fill_alpha=0.22, linewidth=2.0)
            self._draw_geom(ax, self.merc(ctx.plume.polygon), pc, ps, 12)
            handles.insert(0, Patch(fc=pc, ec=pc, alpha=0.45, label="Modeled gas migration envelope"))
        sub_style = LayerStyle("submission", color=th.hex("ink"), symbol="*", size=190 if r.geometry.geom_type == "Point" else 60, linewidth=3.2, fill_alpha=0.18)
        self._draw_geom(ax, self.merc(r.geometry), th.hex("ink"), sub_style, 40, halo=True)
        handles.insert(0, Line2D([0], [0], marker="*", color="none", markerfacecolor=th.hex("ink"), markeredgecolor="white", markersize=11, label="Submitted feature"))
        if spec.get("scale_bar", True):
            self._scale_bar(ax, ext, r.lat)
        if spec.get("north_arrow", True):
            self._north_arrow(ax, ext)
        if spec.get("legend", True) and handles:
            leg = ax.legend(handles=handles, loc="lower right", fontsize=6.3, frameon=True, framealpha=0.92, edgecolor=th.hex("rule"),
                            borderpad=0.6, labelspacing=0.55, handlelength=1.4)
            leg.set_zorder(40)
        if attribution:
            ax.text(maxx - (maxx - minx) * 0.008, miny + (maxy - miny) * 0.004, attribution, ha="right", va="bottom", fontsize=4.8, color="#333", zorder=31,
                    bbox=dict(fc="white", ec="none", alpha=0.7, pad=0.8))
        for spine in ax.spines.values():
            spine.set_visible(False)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=dpi)
        plt.close(fig)
        return buf.getvalue()
