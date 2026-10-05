"""tests/test_session.py — the Session / STG persistence layer.

Covers: one STG entry per turn (including no-op turns), snapshot decoupling,
save/load round-trip identity, failure events preserved in the record, and
turn-counter restoration on load.
"""

import json
import os
import tempfile
from pathlib import Path

from problem_tracker.core.session import Session
from problem_tracker.updaters import ExplicitUpdater
from tests.conftest import make_pd_with_goal


class _MultiStubClient:
    """Routes to goal / variable / constraint responses based on system prompt."""

    def __init__(self, goal=None, variable=None, constraint=None):
        self._goal = goal or {}
        self._variable = variable or {}
        self._constraint = constraint or {}

    def chat_json(self, messages):
        system = messages[0]["content"] if messages else ""
        turn = messages[-1]["content"].split('User turn: "')[-1].rstrip('"')
        if "goal-tracking" in system:
            return self._goal.get(turn, [{"operation": "no_goal_change"}])
        if "variable-extraction" in system:
            return self._variable.get(turn, [])
        if "constraint-extraction" in system:
            return self._constraint.get(turn, [])
        return []


class _ErrorClient:
    def chat_json(self, messages):
        raise RuntimeError("network down")


def _demo_session():
    client = _MultiStubClient(
        goal={
            "plan a trip": [{"operation": "create_goal", "goal_text": "Plan trip"}],
        },
        variable={
            "plan a trip": [{"name": "destination", "value": "Kyoto", "role": "fact", "required": True}],
        },
        constraint={
            "max 800": [{"name": "budget_ceiling", "description": "budget cannot exceed £800", "constraint_type": "hard"}],
        },
    )
    updater = ExplicitUpdater(client=client)
    session = Session(model="stub", notes="test session")
    session.process_turn(updater, "plan a trip")
    session.process_turn(updater, "max 800")
    session.process_turn(updater, "sounds good")  # no-op turn
    return session


def test_one_stg_entry_per_turn_including_noop():
    session = _demo_session()
    assert len(session.stg) == 3
    assert [e["turn_id"] for e in session.stg] == ["t1", "t2", "t3"]
    assert [e["user_turn"] for e in session.stg] == ["plan a trip", "max 800", "sounds good"]
    # the no-op turn is recorded with an empty operation log
    assert session.stg[2]["operations"] == []
    # and its snapshot equals the previous one
    assert session.stg[2]["state_snapshot"] == session.stg[1]["state_snapshot"]


def test_snapshots_are_decoupled_from_live_state():
    session = _demo_session()
    before = json.dumps(session.stg[0]["state_snapshot"])
    session.pd.explicit.goals[0].status = "abandoned"
    assert json.dumps(session.stg[0]["state_snapshot"]) == before
    session.pd.explicit.goals[0].status = "active"


def test_snapshots_capture_progression():
    session = _demo_session()
    s1, s2 = session.stg[0]["state_snapshot"], session.stg[1]["state_snapshot"]
    assert len(s1["explicit"]["constraints"]) == 0
    assert len(s2["explicit"]["constraints"]) == 1
    assert s2["explicit"]["constraints"][0]["name"] == "budget_ceiling"


def test_save_load_round_trip():
    session = _demo_session()
    with tempfile.TemporaryDirectory() as d:
        path = session.save(directory=d)
        assert os.path.basename(path) == f"{session.session_id}.json"
        loaded = Session.load(path)

    assert loaded.session_id == session.session_id
    assert loaded.to_dict() == session.to_dict()
    # the restored ProblemDescription is a working object, not just a dict
    assert loaded.pd.is_valid()
    assert loaded.pd.get_goal("G1").goal_text == "Plan trip"
    # turn counter restored: next turn continues the sequence
    assert loaded.next_turn_id() == "t4"


def test_failure_events_preserved_in_stg():
    updater = ExplicitUpdater(client=_ErrorClient())
    session = Session()
    session.process_turn(updater, "anything at all")
    ops = session.stg[0]["operations"]
    assert any(op["operation"] == "extraction_failed" for op in ops)
    # failed turn still yields a node, and the state is unchanged and valid
    assert session.stg[0]["state_snapshot"]["explicit"]["goals"] == []
    assert session.pd.is_valid()


def test_prompt_version_recorded_and_preserved():
    """Prompt changes must be visible in the data: each session records a
    fingerprint of the prompts it ran under, and loading an old session keeps
    the RECORDED version rather than adopting the current one."""
    from problem_tracker.core.session import prompt_fingerprint
    session = _demo_session()
    assert session.prompt_version == prompt_fingerprint()
    assert len(session.prompt_version) == 12

    with tempfile.TemporaryDirectory() as d:
        path = session.save(directory=d)
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        assert raw["prompt_version"] == session.prompt_version
        # simulate loading a session recorded under different prompts
        raw["prompt_version"] = "0123456789ab"
        Path(path).write_text(json.dumps(raw), encoding="utf-8")
        loaded = Session.load(path)
    assert loaded.prompt_version == "0123456789ab"


def test_prompt_fingerprint_changes_with_the_prompts():
    import problem_tracker.prompt_specs as ps
    from problem_tracker.core.session import prompt_fingerprint
    before = prompt_fingerprint()
    original = ps.GOAL_SYSTEM_PROMPT
    try:
        ps.GOAL_SYSTEM_PROMPT = original + "\n(an edit)"
        assert prompt_fingerprint() != before
    finally:
        ps.GOAL_SYSTEM_PROMPT = original
    assert prompt_fingerprint() == before


def test_record_turn_low_level():
    pd = make_pd_with_goal()
    session = Session(pd=pd)
    entry = session.record_turn("t9", "hello", [], pd)
    assert entry["turn_id"] == "t9"
    assert session.stg[-1] is entry
    assert entry["state_snapshot"]["explicit"]["goals"][0]["goal_id"] == "G1"


# ---------------------------------------------------------------------------
# Decode settings
# ---------------------------------------------------------------------------

def test_extraction_defaults_to_deterministic_sampling(monkeypatch=None):
    """Extraction is a structured-output task with one intended answer. The API
    default is 1.0, which samples; three replays of one conversation then
    produce three different states. Pinned so a change is deliberate."""
    import os

    from problem_tracker.llm_client import resolve_seed, resolve_temperature
    old = os.environ.pop("LLM_TEMPERATURE", None)
    try:
        assert resolve_temperature() == 0.0
        assert resolve_temperature(0.7) == 0.7
        assert resolve_seed() is None
    finally:
        if old is not None:
            os.environ["LLM_TEMPERATURE"] = old


def test_session_records_decode_settings():
    """A prompt fingerprint would not reveal a temperature change, and
    temperature changes the variance of the recorded state as much as the
    prompts do."""
    s = Session(model="m", decode={"model": "m", "temperature": 0.0, "seed": None})
    assert s.to_dict()["decode"]["temperature"] == 0.0
    assert Session.from_dict(s.to_dict()).decode == s.decode
