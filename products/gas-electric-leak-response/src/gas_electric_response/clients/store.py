"""FeatureStore: the one interface the pipeline uses to read/write feature layers.

`ArcGISFeatureStore` (arcgis_store.py) talks to real feature services. `InMemoryFeatureStore`
is used by the offline demo and the unit tests, so the whole pipeline runs without an Esri account.
Datetime attributes are exchanged as epoch milliseconds (UTC), exactly like ArcGIS date fields.
"""
from __future__ import annotations

import abc
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shapely.geometry.base import BaseGeometry

from ..models import FeatureRecord


class EpochMs:
    """ArcGIS date fields are epoch milliseconds in UTC."""

    @staticmethod
    def from_datetime(dt: datetime) -> int:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)

    @staticmethod
    def to_datetime(ms: int | float | None) -> datetime | None:
        return None if ms is None else datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


@dataclass
class Query:
    equals: dict[str, Any] = field(default_factory=dict)
    time_field: str | None = None
    time_from: datetime | None = None
    time_to: datetime | None = None
    geometry: BaseGeometry | None = None          # WGS84 intersect filter
    out_fields: list[str] | None = None           # None = all
    return_geometry: bool = True
    order_by: str | None = None                   # e.g. "FIELD DESC"


class FeatureStore(abc.ABC):
    name: str = "store"

    @abc.abstractmethod
    def query(self, q: Query) -> list[FeatureRecord]: ...

    @abc.abstractmethod
    def add(self, records: list[FeatureRecord]) -> list[int]:
        """Insert records; return new object ids. Raises StoreError on any failure."""

    @abc.abstractmethod
    def update(self, records: list[FeatureRecord]) -> None:
        """Update by `object_id`. Raises StoreError on any failure."""

    @abc.abstractmethod
    def list_attachments(self, object_id: int) -> list[dict[str, Any]]: ...

    @abc.abstractmethod
    def download_attachment(self, object_id: int, attachment_id: int, dest_dir: Path, name: str) -> Path: ...

    @property
    @abc.abstractmethod
    def oid_field(self) -> str: ...

    def field_names(self) -> list[str]:
        """Names of the fields the layer defines (used by `check`). Default: unknown."""
        return []

    def count(self, q: Query) -> int:
        return len(self.query(Query(q.equals, q.time_field, q.time_from, q.time_to, q.geometry, [self.oid_field], False)))


class InMemoryFeatureStore(FeatureStore):
    """Dictionary-backed store with the same semantics the pipeline relies on."""

    def __init__(self, name: str, records: list[FeatureRecord] | None = None, oid_field: str = "OBJECTID",
                 attachments: dict[int, list[Path]] | None = None):
        self.name, self._oid = name, oid_field
        self._rows: dict[int, FeatureRecord] = {}
        self._attachments = attachments or {}
        self._next = 1
        for r in records or []:
            self._insert(r)

    @property
    def oid_field(self) -> str:
        return self._oid

    def field_names(self) -> list[str]:
        return sorted({k for r in self._rows.values() for k in r.attributes})

    def _insert(self, r: FeatureRecord) -> int:
        oid = r.object_id if r.object_id is not None else self._next
        self._next = max(self._next, oid + 1)
        self._rows[oid] = FeatureRecord(dict(r.attributes, **{self._oid: oid}), r.geometry, oid)
        return oid

    def query(self, q: Query) -> list[FeatureRecord]:
        out = []
        for r in self._rows.values():
            a = r.attributes
            low = {k.lower(): v for k, v in a.items()}     # ArcGIS field names are case-insensitive
            if any(low.get(k.lower()) != v for k, v in q.equals.items()):
                continue
            if q.time_field:
                v = a.get(q.time_field)
                if v is None or (q.time_from and v < EpochMs.from_datetime(q.time_from)) or (q.time_to and v > EpochMs.from_datetime(q.time_to)):
                    continue
            if q.geometry is not None and (r.geometry is None or not r.geometry.intersects(q.geometry)):
                continue
            attrs = {k: v for k, v in a.items() if q.out_fields is None or k in q.out_fields or k == self._oid}
            out.append(FeatureRecord(attrs, r.geometry if q.return_geometry else None, r.object_id))
        if q.order_by:
            fld, _, direction = q.order_by.partition(" ")
            out.sort(key=lambda r: (r.attributes.get(fld) is None, r.attributes.get(fld)), reverse=direction.upper() == "DESC")
        return out

    def add(self, records: list[FeatureRecord]) -> list[int]:
        return [self._insert(r) for r in records]

    def update(self, records: list[FeatureRecord]) -> None:
        for r in records:
            cur = self._rows[r.object_id]
            cur.attributes.update(r.attributes)
            if r.geometry is not None:
                cur.geometry = r.geometry

    def list_attachments(self, object_id: int) -> list[dict[str, Any]]:
        return [{"id": i, "name": p.name, "contentType": "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"}
                for i, p in enumerate(self._attachments.get(object_id, []), 1)]

    def download_attachment(self, object_id: int, attachment_id: int, dest_dir: Path, name: str) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dst = dest_dir / name
        shutil.copyfile(self._attachments[object_id][attachment_id - 1], dst)
        return dst
