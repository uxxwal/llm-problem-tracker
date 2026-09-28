"""tests/conftest.py — shared helpers for the ProblemDescription test suite.

Compatible with both pytest (recommended) and unittest discovery.
goal_schema_v2: goals are immutable (id + text + status, no version history).
"""

from problem_tracker.core.problem_description import ProblemDescription
from problem_tracker.schemas.goal import Goal


def make_goal(goal_id="G1", status="active", text="Plan trip", turn="t1"):
    return Goal(
        goal_id=goal_id,
        goal_text=text,
        status=status,
        introduced_turn=turn,
        last_updated_turn=turn,
    )


def make_pd_with_goal(goal_id="G1", text="Plan trip", turn="t1"):
    pd = ProblemDescription()
    pd.explicit.goals.append(make_goal(goal_id=goal_id, text=text, turn=turn))
    return pd
