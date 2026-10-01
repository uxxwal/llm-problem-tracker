"""tests/test_applicators.py — direct unit tests for the deterministic layer.

The applicators are pure code (no LLM), so they are tested here with raw
operation/item dicts — no stub clients needed. This pins the Extractor/
Applicator boundary: everything in this file must be reproducible.
"""

from problem_tracker.core.problem_description import ProblemDescription
from problem_tracker.updaters.applicators import (
    ConstraintApplicator,
    GoalApplicator,
    VariableApplicator,
)
from tests.conftest import make_pd_with_goal

# ---------------------------------------------------------------------------
# GoalApplicator
# ---------------------------------------------------------------------------

def test_goal_validate_rejects_unknown_operation():
    app = GoalApplicator()
    try:
        app.validate_operations([{"operation": "add_property"}])
        assert False, "expected ValueError"
    except ValueError as e:
        assert "add_property" in str(e)


def test_goal_validate_rejects_missing_field():
    app = GoalApplicator()
    try:
        app.validate_operations([{"operation": "create_goal"}])
        assert False, "expected ValueError"
    except ValueError as e:
        assert "goal_text" in str(e)


def test_goal_apply_create():
    app = GoalApplicator()
    pd = ProblemDescription()
    entry = app.apply_operation(pd, {"operation": "create_goal", "goal_text": "Plan trip"}, "t1")
    g = pd.get_goal("G1")
    assert g.goal_text == "Plan trip" and g.status == "active"
    assert entry["before"] is None and entry["after"]["goal_id"] == "G1"
    assert entry["turn_id"] == "t1"


def test_goal_apply_abandon_then_create_replacement():
    """Objective replacement in v2 is two operations; the abandoned goal's
    text is untouched — goals are immutable."""
    app = GoalApplicator()
    pd = make_pd_with_goal(text="Plan trip")
    e1 = app.apply_operation(pd, {"operation": "abandon_goal", "goal_id": "G1"}, "t2")
    e2 = app.apply_operation(pd, {"operation": "create_goal", "goal_text": "Write CV"}, "t2")
    assert pd.get_goal("G1").status == "abandoned"
    assert pd.get_goal("G1").goal_text == "Plan trip"
    assert pd.get_goal("G2").goal_text == "Write CV"
    assert e1["before"]["status"] == "active" and e1["after"]["status"] == "abandoned"
    assert e2["after"]["status"] == "active"


def test_goal_apply_status_change_missing_goal_raises():
    app = GoalApplicator()
    pd = ProblemDescription()
    try:
        app.apply_operation(pd, {"operation": "abandon_goal", "goal_id": "G9"}, "t1")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "G9" in str(e)
    assert pd.explicit.goals == []  # no partial application


def test_goal_apply_no_goal_change_returns_none():
    app = GoalApplicator()
    pd = make_pd_with_goal()
    assert app.apply_operation(pd, {"operation": "no_goal_change"}, "t1") is None


def test_goal_duplicate_create_is_skipped():
    """Creating a goal whose text duplicates an active goal must be skipped
    (v2 analogue of the v1 no-op refine guard)."""
    app = GoalApplicator()
    pd = make_pd_with_goal(text="Plan trip")
    entry = app.apply_operation(pd, {"operation": "create_goal", "goal_text": "plan trip"}, "t2")
    assert len(pd.explicit.goals) == 1
    assert entry["operation"] == "validation_skip"
    assert entry["component"] == "goal"


def test_spurious_same_turn_pivot_is_reinstated():
    """A detail change misread as a pivot arrives as [abandon, create-with-same-text].
    Observed in pilot: 'instead of a bar, somewhere casual' produced
    abandon G1 + create G2 with IDENTICAL goal text, stranding every variable
    and constraint on the dead goal. The guard must catch this even though the
    abandon has already run when the create is applied."""
    app = GoalApplicator()
    pd = make_pd_with_goal(goal_id="G1", text="Find place to celebrate birthday", turn="t1")

    e1 = app.apply_operation(pd, {"operation": "abandon_goal", "goal_id": "G1"}, "t4")
    assert e1["after"]["status"] == "abandoned"

    e2 = app.apply_operation(
        pd, {"operation": "create_goal", "goal_text": "Find place to celebrate birthday"}, "t4"
    )
    assert e2["operation"] == "validation_skip"
    assert "spurious pivot" in e2["reason"]
    assert len(pd.explicit.goals) == 1              # no G2 created
    assert pd.get_goal("G1").status == "active"     # reinstated, elements not stranded


