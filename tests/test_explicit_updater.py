"""tests/test_explicit_updater.py — end-to-end tests for ExplicitUpdater.

Covers:
- Fix 4: variables attach to the goal affected by this turn's goal ops
- Abandoning a nonexistent goal fails per-operation, without blocking the paired create
- Fix 6: pd.validate() errors appear in log as state_invariant_violation
- Supersede → variable scoping (old goal vars invisible to new goal)
- Budget phrase lands as constraint OR variable, not both
"""

from problem_tracker.core.problem_description import ProblemDescription
from problem_tracker.updaters.explicit_updater import ExplicitUpdater
from tests.conftest import make_pd_with_goal


# ---------------------------------------------------------------------------
# Multi-component stub client
# The ExplicitUpdater uses a single client for all three updaters.
# We route different prompt types by inspecting the system message content.
# ---------------------------------------------------------------------------

class _MultiStubClient:
    """Routes to goal / variable / constraint responses based on system prompt."""

    def __init__(self, goal_responses=None, variable_responses=None, constraint_responses=None):
        self._goal = goal_responses or {}
        self._variable = variable_responses or {}
        self._constraint = constraint_responses or {}

    def chat_json(self, messages):
        system = messages[0]["content"] if messages else ""
        turn = messages[-1]["content"].split('User turn: "')[-1].rstrip('"')

        if "goal-tracking" in system:
            return self._goal.get(turn, [{"operation": "no_goal_change"}])
        elif "variable-extraction" in system:
            return self._variable.get(turn, [])
        elif "constraint-extraction" in system:
            return self._constraint.get(turn, [])
        return []


# ---------------------------------------------------------------------------
# Fix 4: variables attach to the new goal when it's created this turn
# ---------------------------------------------------------------------------

def test_variables_attach_to_newly_created_goal():
    """Pivot turn (abandon G1 + create G2) → variables must attach to G2."""
    client = _MultiStubClient(
        goal_responses={
            "I want to write my CV": [
                {"operation": "abandon_goal", "goal_id": "G1"},
                {"operation": "create_goal", "goal_text": "Write CV"},
            ],
        },
        variable_responses={
            "I want to write my CV": [{"name": "job_type", "value": "data science", "role": "fact", "required": True}],
        },
        constraint_responses={},
    )
    updater = ExplicitUpdater(client=client)
    pd = make_pd_with_goal(goal_id="G1", text="Plan trip")
    result = updater.update(pd, "I want to write my CV", "t2")
    pd = result["state"]

    # G1 abandoned (text untouched — immutable), G2 active
    assert pd.get_goal("G1").status == "abandoned"
    assert pd.get_goal("G1").goal_text == "Plan trip"
    assert pd.get_goal("G2").status == "active"

    # Variable must be scoped to G2, not G1
    g2_vars = [v for v in pd.explicit.variables if v.goal_id == "G2"]
    g1_vars = [v for v in pd.explicit.variables if v.goal_id == "G1"]
    assert len(g2_vars) == 1
    assert g2_vars[0].name == "job_type"
    assert g1_vars == []


def test_concurrent_creates_are_deduplicated_across_goals():
    """A turn creating two goals runs extraction for BOTH, but a statement
    belongs to ONE goal. Observed in probing: 'prefer no-dig beds' landed on
    both the allotment goal and the fruit-tree goal, because each focal goal
    gets its own pass and both claimed the same utterance. Identical content is
    kept on the first goal and dropped (and logged) on the rest."""
    client = _MultiStubClient(
        goal_responses={
            "plan a trip and draft a packing list": [
                {"operation": "create_goal", "goal_text": "Plan trip"},
                {"operation": "create_goal", "goal_text": "Draft packing list"},
            ],
        },
        variable_responses={
            "plan a trip and draft a packing list": [
                {"name": "destination", "value": "Kyoto", "role": "fact", "required": True},
            ],
        },
    )
    updater = ExplicitUpdater(client=client)
    pd = ProblemDescription()
    result = updater.update(pd, "plan a trip and draft a packing list", "t1")
    pd = result["state"]

    assert {g.goal_id for g in pd.active_goals()} == {"G1", "G2"}
    # the stub returns the same item for both passes: kept once, on the first goal
    destinations = [v for v in pd.explicit.variables if v.name == "destination"]
    assert len(destinations) == 1, [(v.goal_id, v.name) for v in pd.explicit.variables]
    assert destinations[0].goal_id == "G1"
    # and the drop is recorded, not silent
    dupes = [o for o in result["operation_log"] if o["operation"] == "cross_goal_duplicate"]
    assert len(dupes) == 1 and dupes[0]["goal_id"] == "G2"
    assert pd.is_valid()


