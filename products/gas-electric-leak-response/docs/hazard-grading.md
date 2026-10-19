# Hazard Severity Index (HSI) and plume logic

All thresholds live in `config/risk_parameters.yaml`. **Your safety/engineering group must approve them.**

## Inputs
* **Effective %LEL** = the larger of the reported %LEL and ppm/500 (methane: 100 % LEL = 50,000 ppm). Using the larger value is conservative.
* **Plume** (see below), **electric assets inside the plume** by category, **critical facilities**, **customer meters** (proxy for structures).

## Grades
| Grade | Default rule |
|---|---|
| 1 Critical | effective LEL **> 20 %** AND (an `ignition_source` or `conduit` electric asset inside the plume OR a critical facility inside the plume) |
| 2 Urgent | not Grade 1, and (LEL >= 10 %, **or** LEL >= 1 % with any electric asset / critical facility / meter inside the plume) |
| 3 Monitored | otherwise |

**Interpretation choices to confirm.** The business definition says Grade 2 is "measurable gas migration without immediate electrical overlap" and
Grade 3 is "non-hazardous readings away from structures and electric infrastructure". Those overlap for low readings, so two parameters
were introduced: `measurable_lel_pct` (1 %) and `monitored_max_lel_pct` (10 %). A reading below 1 % LEL is always Grade 3.
`excavation_ticket_escalates` (off by default) would raise Grade 3 to 2 when an 811 ticket is within 500 ft.

## Readings outside the instrument range
* Negative %LEL / ppm (or temperature outside the sensor limits) are **rejected** as impossible; the rejection alert goes to the GIS admin **and the EOC** with
  "treat the reported leak as live" wording, because a rejected submission may still be a real leak.
* Readings **above** range (e.g. 140 % LEL, an over-ranged/saturated instrument) are **clamped to the maximum and flagged**, never rejected: they are the most dangerous atmospheres.

## Duplicates and escalations
A submission within 50 ft and 12 h of an existing (non-duplicate) incident is stored as a **duplicate row** (`IS_DUPLICATE=1`) and bumps the parent's
`READING_CNT` / `MAX_LEL_PCT`. It always gets Report 1. It is an **escalation** - and gets Report 2, an e-mail, a replacement plume and an update of the parent's
grade/dispatch/valves - when it grades **worse**, or (while Grade 1-2) its effective %LEL is at least `dedupe.escalation_lel_delta_pct` (10) points above the highest
reading so far. Smaller changes are recorded (counter + max %LEL) but do not re-notify; that threshold is a policy choice to confirm with operations.

## Unmapped electric asset types
Asset-type matching ignores case, spaces, hyphens and underscores. A type that is **not** listed in `layer_schema.yaml` is treated as `grading.unmapped_electric_category`
(default `other`, i.e. not escalating) **and the incident is flagged** "unmapped electric asset type ...". Set it to `ignition_source` if you prefer to over- rather than under-grade.
Coded-value domains: list the *stored* values (integers) in `categories`, or the labels if your service returns labels.

## Plume model (heuristic, not physics)
```
R      = base_radius x pressure_multiplier x material_multiplier x surface_multiplier x (1 + lel_gain x LEL/100)    (capped)
wind>=calm:  downwind length = R x (1 + stretch x wind), upwind radius = R x upwind_fraction, crosswind half-width narrows with wind
             direction = wind FROM bearing + 180 deg (meteorological convention)
calm or no data: circle (x1.5 conservative when wind data is missing)
```
Unknown pressure/material/surface values use conservative multipliers and are flagged. The model never claims to predict concentration;
it only makes the intersect analysis directional. Replace `PlumeModel` (one class, `plume.py`) with your approved method if you have one.

## Valves
Returns the **nearest** N valves within `search_radius_m`. True *upstream* isolation requires a Utility Network trace or gas-operations knowledge;
add a `ValveStage` subclass that calls your trace and register it in `bootstrap.build_stages()`.
