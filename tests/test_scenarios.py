"""Scenario YAML structural tests — load + condition coverage + classification shape."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harness import CONDITIONS  # noqa: E402

SCENARIO_PATHS = sorted((ROOT / "scenarios").glob("scenario_*.yaml"))


@pytest.fixture(params=SCENARIO_PATHS, ids=[p.name for p in SCENARIO_PATHS])
def scenario(request: pytest.FixtureRequest) -> dict:
    with request.param.open() as f:
        return yaml.safe_load(f)


def test_required_top_level_keys(scenario: dict) -> None:
    for k in ("scenario", "conditions", "classification"):
        assert k in scenario, f"missing top-level key: {k}"


def test_all_seven_conditions_present(scenario: dict) -> None:
    missing = [c for c in CONDITIONS if c not in scenario["conditions"]]
    assert not missing, f"scenario missing conditions: {missing}"


def test_scenario_id_format(scenario: dict) -> None:
    sid = scenario["scenario"]["id"]
    assert sid.startswith("scenario_"), f"bad scenario id: {sid}"


def test_scenario_1_classification_block() -> None:
    p = ROOT / "scenarios" / "scenario_01_triage.yaml"
    with p.open() as f:
        scn = yaml.safe_load(f)
    cls = scn["classification"]
    assert "per_condition_regex" in cls
    for cond in CONDITIONS:
        assert cond in cls["per_condition_regex"], f"missing regex for {cond}"
    assert cls["success_values"] == [3, 4, 5]
    assert cls["partial_values"] == [2]
    assert cls["fail_values"] == [1]
    assert cls["unparsed_handling"] == "exclude_from_primary"


def test_scenario_2_classification_block() -> None:
    p = ROOT / "scenarios" / "scenario_02_summarization.yaml"
    with p.open() as f:
        scn = yaml.safe_load(f)
    cls = scn["classification"]
    assert "anchors_clinical" in cls
    assert "anchors_non_clinical" in cls
    # 6 anchors per entity — methodology spec.
    for entity, anchors in cls["anchors_clinical"].items():
        assert len(anchors) == 6, f"clinical entity {entity}: {len(anchors)} anchors (expected 6)"
    for entity, anchors in cls["anchors_non_clinical"].items():
        assert len(anchors) == 6, (
            f"non_clinical entity {entity}: {len(anchors)} anchors (expected 6)"
        )
    assert cls["anchor_match"] == "word_boundary_case_insensitive"


def test_scenario_2_domain_assignment_complete() -> None:
    p = ROOT / "scenarios" / "scenario_02_summarization.yaml"
    with p.open() as f:
        scn = yaml.safe_load(f)
    da = scn["classification"]["domain_assignment"]
    for cond in CONDITIONS:
        assert cond in da, f"condition {cond} missing from domain_assignment"
        assert da[cond] in ("clinical", "non_clinical")
