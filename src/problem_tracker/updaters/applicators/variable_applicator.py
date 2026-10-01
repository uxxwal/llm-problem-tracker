"""updaters/applicators/variable_applicator.py

VariableApplicator — deterministic application of extracted variable items.

Application function A for variables: given raw items proposed by the
VariableExtractor, resolve identity by (goal_id, name), decide create vs
update vs no-op, enforce schema validation with rollback, and emit log
entries with before/after snapshots.

Identity rules:
- variable_id is assigned here (V1, V2, ...), never by the LLM.
- A name already tracked for the goal is an update to that variable.
- Duplicate names within the same extraction batch are merged into the
  queued create op (the ``created_this_turn`` guard — intra-turn collision fix).
"""

from __future__ import annotations

import logging
from typing import Any

from problem_tracker.core.problem_description import ProblemDescription
from problem_tracker.schemas.variable import (
    VALID_VARIABLE_ROLES,
    Variable,
    validate_variable,
)

__all__ = ["VariableApplicator"]

logger = logging.getLogger(__name__)


def _next_variable_id(pd: ProblemDescription) -> str:
    nums = [int(v.variable_id[1:]) for v in pd.explicit.variables if v.variable_id[1:].isdigit()]
    return f"V{max(nums) + 1}" if nums else "V1"


class VariableApplicator:
    def apply(
        self,
        pd: ProblemDescription,
        raw: list[dict[str, Any]],
        active_goal_id: str,
        turn_id: str,
    ) -> list[dict[str, Any]]:
        """Apply raw extracted items to pd. Returns the operation log entries."""
        existing_by_name = {
            v.name: v for v in pd.explicit.variables if v.goal_id == active_goal_id
        }

        ops: list[dict[str, Any]] = []
        created_this_turn: set = set()  # variable_ids created during this call

        for item in raw:
            if not isinstance(item, dict):
                continue

            name = (item.get("name") or "").strip()
            role = item.get("role", "fact")
            if role not in VALID_VARIABLE_ROLES:
                logger.warning("[VariableApplicator] skipping %r: invalid role %r", name, role)
                ops.append({"operation": "validation_skip", "component": "variable",
                            "name": name, "reason": f"invalid role {role!r}", "turn_id": turn_id})
                continue

            value = item.get("value")
            if role == "unknown":
                value = None
            elif value is not None:
                value = str(value)

            required = bool(item.get("required", False))

            if name in existing_by_name:
                var = existing_by_name[name]

                if var.variable_id in created_this_turn:
                    # Duplicate name within the same LLM response: merge into the
                    # existing create op rather than emitting a contradictory update.
                    if var.value == value and var.role == role and var.required == required:
                        continue  # identical duplicate, skip entirely
                    var.value = value
                    var.role = role
                    var.required = required
                    # Patch the already-queued create_variable op so it reflects the
                    # most complete value without adding a second op for this variable.
                    for op in ops:
                        if op.get("operation") == "create_variable" and op.get("variable_id") == var.variable_id:
                            op["after"] = var.to_dict()
                            break
                    continue

                # Variable existed before this turn — emit a real update.
                if var.value == value and var.role == role and var.required == required:
                    continue  # true no-op

                before = var.to_dict()
                var.value = value
                var.role = role
                var.required = required
                var.last_updated_turn = turn_id

                errors = validate_variable(var)
                if errors:
                    var.value = before["value"]
                    var.role = before["role"]
                    var.required = before["required"]
                    var.last_updated_turn = before["last_updated_turn"]
                    logger.warning("[VariableApplicator] skipping invalid update to %r: %s", name, errors)
                    ops.append({"operation": "validation_skip", "component": "variable",
                                "name": name, "reason": str(errors), "turn_id": turn_id})
                    continue

                ops.append({
                    "operation": "update_variable",
                    "variable_id": var.variable_id,
                    "name": name,
                    "turn_id": turn_id,
                    "before": before,
                    "after": var.to_dict(),
                })
            else:
                if not name:
                    continue

                variable_id = _next_variable_id(pd)
                var = Variable(
                    variable_id=variable_id,
                    goal_id=active_goal_id,
                    name=name,
                    value=value,
                    role=role,
                    required=required,
                    introduced_turn=turn_id,
                    last_updated_turn=turn_id,
                )
                errors = validate_variable(var)
                if errors:
                    logger.warning("[VariableApplicator] skipping invalid variable %r: %s", name, errors)
                    ops.append({"operation": "validation_skip", "component": "variable",
                                "name": name, "reason": str(errors), "turn_id": turn_id})
                    continue

                pd.explicit.variables.append(var)
                existing_by_name[name] = var  # guard against duplicate names in same turn
                created_this_turn.add(variable_id)

                ops.append({
                    "operation": "create_variable",
                    "variable_id": variable_id,
                    "name": name,
                    "goal_id": active_goal_id,
                    "turn_id": turn_id,
                    "after": var.to_dict(),
                })

        return ops
