"""Verify simulator outcomes rather than matching skill prose."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("skill_transfer_check", Path(__file__).parents[1] / "scripts/skill_transfer_check.py")
sim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sim)


def plan(*ops):
    return [{"op": op} if isinstance(op, str) else op for op in ops]


@pytest.mark.parametrize("case", sim.fixtures(), ids=lambda case: case["id"])
def test_valid_recovery_produces_verified_state(case):
    plans = {
        "layout": plan("observe", {"op": "save", "selector": "$view.target"}, "verify"),
        "save": plan("inspect_saved", {"op": "save", "unless_saved": True}, "verify"),
        "frames": plan("inspect_frames", "fill_missing", "encode", "verify_media"),
        "api": plan("inspect_api", {"op": "set_engine", "engine": "$api.engine"}, "save_copy", "verify"),
    }
    assert sim.replay(case, plans[case["kind"]])["passed"]


@pytest.mark.parametrize("case_index,actions,error", [
    (0, plan({"op": "save", "selector": "#save-old"}, "verify"), "stale or wrong"),
    (1, plan("save", "verify"), None),
    (1, plan({"op": "save", "unless_saved": True}, "verify"), "no persistence observation"),
    (2, plan("inspect_frames", "render_all", "encode", "verify_media"), None),
    (6, plan("inspect_frames", "encode", "verify_media"), "incomplete frames"),
    (7, plan({"op": "set_engine", "engine": "BLENDER_EEVEE"}, "save_copy", "verify"), "not selectable"),
    (3, plan("inspect_api", {"op": "set_engine", "engine": "$api.engine"}, "save_copy", "render", "verify"), "outside fixture goal"),
])
def test_bad_recovery_cannot_claim_success(case_index, actions, error):
    result = sim.replay(sim.fixtures()[case_index], actions)
    assert not result["passed"]
    if error:
        assert any(error in message for message in result["state"]["errors"])


def test_unperformed_and_malformed_work_stays_failed():
    assert sim.evaluate({"cases": []})["passed"] == 0
    assert sim.evaluate({"cases": "bad"})["passed"] == 0
    assert sim.evaluate({"cases": [{"id": "layout-0", "actions": "bad"}]})["passed"] == 0
    assert not sim.replay(sim.fixtures()[0], [{}] * 13)["passed"]


def test_observation_without_verification_is_not_completion():
    assert not sim.replay(sim.fixtures()[0], plan("observe", {"op": "save", "selector": "$view.target"}))["passed"]
