"""Call-Before-You-Dig (811) excavation tickets within a radius of the leak.

There is NO single national 811 REST API: one-call centers and ticket-management vendors each expose
their own. So this module offers (a) `RestTicketClient`, a configurable generic JSON-over-HTTP client you
point at YOUR provider, (b) `LayerTicketClient` for utilities that load tickets into a feature layer, and
(c) `NullTicketClient`. See docs/excavation-811.md for how to adapt the REST mapping.
"""
from __future__ import annotations

import abc
import logging
from typing import Any

import requests
from shapely.geometry import Point

from ..geo import LocalFrame
from ..models import ExcavationTicket
from .store import FeatureStore, Query

log = logging.getLogger(__name__)
RADIUS_500FT_M = 152.4


class ExcavationTicketClient(abc.ABC):
    @abc.abstractmethod
    def find_tickets(self, lat: float, lon: float, radius_m: float = RADIUS_500FT_M) -> list[ExcavationTicket] | None:
        """Active tickets within the radius, or None if the lookup failed (distinct from 'no tickets')."""


class NullTicketClient(ExcavationTicketClient):
    def find_tickets(self, lat, lon, radius_m=RADIUS_500FT_M):
        return []


class LayerTicketClient(ExcavationTicketClient):
    def __init__(self, store: FeatureStore, fields: dict[str, str]):
        self.store, self.f = store, fields

    def find_tickets(self, lat, lon, radius_m=RADIUS_500FT_M):
        try:
            frame = LocalFrame(lon, lat)
            recs = self.store.query(Query(geometry=frame.circle(radius_m)))
        except Exception as exc:
            log.warning("Ticket layer query failed: %s", exc)
            return None
        out = []
        for r in recs:
            a = r.attributes
            out.append(ExcavationTicket(str(a.get(self.f["ticket_number_field"], "")), str(a.get(self.f.get("status_field", ""), "")),
                                        str(a.get(self.f.get("work_type_field", ""), "")),
                                        frame.distance(Point(lon, lat), r.geometry) if r.geometry is not None else None,
                                        str(a.get(self.f.get("expires_field", ""), ""))))
        return out


class RestTicketClient(ExcavationTicketClient):
    """Generic REST client. ADAPT `request_params` / `parse_item` to your provider's contract.

    Default contract (a placeholder, not a real service): GET {url}?lat=..&lon=..&radius_m=.. with header
    `Authorization: Bearer <key>` returning {"tickets": [{"ticket": "...", "status": "...", "work_type": "...",
    "distance_m": 12.3, "expires": "..."}]}.
    """

    def __init__(self, url: str, api_key: str = "", timeout: int = 15, session: requests.Session | None = None):
        self.url, self.timeout = url, timeout
        self.s = session or requests.Session()
        if api_key:
            self.s.headers["Authorization"] = f"Bearer {api_key}"

    def request_params(self, lat: float, lon: float, radius_m: float) -> dict[str, Any]:
        return {"lat": lat, "lon": lon, "radius_m": radius_m}

    def parse_item(self, item: dict[str, Any]) -> ExcavationTicket:
        return ExcavationTicket(str(item.get("ticket", "")), str(item.get("status", "")), str(item.get("work_type", "")),
                                item.get("distance_m"), str(item.get("expires", "")))

    def find_tickets(self, lat, lon, radius_m=RADIUS_500FT_M):
        try:
            r = self.s.get(self.url, params=self.request_params(lat, lon, radius_m), timeout=self.timeout)
            r.raise_for_status()
            return [self.parse_item(i) for i in r.json().get("tickets", [])]
        except (requests.RequestException, ValueError, AttributeError) as exc:
            log.warning("811 lookup failed: %s", exc)
            return None
