"""README diagrams generated from the code: animated SVGs, a Mermaid fallback, and a stage table."""
from __future__ import annotations

import re
from pathlib import Path

from .stages.base import Stage
from .svgflow import PipelineSvg, StepsSvg


class MermaidRenderer:
    """Static Mermaid fallback (renders natively on GitHub; shown in a collapsible block)."""

    @staticmethod
    def esc(t: str) -> str:
        """Make text safe inside a double-quoted Mermaid label."""
        return t.replace('"', "'").replace("&", "and").replace("=>", "to").replace("->", "to")

    def pipeline(self, stages: list[Stage], trigger_label: str = "Survey123 / Field Maps submission") -> str:
        e = self.esc
        lines = ["flowchart TD", f'  T(["{e(trigger_label)}<br/>webhook POST or polling"]):::trigger']
        groups: list[tuple[str, list[int]]] = []
        for i, st in enumerate(stages):
            g = st.group or "Pipeline"
            if groups and groups[-1][0] == g:
                groups[-1][1].append(i)
            else:
                groups.append((g, [i]))
        for gi, (g, idx) in enumerate(groups):
            lines += [f'  subgraph G{gi}["{e(g)}"]', "    direction LR"]
            for i in idx:
                st = stages[i]
                ext = f"<br/><i>uses: {e(st.external)}</i>" if st.external else ""
                lines.append(f'    S{i}["{i + 1}. {e(st.name)}{ext}"]' + (":::ext" if st.external else ""))
            lines += [f"    {' --> '.join(f'S{i}' for i in idx)}" if len(idx) > 1 else "", "  end"]
        lines.append(f"  T --> S{groups[0][1][0]}")
        for (_, a), (_, b) in zip(groups, groups[1:]):
            lines.append(f"  S{a[-1]} --> S{b[0]}")
        last = groups[-1][1][-1]
        lines += ['  X{{"Rejected or failed?"}}:::warn', '  AL["Alert GIS admin; EOC too if automation failed"]:::warn',
                  "  G0 -. reject / error .-> X", "  X --> AL", f"  S{last} --> E([Done]):::trigger",
                  "  classDef trigger fill:#e8f1fb,stroke:#1f4e79;", "  classDef ext fill:#fff8e1,stroke:#b58900;",
                  "  classDef warn fill:#fdeaea,stroke:#c0392b;"]
        return "```mermaid\n" + "\n".join(l for l in lines if l) + "\n```"

    def steps(self, steps: list[tuple[str, str]], trigger: str) -> str:
        e = self.esc
        lines = ["flowchart LR", f'  C(["{e(trigger)}"]):::trigger']
        prev = "C"
        for i, (name, desc) in enumerate(steps):
            lines += [f'  D{i}["{i + 1}. {e(name)}<br/><i>{e(desc)}</i>"]', f"  {prev} --> D{i}"]
            prev = f"D{i}"
        lines += [f"  {prev} --> Z([Executive summary e-mail]):::trigger", "  classDef trigger fill:#e8f1fb,stroke:#1f4e79;"]
        return "```mermaid\n" + "\n".join(lines) + "\n```"


class StageTable:
    @staticmethod
    def markdown(stages: list[Stage]) -> str:
        rows = ["| # | Stage | Group | What it does | Talks to |", "|:-:|---|---|---|---|"]
        for i, st in enumerate(stages, 1):
            rows.append(f"| {i} | **{st.name}** | {st.group} | {st.description} | {st.external or '–'} |")
        return "\n".join(rows)


class ReadmeInjector:
    """Replaces the text between `<!-- FLOWCHART:NAME:START -->` and `<!-- FLOWCHART:NAME:END -->` markers."""

    START, END = "<!-- FLOWCHART:{name}:START -->", "<!-- FLOWCHART:{name}:END -->"

    def inject(self, readme: str, name: str, block: str) -> str:
        s, e = self.START.format(name=name), self.END.format(name=name)
        pat = re.compile(re.escape(s) + r".*?" + re.escape(e), re.S)
        if not pat.search(readme):
            raise ValueError(f"README is missing the markers {s} ... {e}")
        return pat.sub(lambda _: f"{s}\n{block}\n{e}", readme)


class DiagramPublisher:
    """Produces every generated diagram artifact and keeps the README in sync (`write` or `check`)."""

    def __init__(self, stages: list[Stage], recon_steps: list[tuple[str, str]], readme: Path, assets_dir: Path | None = None):
        self.stages, self.steps, self.readme = stages, recon_steps, Path(readme)
        self.assets = Path(assets_dir) if assets_dir else self.readme.parent / "docs" / "assets"
        self.mermaid, self.injector = MermaidRenderer(), ReadmeInjector()

    def artifacts(self) -> dict[Path, str]:
        pipeline = PipelineSvg(self.stages).render()
        recon = StepsSvg(self.steps, "Daily reconciliation", "Weekdays 17:00 local  →  audit layers and drives  →  executive briefing",
                         "Scheduler 17:00").render()
        return {self.assets / "pipeline.svg": pipeline, self.assets / "reconcile.svg": recon}

    def readme_text(self) -> str:
        rel = lambda p: p.relative_to(self.readme.parent).as_posix()
        pipe, recon = self.assets / "pipeline.svg", self.assets / "reconcile.svg"
        blocks = {
            "PIPELINE": f'<p align="center"><img src="{rel(pipe)}" alt="Animated data pipeline: Survey123 submission to validated, analysed, published, reported and delivered" width="100%"/></p>\n\n'
                        f"<details><summary>Static fallback (Mermaid)</summary>\n\n{self.mermaid.pipeline(self.stages)}\n\n</details>",
            "STAGES": StageTable.markdown(self.stages),
            "RECONCILE": f'<p align="center"><img src="{rel(recon)}" alt="Animated daily reconciliation job" width="100%"/></p>',
        }
        text = self.readme.read_text(encoding="utf-8")
        for name, block in blocks.items():
            text = self.injector.inject(text, name, block)
        return text

    def write(self) -> None:
        self.assets.mkdir(parents=True, exist_ok=True)
        for path, content in self.artifacts().items():
            path.write_text(content, encoding="utf-8")
        self.readme.write_text(self.readme_text(), encoding="utf-8")

    def stale(self) -> list[str]:
        out = [str(p) for p, c in self.artifacts().items() if not p.exists() or p.read_text(encoding="utf-8") != c]
        if self.readme.read_text(encoding="utf-8") != self.readme_text():
            out.append(str(self.readme))
        return out
