"""tests/test_constraint_updater.py — unit tests for ExplicitConstraintUpdater."""

from problem_tracker.core.problem_description import ProblemDescription
from problem_tracker.updaters.explicit_constraint_updater import ExplicitConstraintUpdater
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

def test_creates_constraints_from_turn():
    client = _StubClient({"budget £800 by Friday": [
        {"name": "budget_ceiling", "description": "budget cannot exceed £800", "constraint_type": "hard"},
        {"name": "arrival_deadline", "description": "must arrive by Friday", "constraint_type": "hard"},
    ]})
    updater = ExplicitConstraintUpdater(client=client)
    pd = make_pd_with_goal()
    pd, ops = updater.update(pd, "budget £800 by Friday", "G1", "t1")
    assert len(pd.explicit.constraints) == 2
    assert [c.name for c in pd.explicit.constraints] == ["budget_ceiling", "arrival_deadline"]
    assert all(o["operation"] == "create_constraint" for o in ops)


def test_updates_existing_constraint():
    client = _StubClient({
        "budget £800": [{"name": "budget_ceiling", "description": "budget cannot exceed £800", "constraint_type": "hard"}],
        "make it £600": [{"name": "budget_ceiling", "description": "budget cannot exceed £600", "constraint_type": "hard"}],
    })
    updater = ExplicitConstraintUpdater(client=client)
    pd = make_pd_with_goal()
    pd, _ = updater.update(pd, "budget £800", "G1", "t1")
    pd, ops = updater.update(pd, "make it £600", "G1", "t2")
    con = pd.explicit.constraints[0]
    assert con.description == "budget cannot exceed £600"
    assert con.constraint_id == "C1"   # identity preserved
    assert con.last_updated_turn == "t2"
    assert ops[0]["operation"] == "update_constraint"
    assert ops[0]["before"]["description"] == "budget cannot exceed £800"
    assert ops[0]["after"]["description"] == "budget cannot exceed £600"


def test_no_op_on_identical_constraint():
    client = _StubClient({
        "vegan": [{"name": "diet", "description": "must be vegan", "constraint_type": "hard"}],
    })
    updater = ExplicitConstraintUpdater(client=client)
    pd = make_pd_with_goal()
    pd, _ = updater.update(pd, "vegan", "G1", "t1")
    pd, ops = updater.update(pd, "vegan", "G1", "t2")
    assert ops == []
    assert len(pd.explicit.constraints) == 1


# ---------------------------------------------------------------------------
# Fix 8: failures → extraction_failed log entry
# ---------------------------------------------------------------------------

def test_llm_error_returns_extraction_failed():
    updater = ExplicitConstraintUpdater(client=_ErrorClient())
    pd = make_pd_with_goal()
    pd, ops = updater.update(pd, "some turn", "G1", "t1")
    assert ops == [{"operation": "extraction_failed", "component": "constraint", "turn_id": "t1"}]
    assert pd.explicit.constraints == []


def test_all_ops_have_turn_id():
    client = _StubClient({"no red-eye": [{"name": "flight_time", "description": "no red-eye flights", "constraint_type": "soft"}]})
    updater = ExplicitConstraintUpdater(client=client)
    pd = make_pd_with_goal()
    pd, ops = updater.update(pd, "no red-eye", "G1", "t1")
    for op in ops:
        assert "turn_id" in op
