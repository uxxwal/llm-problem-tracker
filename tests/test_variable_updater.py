"""tests/test_variable_updater.py — unit tests for ExplicitVariableUpdater."""

from problem_tracker.core.problem_description import ProblemDescription
from problem_tracker.updaters.explicit_variable_updater import ExplicitVariableUpdater
from tests.conftest import make_pd_with_goal


class _StubClient:
    def __init__(self, responses: dict):
        self._responses = responses

    def chat_json(self, messages):
        turn = messages[-1]["content"].split('User turn: "')[-1].rstrip('"')
        return self._responses.get(turn, [])


class _ErrorClient:
    def chat_json(self, messages):
        raise RuntimeError("network error")


# ---------------------------------------------------------------------------
# Basic extraction
# ---------------------------------------------------------------------------

def test_creates_variables_from_turn():
    client = _StubClient({"Kyoto 5 days": [
        {"name": "destination", "value": "Kyoto", "role": "fact", "required": True},
        {"name": "trip_duration", "value": "5 days", "role": "fact", "required": True},
    ]})
    updater = ExplicitVariableUpdater(client=client)
    pd = make_pd_with_goal()
    pd, ops = updater.update(pd, "Kyoto 5 days", "G1", "t1")
    assert len(pd.explicit.variables) == 2
    assert [v.name for v in pd.explicit.variables] == ["destination", "trip_duration"]
    assert [o["operation"] for o in ops] == ["create_variable", "create_variable"]


def test_updates_existing_variable():
    client = _StubClient({"Kyoto 5 days": [
        {"name": "destination", "value": "Kyoto", "role": "fact", "required": True},
        {"name": "trip_duration", "value": "5 days", "role": "fact", "required": True},
    ], "actually 7 days": [
        {"name": "trip_duration", "value": "7 days", "role": "fact", "required": True},
    ]})
    updater = ExplicitVariableUpdater(client=client)
    pd = make_pd_with_goal()
    pd, _ = updater.update(pd, "Kyoto 5 days", "G1", "t1")
    pd, ops = updater.update(pd, "actually 7 days", "G1", "t2")
    dur = next(v for v in pd.explicit.variables if v.name == "trip_duration")
    assert dur.value == "7 days"
    assert dur.variable_id == "V2"   # identity preserved
    assert dur.last_updated_turn == "t2"
    assert ops[0]["operation"] == "update_variable"
    assert ops[0]["before"]["value"] == "5 days"
    assert ops[0]["after"]["value"] == "7 days"


def test_no_duplicate_variables_on_same_name_twice():
    """Fix 2 regression: duplicate name in LLM response → single create_variable."""
    class _DupClient:
        def chat_json(self, _):
            return [
                {"name": "destination", "value": "Paris", "role": "fact", "required": True},
                {"name": "destination", "value": "Paris, France", "role": "fact", "required": True},
            ]
    updater = ExplicitVariableUpdater(client=_DupClient())
    pd = make_pd_with_goal()
    pd, ops = updater.update(pd, "Paris trip", "G1", "t1")
    dest_vars = [v for v in pd.explicit.variables if v.name == "destination"]
    assert len(dest_vars) == 1
    assert dest_vars[0].value == "Paris, France"
    assert len(ops) == 1 and ops[0]["operation"] == "create_variable"


def test_variable_scoped_to_goal():
    client = _StubClient({"Kyoto": [{"name": "destination", "value": "Kyoto", "role": "fact", "required": True}]})
    updater = ExplicitVariableUpdater(client=client)
    pd = make_pd_with_goal()
    pd, _ = updater.update(pd, "Kyoto", "G1", "t1")
    # querying for G2 should return nothing
    assert updater.extractor.extract("Kyoto", "G2", []) is not None  # call succeeds
    g2_vars = [v for v in pd.explicit.variables if v.goal_id == "G2"]
    assert g2_vars == []


# ---------------------------------------------------------------------------
# Fix 8: failures → extraction_failed log entry, turn_id in entries
# ---------------------------------------------------------------------------

def test_llm_error_returns_extraction_failed():
    updater = ExplicitVariableUpdater(client=_ErrorClient())
    pd = make_pd_with_goal()
    pd, ops = updater.update(pd, "some turn", "G1", "t1")
    assert ops == [{"operation": "extraction_failed", "component": "variable", "turn_id": "t1"}]
    assert pd.explicit.variables == []


def test_all_create_ops_have_turn_id():
    client = _StubClient({"Kyoto": [{"name": "destination", "value": "Kyoto", "role": "fact", "required": True}]})
    updater = ExplicitVariableUpdater(client=client)
    pd = make_pd_with_goal()
    pd, ops = updater.update(pd, "Kyoto", "G1", "t1")
    for op in ops:
        assert "turn_id" in op, f"op missing turn_id: {op}"
