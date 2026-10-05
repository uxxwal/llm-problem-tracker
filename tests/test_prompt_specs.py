"""tests/test_prompt_specs.py — properties of the prompts and few-shots.

These are not LLM tests; they assert that the prompt material is internally
consistent. Several observed extraction failures traced back to few-shots that
contradicted the stated rules, so the few-shots are pinned here.
"""

import json
from itertools import pairwise

from problem_tracker.prompt_specs import (
    _CONSTRAINT_FEW_SHOTS,
    _FEW_SHOTS,
    _VARIABLE_EXTRACTION_FEW_SHOTS,
    CONSTRAINT_EXTRACTION_SYSTEM_PROMPT,
    GOAL_SYSTEM_PROMPT,
    VARIABLE_EXTRACTION_SYSTEM_PROMPT,
    build_constraint_extraction_messages,
    build_variable_extraction_messages,
)
from problem_tracker.schemas.constraint import VALID_CONSTRAINT_TYPES
from problem_tracker.schemas.variable import VALID_VARIABLE_ROLES

# ---------------------------------------------------------------------------
# Goal few-shots must obey the rules stated in the goal prompt
# ---------------------------------------------------------------------------

def test_goal_few_shots_use_valid_operations():
    # goal_schema_v2: goals are immutable — refine/supersede no longer exist.
    valid = {"create_goal", "complete_goal", "abandon_goal", "no_goal_change"}
    for _, _, output in _FEW_SHOTS:
        for op in json.loads(output):
            assert op["operation"] in valid, op


def test_pivot_few_shots_are_abandon_plus_create():
    """The prompt mandates abandon+create for objective replacement. Wherever a
    few-shot creates a goal while an existing one is active, it must either be
    a genuine pivot (abandon THEN create) or a pure addition (create alone) —
    never a create that silently orphans the old goal."""
    demonstrated_pivot = False
    for state, turn, output in _FEW_SHOTS:
        ops = [o["operation"] for o in json.loads(output)]
        if "abandon_goal" in ops and "create_goal" in ops:
            assert ops == ["abandon_goal", "create_goal"], (turn, ops)
            demonstrated_pivot = True
    assert demonstrated_pivot, "no few-shot demonstrates a pivot as abandon+create"


def test_addition_few_shot_does_not_abandon():
    """Additive language must create a goal WITHOUT abandoning the existing one
    — the failure observed in pilot."""
    found = False
    for state, turn, output in _FEW_SHOTS:
        if any(w in turn.lower() for w in ("as well", "also", "both")):
            ops = [o["operation"] for o in json.loads(output)]
            assert "abandon_goal" not in ops, (turn, ops)
            found = True
    assert found, "no few-shot demonstrates additive language"


def test_detail_change_few_shot_is_no_goal_change():
    """'Make it a cycling trip' is a detail change: goals are immutable, so
    the goal few-shot must answer no_goal_change, and the variable few-shots
    must capture the detail instead (travel_mode)."""
    for state, turn, output in _FEW_SHOTS:
        if "cycling" in turn:
            ops = [o["operation"] for o in json.loads(output)]
            assert ops == ["no_goal_change"], (turn, ops)

    cycling_var_shots = [
        output for turn, _, _, output in _VARIABLE_EXTRACTION_FEW_SHOTS
        if "cycling" in turn
    ]
    assert cycling_var_shots, "no variable few-shot captures the cycling detail"
    assert "travel_mode" in cycling_var_shots[0]


def test_concurrent_goal_few_shot_exists():
    """At least one few-shot must demonstrate creating an additional goal
    while another remains active (concurrent goals are part of the design)."""
    found = False
    for state, turn, output in _FEW_SHOTS:
        ops = [o["operation"] for o in json.loads(output)]
        if ops == ["create_goal"] and '"status": "active"' in state:
            found = True
    assert found, "no few-shot demonstrates concurrent goal creation"


# ---------------------------------------------------------------------------
# Variable few-shots: only valid roles, and no conditions/preferences
# ---------------------------------------------------------------------------

def test_variable_few_shots_only_use_valid_roles():
    for _, _, _, output in _VARIABLE_EXTRACTION_FEW_SHOTS:
        for item in json.loads(output):
            assert item["role"] in VALID_VARIABLE_ROLES, item


def test_variable_few_shots_contain_no_preference_role():
    """Preferences are soft constraints; no variable few-shot may model one."""
    for _, _, _, output in _VARIABLE_EXTRACTION_FEW_SHOTS:
        assert "preference" not in output


def test_variable_few_shots_do_not_extract_budget_conditions():
    """Turns mentioning an approximate budget must not yield a budget variable —
    that is a soft constraint."""
    for turn, _, _, output in _VARIABLE_EXTRACTION_FEW_SHOTS:
        if "around" in turn and ("£" in turn or "budget" in turn):
            names = [i["name"] for i in json.loads(output)]
            assert not any("budget" in n for n in names), (turn, names)


def test_abstracted_goal_specifics_are_captured_as_variables():
    """Goal text is deliberately minimal, so specifics stripped from the goal
    must be captured as variables or the information is lost between the two
    extractors (observed failure: 'plan a honeymoon' -> goal 'Plan trip', no
    variable, occasion dropped entirely)."""
    # At least one few-shot must show a specific that the GOAL prompt would
    # abstract away being caught here instead (e.g. the role a cover letter is
    # for, when the goal text is only "Write cover letter").
    caught = [
        output for turn, _, _, output in _VARIABLE_EXTRACTION_FEW_SHOTS
        if any(w in turn.lower() for w in ("cover letter", "honeymoon", "trip to"))
        and json.loads(output)
    ]
    assert caught, "no few-shot demonstrates catching a specific stripped from goal text"

    # The rule must also be stated, not only demonstrated.
    assert "goal text is deliberately kept minimal" in VARIABLE_EXTRACTION_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Constraint few-shots