def test_distinct_content_still_reaches_each_concurrent_goal():
    """Dedup must only remove IDENTICAL content — genuinely different details
    for different goals must both survive."""

    class _PerGoalStub:
        def chat_json(self, messages):
            system = messages[0]["content"]; content = messages[-1]["content"]
            if "goal-tracking" in system:
                return [{"operation": "create_goal", "goal_text": "Find flat"},
                        {"operation": "create_goal", "goal_text": "Move furniture"}]
            if "variable-extraction" in system:
                if '("Find flat")' in content:
                    return [{"name": "city", "value": "Berlin", "role": "fact", "required": True}]
                if '("Move furniture")' in content:
                    return [{"name": "volume", "value": "two rooms", "role": "fact", "required": True}]
            return []

    updater = ExplicitUpdater(client=_PerGoalStub())
    result = updater.update(ProblemDescription(), "moving to Berlin, two rooms of stuff", "t1")
    pd = result["state"]
    got = {(v.goal_id, v.name) for v in pd.explicit.variables}
    assert got == {("G1", "city"), ("G2", "volume")}, got
    assert not [o for o in result["operation_log"] if o["operation"] == "cross_goal_duplicate"]


def test_dedup_survives_determiner_variation():
    """The same condition restated for a second goal is a duplicate even when a
    determiner differs. Exact-string comparison let these through, which is how
    one condition ended up recorded twice under different goals."""

    class _ParaphraseStub:
        def chat_json(self, messages):
            system = messages[0]["content"]; content = messages[-1]["content"]
            if "goal-tracking" in system:
                return [{"operation": "create_goal", "goal_text": "Book venue"},
                        {"operation": "create_goal", "goal_text": "Arrange catering"}]
            if "constraint-extraction" in system:
                # same condition, one determiner apart
                if '("Book venue")' in content:
                    return [{"name": "end_time", "description": "the event must finish by 4pm",
                             "constraint_type": "hard"}]
                if '("Arrange catering")' in content:
                    return [{"name": "end_time", "description": "an event must finish by 4pm",
                             "constraint_type": "hard"}]
            return []

    result = ExplicitUpdater(client=_ParaphraseStub()).update(
        ProblemDescription(), "it all has to be done by 4", "t1")
    pd = result["state"]
    assert len([c for c in pd.explicit.constraints if c.name == "end_time"]) == 1, \
        [(c.goal_id, c.description) for c in pd.explicit.constraints]
    assert [o for o in result["operation_log"] if o["operation"] == "cross_goal_duplicate"]


def test_dedup_does_not_merge_different_values_sharing_a_name():
    """Normalisation must not collapse two genuinely different conditions that
    happen to be recorded under the same dimension name."""

    class _SameNameStub:
        def chat_json(self, messages):
            system = messages[0]["content"]; content = messages[-1]["content"]
            if "goal-tracking" in system:
                return [{"operation": "create_goal", "goal_text": "Book venue"},
                        {"operation": "create_goal", "goal_text": "Arrange catering"}]
            if "constraint-extraction" in system:
                if '("Book venue")' in content:
                    return [{"name": "budget_ceiling", "description": "must not exceed £900",
                             "constraint_type": "hard"}]
                if '("Arrange catering")' in content:
                    return [{"name": "budget_ceiling", "description": "must not exceed £200",
                             "constraint_type": "hard"}]
            return []

    result = ExplicitUpdater(client=_SameNameStub()).update(
        ProblemDescription(), "£900 for the room, £200 for food", "t1")
    pd = result["state"]
    assert len([c for c in pd.explicit.constraints if c.name == "budget_ceiling"]) == 2


