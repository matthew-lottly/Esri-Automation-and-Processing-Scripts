"""Bundle of the reference/output stores + config the stages share (dependency injection container)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .clients.excavation import ExcavationTicketClient
from .clients.store import FeatureStore
from .clients.weather import WeatherClient
from .config import LayerSchema, RiskParameters, Settings


@dataclass
class Services:
    settings: Settings
    schema: LayerSchema
    params: RiskParameters
    survey: FeatureStore
    incidents: FeatureStore
    plumes: FeatureStore
    electric: FeatureStore
    valves: FeatureStore
    facilities: FeatureStore
    weather: WeatherClient
    tickets: ExcavationTicketClient
    meters: FeatureStore | None = None
    franchise_polygon: object | None = None          # shapely geometry, WGS84, or None when check disabled
    extra_layers: dict = field(default_factory=dict)  # add your own FeatureStores here: they become usable in report maps

    @property
    def layers(self) -> dict[str, FeatureStore]:
        """Name -> FeatureStore registry. Report-template maps and tables refer to layers by these names."""
        reg = {"survey": self.survey, "incidents": self.incidents, "plumes": self.plumes, "electric": self.electric,
               "valves": self.valves, "facilities": self.facilities}
        if self.meters is not None:
            reg["meters"] = self.meters
        reg.update(self.extra_layers)
        return reg