# ---------------------------------------------------------------------------

def test_constraint_descriptions_state_conditions_not_changes():
    """Observed in pilot: an updated constraint read 'budget increased to £100
    per person' — a narration of the revision, not a statement of the condition.
    No few-shot may model that phrasing, and the rule must be stated."""
    assert "SELF-CONTAINED" in CONSTRAINT_EXTRACTION_SYSTEM_PROMPT
    assert "never the change" in CONSTRAINT_EXTRACTION_SYSTEM_PROMPT

    banned = ("increased to", "raised to", "changed to", "now £")
    for _, _, _, output in _CONSTRAINT_FEW_SHOTS:
        for item in json.loads(output):
            desc = item["description"].lower()
            for phrase in banned:
                assert phrase not in desc, (phrase, item)


def test_an_update_few_shot_demonstrates_full_rewrite():
    """At least one constraint few-shot must show an update whose description is
    rewritten in full rather than phrased as an amendment."""
    found = False
    for turn, _, existing, output in _CONSTRAINT_FEW_SHOTS:
        if existing and any(e["name"] == i["name"] for e in existing for i in json.loads(output)):
            found = True
            for item in json.loads(output):
                assert item["description"].startswith(("must", "should", "ideally", "prefer")) \
                    or "must" in item["description"], item
    assert found, "no few-shot demonstrates updating an existing constraint"


def test_money_is_never_a_variable():
    """Money amounts belong to constraints; the variable prompt must say so."""
    assert "money is ALWAYS a constraint" in VARIABLE_EXTRACTION_SYSTEM_PROMPT
    for _, _, _, output in _VARIABLE_EXTRACTION_FEW_SHOTS:
        for item in json.loads(output):
            assert "budget" not in item["name"], item
            assert "£" not in str(item.get("value", "")), item


def test_constraint_few_shots_only_use_valid_types():
    for _, _, _, output in _CONSTRAINT_FEW_SHOTS:
        for item in json.loads(output):
            assert item["constraint_type"] in VALID_CONSTRAINT_TYPES, item


def test_preferences_are_soft_constraints_in_few_shots():
    """A stated preference must appear as a soft constraint somewhere."""
    found = False
    for turn, _, _, output in _CONSTRAINT_FEW_SHOTS:
        if "prefer" in turn:
            for item in json.loads(output):
                if item["constraint_type"] == "soft":
                    found = True
    assert found, "no few-shot models a preference as a soft constraint"


# ---------------------------------------------------------------------------
# Message builders produce well-formed chat sequences
# ---------------------------------------------------------------------------

def test_variable_messages_alternate_roles_and_end_with_user():
    messages = build_variable_extraction_messages("some turn", "G1", None)
    assert messages[0]["role"] == "system"
    assert messages[-1]["role"] == "user"
    assert 'User turn: "some turn"' in messages[-1]["content"]
    for a, b in pairwise(messages[1:]):
        assert a["role"] != b["role"], "few-shot turns must alternate user/assistant"


def test_constraint_messages_include_existing_constraints_when_given():
    existing = [{"constraint_id": "C1", "name": "budget_ceiling",
                 "description": "under £3000", "constraint_type": "hard"}]
    messages = build_constraint_extraction_messages("tighten it", "G1", existing)
    assert "Existing constraints for this goal" in messages[-1]["content"]
    assert "budget_ceiling" in messages[-1]["content"]


def test_goal_prompt_treats_conditional_dismissal_as_no_change():
    """"drop it if it doesn't fit" states a condition, not a decision. Reading it
    as a dismissal ends the goal and detaches everything under it."""
    assert "CONDITIONAL dismissal is not a dismissal" in GOAL_SYSTEM_PROMPT


def test_variable_prompt_forbids_filling_an_unrelated_variable():
    """A loose value must not be written into whichever tracked variable happens
    to be empty — the resulting state is wrong and no guard can detect it."""
    assert "NAMES ITS SUBJECT" in VARIABLE_EXTRACTION_SYSTEM_PROMPT


def test_both_extractors_forbid_restating_one_statement_under_every_goal():
    """Each focal goal gets its own extraction pass, so without this rule a
    single condition is recorded once per active goal."""
    for prompt in (VARIABLE_EXTRACTION_SYSTEM_PROMPT, CONSTRAINT_EXTRACTION_SYSTEM_PROMPT):
        assert "under every goal" in prompt


def test_constraint_prompt_guards_name_reuse_against_overwriting():
    """Two conditions on the same subject that can both hold are separate
    constraints. Sharing a name makes the second silently destroy the first."""
    assert "REPLACES THE OLD ONE" in CONSTRAINT_EXTRACTION_SYSTEM_PROMPT


def test_goal_prompt_has_a_subsumption_tie_break():
    """The schema holds a FLAT set of goals, so an umbrella objective and its
    components can only be recorded as siblings (duplicating shared conditions)
    or as one goal (losing the grouping). Both are lossy; the tie-break makes the
    choice deterministic instead of arbitrary."""
    assert "GOAL OR COMPONENT" in GOAL_SYSTEM_PROMPT
    assert "stands on its own" in GOAL_SYSTEM_PROMPT


def test_system_prompts_state_the_variable_constraint_separation():
    assert "POINT-VALUED" in VARIABLE_EXTRACTION_SYSTEM_PROMPT
    assert "WHAT IS NOT A VARIABLE" in VARIABLE_EXTRACTION_SYSTEM_PROMPT
    assert "feasibility" in CONSTRAINT_EXTRACTION_SYSTEM_PROMPT
    assert "GOAL ABSTRACTION RULE" in GOAL_SYSTEM_PROMPT
