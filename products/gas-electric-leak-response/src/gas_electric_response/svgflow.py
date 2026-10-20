"""Animated SVG flow charts, generated from the real stage list (so they cannot drift from the code).

The SVG uses only CSS animation and SMIL (no JavaScript), so it animates when embedded with <img> in a GitHub README.
Data "packets" glide along the pipeline path; each stage card pulses as a packet reaches it.
"""
from __future__ import annotations

import html
import math
import textwrap
from dataclasses import dataclass

from .stages.base import Stage

FONT = "Inter, 'Segoe UI', -apple-system, Helvetica, Arial, sans-serif"
PALETTE = ["#4DA3FF", "#2EE6C5", "#B28DFF", "#FFC145", "#5BE584", "#FF7A90"]


@dataclass
class Card:
    """One rounded stage card in the diagram."""

    index: int
    title: str
    chip: str | None
    color: str
    x: float
    y: float
    w: float = 184
    h: float = 62

    @property
    def left(self): return (self.x, self.y + self.h / 2)
    @property
    def right(self): return (self.x + self.w, self.y + self.h / 2)
    @property
    def top(self): return (self.x + self.w / 2, self.y)
    @property
    def bottom(self): return (self.x + self.w / 2, self.y + self.h)


class Route:
    """An ordered set of connector polylines. Draws them with rounded corners and exposes ONE continuous main path
    (through the cards) plus the distance at which each connector ends, so card pulses can sync with the packets."""

    def __init__(self, radius: float = 11):
        self.r = radius
        self.segments: list[tuple[str, list[tuple[float, float]]]] = []

    @staticmethod
    def elbow(a: tuple[float, float], b: tuple[float, float], mid_x: float | None = None) -> list[tuple[float, float]]:
        if abs(a[1] - b[1]) < 1:
            return [a, b]
        mx = (a[0] + b[0]) / 2 if mid_x is None else mid_x
        return [a, (mx, a[1]), (mx, b[1]), b]

    def add(self, color: str, pts: list[tuple[float, float]]) -> int:
        self.segments.append((color, pts))
        return len(self.segments) - 1

    def rounded(self, pts: list[tuple[float, float]]) -> str:
        d = [f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"]
        for i in range(1, len(pts) - 1):
            p0, p1, p2 = pts[i - 1], pts[i], pts[i + 1]
            r = min(self.r, math.dist(p0, p1) / 2, math.dist(p1, p2) / 2)
            if r < 0.5:
                d.append(f"L{p1[0]:.1f},{p1[1]:.1f}")
                continue
            u = ((p0[0] - p1[0]) / math.dist(p0, p1), (p0[1] - p1[1]) / math.dist(p0, p1))
            v = ((p2[0] - p1[0]) / math.dist(p1, p2), (p2[1] - p1[1]) / math.dist(p1, p2))
            d.append(f"L{p1[0] + u[0] * r:.1f},{p1[1] + u[1] * r:.1f} Q{p1[0]:.1f},{p1[1]:.1f} {p1[0] + v[0] * r:.1f},{p1[1] + v[1] * r:.1f}")
        d.append(f"L{pts[-1][0]:.1f},{pts[-1][1]:.1f}")
        return " ".join(d)

    def _main_points(self) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for _, pts in self.segments:
            out += pts
        return out

    def main_path(self) -> str:
        return self.rounded(self._main_points())

    def arrival_fraction(self, seg_index: int) -> float:
        """Share of the main path travelled when the packet reaches the END of connector `seg_index`."""
        pts = self._main_points()
        n = sum(len(p) for _, p in self.segments[: seg_index + 1])
        lens = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
        return sum(lens[: n - 1]) / (sum(lens) or 1)


class FlowSvg:
    """Common chrome: background, title, defs (glow, gradients), CSS."""

    W, H = 1560, 720
    CYCLE = 18.0           # seconds for a packet to traverse the whole path
    PACKETS = 3

    def __init__(self, title: str, subtitle: str):
        self.title, self.subtitle = title, subtitle
        self.parts: list[str] = []

    @staticmethod
    def esc(t: str) -> str:
        return html.escape(t, quote=True)

    def css(self) -> str:
        return f"""
        .bg{{fill:url(#bg)}} .grid{{fill:url(#dots)}}
        text{{font-family:{FONT};}}
        .title{{font-size:27px;font-weight:700;fill:#F4F7FB;letter-spacing:.2px}}
        .sub{{font-size:14px;fill:#8FA3BD}}
        .card{{fill:#16233A;stroke-width:1.4}}
        .name{{font-size:12.6px;font-weight:600;fill:#EAF1FA}}
        .chip-t{{font-size:10.5px;fill:#9FB4CF}}
        .badge-t{{font-size:11px;font-weight:700;fill:#0B1220}}
        .gh{{font-size:11.5px;font-weight:700;letter-spacing:1.4px}}
        .edge{{fill:none;stroke-width:2.2;stroke-linecap:round;stroke-dasharray:3 7;animation:flow 1.1s linear infinite;opacity:.85}}
        .rail{{fill:none;stroke:#22324D;stroke-width:5;stroke-linecap:round}}
        .reject{{fill:none;stroke:#FF6B7F;stroke-width:1.6;stroke-dasharray:4 5;animation:flow 1.6s linear infinite}}
        .legend-t{{font-size:11.5px;fill:#9FB4CF}}
        @keyframes flow{{to{{stroke-dashoffset:-20}}}}
        @keyframes float{{0%,100%{{transform:translateY(0)}}50%{{transform:translateY(-3px)}}}}
        .float{{animation:float 4.5s ease-in-out infinite}}
        @media (prefers-reduced-motion: reduce){{.edge,.reject,.float{{animation:none}}}}
        """

    def defs(self) -> str:
        return """<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0A1020"/><stop offset="1" stop-color="#101B33"/></linearGradient>
  <pattern id="dots" width="26" height="26" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r="1.1" fill="#1B2A45"/></pattern>
  <filter id="glow" x="-80%" y="-80%" width="260%" height="260%"><feGaussianBlur stdDeviation="3.2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  <filter id="soft" x="-20%" y="-20%" width="140%" height="140%"><feDropShadow dx="0" dy="6" stdDeviation="7" flood-color="#000" flood-opacity=".35"/></filter>
</defs>"""

    def header(self) -> str:
        return (f'<rect class="bg" width="{self.W}" height="{self.H}" rx="22"/><rect class="grid" width="{self.W}" height="{self.H}" rx="22" opacity=".7"/>'
                f'<text class="title" x="44" y="58">{self.esc(self.title)}</text><text class="sub" x="44" y="84">{self.esc(self.subtitle)}</text>')

    def card(self, c: Card, pulse_at: float, cycle_mod: float) -> str:
        chip = (f'<g><rect x="{c.x + 10}" y="{c.y + c.h - 21}" rx="8" width="{min(c.w - 20, 7 * len(c.chip) + 20)}" height="15" fill="#0E182B" stroke="#25365A" stroke-width=".8"/>'
                f'<text class="chip-t" x="{c.x + 18}" y="{c.y + c.h - 10.2}">{self.esc(c.chip)}</text></g>') if c.chip else ""
        lines = textwrap.wrap(c.title, 21, break_long_words=False)[:2]
        ty = c.y + (21 if c.chip else 27) + (0 if len(lines) == 2 else 5 if not c.chip else 3)
        text = "".join(f'<text class="name" x="{c.x + 40}" y="{ty + i * 15}">{self.esc(l)}</text>' for i, l in enumerate(lines))
        halo = (f'<rect x="{c.x - 3}" y="{c.y - 3}" width="{c.w + 6}" height="{c.h + 6}" rx="16" fill="none" stroke="{c.color}" stroke-width="2.4" opacity="0" filter="url(#glow)">'
                f'<animate attributeName="opacity" dur="{cycle_mod:.2f}s" begin="{pulse_at:.2f}s" repeatCount="indefinite" keyTimes="0;0.05;0.3;1" values="0;1;0;0"/></rect>')
        return (f'<g filter="url(#soft)"><rect class="card" x="{c.x}" y="{c.y}" width="{c.w}" height="{c.h}" rx="14" stroke="{c.color}" stroke-opacity=".55"/></g>{halo}'
                f'<circle cx="{c.x + 21}" cy="{c.y + 21}" r="11.5" fill="{c.color}"/><text class="badge-t" x="{c.x + 21}" y="{c.y + 25}" text-anchor="middle">{c.index}</text>{text}{chip}')

    def packets(self, path_id: str) -> str:
        out = []
        for k in range(self.PACKETS):
            begin = -(self.CYCLE / self.PACKETS) * k
            out.append(f'<g filter="url(#glow)"><circle r="5.2" fill="#FFFFFF"><animateMotion dur="{self.CYCLE}s" begin="{begin:.2f}s" repeatCount="indefinite" '
                       f'rotate="auto"><mpath href="#{path_id}"/></animateMotion></circle></g>')
            out.append(f'<circle r="9" fill="#FFFFFF" opacity=".18"><animateMotion dur="{self.CYCLE}s" begin="{begin:.2f}s" repeatCount="indefinite"><mpath href="#{path_id}"/></animateMotion></circle>')
        return "".join(out)

    def wrap(self, body: str) -> str:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 {self.W} {self.H}" width="{self.W}" height="{self.H}" role="img" '
                f'aria-label="{self.esc(self.title)}"><title>{self.esc(self.title)}</title><desc>{self.esc(self.subtitle)}</desc><style>{self.css()}</style>{self.defs()}{body}</svg>')


