"""updaters/applicators/goal_applicator.py

GoalApplicator — deterministic application of goal operations.

This is the application function A of the formal model: A(S_t, Ω) -> (S_{t+1}, L_t).
It is pure code — no LLM involvement. Each operation has preconditions
(referenced goals must exist) and postconditions (the resulting Goal must
satisfy goal_schema_v2), and every applied operation emits a log entry with
before/after snapshots for the state transition record.

Goals are IMMUTABLE (goal_schema_v2): goal_text never changes after creation,
and the only mutation is a lifecycle status transition. Objective replacement
is expressed as abandon_goal + create_goal in the same turn. Multiple goals
may be active concurrently.

IDs (goal_id) are assigned here, never by the LLM.
"""

from __future__ import annotations

from typing import Any

from problem_tracker.core.problem_description import ProblemDescription
from problem_tracker.schemas.goal import Goal, validate_goal

__all__ = ["VALID_GOAL_OPERATIONS", "GoalApplicator"]

VALID_GOAL_OPERATIONS = {
    "create_goal",
    "complete_goal",
    "abandon_goal",
    "no_goal_change",
}

_REQUIRED_FIELDS = {
    "create_goal":    ["goal_text"],
    "complete_goal":  ["goal_id"],
    "abandon_goal":   ["goal_id"],
    "no_goal_change": [],
}


def _next_goal_id(pd: ProblemDescription) -> str:
    nums = [int(g.goal_id[1:]) for g in pd.explicit.goals if g.goal_id[1:].isdigit()]
    return f"G{max(nums) + 1}" if nums else "G1"


class GoalApplicator:
    """Validates and applies goal operations to a ProblemDescription."""

    # ------------------------------------------------------------------
    # Validation (shape only — no state access)
    # ------------------------------------------------------------------

    def validate_operations(self, operations: list[dict[str, Any]]) -> None:
        """Check operation shape against the fixed vocabulary.

        Raises ValueError on an unknown operation type or missing field.
        """
        for op in operations:
            if not isinstance(op, dict):
                raise TypeError(f"Operation must be a dict, got {type(op).__name__}")
            op_type = op.get("operation")
            if op_type not in VALID_GOAL_OPERATIONS:
                raise ValueError(f"Invalid goal operation: {op_type!r}")
            for fld in _REQUIRED_FIELDS.get(op_type, []):
                if not op.get(fld):
                    raise ValueError(f"Missing field {fld!r} in {op_type} operation")

    # ------------------------------------------------------------------
    # Application (deterministic state transition)
    # ------------------------------------------------------------------

    def apply_operation(
        self, pd: ProblemDescription, op: dict[str, Any], turn_id: str
    ) -> dict[str, Any] | None:
        """Apply one validated operation. See _apply for behaviour.

        ``turn_id`` also identifies goals ended earlier in the SAME turn, which
        the duplicate-create guard needs (see below).
        """
        return self._apply(pd, op, turn_id)

    def _apply(
        self, pd: ProblemDescription, op: dict[str, Any], turn_id: str
    ) -> dict[str, Any] | None:
        """Apply one validated operation. Returns a log entry (None for no-op).

        Raises ValueError when a precondition fails (referenced goal missing)
        or a postcondition fails (resulting goal invalid). State is only
        mutated on success.
        """
        op_type = op["operation"]

        if op_type == "no_goal_change":
            return None

        if op_type == "create_goal":
            text = op["goal_text"].strip()
            # Guard: re-creating a goal whose objective text matches one that is
            # already active — OR one abandoned/completed EARLIER IN THIS TURN —
            # is a model error, not a genuine pivot. The same-turn case is the
            # important one: a detail change misread as a pivot arrives as
            # [abandon_goal G1, create_goal "<same text>"], and without the
            # same-turn clause the abandon would defeat the guard and strand
            # every variable and constraint on the dead goal.
            duplicate = next(
                (g for g in pd.explicit.goals
                 if g.goal_text.strip().lower() == text.lower()
                 and (g.status == "active" or g.last_updated_turn == turn_id)),
                None,
            )
            if duplicate is not None and duplicate.status != "active":
                # Undo the same-turn ending: the pivot was spurious.
                duplicate.status = "active"
                return {
                    "operation": "validation_skip",
                    "component": "goal",
                    "goal_id": duplicate.goal_id,
                    "reason": (f"spurious pivot: goal {duplicate.goal_id} was ended and "
                               f"recreated with identical text ({text!r}) in the same turn; "
                               f"reinstated to avoid stranding its elements"),
                    "turn_id": turn_id,
                }
            if duplicate is not None:
                return {
                    "operation": "validation_skip",
                    "component": "goal",
                    "goal_id": duplicate.goal_id,
                    "reason": f"create_goal duplicates active goal {duplicate.goal_id} ({text!r})",
                    "turn_id": turn_id,
                }

            goal_id = _next_goal_id(pd)
            goal = Goal(
                goal_id=goal_id,
                goal_text=text,
                status="active",
                introduced_turn=turn_id,
                last_updated_turn=turn_id,
            )
            self._assert_valid(goal)
            pd.explicit.goals.append(goal)
            return self._log("create_goal", goal_id, turn_id, None, goal)

        if op_type == "complete_goal":
            return self._set_status(pd, op["goal_id"], "completed", "complete_goal", turn_id)

        if op_type == "abandon_goal":
            return self._set_status(pd, op["goal_id"], "abandoned", "abandon_goal", turn_id)

        raise ValueError(f"Unhandled goal operation: {op_type!r}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _set_status(
        self, pd: ProblemDescription, goal_id: str, status: str, operation: str, turn_id: str
    ) -> dict[str, Any] | None:
        goal = self._require_goal(pd, goal_id)
        if goal.status == status:
            # Already in this state. Re-ending is a no-op, not a transition:
            # emitting one would log a second edge that changed nothing and
            # re-trigger the elements_detached warning for elements that were
            # detached on the earlier turn.
            return None
        before = goal.to_dict()
        goal.status = status
        goal.last_updated_turn = turn_id
        self._assert_valid(goal)
        return self._log(operation, goal_id, turn_id, before, goal, before_dict=True)

    def _require_goal(self, pd: ProblemDescription, goal_id: str) -> Goal:
        goal = pd.get_goal(goal_id)
        if goal is None:
            raise ValueError(f"Goal not found: {goal_id!r}")
        return goal

    def _assert_valid(self, goal: Goal) -> None:
        errors = validate_goal(goal)
        if errors:
            raise ValueError(f"Goal {goal.goal_id!r} failed validation: {errors}")

    def _log(
        self,
        operation: str,
        goal_id: str,
        turn_id: str,
        before: Any,
        goal: Goal,
        before_dict: bool = False,
    ) -> dict[str, Any]:
        return {
            "operation": operation,
            "goal_id": goal_id,
            "turn_id": turn_id,
            "before": before if (before_dict or before is None) else before.to_dict(),
            "after": goal.to_dict(),
        }