# ---------------------------------------------------------------------------
# Pivot → variable scoping: old goal's vars invisible after abandon+create
# ---------------------------------------------------------------------------

def test_detail_turn_with_concurrent_goals_routes_by_content():
    """With two active goals and no goal ops this turn, extraction runs for
    ALL active goals; the extractor (which sees each goal's text) routes the
    detail to the goal it concerns. Recency-based routing misassigned exactly
    this case (cycling detail attached to a packing list)."""

    class _GoalAwareStub:
        def chat_json(self, messages):
            system = messages[0]["content"]; content = messages[-1]["content"]
            turn = content.split('User turn: "')[-1].rstrip('"')
            if "goal-tracking" in system:
                return [{"operation": "no_goal_change"}]
            if "variable-extraction" in system and turn == "make it a cycling trip":
                # relevant only to the trip goal
                if '("Plan trip")' in content:
                    return [{"name": "travel_mode", "value": "cycling", "role": "fact", "required": True}]
                return []
            return []

    updater = ExplicitUpdater(client=_GoalAwareStub())
    pd = make_pd_with_goal(goal_id="G1", text="Plan trip", turn="t1")
    from tests.conftest import make_goal
    pd.explicit.goals.append(make_goal(goal_id="G2", text="Draft packing list", turn="t2"))

    result = updater.update(pd, "make it a cycling trip", "t3")
    pd = result["state"]
    routed = {(v.goal_id, v.name) for v in pd.explicit.variables}
    assert routed == {("G1", "travel_mode")}, routed


def test_old_goal_variables_invisible_after_pivot():
    """Variables from an abandoned goal are kept as historical record but not
    touched by future extraction passes for the new goal."""
    client = _MultiStubClient(
        goal_responses={
            "switch to CV": [
                {"operation": "abandon_goal", "goal_id": "G1"},
                {"operation": "create_goal", "goal_text": "Write CV"},
            ],
            "no change": [{"operation": "no_goal_change"}],
        },
        variable_responses={
            "switch to CV": [],  # no new vars on the pivot turn
            "no change": [{"name": "job_type", "value": "data science", "role": "fact", "required": True}],
        },
    )
    updater = ExplicitUpdater(client=client)
    pd = make_pd_with_goal(goal_id="G1", text="Plan trip")

    # Add a variable to G1 manually
    from problem_tracker.schemas.variable import Variable
    pd.explicit.variables.append(
        Variable(variable_id="V1", goal_id="G1", name="destination", value="Kyoto", role="fact", required=True, introduced_turn="t1", last_updated_turn="t1")
    )

    # Pivot G1 → G2
    result = updater.update(pd, "switch to CV", "t2")
    pd = result["state"]

    # Add a var on the new goal
    result = updater.update(pd, "no change", "t3")
    pd = result["state"]

    g1_vars = [v for v in pd.explicit.variables if v.goal_id == "G1"]
    g2_vars = [v for v in pd.explicit.variables if v.goal_id == "G2"]

    # G1 var is preserved as history
    assert len(g1_vars) == 1 and g1_vars[0].name == "destination"
    # G2 var was created fresh
    assert len(g2_vars) == 1 and g2_vars[0].name == "job_type"


# ---------------------------------------------------------------------------
# Abandon with a nonexistent goal_id
# ---------------------------------------------------------------------------

def test_abandon_bad_goal_id_in_pipeline():
    """Abandoning a nonexistent goal fails per-operation; the paired create
    still proceeds (per-op isolation), and the failure is recorded."""
    client = _MultiStubClient(
        goal_responses={
            "switch": [
                {"operation": "abandon_goal", "goal_id": "G99"},
                {"operation": "create_goal", "goal_text": "Write CV"},
            ],
        },
    )
    updater = ExplicitUpdater(client=client)
    pd = make_pd_with_goal(goal_id="G1")
    result = updater.update(pd, "switch", "t1")
    pd = result["state"]
    log = result["operation_log"]

    assert any(e["operation"] == "apply_failed" and "G99" in e["reason"] for e in log)
    assert pd.get_goal("G1").status == "active"          # untouched
    assert pd.get_goal("G2").goal_text == "Write CV"     # create still applied
    assert pd.is_valid()