class PipelineSvg(FlowSvg):
    """Swim-lane diagram: one column per stage group, input on the left, outputs on the right."""

    def __init__(self, stages: list[Stage], title: str = "Data pipeline", subtitle: str = "Survey123 submission  →  validated  →  analysed  →  published  →  reported  →  delivered",
                 input_label: tuple[str, str] = ("Survey123 / Field Maps", "webhook POST or polling"),
                 outputs: tuple[tuple[str, str], ...] = (("Live feature layers", "dashboards • Field Maps"), ("PDF reports A and B", "shared drives"), ("E-mail alerts", "EOC • dispatch • fire"))):
        super().__init__(title, subtitle)
        self.stages, self.input_label, self.outputs = stages, input_label, outputs

    def groups(self) -> list[tuple[str, list[int]]]:
        groups: list[tuple[str, list[int]]] = []
        for i, st in enumerate(self.stages):
            g = st.group or "Pipeline"
            if groups and groups[-1][0] == g:
                groups[-1][1].append(i)
            else:
                groups.append((g, [i]))
        return groups

    def render(self) -> str:
        groups = self.groups()
        n = len(groups)
        left_w, right_w, col_w, gap = 168, 190, 190, 46
        x0 = 44
        col_x = [x0 + left_w + gap + i * (col_w + gap) for i in range(n)]
        self.W = int(col_x[-1] + col_w + gap + right_w + 44)
        top, node_h, node_gap = 138, 62, 24
        tallest = max(len(idx) for _, idx in groups)
        self.H = int(top + 46 + tallest * node_h + (tallest - 1) * node_gap + 86)
        cards: dict[int, Card] = {}
        labels = []
        for gi, (gname, idx) in enumerate(groups):
            color = PALETTE[gi % len(PALETTE)]
            labels.append(f'<text class="gh" x="{col_x[gi]}" y="{top - 6}" fill="{color}">{self.esc(gname.upper())}</text>'
                          f'<rect x="{col_x[gi]}" y="{top + 2}" width="{col_w}" height="2.5" rx="1.2" fill="{color}" opacity=".7"/>')
            for k, i in enumerate(idx):
                st = self.stages[i]
                chip = st.external if st.external and len(st.external) <= 30 else (st.external[:28] + "\u2026" if st.external else None)
                cards[i] = Card(i + 1, st.name, chip, color, col_x[gi], top + 46 + k * (node_h + node_gap), col_w, node_h)
        mid_y = top + 46 + (tallest * node_h + (tallest - 1) * node_gap) / 2
        inp = Card(0, "", None, "#FFFFFF", x0, mid_y - 46, left_w, 92)
        order = sorted(cards)
        out_x = col_x[-1] + col_w + gap
        out_h, out_gap = 58, 14
        total_out = len(self.outputs) * out_h + (len(self.outputs) - 1) * out_gap
        outs = [Card(0, t, s, PALETTE[(n + j) % len(PALETTE)], out_x, mid_y - total_out / 2 + j * (out_h + out_gap), right_w, out_h)
                for j, (t, s) in enumerate(self.outputs)]
        # ---- connectors ------------------------------------------------------------------------------------------------------------
        route = Route()
        arrive: dict[int, int] = {}
        arrive[order[0]] = route.add("#FFFFFF", Route.elbow(inp.right, cards[order[0]].left))
        for gi, (_, idx) in enumerate(groups):
            color = PALETTE[gi % len(PALETTE)]
            for a, b in zip(idx, idx[1:]):
                arrive[b] = route.add(color, [cards[a].bottom, cards[b].top])
            if gi < n - 1:
                nxt = groups[gi + 1][1][0]
                arrive[nxt] = route.add(color, Route.elbow(cards[idx[-1]].right, cards[nxt].left))
        last = cards[order[-1]]
        fan_mid = (last.right[0] + outs[0].left[0]) / 2
        for o in outs:
            route.add(o.color, Route.elbow(last.right, o.left, fan_mid))
        cyc = self.CYCLE / self.PACKETS
        # ---- draw --------------------------------------------------------------------------------------------------------------------
        body = [self.header()] + labels
        body.append(f'<path id="main" class="rail" d="{route.main_path()}"/>')
        for color, pts in route.segments:
            body.append(f'<path class="edge" stroke="{color}" d="{route.rounded(pts)}"/>')
        t1, t2 = self.input_label
        body.append(f'<g class="float" filter="url(#soft)"><rect class="card" x="{inp.x}" y="{inp.y}" width="{inp.w}" height="{inp.h}" rx="16" stroke="#FFFFFF" stroke-opacity=".5"/>'
                    f'<rect x="{inp.x + 16}" y="{inp.y + 16}" width="22" height="34" rx="5" fill="none" stroke="#EAF1FA" stroke-width="1.6"/><circle cx="{inp.x + 27}" cy="{inp.y + 44}" r="2" fill="#EAF1FA"/>'
                    f'<path d="M{inp.x + 45},{inp.y + 24} q8,-6 16,0 M{inp.x + 45},{inp.y + 32} q12,-9 24,0" stroke="#4DA3FF" stroke-width="1.8" fill="none" stroke-linecap="round"/>'
                    f'<text class="name" x="{inp.x + 16}" y="{inp.y + 68}">{self.esc(t1)}</text><text class="chip-t" x="{inp.x + 16}" y="{inp.y + 83}">{self.esc(t2)}</text></g>')
        body.append(f'<text class="gh" x="{inp.x}" y="{inp.y - 14}" fill="#EAF1FA">INPUT</text>')
        for i in order:
            t_arrive = route.arrival_fraction(arrive[i]) * self.CYCLE
            body.append(self.card(cards[i], t_arrive % cyc, cyc))
        body.append(f'<text class="gh" x="{out_x}" y="{outs[0].y - 14}" fill="#EAF1FA">OUTPUT</text>')
        for o in outs:
            body.append(f'<g class="float" filter="url(#soft)"><rect class="card" x="{o.x}" y="{o.y}" width="{o.w}" height="{o.h}" rx="14" stroke="{o.color}" stroke-opacity=".6"/>'
                        f'<circle cx="{o.x + 22}" cy="{o.y + o.h / 2}" r="6" fill="{o.color}"/><text class="name" x="{o.x + 38}" y="{o.y + 25}">{self.esc(o.title)}</text>'
                        f'<text class="chip-t" x="{o.x + 38}" y="{o.y + 42}">{self.esc(o.chip or "")}</text></g>')
        c1 = cards[order[0]]
        body.append(f'<path class="reject" d="M{c1.x + 30:.1f},{c1.y - 2} Q{c1.x + 30:.1f},{c1.y - 24} {c1.x + 50:.1f},{c1.y - 24} L{c1.x + c1.w - 4:.1f},{c1.y - 24}"/>'
                    f'<text class="chip-t" x="{c1.x + 56}" y="{c1.y - 29}" fill="#FF8C9B">rejected \u2192 alert GIS admin + EOC</text>')
        body.append(self.packets("main"))
        ly, lx = self.H - 34, 44
        for gi, (gname, _) in enumerate(groups):
            body.append(f'<circle cx="{lx}" cy="{ly}" r="5" fill="{PALETTE[gi % len(PALETTE)]}"/><text class="legend-t" x="{lx + 12}" y="{ly + 4}">{self.esc(gname)}</text>')
            lx += 26 + 7.2 * len(gname)
        body.append(f'<circle cx="{lx + 8}" cy="{ly}" r="5" fill="#fff" filter="url(#glow)"/><text class="legend-t" x="{lx + 20}" y="{ly + 4}">data packet</text>')
        return self.wrap("".join(body))


