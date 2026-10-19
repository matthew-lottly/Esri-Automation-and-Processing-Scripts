"""Live weather lookup. `NWSWeatherClient` uses api.weather.gov (US only, no key, User-Agent required).

Note: the NWS "latest observation" comes from the nearest reporting station, which can be many km away
and up to ~1 h old. The pipeline records the station and observation age so reports can say so.
Units are read from each value's `unitCode` instead of being assumed.
"""
from __future__ import annotations

import abc
import logging
from datetime import datetime, timedelta, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..models import WeatherObservation

log = logging.getLogger(__name__)


class WeatherClient(abc.ABC):
    @abc.abstractmethod
    def get_observation(self, lat: float, lon: float) -> WeatherObservation | None:
        """Return an observation, or None if weather could not be obtained (caller must cope)."""


class FixedWeatherClient(WeatherClient):
    """Returns a canned observation (demo / tests)."""

    def __init__(self, obs: WeatherObservation | None):
        self.obs = obs

    def get_observation(self, lat, lon):
        return self.obs


def _to_mps(value: float | None, unit: str) -> float | None:
    if value is None:
        return None
    u = (unit or "").lower()
    if "km_h" in u:
        return value / 3.6
    if "m_s" in u:
        return value
    if "mi_h" in u:
        return value * 0.44704
    if "kt" in u or "knot" in u:
        return value * 0.514444
    raise ValueError(f"Unknown wind speed unit: {unit}")


def _to_c(value: float | None, unit: str) -> float | None:
    if value is None:
        return None
    return (value - 32) * 5 / 9 if "degf" in (unit or "").lower() else value


def _to_hpa(value: float | None, unit: str) -> float | None:
    if value is None:
        return None
    u = (unit or "").lower()
    return value / 100 if u.endswith(":pa") else value if "hpa" in u or "mb" in u else None


def parse_observation(props: dict, station: str, max_age_min: int = 90) -> WeatherObservation:
    """Convert an NWS observation `properties` dict to a WeatherObservation (pure function, unit-tested)."""
    g = lambda k: props.get(k) or {}
    ws, wd, t, p = g("windSpeed"), g("windDirection"), g("temperature"), g("barometricPressure")
    obs_time = None
    if props.get("timestamp"):
        obs_time = datetime.fromisoformat(props["timestamp"].replace("Z", "+00:00"))
    speed = _to_mps(ws.get("value"), ws.get("unitCode", ""))
    obs = WeatherObservation(speed, wd.get("value"), _to_c(t.get("value"), t.get("unitCode", "")),
                             _to_hpa(p.get("value"), p.get("unitCode", "")), station, obs_time)
    calm = speed is not None and speed < 1.0
    if speed is None or (obs.wind_from_deg is None and not calm):
        obs.status = "PARTIAL"   # speed missing, or a real wind with no direction
    # (a calm reading with a null direction is normal for NWS and is valid)
    if obs_time and datetime.now(timezone.utc) - obs_time > timedelta(minutes=max_age_min):
        obs.status = "STALE" if obs.status == "OK" else obs.status
    return obs


class NWSWeatherClient(WeatherClient):
    BASE = "https://api.weather.gov"

    def __init__(self, user_agent: str, max_stations: int = 4, timeout: int = 15, session: requests.Session | None = None):
        if not user_agent:
            raise ValueError("NWS requires a User-Agent that identifies you (NWS_USER_AGENT).")
        self.max_stations, self.timeout = max_stations, timeout
        self.s = session or requests.Session()
        self.s.headers.update({"User-Agent": user_agent, "Accept": "application/geo+json"})
        retry = Retry(total=3, backoff_factor=1.0, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
        self.s.mount("https://", HTTPAdapter(max_retries=retry))

    def _get(self, url: str) -> dict:
        r = self.s.get(url, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def get_observation(self, lat: float, lon: float) -> WeatherObservation | None:
        try:
            point = self._get(f"{self.BASE}/points/{lat:.4f},{lon:.4f}")
            stations = self._get(point["properties"]["observationStations"])["features"]
        except (requests.RequestException, KeyError, ValueError) as exc:
            log.warning("NWS point/station lookup failed: %s", exc)
            return None
        best: WeatherObservation | None = None
        for feat in stations[: self.max_stations]:
            sid = feat["properties"]["stationIdentifier"]
            try:
                props = self._get(f"{self.BASE}/stations/{sid}/observations/latest")["properties"]
            except (requests.RequestException, KeyError, ValueError) as exc:
                log.info("NWS station %s unavailable: %s", sid, exc)
                continue
            obs = parse_observation(props, sid)
            if obs.status == "OK":
                return obs
            best = best or obs      # remember the first partial/stale one as a fallback
        return best
