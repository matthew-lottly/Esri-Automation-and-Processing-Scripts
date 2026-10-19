"""Real ArcGIS implementation of FeatureStore using the ArcGIS API for Python.

!! Written against the documented API (arcgis 2.4.x signatures were checked by introspection) but NOT
!! exercised against a live ArcGIS Online/Enterprise org by the author (no Esri account available).
!! Run `python scripts/check_setup.py` first - it makes read-only calls and tells you what is wrong.
"""
from __future__ import annotations

import logging
from datetime import timezone
from pathlib import Path
from typing import Any

from ..config import Settings
from ..exceptions import ConfigError, StoreError
from ..geo import from_esri_json, to_esri_json
from ..models import FeatureRecord
from .store import FeatureStore, Query

log = logging.getLogger(__name__)


def connect(settings: Settings):
    """Return an authenticated `arcgis.gis.GIS`. Imported lazily so the rest of the code works without arcgis."""
    try:
        from arcgis.gis import GIS
    except ImportError as exc:  # pragma: no cover
        raise ConfigError("The `arcgis` package is not installed. Run: pip install -r requirements.txt") from exc
    try:
        if settings.client_id and settings.client_secret:
            gis = GIS(settings.portal_url, client_id=settings.client_id, client_secret=settings.client_secret)
        else:
            gis = GIS(settings.portal_url, settings.username, settings.password)
    except Exception as exc:  # the API raises many exception types
        raise StoreError(f"Could not sign in to {settings.portal_url}: {exc}") from exc
    if getattr(gis, "users", None) and gis.users.me is None:
        raise StoreError("ArcGIS sign-in returned no user. Check credentials/portal URL.")
    return gis


def _sql_literal(v: Any) -> str:
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


class ArcGISFeatureStore(FeatureStore):
    def __init__(self, gis, url: str, name: str):
        from arcgis.features import FeatureLayer

        self.name = name
        self.layer = FeatureLayer(url, gis)
        try:
            self._oid = self.layer.properties.objectIdField
        except Exception as exc:
            raise StoreError(f"Cannot open layer '{name}' at {url}: {exc}") from exc

    @property
    def oid_field(self) -> str:
        return self._oid

    def field_names(self) -> list[str]:
        return [f["name"] for f in self.layer.properties.fields]

    # ---- reads -----------------------------------------------------------------------
    @staticmethod
    def build_where(q: Query) -> str:
        parts = [f"{k} = {_sql_literal(v)}" for k, v in q.equals.items()]
        if q.time_field:
            fmt = "%Y-%m-%d %H:%M:%S"
            if q.time_from:
                parts.append(f"{q.time_field} >= timestamp '{q.time_from.astimezone(timezone.utc).strftime(fmt)}'")
            if q.time_to:
                parts.append(f"{q.time_field} <= timestamp '{q.time_to.astimezone(timezone.utc).strftime(fmt)}'")
        return " AND ".join(parts) or "1=1"

    def query(self, q: Query) -> list[FeatureRecord]:
        kwargs: dict[str, Any] = dict(where=self.build_where(q), out_fields=q.out_fields or "*",
                                      return_geometry=q.return_geometry, out_sr=4326)
        if q.geometry is not None:
            from arcgis.geometry import Geometry
            from arcgis.geometry.filters import intersects

            kwargs["geometry_filter"] = intersects(Geometry(to_esri_json(q.geometry)), sr=4326)
        if q.order_by:
            kwargs["order_by_fields"] = q.order_by
        try:
            fset = self.layer.query(**kwargs)
        except Exception as exc:
            raise StoreError(f"Query on '{self.name}' failed: {exc}") from exc
        recs = []
        for f in fset.features:
            geom = None
            if q.return_geometry and f.geometry:
                geom = from_esri_json(dict(f.geometry))
            attrs = dict(f.attributes)
            recs.append(FeatureRecord(attrs, geom, attrs.get(self._oid)))
        return recs

    # ---- writes ----------------------------------------------------------------------
    @staticmethod
    def _payload(r: FeatureRecord, oid_field: str) -> dict[str, Any]:
        d: dict[str, Any] = {"attributes": dict(r.attributes)}
        if r.object_id is not None:
            d["attributes"][oid_field] = r.object_id
        if r.geometry is not None:
            d["geometry"] = to_esri_json(r.geometry)
        return d

    def add(self, records: list[FeatureRecord]) -> list[int]:
        res = self._edit(adds=[self._payload(r, self._oid) for r in records])
        return [x["objectId"] for x in res["addResults"]]

    def update(self, records: list[FeatureRecord]) -> None:
        self._edit(updates=[self._payload(r, self._oid) for r in records])

    def _edit(self, **edits) -> dict[str, Any]:
        try:
            res = self.layer.edit_features(rollback_on_failure=True, **edits)
        except Exception as exc:
            raise StoreError(f"edit_features on '{self.name}' failed: {exc}") from exc
        failures = [x for k in ("addResults", "updateResults", "deleteResults") for x in res.get(k, []) if not x.get("success")]
        if failures:
            raise StoreError(f"'{self.name}' rejected edits: {failures}")
        return res

    # ---- attachments -----------------------------------------------------------------
    def list_attachments(self, object_id: int) -> list[dict[str, Any]]:
        try:
            return list(self.layer.attachments.get_list(object_id))
        except Exception as exc:
            raise StoreError(f"Listing attachments for OID {object_id} failed: {exc}") from exc

    def download_attachment(self, object_id: int, attachment_id: int, dest_dir: Path, name: str) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            out = self.layer.attachments.download(oid=object_id, attachment_id=attachment_id, save_path=str(dest_dir))
        except Exception as exc:
            raise StoreError(f"Downloading attachment {attachment_id} failed: {exc}") from exc
        # The API picks the saved file name; normalise to something predictable.
        saved = Path(out[0] if isinstance(out, (list, tuple)) else out)
        target = dest_dir / name
        if saved != target:
            saved.replace(target)
        return target
