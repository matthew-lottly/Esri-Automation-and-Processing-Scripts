"""Load the synthetic sample data into in-memory stores (offline demo + tests)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shapely.geometry import Point, shape

from .clients.excavation import NullTicketClient
from .clients.store import EpochMs, InMemoryFeatureStore
from .clients.weather import FixedWeatherClient
from .config import LayerSchema, RiskParameters, Settings
from .models import FeatureRecord, WeatherObservation
from .reference import Services


class SampleDataLoader:
    """Reads `sample_data/` (GeoJSON layers + survey submissions + weather) into a ready-to-run Services bundle."""

    def __init__(self, sample_dir: Path):
        self.dir = Path(sample_dir)

    def layer(self, filename: str) -> list[FeatureRecord]:
        feats = json.loads((self.dir / filename).read_text())["features"]
        return [FeatureRecord(dict(f["properties"]), shape(f["geometry"])) for f in feats]

    def submissions(self) -> list[dict]:
        return json.loads((self.dir / "survey_submissions.json").read_text())

    def survey_store(self) -> InMemoryFeatureStore:
        now = datetime.now(timezone.utc)
        recs, attachments = [], {}
        for i, s in enumerate(self.submissions(), 1):
            attrs = dict(s["attributes"], GlobalID="{%08d-0000-4000-8000-%012d}" % (i, i),
                         CreationDate=EpochMs.from_datetime(now - timedelta(minutes=s["minutes_ago"])))
            geom = shape(s["geometry"]) if "geometry" in s else Point(*s["xy"])
            recs.append(FeatureRecord(attrs, geom, i))
            if s["photos"]:
                attachments[i] = [self.dir / "photos" / p for p in s["photos"]]
        return InMemoryFeatureStore("survey", recs, attachments=attachments)

    def weather(self) -> FixedWeatherClient:
        w = json.loads((self.dir / "weather.json").read_text())
        return FixedWeatherClient(WeatherObservation(w["wind_speed_mps"], w["wind_from_deg"], w["air_temp_c"], w["pressure_hpa"],
                                                    w["station"], datetime.now(timezone.utc)))

    def services(self, settings: Settings) -> Services:
        schema, params = LayerSchema.load(settings.config_dir), RiskParameters.load(settings.config_dir)
        franchise = shape(json.loads((self.dir / "franchise.geojson").read_text())["features"][0]["geometry"])
        return Services(settings, schema, params, survey=self.survey_store(),
                        incidents=InMemoryFeatureStore("incidents"), plumes=InMemoryFeatureStore("plumes"),
                        electric=InMemoryFeatureStore("electric", self.layer("electric_assets.geojson")),
                        valves=InMemoryFeatureStore("valves", self.layer("gas_valves.geojson")),
                        facilities=InMemoryFeatureStore("facilities", self.layer("critical_facilities.geojson")),
                        meters=InMemoryFeatureStore("meters", self.layer("customer_meters.geojson")),
                        weather=self.weather(), tickets=NullTicketClient(), franchise_polygon=franchise)