# ---------------------------------------------------------------------------
# Fix 6: pd.validate() errors appear in operation_log
# ---------------------------------------------------------------------------

def test_state_invariant_violation_logged():
    """If pd.validate() finds errors after a turn, they appear in the log."""
    # We'll create a situation where a constraint is orphaned post-apply
    # by patching pd after the turn using a client that does nothing.
    client = _MultiStubClient()
    updater = ExplicitUpdater(client=client)
    pd = make_pd_with_goal()

    # Inject an orphaned constraint that will fail validate()
    from problem_tracker.schemas.constraint import Constraint
    pd.explicit.constraints.append(
        Constraint("C1", "G99", "bad", "orphaned constraint", "hard", "t0", "t0")
    )

    result = updater.update(pd, "sure", "t1")
    log = result["operation_log"]
    violation_entries = [e for e in log if e["operation"] == "state_invariant_violation"]
    assert len(violation_entries) == 1
    assert "G99" in str(violation_entries[0]["errors"])


# ---------------------------------------------------------------------------
# Budget phrase: lands as constraint OR variable, not both
# ---------------------------------------------------------------------------

def test_budget_phrase_lands_as_constraint_not_variable():
    """'budget cannot exceed £500' extracted as hard constraint should NOT
    also appear as a variable in the same turn."""
    client = _MultiStubClient(
        goal_responses={"budget £500 cap": [{"operation": "no_goal_change"}]},
        variable_responses={"budget £500 cap": []},   # LLM correctly returns no variable
        constraint_responses={"budget £500 cap": [
            {"name": "budget_ceiling", "description": "budget cannot exceed £500", "constraint_type": "hard"}
        ]},
    )
    updater = ExplicitUpdater(client=client)
    pd = make_pd_with_goal()
    result = updater.update(pd, "budget £500 cap", "t1")
    pd = result["state"]

    assert len(pd.explicit.constraints) == 1
    assert pd.explicit.constraints[0].name == "budget_ceiling"
    assert pd.explicit.variables == []


def test_soft_budget_phrase_lands_as_soft_constraint_not_variable():
    """'budget around £500' is a desire, not a point value — under the
    variable/constraint separation it must land as a SOFT constraint and
    produce no variable."""
    client = _MultiStubClient(
        goal_responses={"budget around £500": [{"operation": "no_goal_change"}]},
        variable_responses={"budget around £500": []},  # correctly extracts no variable
        constraint_responses={"budget around £500": [
            {"name": "budget_target", "description": "budget around £500", "constraint_type": "soft"}
        ]},
    )
    updater = ExplicitUpdater(client=client)
    pd = make_pd_with_goal()
    result = updater.update(pd, "budget around £500", "t1")
    pd = result["state"]

    assert pd.explicit.variables == []
    assert len(pd.explicit.constraints) == 1
    assert pd.explicit.constraints[0].name == "budget_target"
    assert pd.explicit.constraints[0].constraint_type == "soft"
    assert not [op for op in result["operation_log"] if op["operation"] == "overlap_detected"]


def test_preference_role_is_rejected():
    """The 'preference' variable role no longer exists — an extractor that
    emits it must produce a validation_skip, not a variable."""
    client = _MultiStubClient(
        goal_responses={"budget around £500": [{"operation": "no_goal_change"}]},
        variable_responses={"budget around £500": [
            {"name": "budget", "value": "£500", "role": "preference", "required": False}
        ]},
        constraint_responses={"budget around £500": []},
    )
    updater = ExplicitUpdater(client=client)
    pd = make_pd_with_goal()
    result = updater.update(pd, "budget around £500", "t1")
    pd = result["state"]

    assert pd.explicit.variables == []
    skips = [op for op in result["operation_log"] if op["operation"] == "validation_skip"]
    assert len(skips) == 1 and "preference" in skips[0]["reason"]


