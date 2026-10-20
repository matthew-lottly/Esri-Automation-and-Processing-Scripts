"""Colours shared by report HTML (as CSS variables) and report maps. Edit templates/theme.yaml."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Theme:
    name: str = "slate"
    colors: dict[str, str] = field(default_factory=lambda: {
        "ink": "#14202B", "muted": "#5F6C7B", "faint": "#93A0AE", "rule": "#E3E8EE", "surface": "#F6F8FA",
        "accent": "#1F6FEB", "white": "#FFFFFF", "grade_1": "#D1271E", "grade_2": "#E8890C", "grade_3": "#2F9E5B"})

    @classmethod
    def load(cls, path: Path | None) -> "Theme":
        t = cls()
        if path and Path(path).exists():
            raw: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
            t.name = raw.get("name", t.name)
            t.colors.update(raw.get("colors", {}))
        return t

    def hex(self, name: str) -> str:
        return self.colors.get(name, name)

    def grade_hex(self, grade: int | None) -> str:
        return self.colors.get(f"grade_{grade}", self.colors["muted"])

    def css_variables(self) -> str:
        """`:root{--ink:#14202B; ...}` - injected into every report so CSS and maps use the same palette."""
        return ":root{" + ";".join(f"--{k.replace('_', '-')}:{v}" for k, v in self.colors.items()) + "}"
