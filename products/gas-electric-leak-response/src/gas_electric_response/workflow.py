"""Interactive workflow page: nodes come from the REAL stage list, scenarios are validated against it.

`WorkflowModel.from_stages()` turns the pipeline into nodes and edges; `ScenarioLibrary` describes the stories told on top of it
(each step names the nodes/edges it lights up and is checked against the model, so a renamed or removed stage fails CI instead of
leaving a stale diagram); `WorkflowPage` renders one self-contained HTML file (inline CSS + JS, no web fonts, no network).
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .stages.base import Stage

TEMPLATE = Path(__file__).with_name("workflow_template.html")


@dataclass
class WNode:
    id: str
    col: str
    name: str
    sub: str
    kind: str                  # logic (pure code, tested offline) | external (talks to another system) | person | virtual
    what: str
    today: str
    bound: str = ""
    num: int | None = None      # step number in the pipeline (stages only)


@dataclass
class WEdge:
    id: str
    src: str
    dst: str
    note: str = ""


@dataclass
class WStep:
    title: str
    text: str
    nodes: list[str] = field(default_factory=list)
    edges: list[str] = field(default_factory=list)


@dataclass
class WScenario:
    name: str
    steps: list[WStep]


class StageIds:
    @staticmethod
    def of(stage: Stage) -> str:
        return re.sub(r"Stage$", "", type(stage).__name__).lower()


class WorkflowModel:
    COLUMNS = [("in", "Inputs"), ("ingest", "Validate & enrich"), ("analyse", "Analyse"), ("decide", "Decide & publish"),
               ("deliver", "Report & alert"), ("out", "People & systems")]
    GROUP_COLUMN = {"Cleaning Stage 1": "ingest", "Spatial & Risk Analysis": "analyse", "Cleaning Stage 2": "decide",
                    "Enterprise GIS Update": "decide", "Reporting & Distribution": "deliver"}
    TESTED = "Implemented and unit-tested offline on synthetic data."
    UNVERIFIED = "Logic tested offline; the live call to {ext} has NOT been verified in a real environment."

    @staticmethod
    def short(text: str, width: int = 34) -> str:
        """Shorten at a word boundary (never mid-word, no dangling punctuation)."""
        if len(text) <= width:
            return text
        cut = text[:width].rsplit(" ", 1)[0].rstrip(" ,;:-")
        return cut + "\u2026"

    def __init__(self, nodes: list[WNode], edges: list[WEdge]):
        self.nodes, self.edges = nodes, edges
        self.node_ids, self.edge_ids = {n.id for n in nodes}, {e.id for e in edges}

    # ---- construction ------------------------------------------------------------------------
    @classmethod
    def from_stages(cls, stages: list[Stage]) -> "WorkflowModel":
        nodes: list[WNode] = [
            WNode("in-survey", "in", "Survey123 / Field Maps", "technician submission", "external", "The field technician's gas-leak inspection: readings, location (point, line or polygon), photos, badge.",
                  "Form definition provided (survey123/gas_leak_form.xlsx).", "Needs an ArcGIS org with Survey123. Not verified against a live org."),
            WNode("in-intake", "in", "Webhook or poller", "idempotent intake", "logic", "Receives the submission's ID (webhook) or finds new records (polling). Only the ID is trusted: the record is re-read from the survey layer.",
                  "Tested offline: token/HMAC auth, retries without blocking, atomic claim so serve + poll never double-process.", "Real Survey123 webhook traffic has not been tested."),
            WNode("in-layers", "in", "ArcGIS reference layers", "electric | valves | facilities | meters", "external", "Your enterprise layers: electric assets, gas valves, critical facilities, customer meters, franchise area.",
                  "Read through the FeatureStore interface; field names mapped in config/layer_schema.yaml.", "Read-only. Not verified against a live org - run the `check` command first."),
            WNode("in-nws", "in", "NWS weather API", "wind, temperature, pressure", "external", "Nearest-station observation for the leak location (api.weather.gov, no key).",
                  "Parsing and unit conversion tested offline.", "The live API has not been called from the development environment."),
            WNode("in-811", "in", "811 ticket service", "excavation tickets (optional)", "external", "Active dig tickets within 500 ft.", "Placeholder REST contract or a ticket layer you adapt.", "No national 811 API exists."),
        ]
        edges: list[WEdge] = [WEdge("survey-intake", "in-survey", "in-intake"), WEdge("intake-validate", "in-intake", "validate")]
        prev = None
        for num, st in enumerate(stages, 1):
            sid = StageIds.of(st)
            ext = st.external
            nodes.append(WNode(sid, cls.GROUP_COLUMN.get(st.group, "analyse"), st.name, ext or cls.short(st.description), "external" if ext else "logic", st.description,
                               cls.UNVERIFIED.format(ext=ext) if ext else cls.TESTED, st.limits, num))
            if prev:
                edges.append(WEdge(f"seq-{prev}-{sid}", prev, sid))
            prev = sid
        have = {n.id for n in nodes}
        for sid, dst in (("in-nws", "weather"), ("in-811", "excavation"), ("in-layers", "electricintersect"), ("in-layers", "facilities"),
                         ("in-layers", "meters"), ("in-layers", "valve")):
            if dst in have:
                edges.append(WEdge(f"{sid}-{dst}", sid, dst))
        nodes += [
            WNode("retry", "deliver", "Retry failed alerts", "every 60 s until sent", "logic", "Re-sends any Grade 1-2 notification whose status is FAILED or stuck, from the layer row and the PDF already on the drive.",
                  "Tested offline.", "Provider acceptance is not inbox receipt."),
            WNode("reconcile", "deliver", "Daily 17:00 reconciliation", "audit + executive summary", "external", "Rolling-window audit of layers and shared drives; e-mails totals, grades, joint hazards and discrepancies.",
                  "Tested offline (missing PDFs, missed submissions, failed e-mails are detected).", "Schedule it with cron / Task Scheduler. Windows scheduling and UNC shares untested."),
            WNode("out-layers", "out", "Live feature layers", "dashboards | Field Maps", "external", "Active_Gas_Electric_Incidents: incident points and plume polygons update in real time.", "Fields come from layer_schema.yaml.", "Hosted-layer writes not verified live."),
            WNode("out-drives", "out", "Shared drives A and B", "write-once PDFs", "external", "Location A: field tickets. Location B: hazard assessments. YYYY-MM folders, SHA-256 sidecars.", "Tested with local paths.", "UNC shares untested; immutability is enforced by share permissions."),
            WNode("out-eoc", "out", "EOC / dispatch / fire", "grade 1-2 alerts", "person", "Emergency Operations Center, Electric Dispatch and the Municipal Fire Liaison.", "HTML + plain-text e-mail with the hazard PDF attached.", "Screening-level output: confirm with the field crew."),
            WNode("out-gis", "out", "GIS administrator", "rejections and failures", "person", "Receives rejected-submission and automation-failure alerts (with the EOC when a leak was not graded).", "Tested offline.", ""),
            WNode("out-exec", "out", "Director / CSO / GIS admin", "daily summary", "person", "Director of Gas Operations, Chief Safety Officer and GIS Systems Administrator.", "HTML summary with totals and discrepancies.", ""),
        ]
        edges += [WEdge("publish-layers", "publish", "out-layers"), WEdge("report-drives", "report", "out-drives"), WEdge("notify-eoc", "notify", "out-eoc"),
                  WEdge("reject-gis", "validate", "out-gis"), WEdge("reject-eoc", "validate", "out-eoc"), WEdge("notify-retry", "notify", "retry"),
                  WEdge("retry-eoc", "retry", "out-eoc"), WEdge("dedupe-reads", "out-layers", "dedupe"), WEdge("layers-reconcile", "out-layers", "reconcile"),
                  WEdge("drives-reconcile", "out-drives", "reconcile"), WEdge("reconcile-exec", "reconcile", "out-exec")]
        return cls(nodes, edges)

    def validate(self, scenarios: list[WScenario]) -> None:
        """Every id a scenario mentions must exist - a renamed/removed stage fails here (and in CI)."""
        for sc in scenarios:
            for st in sc.steps:
                for i in st.nodes:
                    if i not in self.node_ids:
                        raise ValueError(f"scenario '{sc.name}' step '{st.title}': unknown node '{i}'")
                for i in st.edges:
                    if i not in self.edge_ids:
                        raise ValueError(f"scenario '{sc.name}' step '{st.title}': unknown edge '{i}'")

    def to_dict(self) -> dict:
        return {"columns": [{"id": c, "label": l} for c, l in self.COLUMNS], "nodes": [asdict(n) for n in self.nodes], "edges": [asdict(e) for e in self.edges]}


class ScenarioLibrary:
    """The stories told on the diagram for THIS product. Add a scenario by appending to `build()`."""

    @staticmethod
    def build() -> list[WScenario]:
        S = WStep
        return [
            WScenario("Critical leak (Grade 1)", [
                S("A technician submits a leak", "A crew member fills in the Survey123 form beside a pad-mount transformer: 35 % LEL, 17,500 ppm, asphalt, cast iron, a location and photos.", ["in-survey", "in-intake"], ["survey-intake"]),
                S("Intake trusts only the ID", "The webhook (or the poller) hands over just the submission's GlobalID. The record is re-read from the survey layer with our own credentials, so a forged request cannot inject readings. An atomic claim means two workers never process the same submission.", ["in-intake", "validate"], ["intake-validate"]),
                S("Validate and de-duplicate", "Coordinates must fall inside the franchise area; negative readings are rejected, over-range readings are clamped and flagged. Another leak within 50 ft in the last 12 hours would make this a duplicate - here it is a new incident.", ["validate", "dedupe"], ["seq-validate-dedupe"]),
                S("Enrich with photos, weather and 811", "Photos are downloaded for the field ticket. The nearest NWS station gives wind (4 m/s from the west); 811 tickets within 500 ft are checked. Missing or stale weather produces a conservative omnidirectional envelope instead of failing.", ["fetchphotos", "weather", "excavation", "in-nws", "in-811"], ["seq-dedupe-fetchphotos", "seq-fetchphotos-weather", "seq-weather-excavation", "in-nws-weather", "in-811-excavation"]),
                S("Build a directional plume", "Pressure class, material, surface cover, %LEL and wind shape a downwind envelope around the submitted feature (point, line or polygon). It is a screening heuristic, not a dispersion model.", ["plume"], ["seq-excavation-plume"]),
                S("Intersect with the grid and the neighbourhood", "The envelope is intersected with electric assets (pad-mount ignition sources, underground conduit, lines report length in plume), critical facilities and customer meters; the three nearest gas valves are found.", ["electricintersect", "facilities", "meters", "valve", "in-layers"], ["seq-plume-electricintersect", "seq-electricintersect-facilities", "seq-facilities-meters", "seq-meters-valve", "in-layers-electricintersect", "in-layers-facilities", "in-layers-meters", "in-layers-valve"]),
                S("Grade the hazard", "> 20 % LEL overlapping an ignition source or a critical facility is Grade 1 - Critical. The dispatch code becomes DISPATCH_JOINT_GAS_ELECTRIC_CREW.", ["grade", "dispatchtag"], ["seq-valve-grade", "seq-grade-dispatchtag"]),
                S("Align and publish", "Attributes are mapped to your schema (names, types, integer status codes, status Active_Triage). The incident point and plume polygon are written to the feature service; dashboards and Field Maps update live.", ["schematransform", "publish", "out-layers"], ["seq-dispatchtag-schematransform", "seq-schematransform-publish", "publish-layers"]),
                S("Two compliance PDFs", "Report 1 (field ticket) and Report 2 (hazard assessment) are rendered from your Jinja2 templates and written write-once to the two shared drives, with SHA-256 sidecars.", ["report", "out-drives"], ["seq-publish-report", "report-drives"]),
                S("Alert the people who act", "Grade 1-2 sends an HTML alert with the hazard PDF to the EOC, electric dispatch and the fire liaison.", ["notify", "out-eoc"], ["seq-report-notify", "notify-eoc"]),
            ]),
            WScenario("Duplicate and escalation", [
                S("A second crew reports the same spot", "Twelve metres from the first leak, three hours later, at 12 % LEL.", ["in-survey", "in-intake", "validate"], ["survey-intake", "intake-validate"]),
                S("The incident layer is checked", "Dedupe queries the incident layer for a non-duplicate incident within 50 ft and 12 h.", ["dedupe", "out-layers"], ["dedupe-reads", "seq-validate-dedupe"]),
                S("A plain duplicate", "It is stored as its own row (traceable), bumps the parent's reading count and max %LEL, and gets Report 1 only - no second alert for something already known.", ["publish", "report", "out-drives"], ["seq-schematransform-publish", "seq-publish-report", "report-drives"]),
                S("A worse reading is an escalation", "If the duplicate grades worse, or its %LEL is 10+ points above the highest so far, it is fully re-analysed: the parent's grade, dispatch code and valves are updated, the plume is replaced, Report 2 is written and the EOC is alerted again. A worsening reading is never silently merged.", ["grade", "publish", "notify", "out-eoc"], ["seq-valve-grade", "seq-schematransform-publish", "seq-report-notify", "notify-eoc"]),
            ]),
            WScenario("Rejected submission", [
                S("Validation says no", "A negative %LEL, missing coordinates, or a location outside the franchise area stops the pipeline at the first stage.", ["in-intake", "validate"], ["intake-validate"]),
                S("People are told - the leak is not ignored", "The GIS administrator AND the EOC are alerted with 'treat the reported leak as live': a rejected submission may still be a real leak. The rejection is also logged for the daily audit.", ["validate", "out-gis", "out-eoc"], ["reject-gis", "reject-eoc"]),
                S("Over-range readings are the opposite", "A reading above the instrument range (e.g. 140 % LEL) is clamped to the maximum and flagged - it keeps going, because that is the most dangerous atmosphere.", ["validate", "plume"], []),
            ]),
            WScenario("Failure and recovery", [
                S("Something breaks after the layer was written", "For example the SMTP server is down when the Grade 1 alert is sent.", ["publish", "report", "notify"], ["seq-publish-report", "seq-report-notify"]),
                S("The record is kept, people are warned", "The incident row exists, so the run finishes as PARTIAL: the GIS admin and EOC get an 'automation partially failed' message and the layer row shows NOTIFY_STATUS = FAILED.", ["notify", "out-gis"], []),
                S("Retries until delivered", "Every 60 seconds the retrier re-sends failed or stuck alerts from the layer row, attaching the PDF already on the drive. The 17:00 summary lists anything still outstanding.", ["notify", "retry", "out-eoc"], ["notify-retry", "retry-eoc"]),
            ]),
            WScenario("Daily 17:00 reconciliation", [
                S("A rolling window, not a calendar day", "From the end of the previous successful run to today's 17:00, so after-hours leaks and weekends are never skipped.", ["reconcile"], []),
                S("Audit layers and drives", "It reads the incident and survey layers and tallies the PDFs actually on shared drives A and B.", ["reconcile", "out-layers", "out-drives"], ["layers-reconcile", "drives-reconcile"]),
                S("Executive summary", "Totals, grade breakdown, joint-utility hazards (e.g. gas within 20 ft of an energized pad-mount), escalations and discrepancies go to the Director, CSO and GIS administrator.", ["reconcile", "out-exec"], ["reconcile-exec"]),
            ]),
            WScenario("Adapt it to your data", [
                S("Change field names and codes", "Everything about your schema lives in config/layer_schema.yaml - no code. The same file builds the output service.", ["in-layers", "schematransform"], []),
                S("Plug in a new analysis", "Subclass OverlayStage for a new point, line or polygon layer (length-in-plume and area-in-plume come for free), or swap GradingPolicy / PlumeModel. Add the stage in one list and this diagram regenerates.", ["plume", "electricintersect", "facilities", "grade"], []),
                S("Change what reports say", "Reports and e-mails are Jinja2 templates in the templates/ folder: {{ placeholders }}, loops, conditions and a map() call zoomed to the submitted feature.", ["report", "notify"], []),
            ]),
        ]


class WorkflowPage:
    """Renders the self-contained interactive HTML (also used to record the README GIF)."""

    def __init__(self, stages: list[Stage], scenarios: list[WScenario] | None = None):
        self.model = WorkflowModel.from_stages(stages)
        self.scenarios = scenarios or ScenarioLibrary.build()
        self.model.validate(self.scenarios)

    def data(self) -> dict:
        return {**self.model.to_dict(), "scenarios": [asdict(s) for s in self.scenarios],
                "title": "Gas leak and electric arcing risk response", "subtitle": "How one field submission moves through the pipeline"}

    def render(self) -> str:
        payload = json.dumps(self.data(), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        return TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", payload)
