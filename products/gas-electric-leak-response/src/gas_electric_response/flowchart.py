"""README diagrams generated from the code: animated SVGs, a Mermaid fallback, and a stage table."""
from __future__ import annotations

import re
from pathlib import Path

from .stages.base import Stage
from .workflow import WorkflowPage


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
    """Writes the interactive workflow page and keeps the README in sync (`write` or `check`).

    The animated GIF is recorded separately (scripts/build_workflow_gif.py) because it needs a browser; `check` therefore verifies the
    HTML page, the stage table and the README text - everything that could drift from the code.
    """

    PREVIEW = "https://htmlpreview.github.io/?{repo}/blob/main/{path}"

    def __init__(self, stages: list[Stage], readme: Path, repo_url: str = "https://github.com/matthew-lottly/Esri-Automation-and-Processing-Scripts",
                 product_path: str = "products/gas-electric-leak-response", assets_dir: Path | None = None):
        self.stages, self.readme = stages, Path(readme)
        self.repo_url, self.product_path = repo_url.rstrip("/"), product_path
        self.docs = self.readme.parent / "docs"
        self.assets = Path(assets_dir) if assets_dir else self.docs / "assets"
        self.mermaid, self.injector, self.page = MermaidRenderer(), ReadmeInjector(), WorkflowPage(stages)

    def artifacts(self) -> dict[Path, str]:
        return {self.docs / "workflow.html": self.page.render()}

    def interactive_url(self) -> str:
        return self.PREVIEW.format(repo=self.repo_url, path=f"{self.product_path}/docs/workflow.html")

    def readme_text(self) -> str:
        url, gif = self.interactive_url(), self.assets.relative_to(self.readme.parent).as_posix() + "/workflow.gif"
        names = " &middot; ".join(sc.name for sc in self.page.scenarios)
        blocks = {
            "PIPELINE": f'<p align="center"><a href="{url}"><img src="{gif}" alt="Animated workflow: a field submission moves through validation, analysis, grading, publishing, reports and alerts" width="100%"/></a></p>\n\n'
                        f'<p align="center"><b>&#9654; <a href="{url}">Open the interactive workflow</a></b> &nbsp;|&nbsp; <a href="docs/workflow.html">docs/workflow.html</a> (download and open in any browser)<br/>'
                        f"<sub>Scenarios: {names}. Click any box for what it does, what is tested and its limits.</sub></p>\n\n"
                        f"<details><summary>Static fallback (Mermaid)</summary>\n\n{self.mermaid.pipeline(self.stages)}\n\n</details>",
            "STAGES": StageTable.markdown(self.stages),
        }
        text = self.readme.read_text(encoding="utf-8")
        for name, block in blocks.items():
            text = self.injector.inject(text, name, block)
        return text

    def write(self) -> None:
        self.docs.mkdir(exist_ok=True)
        for path, content in self.artifacts().items():
            path.write_text(content, encoding="utf-8")
        self.readme.write_text(self.readme_text(), encoding="utf-8")

    def stale(self) -> list[str]:
        out = [str(p) for p, c in self.artifacts().items() if not p.exists() or p.read_text(encoding="utf-8") != c]
        if self.readme.read_text(encoding="utf-8") != self.readme_text():
            out.append(str(self.readme))
        return out
