"""tests/test_goal_updater.py — unit tests for ExplicitGoalUpdater (goal_schema_v2).

Goals are immutable: create / complete / abandon / no_goal_change only.
Objective replacement is abandon_goal + create_goal in the same turn.
"""

from problem_tracker.core.problem_description import ProblemDescription
from problem_tracker.updaters.explicit_goal_updater import ExplicitGoalUpdater
from tests.conftest import make_pd_with_goal

# ---------------------------------------------------------------------------
# Stub clients
# ---------------------------------------------------------------------------

class _StubClient:
    def __init__(self, responses: dict):
        self._responses = responses

    def chat_json(self, messages):
        user_turn = messages[-1]["content"].split('User turn: "')[-1].rstrip('"')
        return self._responses.get(user_turn, [{"operation": "no_goal_change"}])


class _ErrorClient:
    def chat_json(self, messages):
        raise RuntimeError("network error")


class _GarbageClient:
    def chat_json(self, messages):
        return "not a list"


# ---------------------------------------------------------------------------
# Basic operations — all log entries carry turn_id
# ---------------------------------------------------------------------------

def test_create_goal_log_has_turn_id():
    client = _StubClient({"create a plan": [{"operation": "create_goal", "goal_text": "Plan trip"}]})
    updater = ExplicitGoalUpdater(client=client)
    pd = ProblemDescription()
    pd, log = updater.update(pd, "create a plan", "t1")
    assert len(pd.explicit.goals) == 1
    assert pd.explicit.goals[0].goal_text == "Plan trip"
    assert log[0]["operation"] == "create_goal"
    assert log[0]["turn_id"] == "t1"
    assert "turn_number" not in log[0]


def test_complete_goal():
    client = _StubClient({"all done": [{"operation": "complete_goal", "goal_id": "G1"}]})
    updater = ExplicitGoalUpdater(client=client)
    pd = make_pd_with_goal()
    pd, log = updater.update(pd, "all done", "t2")
    assert pd.get_goal("G1").status == "completed"
    assert log[0]["before"]["status"] == "active"
    assert log[0]["after"]["status"] == "completed"


def test_abandon_goal():
    client = _StubClient({"never mind": [{"operation": "abandon_goal", "goal_id": "G1"}]})
    updater = ExplicitGoalUpdater(client=client)
    pd = make_pd_with_goal()
    pd, log = updater.update(pd, "never mind", "t2")
    assert pd.get_goal("G1").status == "abandoned"


# ---------------------------------------------------------------------------
# Objective replacement = abandon + create in one turn
# ---------------------------------------------------------------------------

def test_pivot_is_abandon_plus_create():
    client = _StubClient({"forget it, write my cv": [
        {"operation": "abandon_goal", "goal_id": "G1"},
        {"operation": "create_goal", "goal_text": "Write CV"},
    ]})
    updater = ExplicitGoalUpdater(client=client)
    pd = make_pd_with_goal(text="Plan trip")
    pd, log = updater.update(pd, "forget it, write my cv", "t2")

    assert pd.get_goal("G1").status == "abandoned"
    g2 = pd.get_goal("G2")
    assert g2 is not None and g2.status == "active" and g2.goal_text == "Write CV"
    assert [e["operation"] for e in log] == ["abandon_goal", "create_goal"]
    # goal text of the abandoned goal is untouched — goals are immutable
    assert pd.get_goal("G1").goal_text == "Plan trip"


# ---------------------------------------------------------------------------
# Concurrent goals
# ---------------------------------------------------------------------------

def test_additional_goal_leaves_existing_active():
    client = _StubClient({"also a packing list": [{"operation": "create_goal", "goal_text": "Draft packing list"}]})
    updater = ExplicitGoalUpdater(client=client)
    pd = make_pd_with_goal(text="Plan trip")
    pd, log = updater.update(pd, "also a packing list", "t2")
    statuses = {g.goal_id: g.status for g in pd.explicit.goals}
    assert statuses == {"G1": "active", "G2": "active"}


def test_duplicate_create_is_skipped():
    """Re-creating an already-active goal with the same text must not
    duplicate the solution space (the v2 analogue of the no-op refine guard)."""
    client = _StubClient({"plan a trip please": [{"operation": "create_goal", "goal_text": "Plan trip"}]})
    updater = ExplicitGoalUpdater(client=client)
    pd = make_pd_with_goal(text="Plan trip")
    pd, log = updater.update(pd, "plan a trip please", "t2")
    assert len(pd.explicit.goals) == 1
    assert log[0]["operation"] == "validation_skip"
    assert log[0]["component"] == "goal"


# ---------------------------------------------------------------------------
# Invalid operations from the model
# ---------------------------------------------------------------------------

def test_removed_operations_are_rejected():
    """refine_goal and supersede_goal no longer exist; a model that emits them
    must produce validation_failed, not a state change."""
    for bad_op in (
        {"operation": "refine_goal", "goal_id": "G1", "goal_text": "Plan cycling trip"},
        {"operation": "supersede_goal", "old_goal_id": "G1", "new_goal_text": "Write CV"},
    ):
        client = _StubClient({"pivot": [bad_op]})
        updater = ExplicitGoalUpdater(client=client)
        pd = make_pd_with_goal(text="Plan trip")
        pd, log = updater.update(pd, "pivot", "t2")
        assert log[0]["operation"] == "validation_failed"
        assert pd.get_goal("G1").goal_text == "Plan trip"
        assert pd.get_goal("G1").status == "active"


def test_missing_goal_records_apply_failed():
    client = _StubClient({"done": [{"operation": "complete_goal", "goal_id": "G9"}]})
    updater = ExplicitGoalUpdater(client=client)
    pd = make_pd_with_goal()
    pd, log = updater.update(pd, "done", "t2")
    assert log[0]["operation"] == "apply_failed"
    assert "G9" in log[0]["reason"]
    assert pd.get_goal("G1").status == "active"


# ---------------------------------------------------------------------------
# Extraction failures
# ---------------------------------------------------------------------------

def test_llm_error_records_extraction_failed():
    updater = ExplicitGoalUpdater(client=_ErrorClient())
    pd = ProblemDescription()
    pd, log = updater.update(pd, "anything", "t1")
    assert log == [{"operation": "extraction_failed", "component": "goal", "turn_id": "t1"}]
    assert pd.explicit.goals == []


def test_garbage_response_records_extraction_failed():
    updater = ExplicitGoalUpdater(client=_GarbageClient())
    pd = ProblemDescription()
    pd, log = updater.update(pd, "anything", "t1")
    assert log[0]["operation"] == "extraction_failed"


def test_invalid_operation_name_records_validation_failed():
    client = _StubClient({"x": [{"operation": "add_property", "goal_text": "?"}]})
    updater = ExplicitGoalUpdater(client=client)
    pd = ProblemDescription()
    pd, log = updater.update(pd, "x", "t1")
    assert log[0]["operation"] == "validation_failed"
    assert log[0]["component"] == "goal"
