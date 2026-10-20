"""Terminal presentation (rich). Keeps all colours/layout out of the business logic."""
from __future__ import annotations

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .models import IncidentContext

GRADE_STYLE = {1: "bold white on red3", 2: "bold black on dark_orange", 3: "bold white on green4"}


class ConsoleUI:
    def __init__(self, console: Console | None = None):
        self.c = console or Console()

    def banner(self, title: str, subtitle: str = "") -> None:
        self.c.print(Panel(Text.assemble((title, "bold cyan"), ("\n" + subtitle, "dim")), box=box.ROUNDED, border_style="cyan", padding=(1, 3)))

    def info(self, msg: str) -> None:
        self.c.print(f"[cyan]ℹ[/cyan] {msg}")

    def ok(self, msg: str) -> None:
        self.c.print(f"[green]✔[/green] {msg}")

    def fail(self, msg: str) -> None:
        self.c.print(f"[red]✘[/red] {msg}")

    def results(self, rows: list[tuple[str, IncidentContext | None]]) -> None:
        t = Table(title="Pipeline results", box=box.SIMPLE_HEAVY, header_style="bold", title_style="bold")
        for col in ("Submission", "Outcome", "Dispatch", "Electric in plume", "Valves", "Reports", "E-mail"):
            t.add_column(col, overflow="fold")
        for lid, ctx in rows:
            if ctx is None:
                t.add_row(lid, "[dim]skipped[/dim]", "", "", "", "", "")
            elif ctx.rejected:
                t.add_row(lid, f"[red]REJECTED[/red] [dim]{ctx.rejected.split(': ', 1)[-1]}[/dim]", "", "", "", "", "")
            else:
                g = int(ctx.grade)
                outcome = Text(f" GRADE {g} {ctx.grade.label.upper()} ", style=GRADE_STYLE[g])
                if ctx.is_duplicate:
                    outcome.append(f"  duplicate of {ctx.duplicate_of}" + (" (escalation)" if ctx.is_escalation else ""), style="dim")
                t.add_row(lid, outcome, ctx.dispatch_code.replace("DISPATCH_", ""), str(len(ctx.electric_hits)),
                          ", ".join(v.valve_id for v in ctx.valves) or "–", f"A:{ctx.report_a_status}  B:{ctx.report_b_status}", ctx.notify_status)
        self.c.print(t)

    def checks(self, results) -> int:
        bad = 0
        for r in results:
            (self.ok if r.ok else self.fail)(r.message)
            bad += not r.ok
        return bad

    def listing(self, title: str, mapping: dict[str, list[str]]) -> None:
        t = Table(title=title, box=box.SIMPLE, header_style="bold cyan")
        t.add_column("Namespace", style="bold")
        t.add_column("Placeholders you can use")
        for k, v in mapping.items():
            t.add_row(k, ", ".join(("{{ raw['%s'] }}" % x) if k == "raw" else "{{ %s.%s }}" % (k, x) for x in v) if v else "{{ %s }}" % k)
        self.c.print(t)