def test_double_extraction_logs_overlap_detected():
    """The same information landing as both a variable and a constraint in
    one turn must be flagged (budget vs budget_ceiling share a name token)."""
    client = _MultiStubClient(
        goal_responses={"under £3000": [{"operation": "no_goal_change"}]},
        variable_responses={"under £3000": [
            {"name": "budget", "value": "£3000", "role": "fact", "required": False}
        ]},
        constraint_responses={"under £3000": [
            {"name": "budget_ceiling", "description": "budget cannot exceed £3000", "constraint_type": "hard"}
        ]},
    )
    updater = ExplicitUpdater(client=client)
    pd = make_pd_with_goal()
    result = updater.update(pd, "under £3000", "t1")

    overlaps = [op for op in result["operation_log"] if op["operation"] == "overlap_detected"]
    assert len(overlaps) == 1
    assert overlaps[0]["variable_name"] == "budget"
    assert overlaps[0]["constraint_name"] == "budget_ceiling"
    assert overlaps[0]["matched_on"] == "value"
    assert overlaps[0]["turn_id"] == "t1"

    # The variable is KEPT in the state — a later update needs a slot to land in.
    assert [v.name for v in result["state"].explicit.variables] == ["budget"]
    # ...but the prompt does not carry the same fact twice.
    from problem_tracker.study.reply import render_problem_description
    rendered = render_problem_description(result["state"])
    assert "Known details" not in rendered, rendered
    assert "budget cannot exceed £3000" in rendered


def test_render_suppresses_a_stale_variable_sharing_a_constraint_name():
    """`scheduling` t6 moves the appointment, updates the constraint, and leaves
    the same-named variable reading "morning". Rendering both states the
    appointment twice and contradicts itself; the constraint is authoritative."""
    from problem_tracker.study.reply import render_problem_description
    client = _MultiStubClient(
        goal_responses={"mechanic in the morning": [{"operation": "no_goal_change"}]},
        variable_responses={"mechanic in the morning": [
            {"name": "mechanic_availability", "value": "morning", "role": "fact",
             "required": True}]},
        constraint_responses={"mechanic in the morning": [
            {"name": "mechanic_availability",
             "description": "the car must go to the mechanic at 2pm",
             "constraint_type": "hard"}]},
    )
    result = ExplicitUpdater(client=client).update(
        make_pd_with_goal(), "mechanic in the morning", "t1")
    rendered = render_problem_description(result["state"])
    assert "morning" not in rendered, rendered
    assert "the car must go to the mechanic at 2pm" in rendered
    # ...and the state still holds both, so a later update has its slot.
    assert [v.name for v in result["state"].explicit.variables] == ["mechanic_availability"]


def test_render_keeps_variables_the_constraints_do_not_state():
    """Only duplicated values are suppressed. A fact no constraint mentions must
    still reach the reply model."""
    from problem_tracker.study.reply import render_problem_description
    client = _MultiStubClient(
        goal_responses={"25 of us, must serve food": [{"operation": "no_goal_change"}]},
        variable_responses={"25 of us, must serve food": [
            {"name": "group_size", "value": "25", "role": "fact", "required": True}
        ]},
        constraint_responses={"25 of us, must serve food": [
            {"name": "food_requirement", "description": "must serve food, not just drinks",
             "constraint_type": "hard"}
        ]},
    )
    result = ExplicitUpdater(client=client).update(
        make_pd_with_goal(), "25 of us, must serve food", "t1")
    rendered = render_problem_description(result["state"])
    assert "group size: 25" in rendered, rendered


def test_overlap_on_name_alone_is_reported_but_not_resolved():
    """A shared name token does not establish that two elements hold the same
    information — `sister_call` vs `call_timing` do not. Only a value appearing
    verbatim inside the constraint justifies removing the variable."""
    client = _MultiStubClient(
        goal_responses={"call my sister after the gym": [{"operation": "no_goal_change"}]},
        variable_responses={"call my sister after the gym": [
            {"name": "sister_call", "value": "owed", "role": "fact", "required": False}
        ]},
        constraint_responses={"call my sister after the gym": [
            {"name": "call_timing", "description": "the call must happen after the gym",
             "constraint_type": "soft"}
        ]},
    )
    result = ExplicitUpdater(client=client).update(
        make_pd_with_goal(), "call my sister after the gym", "t1")

    assert [op["operation"] for op in result["operation_log"] if "overlap" in op["operation"]] \
        == ["overlap_detected"]
    assert [v.name for v in result["state"].explicit.variables] == ["sister_call"]


