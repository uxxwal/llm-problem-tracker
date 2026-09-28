"""tests/test_schemas.py — unit tests for Goal, Variable, Constraint schemas."""

from problem_tracker.schemas.goal import Goal, validate_goal, VALID_GOAL_STATUSES
from problem_tracker.schemas.variable import Variable, validate_variable, VALID_VARIABLE_ROLES
from problem_tracker.schemas.constraint import Constraint, validate_constraint, VALID_CONSTRAINT_TYPES
from problem_tracker.core.problem_description import ProblemDescription
from tests.conftest import make_goal, make_pd_with_goal


# ---------------------------------------------------------------------------
# Fix 5: constraint is NOT in VALID_VARIABLE_ROLES
# ---------------------------------------------------------------------------

def test_constraint_not_a_variable_role():
    assert "constraint" not in VALID_VARIABLE_ROLES


def test_preference_not_a_variable_role():
    # Preferences are soft constraints (objective), not variables.
    assert "preference" not in VALID_VARIABLE_ROLES


def test_variable_roles_are_fact_unknown():
    assert VALID_VARIABLE_ROLES == {"fact", "unknown"}


# ---------------------------------------------------------------------------
# Goal schema
# ---------------------------------------------------------------------------

def test_valid_goal_passes():
    g = make_goal()
    assert validate_goal(g) == []


def test_goal_statuses_are_v2():
    # "superseded" was removed with immutable goals: replacement is
    # abandon_goal + create_goal, both visible in the turn's log.
    assert VALID_GOAL_STATUSES == {"active", "completed", "abandoned"}


def test_goal_invalid_status():
    g = make_goal()
    g.status = "pending"
    errors = validate_goal(g)
    assert any("status" in e for e in errors)


def test_goal_empty_goal_text():
    g = make_goal()
    g.goal_text = ""
    errors = validate_goal(g)
    assert any("goal_text" in e for e in errors)


def test_goal_roundtrip():
    g = make_goal()
    g2 = Goal.from_dict(g.to_dict())
    assert g2.goal_id == g.goal_id
    assert g2.goal_text == g.goal_text


def test_goal_from_dict_tolerates_v1_version_history():
    v1 = {
        "goal_id": "G1",
        "status": "superseded",
        "active_version_id": "G1_v2",
        "versions": [
            {"version_id": "G1_v1", "goal_text": "Plan trip", "introduced_turn": "t1"},
            {"version_id": "G1_v2", "goal_text": "Plan cycling trip", "introduced_turn": "t3"},
        ],
        "introduced_turn": "t1",
        "last_updated_turn": "t5",
    }
    g = Goal.from_dict(v1)
    assert g.goal_text == "Plan cycling trip"   # active version adopted
    assert g.status == "abandoned"              # superseded mapped to abandoned
    assert validate_goal(g) == []


# ---------------------------------------------------------------------------
# Variable schema
# ---------------------------------------------------------------------------

def test_valid_variable_passes():
    v = Variable(variable_id="V1", goal_id="G1", name="destination", value="Kyoto", role="fact", required=True, introduced_turn="t1", last_updated_turn="t1")
    assert validate_variable(v) == []


def test_variable_bad_role():
    v = Variable(variable_id="V1", goal_id="G1", name="destination", value="Kyoto", role="constraint", required=True, introduced_turn="t1", last_updated_turn="t1")
    errors = validate_variable(v)
    assert any("role" in e for e in errors)


def test_variable_unknown_must_have_null_value():
    v = Variable(variable_id="V1", goal_id="G1", name="budget", value="£500", role="unknown", required=False, introduced_turn="t1", last_updated_turn="t1")
    errors = validate_variable(v)
    assert any("unknown" in e for e in errors)


def test_variable_name_must_be_snake_case():
    v = Variable(variable_id="V1", goal_id="G1", name="bad name", value="Kyoto", role="fact", required=True, introduced_turn="t1", last_updated_turn="t1")
    errors = validate_variable(v)
    assert any("snake_case" in e for e in errors)


# ---------------------------------------------------------------------------
# Constraint schema
# ---------------------------------------------------------------------------

def test_valid_hard_constraint_passes():
    c = Constraint("C1", "G1", "budget_ceiling", "budget cannot exceed £500", "hard", "t1", "t1")
    assert validate_constraint(c) == []


def test_valid_soft_constraint_passes():
    c = Constraint("C1", "G1", "flight_stops", "prefer non-stop", "soft", "t1", "t1")
    assert validate_constraint(c) == []


def test_constraint_invalid_type():
    c = Constraint("C1", "G1", "budget_ceiling", "£500 max", "medium", "t1", "t1")
    errors = validate_constraint(c)
    assert any("constraint_type" in e for e in errors)


def test_constraint_bad_name():
    c = Constraint("C1", "G1", "bad name", "£500 max", "hard", "t1", "t1")
    errors = validate_constraint(c)
    assert any("snake_case" in e for e in errors)


def test_constraint_empty_description():
    c = Constraint("C1", "G1", "budget_ceiling", "", "hard", "t1", "t1")
    errors = validate_constraint(c)
    assert any("description" in e for e in errors)


def test_constraint_roundtrip():
    c = Constraint("C1", "G1", "diet_restriction", "must be vegetarian", "hard", "t1", "t1")
    c2 = Constraint.from_dict(c.to_dict())
    assert c2.name == c.name and c2.constraint_type == c.constraint_type


# ---------------------------------------------------------------------------
# ProblemDescription validation
# ---------------------------------------------------------------------------

def test_pd_validates_cleanly_with_all_schema_types():
    pd = make_pd_with_goal()
    pd.explicit.variables.append(
        Variable(variable_id="V1", goal_id="G1", name="destination", value="Kyoto", role="fact", required=True, introduced_turn="t1", last_updated_turn="t1")
    )
    pd.explicit.constraints.append(
        Constraint("C1", "G1", "budget_ceiling", "budget cannot exceed £500", "hard", "t1", "t1")
    )
    assert pd.validate() == []


def test_pd_catches_orphaned_constraint():
    pd = make_pd_with_goal()
    pd.explicit.constraints.append(
        Constraint("C1", "G99", "budget_ceiling", "£500 max", "hard", "t1", "t1")
    )
    errors = pd.validate()
    assert any("G99" in e for e in errors)


def test_pd_catches_duplicate_constraint_id():
    pd = make_pd_with_goal()
    c = Constraint("C1", "G1", "budget_ceiling", "£500 max", "hard", "t1", "t1")
    pd.explicit.constraints.extend([c, Constraint("C1", "G1", "diet", "vegan", "hard", "t1", "t1")])
    errors = pd.validate()
    assert any("duplicate constraint_id" in e for e in errors)


def test_pd_from_dict_roundtrip_with_constraints():
    pd = make_pd_with_goal()
    pd.explicit.constraints.append(
        Constraint("C1", "G1", "budget_ceiling", "£500 max", "hard", "t1", "t1")
    )
    pd2 = ProblemDescription.from_dict(pd.to_dict())
    assert len(pd2.explicit.constraints) == 1
    assert pd2.explicit.constraints[0].constraint_type == "hard"
