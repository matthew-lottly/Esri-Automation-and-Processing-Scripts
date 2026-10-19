"""`check` command: read-only verification that your layers contain the fields this tool expects."""
from __future__ import annotations

from .reference import Services


def expected_fields(svc: Services) -> dict[str, tuple[object, list[str]]]:
    s = svc.schema
    surv = s.survey_layer
    out = {
        "survey": (svc.survey, [surv.leak_id, surv.lel_pct, surv.ppm, surv.pipeline_type, surv.pressure_class, surv.surface_cover,
                                surv.crew_badge, surv.created_field]),
        "electric": (svc.electric, [s.electric_assets.asset_id_field, s.electric_assets.asset_type_field]),
        "valves": (svc.valves, [s.gas_valves.valve_id_field]),
        "facilities": (svc.facilities, [s.critical_facilities.name_field, s.critical_facilities.type_field]),
        "incidents": (svc.incidents, [v["name"] for v in s.incident_layer.fields.raw().values()]),
        "plumes": (svc.plumes, [v["name"] for v in s.plume_layer.fields.raw().values()]),
    }
    if s.electric_assets.get("energized_field"):
        out["electric"][1].append(s.electric_assets.energized_field)
    return out


def run_checks(svc: Services) -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []
    for name, (store, wanted) in expected_fields(svc).items():
        try:
            have = {f.lower() for f in store.field_names()}
        except Exception as exc:
            results.append((False, f"{name}: cannot open layer ({exc})"))
            continue
        missing = [w for w in wanted if w.lower() not in have]
        results.append((not missing, f"{name}: " + ("all expected fields present" if not missing else f"MISSING fields {missing}")))
    results.append((svc.franchise_polygon is not None or not svc.settings.require_franchise_check,
                    "franchise polygon loaded" if svc.franchise_polygon is not None else "franchise polygon NOT loaded"))
    return results
