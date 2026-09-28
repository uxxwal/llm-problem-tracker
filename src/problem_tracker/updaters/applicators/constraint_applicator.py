"""updaters/applicators/constraint_applicator.py

ConstraintApplicator — deterministic application of extracted constraint items.

Application function A for constraints: given raw items proposed by the
ConstraintExtractor, resolve identity by (goal_id, name), decide create vs
update vs no-op, enforce schema validation with rollback, and emit log
entries with before/after snapshots.

Identity rules mirror VariableApplicator:
- constraint_id is assigned here (C1, C2, ...), never by the LLM.
- A name already tracked for the goal is an update to that constraint.
- Duplicate names within the same extraction batch are merged into the
  queued create op (the ``created_this_turn`` guard).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from problem_tracker.core.problem_description import ProblemDescription
from problem_tracker.schemas.constraint import Constraint, validate_constraint, VALID_CONSTRAINT_TYPES

__all__ = ["ConstraintApplicator"]

logger = logging.getLogger(__name__)


def _next_constraint_id(pd: ProblemDescription) -> str:
    nums = [
        int(c.constraint_id[1:])
        for c in pd.explicit.constraints
        if c.constraint_id[1:].isdigit()
    ]
    return f"C{max(nums) + 1}" if nums else "C1"


class ConstraintApplicator:
    def apply(
        self,
        pd: ProblemDescription,
        raw: List[Dict[str, Any]],
        active_goal_id: str,
        turn_id: str,
    ) -> List[Dict[str, Any]]:
        """Apply raw extracted items to pd. Returns the operation log entries."""
        existing_by_name = {
            c.name: c for c in pd.explicit.constraints if c.goal_id == active_goal_id
        }

        ops: List[Dict[str, Any]] = []
        created_this_turn: set = set()

        for item in raw:
            if not isinstance(item, dict):
                continue

            name = (item.get("name") or "").strip()
            if not name:
                continue

            description = (item.get("description") or "").strip()
            if not description:
                continue

            constraint_type = item.get("constraint_type", "hard")
            if constraint_type not in VALID_CONSTRAINT_TYPES:
                logger.warning(
                    "[ConstraintApplicator] skipping %r: invalid constraint_type %r",
                    name, constraint_type,
                )
                ops.append({"operation": "validation_skip", "component": "constraint",
                            "name": name, "reason": f"invalid constraint_type {constraint_type!r}",
                            "turn_id": turn_id})
                continue

            if name in existing_by_name:
                con = existing_by_name[name]

                if con.constraint_id in created_this_turn:
                    # Duplicate name in same LLM response — merge into existing create op.
                    if con.description == description and con.constraint_type == constraint_type:
                        continue  # identical duplicate, skip
                    con.description = description
                    con.constraint_type = constraint_type
                    for op in ops:
                        if (
                            op.get("operation") == "create_constraint"
                            and op.get("constraint_id") == con.constraint_id
                        ):
                            op["after"] = con.to_dict()
                            break
                    continue

                # Constraint existed before this turn — emit a real update if changed.
                if con.description == description and con.constraint_type == constraint_type:
                    continue  # true no-op

                before = con.to_dict()
                con.description = description
                con.constraint_type = constraint_type
                con.last_updated_turn = turn_id

                errors = validate_constraint(con)
                if errors:
                    con.description = before["description"]
                    con.constraint_type = before["constraint_type"]
                    con.last_updated_turn = before["last_updated_turn"]
                    logger.warning("[ConstraintApplicator] skipping invalid update to %r: %s", name, errors)
                    ops.append({"operation": "validation_skip", "component": "constraint",
                                "name": name, "reason": str(errors), "turn_id": turn_id})
                    continue

                ops.append({
                    "operation": "update_constraint",
                    "constraint_id": con.constraint_id,
                    "name": name,
                    "turn_id": turn_id,
                    "before": before,
                    "after": con.to_dict(),
                })

            else:
                constraint_id = _next_constraint_id(pd)
                con = Constraint(
                    constraint_id=constraint_id,
                    goal_id=active_goal_id,
                    name=name,
                    description=description,
                    constraint_type=constraint_type,
                    introduced_turn=turn_id,
                    last_updated_turn=turn_id,
                )
                errors = validate_constraint(con)
                if errors:
                    logger.warning("[ConstraintApplicator] skipping invalid constraint %r: %s", name, errors)
                    ops.append({"operation": "validation_skip", "component": "constraint",
                                "name": name, "reason": str(errors), "turn_id": turn_id})
                    continue

                pd.explicit.constraints.append(con)
                existing_by_name[name] = con
                created_this_turn.add(constraint_id)

                ops.append({
                    "operation": "create_constraint",
                    "constraint_id": constraint_id,
                    "name": name,
                    "goal_id": active_goal_id,
                    "turn_id": turn_id,
                    "after": con.to_dict(),
                })

        return ops
