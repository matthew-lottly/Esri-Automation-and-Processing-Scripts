"""Configuration: `.env` secrets/URLs (Settings), YAML tunables (RiskParameters, LayerSchema)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from .exceptions import ConfigError


def _split(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def _bool(v: str | None, default: bool) -> bool:
    return default if v in (None, "") else v.strip().lower() in ("1", "true", "yes", "y", "on")


@dataclass
class Settings:
    """Environment-driven settings. Build with `Settings.from_env()`."""

    portal_url: str = "https://www.arcgis.com"
    username: str = ""
    password: str = ""
    client_id: str = ""
    client_secret: str = ""
    survey_layer_url: str = ""
    incident_layer_url: str = ""
    plume_layer_url: str = ""
    electric_layer_url: str = ""
    valves_layer_url: str = ""
    facilities_layer_url: str = ""
    meters_layer_url: str = ""
    franchise_layer_url: str = ""
    franchise_geojson: str = ""
    require_franchise_check: bool = True
    nws_user_agent: str = ""
    excavation_provider: str = "none"
    excavation_rest_url: str = ""
    excavation_rest_api_key: str = ""
    excavation_layer_url: str = ""
    report_path_a: str = ""
    report_path_b: str = ""
    email_mode: str = "smtp"
    email_outbox_dir: str = "./outbox"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_starttls: bool = True
    smtp_username: str = ""
    smtp_password: str = ""
    email_from: str = ""
    eoc: list[str] = field(default_factory=list)
    electric_dispatch: list[str] = field(default_factory=list)
    fire_liaison: list[str] = field(default_factory=list)
    gis_admin: list[str] = field(default_factory=list)
    director_gas: list[str] = field(default_factory=list)
    chief_safety: list[str] = field(default_factory=list)
    webhook_token: str = ""
    webhook_hmac_secret: str = ""
    webhook_hmac_header: str = "X-Signature"
    state_db_path: str = "./state/processed.sqlite"
    timezone: str = "America/Chicago"
    config_dir: str = "./config"
    log_level: str = "INFO"

    @property
    def rejections_log(self) -> Path:
        return Path(self.state_db_path).with_name("rejections.jsonl")

    @classmethod
    def from_env(cls, env_file: str | None = None) -> "Settings":
        load_dotenv(env_file or ".env", override=False)
        e = os.environ.get
        return cls(
            portal_url=e("ARCGIS_PORTAL_URL", "https://www.arcgis.com"),
            username=e("ARCGIS_USERNAME", ""), password=e("ARCGIS_PASSWORD", ""),
            client_id=e("ARCGIS_CLIENT_ID", ""), client_secret=e("ARCGIS_CLIENT_SECRET", ""),
            survey_layer_url=e("SURVEY_FEATURE_LAYER_URL", ""),
            incident_layer_url=e("INCIDENT_LAYER_URL", ""), plume_layer_url=e("PLUME_LAYER_URL", ""),
            electric_layer_url=e("ELECTRIC_ASSETS_LAYER_URL", ""), valves_layer_url=e("GAS_VALVES_LAYER_URL", ""),
            facilities_layer_url=e("CRITICAL_FACILITIES_LAYER_URL", ""), meters_layer_url=e("CUSTOMER_METERS_LAYER_URL", ""),
            franchise_layer_url=e("FRANCHISE_LAYER_URL", ""), franchise_geojson=e("FRANCHISE_GEOJSON", ""),
            require_franchise_check=_bool(e("REQUIRE_FRANCHISE_CHECK"), True),
            nws_user_agent=e("NWS_USER_AGENT", ""),
            excavation_provider=e("EXCAVATION_PROVIDER", "none").lower(),
            excavation_rest_url=e("EXCAVATION_REST_URL", ""), excavation_rest_api_key=e("EXCAVATION_REST_API_KEY", ""),
            excavation_layer_url=e("EXCAVATION_LAYER_URL", ""),
            report_path_a=e("REPORT_PATH_A", ""), report_path_b=e("REPORT_PATH_B", ""),
            email_mode=e("EMAIL_MODE", "smtp").lower(), email_outbox_dir=e("EMAIL_OUTBOX_DIR", "./outbox"),
            smtp_host=e("SMTP_HOST", ""), smtp_port=int(e("SMTP_PORT", "587") or 587),
            smtp_starttls=_bool(e("SMTP_USE_STARTTLS"), True),
            smtp_username=e("SMTP_USERNAME", ""), smtp_password=e("SMTP_PASSWORD", ""), email_from=e("EMAIL_FROM", ""),
            eoc=_split(e("EOC_DISTRIBUTION")), electric_dispatch=_split(e("ELECTRIC_DISPATCH_DISTRIBUTION")),
            fire_liaison=_split(e("FIRE_LIAISON_DISTRIBUTION")), gis_admin=_split(e("GIS_ADMIN_EMAIL")),
            director_gas=_split(e("DIRECTOR_GAS_OPERATIONS_EMAIL")), chief_safety=_split(e("CHIEF_SAFETY_OFFICER_EMAIL")),
            webhook_token=e("WEBHOOK_TOKEN", ""), webhook_hmac_secret=e("WEBHOOK_HMAC_SECRET", ""),
            webhook_hmac_header=e("WEBHOOK_HMAC_HEADER", "X-Signature"),
            state_db_path=e("STATE_DB_PATH", "./state/processed.sqlite"),
            timezone=e("LOCAL_TIMEZONE", "America/Chicago"), config_dir=e("CONFIG_DIR", "./config"),
            log_level=e("LOG_LEVEL", "INFO"),
        )

    def validate_for_production(self) -> None:
        """Raise ConfigError listing EVERYTHING that is missing (not just the first problem)."""
        missing: list[str] = []
        if not ((self.username and self.password) or (self.client_id and self.client_secret)):
            missing.append("ARCGIS_USERNAME+ARCGIS_PASSWORD or ARCGIS_CLIENT_ID+ARCGIS_CLIENT_SECRET")
        for name, val in [("SURVEY_FEATURE_LAYER_URL", self.survey_layer_url), ("INCIDENT_LAYER_URL", self.incident_layer_url),
                          ("PLUME_LAYER_URL", self.plume_layer_url), ("ELECTRIC_ASSETS_LAYER_URL", self.electric_layer_url),
                          ("GAS_VALVES_LAYER_URL", self.valves_layer_url), ("CRITICAL_FACILITIES_LAYER_URL", self.facilities_layer_url),
                          ("REPORT_PATH_A", self.report_path_a), ("REPORT_PATH_B", self.report_path_b),
                          ("NWS_USER_AGENT", self.nws_user_agent), ("EMAIL_FROM", self.email_from)]:
            if not val:
                missing.append(name)
        if self.require_franchise_check and not (self.franchise_layer_url or self.franchise_geojson):
            missing.append("FRANCHISE_LAYER_URL or FRANCHISE_GEOJSON (or set REQUIRE_FRANCHISE_CHECK=false)")
        if self.email_mode == "smtp" and not self.smtp_host:
            missing.append("SMTP_HOST (or EMAIL_MODE=file)")
        if not (self.eoc or self.electric_dispatch or self.fire_liaison):
            missing.append("at least one of EOC_DISTRIBUTION / ELECTRIC_DISPATCH_DISTRIBUTION / FIRE_LIAISON_DISTRIBUTION")
        if self.excavation_provider == "rest" and not self.excavation_rest_url:
            missing.append("EXCAVATION_REST_URL")
        if self.excavation_provider == "layer" and not self.excavation_layer_url:
            missing.append("EXCAVATION_LAYER_URL")
        if self.excavation_provider not in ("none", "rest", "layer"):
            missing.append("EXCAVATION_PROVIDER must be none | rest | layer")
        if self.email_mode not in ("smtp", "file"):
            missing.append("EMAIL_MODE must be smtp | file")
        if missing:
            raise ConfigError("Missing/invalid configuration:\n  - " + "\n  - ".join(missing))


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


class _Section:
    """Attribute access over a nested dict (keeps YAML keys visible and typo errors loud)."""

    def __init__(self, data: dict[str, Any], path: str = ""):
        self._d, self._p = data, path

    def __getattr__(self, k: str) -> Any:
        if k.startswith("_"):
            raise AttributeError(k)
        if k not in self._d:
            raise ConfigError(f"Missing key '{self._p}{k}' in config YAML")
        v = self._d[k]
        return _Section(v, f"{self._p}{k}.") if isinstance(v, dict) else v

    def get(self, k: str, default: Any = None) -> Any:
        v = self._d.get(k, default)
        return _Section(v, f"{self._p}{k}.") if isinstance(v, dict) else v

    def raw(self) -> dict[str, Any]:
        return self._d


class RiskParameters(_Section):
    @classmethod
    def load(cls, config_dir: str | Path) -> "RiskParameters":
        return cls(_load_yaml(Path(config_dir) / "risk_parameters.yaml"))


class LayerSchema(_Section):
    @classmethod
    def load(cls, config_dir: str | Path) -> "LayerSchema":
        s = cls(_load_yaml(Path(config_dir) / "layer_schema.yaml"))
        for sec in ("survey_layer", "electric_assets", "gas_valves", "critical_facilities", "incident_layer", "plume_layer"):
            s.get(sec)  # fail early if a section vanished
            if sec not in s.raw():
                raise ConfigError(f"layer_schema.yaml is missing section '{sec}'")
        return s

    def incident_field(self, key: str) -> str:
        return self.incident_layer.fields.raw()[key]["name"]