def test_same_condition_under_a_different_name_is_still_a_duplicate():
    """`concurrent` put one visa deadline on three goals under two different
    names. Keying the duplicate check on the name could never see it; what makes
    two constraints the same is the condition."""

    class _RenamingStub:
        def chat_json(self, messages):
            system = messages[0]["content"]; content = messages[-1]["content"]
            if "goal-tracking" in system:
                return [{"operation": "create_goal", "goal_text": "Plan move"},
                        {"operation": "create_goal", "goal_text": "Sort out visa"}]
            if "constraint-extraction" in system:
                if '("Plan move")' in content:
                    return [{"name": "visa_application_deadline",
                             "description": "the application must be in six weeks before travel",
                             "constraint_type": "hard"}]
                if '("Sort out visa")' in content:
                    return [{"name": "application_deadline",
                             "description": "an application must be in six weeks before travel",
                             "constraint_type": "hard"}]
            return []

    result = ExplicitUpdater(client=_RenamingStub()).update(
        ProblemDescription(), "the visa application has to be in six weeks before I travel", "t1")
    assert len(result["state"].explicit.constraints) == 1
    assert [o["operation"] for o in result["operation_log"]
            if o["operation"] == "cross_goal_duplicate"]


def test_update_that_drops_a_quantity_is_logged():
    """`research` t6 turns "nothing older than about 2015" into "older papers are
    acceptable if widely cited" — one update_constraint, no failure event, and the
    rule is gone. Replacing a value is normal; replacing a quantified rule with an
    unquantified one is the silent loss nothing else observes."""
    pd = make_pd_with_goal()
    client = _MultiStubClient(
        goal_responses={"nothing older than 2015": [{"operation": "no_goal_change"}],
                        "older ones are fine if cited a lot": [{"operation": "no_goal_change"}]},
        constraint_responses={
            "nothing older than 2015": [
                {"name": "source_recency", "description": "nothing older than 2015",
                 "constraint_type": "soft"}],
            "older ones are fine if cited a lot": [
                {"name": "source_recency",
                 "description": "older papers are acceptable if widely cited",
                 "constraint_type": "soft"}],
        },
    )
    updater = ExplicitUpdater(client=client)
    pd = updater.update(pd, "nothing older than 2015", "t1")["state"]
    result = updater.update(pd, "older ones are fine if cited a lot", "t2")

    lost = [o for o in result["operation_log"] if o["operation"] == "update_lost_content"]
    assert len(lost) == 1, result["operation_log"]
    assert lost[0]["lost"] == ["2015"]


def test_updating_a_value_is_not_reported_as_lost_content():
    """"no more than 30" -> "no more than 25" replaces a quantity rather than
    dropping one, and must stay silent or the guard is useless."""
    pd = make_pd_with_goal()
    client = _MultiStubClient(
        goal_responses={"30 people": [{"operation": "no_goal_change"}],
                        "make it 25": [{"operation": "no_goal_change"}]},
        constraint_responses={
            "30 people": [{"name": "guest_ceiling", "description": "no more than 30 people",
                           "constraint_type": "hard"}],
            "make it 25": [{"name": "guest_ceiling", "description": "no more than 25 people",
                            "constraint_type": "hard"}],
        },
    )
    updater = ExplicitUpdater(client=client)
    pd = updater.update(pd, "30 people", "t1")["state"]
    result = updater.update(pd, "make it 25", "t2")
    assert not [o for o in result["operation_log"] if o["operation"] == "update_lost_content"]


# ---------------------------------------------------------------------------
# Heuristics added after the full-probe audit
# ---------------------------------------------------------------------------

def _two_turn(updater, pd, t1, t1_vars, t2, t2_vars):
    pd = updater.update(pd, t1, "t1")["state"]
    return updater.update(pd, t2, "t2")


