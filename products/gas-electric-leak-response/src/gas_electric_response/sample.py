"""Load the synthetic sample data into in-memory stores (offline demo + tests)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shapely.geometry import Point, shape

from .clients.excavation import NullTicketClient
from .clients.store import InMemoryFeatureStore, dt_to_ms
from .clients.weather import FixedWeatherClient
from .config import LayerSchema, RiskParameters, Settings
from .models import FeatureRecord, WeatherObservation
from .reference import Services


def _records(path: Path) -> list[FeatureRecord]:
    return [FeatureRecord(dict(f["properties"]), shape(f["geometry"])) for f in json.loads(path.read_text())["features"]]


def survey_template(sample_dir: Path) -> list[dict]:
    return json.loads((sample_dir / "survey_submissions.json").read_text())


def load_sample_services(settings: Settings, sample_dir: Path) -> Services:
    schema, params = LayerSchema.load(settings.config_dir), RiskParameters.load(settings.config_dir)
    now = datetime.now(timezone.utc)
    survey, attachments = [], {}
    for i, s in enumerate(survey_template(sample_dir), 1):
        attrs = dict(s["attributes"], GlobalID="{%08d-0000-4000-8000-%012d}" % (i, i), CreationDate=dt_to_ms(now - timedelta(minutes=s["minutes_ago"])))
        survey.append(FeatureRecord(attrs, Point(*s["xy"]), i))
        if s["photos"]:
            attachments[i] = [sample_dir / "photos" / p for p in s["photos"]]
    w = json.loads((sample_dir / "weather.json").read_text())
    obs = WeatherObservation(w["wind_speed_mps"], w["wind_from_deg"], w["air_temp_c"], w["pressure_hpa"], w["station"], now)
    franchise = shape(json.loads((sample_dir / "franchise.geojson").read_text())["features"][0]["geometry"])
    return Services(settings, schema, params,
                    survey=InMemoryFeatureStore("survey", survey, attachments=attachments),
                    incidents=InMemoryFeatureStore("incidents"), plumes=InMemoryFeatureStore("plumes"),
                    electric=InMemoryFeatureStore("electric", _records(sample_dir / "electric_assets.geojson")),
                    valves=InMemoryFeatureStore("valves", _records(sample_dir / "gas_valves.geojson")),
                    facilities=InMemoryFeatureStore("facilities", _records(sample_dir / "critical_facilities.geojson")),
                    meters=InMemoryFeatureStore("meters", _records(sample_dir / "customer_meters.geojson")),
                    weather=FixedWeatherClient(obs), tickets=NullTicketClient(), franchise_polygon=franchise)
