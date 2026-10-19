"""Static map figure for the Layer Risk Report (matplotlib; no ArcGIS Pro / arcpy licence needed)."""
from __future__ import annotations

import io
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon

from ..geo import to_local
from ..models import IncidentContext

COLORS = {"ignition_source": "#d1271e", "conduit": "#e08a00", "other": "#7a7a7a", "facility": "#6a3d9a", "valve": "#1f78b4"}
GRADE_FILL = {1: "#d1271e", 2: "#f28e2b", 3: "#4e9a51"}


def render_risk_map(ctx: IncidentContext, size_in: tuple[float, float] = (7.2, 6.0), dpi: int = 160) -> bytes:
    r = ctx.report
    fig, ax = plt.subplots(figsize=size_in, dpi=dpi)
    poly = to_local(ctx.plume.polygon, r.lon, r.lat)
    xs, ys = poly.exterior.xy
    ax.add_patch(MplPolygon(list(zip(xs, ys)), closed=True, facecolor=GRADE_FILL[int(ctx.grade)], alpha=0.30,
                            edgecolor=GRADE_FILL[int(ctx.grade)], lw=1.5, label="Gas plume envelope"))
    ax.plot(0, 0, marker="*", ms=16, color="black", zorder=5, label=f"Leak {r.leak_id}")
    pts = [(0.0, 0.0)] + list(zip(xs, ys))
    from ..geo import local_transformers
    fwd, _ = local_transformers(r.lon, r.lat)
    seen = set()
    for h in ctx.electric_hits:
        x, y = fwd.transform(h.lon, h.lat)
        lab = {"ignition_source": "Pad-mount / switchgear", "conduit": "Underground conduit", "other": "Other electric"}[h.category]
        if h.geometry is not None and h.geometry.geom_type in ("LineString", "MultiLineString"):
            local = to_local(h.geometry.intersection(ctx.plume.polygon.buffer(1e-4)), r.lon, r.lat)
            for part in getattr(local, "geoms", [local]):
                if part.is_empty:
                    continue
                lx, ly = part.xy
                ax.plot(lx, ly, color=COLORS[h.category], lw=3, solid_capstyle="round", zorder=3, label=None if lab in seen else lab)
                seen.add(lab)
                pts.extend(zip(lx, ly))
            continue
        ax.scatter([x], [y], s=70, marker="s", color=COLORS[h.category], edgecolor="white", zorder=4, label=None if lab in seen else lab)
        seen.add(lab); pts.append((x, y))
    for h in ctx.facility_hits:
        x, y = fwd.transform(h.lon, h.lat)
        ax.scatter([x], [y], s=110, marker="P", color=COLORS["facility"], edgecolor="white", zorder=4,
                   label=None if "Critical facility" in seen else "Critical facility")
        seen.add("Critical facility"); pts.append((x, y))
        ax.annotate(h.name or h.asset_type, (x, y), textcoords="offset points", xytext=(6, 6), fontsize=7)
    core_span = max(max(abs(p[0]) for p in pts), max(abs(p[1]) for p in pts)) * 1.35 or 20
    off_map = []
    for i, v in enumerate(ctx.valves, 1):
        x, y = fwd.transform(v.lon, v.lat)
        if max(abs(x), abs(y)) > core_span * 1.6:
            off_map.append(f"{i}: {v.valve_id} ({v.distance_m:.0f} m)")
            continue
        ax.scatter([x], [y], s=80, marker="v", color=COLORS["valve"], edgecolor="white", zorder=4,
                   label=None if "Gas shutoff valve" in seen else "Gas shutoff valve")
        seen.add("Gas shutoff valve"); pts.append((x, y))
        ax.annotate(f"{i}: {v.valve_id} ({v.distance_m:.0f} m)", (x, y), textcoords="offset points", xytext=(6, -10), fontsize=7)
    w = ctx.weather
    if w and w.wind_speed_mps is not None and w.wind_from_deg is not None and w.wind_speed_mps >= 1.0:
        span = max(max(abs(p[0]) for p in pts), max(abs(p[1]) for p in pts)) * 1.1 or 20
        b = math.radians((w.wind_from_deg + 180) % 360)       # blowing TOWARD
        ax.annotate("", xy=(-span * 0.85 + math.sin(b) * span * 0.25, span * 0.85 + math.cos(b) * span * 0.25),
                    xytext=(-span * 0.85, span * 0.85), arrowprops=dict(arrowstyle="-|>", lw=2, color="#1f4e79"))
        ax.text(-span * 0.85, span * 0.93, f"wind {w.wind_speed_mps:.1f} m/s from {w.wind_from_deg:.0f}°", fontsize=7, color="#1f4e79")
    span = max(max(abs(p[0]) for p in pts), max(abs(p[1]) for p in pts)) * 1.2 or 20
    if off_map:
        ax.text(0.01, 0.01, "Valves beyond map extent: " + "; ".join(off_map), transform=ax.transAxes, fontsize=6.5, va="bottom")
    ax.set_xlim(-span, span); ax.set_ylim(-span, span); ax.set_aspect("equal")
    ax.set_xlabel("metres east of leak"); ax.set_ylabel("metres north of leak"); ax.grid(alpha=0.25)
    ax.set_title("Gas plume envelope vs. electric infrastructure (screening-level, north up)", fontsize=9)
    ax.legend(loc="lower right", fontsize=7, framealpha=0.9)
    buf = io.BytesIO(); fig.tight_layout(); fig.savefig(buf, format="png"); plt.close(fig)
    return buf.getvalue()
