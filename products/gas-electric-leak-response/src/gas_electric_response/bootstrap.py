"""Wire everything together. `ProductionRuntimeFactory` talks to Esri; `DemoRuntimeFactory` is fully offline."""
from __future__ import annotations

import abc
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import shape
from shapely.ops import unary_union

from .clients.arcgis_store import ArcGISConnection, ArcGISFeatureStore
from .clients.excavation import ExcavationTicketClient, LayerTicketClient, NullTicketClient, RestTicketClient
from .clients.store import FeatureStore, Query
from .clients.weather import NWSWeatherClient
from .config import LayerSchema, RiskParameters, Settings
from .exceptions import ConfigError
from .ledger import ProcessedLedger
from .notify import EmailNotifier
from .pipeline import IncidentPipeline
from .reference import Services
from .stages.analysis import ElectricIntersectStage, FacilitiesStage, GradeStage, MetersStage, PlumeStage, ValveStage
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


class PipelineBuilder:
    """THE pipeline definition. The README flow chart is generated from this list."""

    def __init__(self, svc: Services, notifier: EmailNotifier, work_dir: Path):
        self.svc, self.notifier, self.work_dir = svc, notifier, work_dir

    def stages(self) -> list[Stage]:
        svc = self.svc
        return [ValidateStage(svc), DedupeStage(svc), FetchPhotosStage(svc, self.work_dir), WeatherStage(svc), ExcavationStage(svc),
                PlumeStage(svc), ElectricIntersectStage(svc), FacilitiesStage(svc), MetersStage(svc), ValveStage(svc),
                GradeStage(svc), DispatchTagStage(svc), SchemaTransformStage(svc), PublishStage(svc), ReportStage(svc),
                NotifyStage(svc, self.notifier)]

    def runtime(self, ledger: ProcessedLedger, rejections_log: Path | None = None) -> Runtime:
        pipe = IncidentPipeline(self.svc, self.stages(), ledger, self.notifier, rejections_log)
        return Runtime(self.svc, pipe, self.notifier, ledger)


class FranchiseLoader:
    """The licensed franchise area as one polygon, from a local GeoJSON file or a polygon feature layer."""

    def __init__(self, settings: Settings, store: FeatureStore | None = None):
        self.settings, self.store = settings, store

    def load(self):
        if self.settings.franchise_geojson:
            gj = json.loads(Path(self.settings.franchise_geojson).read_text(encoding="utf-8"))
            feats = gj["features"] if gj.get("type") == "FeatureCollection" else [gj]
            return unary_union([shape(f["geometry"]) for f in feats])
        if self.store is not None:
            recs = self.store.query(Query(out_fields=[self.store.oid_field]))
            if not recs:
                raise ConfigError("Franchise layer returned no polygons")
            return unary_union([r.geometry for r in recs if r.geometry is not None])
        return None


class RuntimeFactory(abc.ABC):
    @abc.abstractmethod
    def create(self) -> Runtime: ...


class ProductionRuntimeFactory(RuntimeFactory):
    def __init__(self, settings: Settings):
        self.settings = settings

    def _tickets(self, store_for, schema: LayerSchema) -> ExcavationTicketClient:
        st = self.settings
        if st.excavation_provider == "rest":
            return RestTicketClient(st.excavation_rest_url, st.excavation_rest_api_key)
        if st.excavation_provider == "layer":
            return LayerTicketClient(store_for(st.excavation_layer_url, "excavation"), schema.excavation_layer.raw())
        return NullTicketClient()

    def create(self) -> Runtime:
        st = self.settings
        st.validate_for_production()
        gis = ArcGISConnection(st).open()
        store = lambda url, name: ArcGISFeatureStore(gis, url, name)
        schema, params = LayerSchema.load(st.config_dir), RiskParameters.load(st.config_dir)
        fr_store = store(st.franchise_layer_url, "franchise") if st.franchise_layer_url and not st.franchise_geojson else None
        svc = Services(st, schema, params, survey=store(st.survey_layer_url, "survey"),
                       incidents=store(st.incident_layer_url, "incidents"), plumes=store(st.plume_layer_url, "plumes"),
                       electric=store(st.electric_layer_url, "electric"), valves=store(st.valves_layer_url, "valves"),
                       facilities=store(st.facilities_layer_url, "facilities"), weather=NWSWeatherClient(st.nws_user_agent),
                       tickets=self._tickets(store, schema), meters=store(st.meters_layer_url, "meters") if st.meters_layer_url else None,
                       franchise_polygon=FranchiseLoader(st, fr_store).load())
        notifier = EmailNotifier(st)
        return PipelineBuilder(svc, notifier, Path(st.state_db_path).parent / "photos").runtime(ProcessedLedger(st.state_db_path))


class DemoRuntimeFactory(RuntimeFactory):
    """Offline runtime: in-memory layers from sample_data/, fixed weather, e-mails written as .eml files."""

    def __init__(self, out_dir: Path, config_dir: str = "./config", sample_dir: str = "./sample_data"):
        self.out_dir, self.config_dir, self.sample_dir = out_dir, config_dir, sample_dir

    def settings(self) -> Settings:
        o = self.out_dir
        return Settings(report_path_a=str(o / "A_field_tickets"), report_path_b=str(o / "B_risk_assessments"),
                        email_mode="file", email_outbox_dir=str(o / "outbox"), email_from="demo@example.com",
                        eoc=["eoc@example.com"], electric_dispatch=["dispatch@example.com"], fire_liaison=["fire@example.com"],
                        gis_admin=["gis-admin@example.com"], director_gas=["gas-director@example.com"], chief_safety=["cso@example.com"],
                        state_db_path=str(o / "state" / "processed.sqlite"), config_dir=self.config_dir, timezone="America/Chicago",
                        template_dir=str(Path(self.config_dir).parent / "templates"), placeholder_media=True)

    def create(self) -> Runtime:
        from .sample import SampleDataLoader

        self.out_dir.mkdir(parents=True, exist_ok=True)
        settings = self.settings()
        svc = SampleDataLoader(Path(self.sample_dir)).services(settings)
        notifier = EmailNotifier(settings)
        return PipelineBuilder(svc, notifier, self.out_dir / "photos").runtime(ProcessedLedger(":memory:"))
