"""Wire everything together. `build_production` talks to Esri; `build_demo` is fully offline."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import shape
from shapely.ops import unary_union

from .clients.excavation import ExcavationTicketClient, LayerTicketClient, NullTicketClient, RestTicketClient
from .clients.store import FeatureStore, InMemoryFeatureStore, Query
from .clients.weather import FixedWeatherClient, NWSWeatherClient
from .config import LayerSchema, RiskParameters, Settings
from .exceptions import ConfigError
from .ledger import ProcessedLedger
from .models import FeatureRecord, WeatherObservation
from .notify import EmailNotifier
from .pipeline import IncidentPipeline
from .reference import Services
from .stages.analysis import (ElectricIntersectStage, FacilitiesAndMetersStage, GradeStage, PlumeStage, ValveStage)
from .stages.base import Stage
from .stages.ingest import DedupeStage, ExcavationStage, FetchPhotosStage, ValidateStage, WeatherStage
from .stages.notify import NotifyStage
from .stages.publish import PublishStage
from .stages.reporting import ReportStage
from .stages.transform import DispatchTagStage, SchemaTransformStage

log = logging.getLogger(__name__)


@dataclass
class Runtime:
    services: Services
    pipeline: IncidentPipeline
    notifier: EmailNotifier
    ledger: ProcessedLedger


def build_stages(svc: Services, notifier: EmailNotifier, work_dir: Path) -> list[Stage]:
    """THE pipeline definition. The README flow chart is generated from this list."""
    return [ValidateStage(svc), DedupeStage(svc), FetchPhotosStage(svc, work_dir), WeatherStage(svc), ExcavationStage(svc),
            PlumeStage(svc), ElectricIntersectStage(svc), FacilitiesAndMetersStage(svc), ValveStage(svc),
            GradeStage(svc), DispatchTagStage(svc), SchemaTransformStage(svc), PublishStage(svc), ReportStage(svc),
            NotifyStage(svc, notifier)]


def load_franchise_polygon(settings: Settings, store: FeatureStore | None):
    if settings.franchise_geojson:
        gj = json.loads(Path(settings.franchise_geojson).read_text(encoding="utf-8"))
        feats = gj["features"] if gj.get("type") == "FeatureCollection" else [gj]
        return unary_union([shape(f["geometry"]) for f in feats])
    if store is not None:
        recs = store.query(Query(out_fields=[store.oid_field]))
        if not recs:
            raise ConfigError("Franchise layer returned no polygons")
        return unary_union([r.geometry for r in recs if r.geometry is not None])
    return None


def build_production(settings: Settings) -> Runtime:
    settings.validate_for_production()
    from .clients.arcgis_store import ArcGISFeatureStore, connect

    gis = connect(settings)
    S = lambda url, name: ArcGISFeatureStore(gis, url, name)
    schema, params = LayerSchema.load(settings.config_dir), RiskParameters.load(settings.config_dir)
    tickets: ExcavationTicketClient = NullTicketClient()
    if settings.excavation_provider == "rest":
        tickets = RestTicketClient(settings.excavation_rest_url, settings.excavation_rest_api_key)
    elif settings.excavation_provider == "layer":
        tickets = LayerTicketClient(S(settings.excavation_layer_url, "excavation"), schema.excavation_layer.raw())
    fr_store = S(settings.franchise_layer_url, "franchise") if settings.franchise_layer_url and not settings.franchise_geojson else None
    svc = Services(settings, schema, params, survey=S(settings.survey_layer_url, "survey"),
                   incidents=S(settings.incident_layer_url, "incidents"), plumes=S(settings.plume_layer_url, "plumes"),
                   electric=S(settings.electric_layer_url, "electric"), valves=S(settings.valves_layer_url, "valves"),
                   facilities=S(settings.facilities_layer_url, "facilities"), weather=NWSWeatherClient(settings.nws_user_agent),
                   tickets=tickets, meters=S(settings.meters_layer_url, "meters") if settings.meters_layer_url else None,
                   franchise_polygon=load_franchise_polygon(settings, fr_store))
    notifier = EmailNotifier(settings)
    ledger = ProcessedLedger(settings.state_db_path)
    work = Path(settings.state_db_path).parent / "photos"
    return Runtime(svc, IncidentPipeline(svc, build_stages(svc, notifier, work), ledger, notifier), notifier, ledger)


def build_demo(out_dir: Path, config_dir: str = "./config", sample_dir: str = "./sample_data") -> Runtime:
    """Offline runtime: in-memory layers loaded from sample_data/, fixed weather, e-mails written as .eml files."""
    from .sample import load_sample_services

    out_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(report_path_a=str(out_dir / "A_field_tickets"), report_path_b=str(out_dir / "B_risk_assessments"),
                        email_mode="file", email_outbox_dir=str(out_dir / "outbox"), email_from="demo@example.com",
                        eoc=["eoc@example.com"], electric_dispatch=["dispatch@example.com"], fire_liaison=["fire@example.com"],
                        gis_admin=["gis-admin@example.com"], director_gas=["gas-director@example.com"], chief_safety=["cso@example.com"],
                        state_db_path=str(out_dir / "state" / "processed.sqlite"), config_dir=config_dir, timezone="America/Chicago")
    svc = load_sample_services(settings, Path(sample_dir))
    notifier = EmailNotifier(settings)
    ledger = ProcessedLedger(":memory:")
    return Runtime(svc, IncidentPipeline(svc, build_stages(svc, notifier, out_dir / "photos"), ledger, notifier), notifier, ledger)
