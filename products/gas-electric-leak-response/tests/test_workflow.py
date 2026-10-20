"""The interactive workflow page is generated from the real stage list and cannot silently drift."""
import json
import re
from pathlib import Path

import pytest

from gas_electric_response.flowchart import DiagramPublisher
from gas_electric_response.workflow import ScenarioLibrary, StageIds, WorkflowModel, WorkflowPage, WScenario, WStep
from tests.conftest import ROOT


def test_every_pipeline_stage_is_a_node(runtime):
    model = WorkflowModel.from_stages(runtime.pipeline.stages)
    ids = {n.id for n in model.nodes}
    assert [StageIds.of(s) for s in runtime.pipeline.stages] and all(StageIds.of(s) in ids for s in runtime.pipeline.stages)
    stage_nodes = [n for n in model.nodes if n.num]
    assert [n.num for n in stage_nodes] == list(range(1, len(runtime.pipeline.stages) + 1))
    assert all(n.bound for n in stage_nodes), "every stage must declare its limits"


def test_node_kind_reflects_external_systems(runtime):
    model = {n.id: n for n in WorkflowModel.from_stages(runtime.pipeline.stages).nodes}
    assert model["validate"].kind == "logic" and model["weather"].kind == "external" and model["publish"].kind == "external"
    assert "NOT been verified" in model["weather"].today and "unit-tested" in model["validate"].today


def test_scenarios_reference_only_real_nodes_and_edges(runtime):
    page = WorkflowPage(runtime.pipeline.stages)         # validates in the constructor
    assert len(page.scenarios) >= 5 and all(len(s.steps) >= 3 for s in page.scenarios)


def test_unknown_stage_in_a_scenario_fails_loudly(runtime):
    bad = [WScenario("x", [WStep("t", "t", ["nope-stage"], [])])]
    with pytest.raises(ValueError, match="unknown node 'nope-stage'"):
        WorkflowPage(runtime.pipeline.stages, bad)
    bad = [WScenario("x", [WStep("t", "t", ["validate"], ["no-such-edge"])])]
    with pytest.raises(ValueError, match="unknown edge"):
        WorkflowPage(runtime.pipeline.stages, bad)


def test_removing_a_stage_breaks_the_build_not_the_diagram(runtime):
    stages = [s for s in runtime.pipeline.stages if StageIds.of(s) != "plume"]
    with pytest.raises(ValueError, match="plume"):
        WorkflowPage(stages)


def test_rendered_page_is_self_contained_and_embeds_the_model(runtime):
    html = WorkflowPage(runtime.pipeline.stages).render()
    assert "https://" not in re.sub(r"<!--.*?-->", "", html) or "fonts.googleapis" not in html      # no web fonts / CDNs
    assert "<script src=" not in html and "<link rel=\"stylesheet\"" not in html
    data = json.loads(re.search(r"var D=(\{.*?\});\nvar NS", html, re.S).group(1).replace("<\\/", "</"))
    assert {n["id"] for n in data["nodes"]} >= {"validate", "publish", "in-survey", "out-eoc"}
    assert len(data["scenarios"]) == len(ScenarioLibrary.build()) and len(html) < 120_000     # loads fast


def test_check_detects_drift_and_write_fixes_it(runtime, tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("# x\n<!-- FLOWCHART:PIPELINE:START -->\nold\n<!-- FLOWCHART:PIPELINE:END -->\n<!-- FLOWCHART:STAGES:START -->\nold\n<!-- FLOWCHART:STAGES:END -->\n")
    pub = DiagramPublisher(runtime.pipeline.stages, readme)
    assert pub.stale()                                   # nothing generated yet
    pub.write()
    assert pub.stale() == [] and (tmp_path / "docs" / "workflow.html").exists()
    assert "workflow.gif" in readme.read_text() and "htmlpreview" in readme.read_text()
    (tmp_path / "docs" / "workflow.html").write_text("tampered")
    assert pub.stale()


def test_committed_artifacts_are_up_to_date(runtime):
    pub = DiagramPublisher(runtime.pipeline.stages, ROOT / "README.md")
    assert pub.stale() == [], "run: python -m gas_electric_response flowchart --write"
    gif = ROOT / "docs" / "assets" / "workflow.gif"
    assert gif.exists() and gif.stat().st_size < 3_000_000