def test_genuine_same_turn_pivot_still_works():
    """A real pivot has DIFFERENT text and must not be blocked."""
    app = GoalApplicator()
    pd = make_pd_with_goal(goal_id="G1", text="Plan trip", turn="t1")
    app.apply_operation(pd, {"operation": "abandon_goal", "goal_id": "G1"}, "t4")
    entry = app.apply_operation(pd, {"operation": "create_goal", "goal_text": "Write CV"}, "t4")
    assert entry["operation"] == "create_goal"
    assert pd.get_goal("G1").status == "abandoned"
    assert pd.get_goal("G2").goal_text == "Write CV"


def test_goal_ended_in_an_earlier_turn_can_be_recreated():
    """Reinstatement applies only within the same turn: a user genuinely
    returning to an old objective later creates a fresh goal."""
    app = GoalApplicator()
    pd = make_pd_with_goal(goal_id="G1", text="Plan trip", turn="t1")
    app.apply_operation(pd, {"operation": "abandon_goal", "goal_id": "G1"}, "t2")
    entry = app.apply_operation(pd, {"operation": "create_goal", "goal_text": "Plan trip"}, "t6")
    assert entry["operation"] == "create_goal"
    assert pd.get_goal("G2") is not None
    assert pd.get_goal("G1").status == "abandoned"


def test_goal_validate_rejects_removed_operations():
    app = GoalApplicator()
    for bad in ("refine_goal", "supersede_goal"):
        try:
            app.validate_operations([{"operation": bad, "goal_id": "G1", "goal_text": "x",
                                      "old_goal_id": "G1", "new_goal_text": "x"}])
            assert False, f"expected ValueError for {bad}"
        except ValueError as e:
            assert bad in str(e)


# ---------------------------------------------------------------------------
# VariableApplicator
# ---------------------------------------------------------------------------

def test_variable_apply_create_update_and_merge():
    app = VariableApplicator()
    pd = make_pd_with_goal()

    ops = app.apply(pd, [
        {"name": "destination", "value": "Kyoto", "role": "fact", "required": True},
    ], "G1", "t1")
    assert [o["operation"] for o in ops] == ["create_variable"]

    # update existing by name — identity preserved
    ops = app.apply(pd, [
        {"name": "destination", "value": "Osaka", "role": "fact", "required": True},
    ], "G1", "t2")
    assert [o["operation"] for o in ops] == ["update_variable"]
    assert ops[0]["before"]["value"] == "Kyoto" and ops[0]["after"]["value"] == "Osaka"
    assert len(pd.explicit.variables) == 1

    # duplicate name within one batch merges into a single create op
    ops = app.apply(pd, [
        {"name": "stated_budget", "value": "£500", "role": "fact"},
        {"name": "stated_budget", "value": "£600", "role": "fact"},
    ], "G1", "t3")
    assert [o["operation"] for o in ops] == ["create_variable"]
    assert ops[0]["after"]["value"] == "£600"


def test_variable_apply_invalid_role_logs_validation_skip():
    app = VariableApplicator()
    pd = make_pd_with_goal()
    ops = app.apply(pd, [{"name": "x", "value": "y", "role": "constraint"}], "G1", "t1")
    assert [o["operation"] for o in ops] == ["validation_skip"]
    assert pd.explicit.variables == []


def test_variable_apply_preference_role_logs_validation_skip():
    # "preference" was removed from VALID_VARIABLE_ROLES — preferences are
    # soft constraints now, so this must be rejected, not stored.
    app = VariableApplicator()
    pd = make_pd_with_goal()
    ops = app.apply(pd, [{"name": "budget", "value": "£500", "role": "preference"}], "G1", "t1")
    assert [o["operation"] for o in ops] == ["validation_skip"]
    assert pd.explicit.variables == []


# ---------------------------------------------------------------------------
# ConstraintApplicator
# ---------------------------------------------------------------------------

def test_constraint_apply_create_and_tighten():
    app = ConstraintApplicator()
    pd = make_pd_with_goal()

    ops = app.apply(pd, [
        {"name": "budget_ceiling", "description": "budget cannot exceed £800", "constraint_type": "hard"},
    ], "G1", "t1")
    assert [o["operation"] for o in ops] == ["create_constraint"]
    assert ops[0]["constraint_id"] == "C1"

    ops = app.apply(pd, [
        {"name": "budget_ceiling", "description": "budget cannot exceed £600", "constraint_type": "hard"},
    ], "G1", "t2")
    assert [o["operation"] for o in ops] == ["update_constraint"]
    assert ops[0]["before"]["description"].endswith("£800")
    assert ops[0]["after"]["description"].endswith("£600")
    assert len(pd.explicit.constraints) == 1


def test_constraint_apply_invalid_type_logs_validation_skip():
    app = ConstraintApplicator()
    pd = make_pd_with_goal()
    ops = app.apply(pd, [{"name": "x", "description": "y", "constraint_type": "maybe"}], "G1", "t1")
    assert [o["operation"] for o in ops] == ["validation_skip"]
    assert pd.explicit.constraints == []
