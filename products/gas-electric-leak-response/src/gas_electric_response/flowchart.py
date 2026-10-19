"""Generate Mermaid flow charts FROM the real stage list so README diagrams cannot drift from the code."""
from __future__ import annotations

import re

from .stages.base import Stage

START, END = "<!-- FLOWCHART:{name}:START -->", "<!-- FLOWCHART:{name}:END -->"


def _esc(t: str) -> str:
    """Make text safe inside a double-quoted Mermaid label."""
    return t.replace('"', "'").replace("&", "and").replace("=>", "to").replace("->", "to")


def pipeline_mermaid(stages: list[Stage], trigger_label: str = "Survey123 / Field Maps submission") -> str:
    """One horizontal row per stage `group`; external systems are named inside the node (details in the stage table)."""
    lines = ["flowchart TD", f'  T(["{_esc(trigger_label)}<br/>webhook POST or polling"]):::trigger']
    groups: list[tuple[str, list[int]]] = []
    for i, st in enumerate(stages):
        g = st.group or "Pipeline"
        if groups and groups[-1][0] == g:
            groups[-1][1].append(i)
        else:
            groups.append((g, [i]))
    for gi, (g, idx) in enumerate(groups):
        lines += [f'  subgraph G{gi}["{_esc(g)}"]', "    direction LR"]
        for i in idx:
            st = stages[i]
            ext = f"<br/><i>uses: {_esc(st.external)}</i>" if st.external else ""
            lines.append(f'    S{i}["{i + 1}. {_esc(st.name)}{ext}"]' + (":::ext" if st.external else ""))
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


def stage_table(stages: list[Stage]) -> str:
    rows = ["| # | Stage | Group | What it does | Talks to |", "|---|---|---|---|---|"]
    for i, st in enumerate(stages, 1):
        rows.append(f"| {i} | **{st.name}** | {st.group} | {st.description} | {st.external or '-'} |")
    return "\n".join(rows)


def reconcile_mermaid(steps: list[tuple[str, str]]) -> str:
    lines = ["flowchart TD", '  C(["Scheduler: weekdays 17:00 local"]):::trigger']
    prev = "C"
    for i, (name, desc) in enumerate(steps):
        lines += [f'  D{i}["{i + 1}. {_esc(name)}<br/><i>{_esc(desc)}</i>"]', f"  {prev} --> D{i}"]
        prev = f"D{i}"
    lines += [f"  {prev} --> Z([Executive summary e-mail sent]):::trigger", "  classDef trigger fill:#e8f1fb,stroke:#1f4e79;"]
    return "```mermaid\n" + "\n".join(lines) + "\n```"


def inject(readme: str, name: str, block: str) -> str:
    s, e = START.format(name=name), END.format(name=name)
    pat = re.compile(re.escape(s) + r".*?" + re.escape(e), re.S)
    if not pat.search(readme):
        raise ValueError(f"README is missing the markers {s} ... {e}")
    return pat.sub(lambda _: f"{s}\n{block}\n{e}", readme)