class StepsSvg(FlowSvg):
    """Single-row animated chain for a short list of (name, description) steps (used for the daily reconciliation job)."""

    def __init__(self, steps: list[tuple[str, str]], title: str, subtitle: str, trigger: str):
        super().__init__(title, subtitle)
        self.steps, self.trigger = steps, trigger

    def render(self) -> str:
        n = len(self.steps)
        card_w, gap, x0, y = 196, 40, 44, 150
        self.W = int(x0 * 2 + 150 + gap + n * card_w + (n - 1) * gap + gap + 170)
        self.H = 320
        trig = Card(0, "", None, "#FFFFFF", x0, y - 12, 150, 92)
        cards = [Card(i + 1, name, None, PALETTE[i % len(PALETTE)], x0 + 150 + gap + i * (card_w + gap), y, card_w, 68) for i, (name, _) in enumerate(self.steps)]
        end = Card(0, "Executive summary", "Director \u2022 CSO \u2022 GIS admin", "#5BE584", cards[-1].x + card_w + gap, y - 6, 170, 80)
        route = Route()
        arrive = [route.add("#FFFFFF", Route.elbow(trig.right, cards[0].left))]
        for a, b in zip(cards, cards[1:]):
            arrive.append(route.add(a.color, Route.elbow(a.right, b.left)))
        route.add(cards[-1].color, Route.elbow(cards[-1].right, end.left))
        cyc = self.CYCLE / self.PACKETS
        body = [self.header(), f'<path id="main" class="rail" d="{route.main_path()}"/>']
        for color, pts in route.segments:
            body.append(f'<path class="edge" stroke="{color}" d="{route.rounded(pts)}"/>')
        body.append(f'<g class="float" filter="url(#soft)"><rect class="card" x="{trig.x}" y="{trig.y}" width="{trig.w}" height="{trig.h}" rx="16" stroke="#FFFFFF" stroke-opacity=".5"/>'
                    f'<circle cx="{trig.x + 32}" cy="{trig.y + 34}" r="17" fill="none" stroke="#EAF1FA" stroke-width="1.8"/><path d="M{trig.x + 32},{trig.y + 23} v11 l8,5" stroke="#4DA3FF" stroke-width="2" fill="none" stroke-linecap="round"/>'
                    f'<text class="name" x="{trig.x + 16}" y="{trig.y + 68}">{self.esc(self.trigger)}</text><text class="chip-t" x="{trig.x + 16}" y="{trig.y + 83}">weekdays \u2022 local time</text></g>')
        for k, c in enumerate(cards):
            body.append(self.card(c, (route.arrival_fraction(arrive[k]) * self.CYCLE) % cyc, cyc))
            for li, line in enumerate(textwrap.wrap(self.steps[k][1], 34)[:3]):
                body.append(f'<text class="chip-t" x="{c.x + 10}" y="{c.y + c.h + 18 + li * 14}">{self.esc(line)}</text>')
        body.append(f'<g class="float" filter="url(#soft)"><rect class="card" x="{end.x}" y="{end.y}" width="{end.w}" height="{end.h}" rx="14" stroke="#5BE584" stroke-opacity=".6"/>'
                    f'<text class="name" x="{end.x + 14}" y="{end.y + 32}">{self.esc(end.title)}</text><text class="chip-t" x="{end.x + 14}" y="{end.y + 52}">{self.esc(end.chip or "")}</text></g>')
        body.append(self.packets("main"))
        return self.wrap("".join(body))