def test_value_written_to_a_variable_the_turn_never_mentions_is_flagged():
    """The 2pm misroute: the turn is about the mechanic, but the update lands on
    the meeting, destroying 11am. A valid operation on a real variable, so
    nothing else in the pipeline sees it."""
    client = _MultiStubClient(
        goal_responses={},
        variable_responses={
            "one meeting at 11 and the car is going in": [
                {"name": "fixed_meeting_time", "value": "11am", "role": "fact", "required": True},
                {"name": "mechanic_availability", "value": "morning", "role": "fact", "required": True}],
            "actually the mechanic will have to be 2pm": [
                {"name": "fixed_meeting_time", "value": "2pm", "role": "fact", "required": True}],
        },
    )
    result = _two_turn(ExplicitUpdater(client=client), make_pd_with_goal(),
                       "one meeting at 11 and the car is going in", None,
                       "actually the mechanic will have to be 2pm", None)
    flagged = [o for o in result["operation_log"] if o["operation"] == "update_subject_mismatch"]
    assert len(flagged) == 1, result["operation_log"]
    assert flagged[0]["name"] == "fixed_meeting_time"
    assert "mechanic_availability" in flagged[0]["mentioned_instead"]


def test_update_is_not_flagged_when_the_turn_names_nothing_tracked():
    """"actually let's double that" names no variable at all. Silence is normal;
    only a MISS while a rival matches is suspicious, or the guard would fire on
    every anaphoric turn."""
    client = _MultiStubClient(
        goal_responses={},
        variable_responses={
            "aiming for 1000 words": [
                {"name": "word_count", "value": "1000", "role": "fact", "required": True}],
            "actually double that": [
                {"name": "word_count", "value": "2000", "role": "fact", "required": True}],
        },
    )
    result = _two_turn(ExplicitUpdater(client=client), make_pd_with_goal(),
                       "aiming for 1000 words", None, "actually double that", None)
    assert not [o for o in result["operation_log"] if o["operation"] == "update_subject_mismatch"]


def test_obligation_language_on_a_soft_constraint_is_flagged():
    """Renders as "Preferred (soft constraints): must ...", muddying the one
    distinction the injected block exists to convey. Counted, never rewritten."""
    client = _MultiStubClient(
        goal_responses={},
        constraint_responses={"somewhere to sit": [
            {"name": "seating", "description": "must have somewhere to sit",
             "constraint_type": "soft"}]},
    )
    result = ExplicitUpdater(client=client).update(make_pd_with_goal(), "somewhere to sit", "t1")
    flagged = [o for o in result["operation_log"] if o["operation"] == "type_language_mismatch"]
    assert len(flagged) == 1 and flagged[0]["name"] == "seating"
    # ...and the constraint itself is untouched
    assert result["state"].explicit.constraints[0].constraint_type == "soft"


def test_hedged_soft_constraint_is_not_flagged():
    client = _MultiStubClient(
        goal_responses={},
        constraint_responses={"three a week ideally": [
            {"name": "frequency", "description": "ideally three sessions a week",
             "constraint_type": "soft"}]},
    )
    result = ExplicitUpdater(client=client).update(make_pd_with_goal(), "three a week ideally", "t1")
    assert not [o for o in result["operation_log"] if o["operation"] == "type_language_mismatch"]


def test_reworded_duplicate_across_goals_is_caught():
    """One visa deadline reached three goals under two names and two phrasings.
    Exact keys cannot see a reordered restatement of the same condition."""

    class _RewordStub:
        def chat_json(self, messages):
            system = messages[0]["content"]; content = messages[-1]["content"]
            if "goal-tracking" in system:
                return [{"operation": "create_goal", "goal_text": "Plan move"},
                        {"operation": "create_goal", "goal_text": "Sort out visa"}]
            if "constraint-extraction" in system:
                if '("Plan move")' in content:
                    return [{"name": "visa_application_deadline",
                             "description": "must submit the visa application six weeks before travel",
                             "constraint_type": "hard"}]
                if '("Sort out visa")' in content:
                    return [{"name": "application_deadline",
                             "description": "visa application must be submitted six weeks before travel",
                             "constraint_type": "hard"}]
            return []

    result = ExplicitUpdater(client=_RewordStub()).update(
        ProblemDescription(), "the visa application has to be in six weeks before I travel", "t1")
    assert len(result["state"].explicit.constraints) == 1, \
        [(c.goal_id, c.name, c.description) for c in result["state"].explicit.constraints]


