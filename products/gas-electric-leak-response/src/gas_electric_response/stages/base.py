"""Stage = one step of the pipeline. The pipeline order, descriptions and `external` hints drive
both execution AND the auto-generated README flow chart, so the diagram can never drift from the code."""
from __future__ import annotations

import abc
import logging

from ..models import IncidentContext

log = logging.getLogger(__name__)


class Stage(abc.ABC):
    name: str = "Stage"
    description: str = ""
    external: str | None = None       # external system this stage talks to (shown in the flow chart)
    group: str = ""                   # flow-chart sub-heading, e.g. "Cleaning Stage 1"
    skip_when_rejected: bool = True

    @abc.abstractmethod
    def run(self, ctx: IncidentContext) -> None: ...
