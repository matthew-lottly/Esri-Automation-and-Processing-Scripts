"""Defensive extraction of a *pointer* (GlobalID / ObjectID) from a Survey123 webhook body.

The payload is treated as untrusted: only the identifier is used, and the submission itself is re-read
from the survey layer with our own credentials. That also makes us robust to payload-shape differences.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Pointer:
    global_id: str | None
    object_id: int | None
    event: str


def _ci_get(d: dict, name: str) -> Any:
    for k, v in d.items():
        if k.lower() == name:
            return v
    return None


def extract_pointer(payload: Any) -> Pointer | None:
    if not isinstance(payload, dict):
        return None
    event = str(_ci_get(payload, "eventtype") or "")
    feature = _ci_get(payload, "feature") or {}
    attrs = _ci_get(feature, "attributes") if isinstance(feature, dict) else None
    if not isinstance(attrs, dict):
        return None
    gid, oid = _ci_get(attrs, "globalid"), _ci_get(attrs, "objectid")
    if gid is None and oid is None:
        return None
    try:
        oid = int(oid) if oid is not None else None
    except (TypeError, ValueError):
        oid = None
    return Pointer(str(gid).strip("{}").lower() if gid else None, oid, event)


def is_new_submission(event: str) -> bool:
    """Only new submissions trigger processing; edits/deletes are ignored (an edit would re-grade a closed incident)."""
    e = event.lower()
    if not e:
        return True
    return any(t in e for t in ("add", "create", "submit")) and not any(t in e for t in ("edit", "update", "delete"))