def test_duplicate_is_kept_on_the_goal_it_is_actually_about():
    """`concurrent` deduped the visa deadline onto "Plan move" purely because
    that goal was created first, leaving "Sort out visa" holding nothing about a
    visa. Creation order is not evidence; goal-text overlap is."""

    class _VisaStub:
        def chat_json(self, messages):
            system = messages[0]["content"]; content = messages[-1]["content"]
            if "goal-tracking" in system:
                return [{"operation": "create_goal", "goal_text": "Plan move"},
                        {"operation": "create_goal", "goal_text": "Sort out visa"}]
            if "constraint-extraction" in system:
                return [{"name": "visa_deadline",
                         "description": "the visa application must be in six weeks before travel",
                         "constraint_type": "hard"}]
            return []

    result = ExplicitUpdater(client=_VisaStub()).update(
        ProblemDescription(), "the visa application has to be in six weeks before I travel", "t1")
    constraints = result["state"].explicit.constraints
    assert len(constraints) == 1
    kept_on = result["state"].get_goal(constraints[0].goal_id).goal_text
    assert kept_on == "Sort out visa", f"kept on {kept_on!r}"
    dropped = [o for o in result["operation_log"] if o["operation"] == "cross_goal_duplicate"]
    assert dropped and dropped[0]["kept_on"] == constraints[0].goal_id


def test_tie_on_affinity_falls_to_the_first_arrival():
    """When neither goal's text relates to the content, behaviour must stay
    deterministic rather than depending on comparison order."""

    class _NeutralStub:
        def chat_json(self, messages):
            system = messages[0]["content"]
            if "goal-tracking" in system:
                return [{"operation": "create_goal", "goal_text": "Book venue"},
                        {"operation": "create_goal", "goal_text": "Arrange catering"}]
            if "constraint-extraction" in system:
                return [{"name": "end_time", "description": "must finish by 4pm",
                         "constraint_type": "hard"}]
            return []

    result = ExplicitUpdater(client=_NeutralStub()).update(
        ProblemDescription(), "it has to be done by 4", "t1")
    constraints = result["state"].explicit.constraints
    assert len(constraints) == 1 and constraints[0].goal_id == "G1"


def test_subject_mismatch_rivals_include_constraints():
    """Validated against 40 recorded sessions: the guard was silent on all four
    runs where the misroute actually happened, because the turn's real subject
    was tracked only as a CONSTRAINT (`mechanic_availability`) and never as a
    variable. A variables-only rival search stays quiet on exactly the cases the
    guard exists for."""
    client = _MultiStubClient(
        goal_responses={},
        variable_responses={
            "two meetings, one at 11": [
                {"name": "work_meeting_2_time", "value": None, "role": "unknown", "required": False}],
            "actually the mechanic can't do the morning, it'll have to be 2pm": [
                {"name": "work_meeting_2_time", "value": "2pm", "role": "fact", "required": True}],
        },
        constraint_responses={
            "two meetings, one at 11": [
                {"name": "mechanic_availability",
                 "description": "the car must go to the mechanic in the morning",
                 "constraint_type": "hard"}],
        },
    )
    updater = ExplicitUpdater(client=client)
    pd = updater.update(make_pd_with_goal(), "two meetings, one at 11", "t1")["state"]
    assert not [v for v in pd.explicit.variables if v.name == "mechanic_availability"], \
        "the subject must exist ONLY as a constraint for this test to mean anything"
    result = updater.update(
        pd, "actually the mechanic can't do the morning, it'll have to be 2pm", "t2")

    flagged = [o for o in result["operation_log"] if o["operation"] == "update_subject_mismatch"]
    assert len(flagged) == 1, result["operation_log"]
    assert flagged[0]["name"] == "work_meeting_2_time"
    assert "mechanic_availability" in flagged[0]["mentioned_instead"]
