"""schemas/variable.py

Authoritative representation of ``variable_schema_v1`` for the dissertation
project. Defines the ``Variable`` dataclass, the allowed role enumeration,
and helpers for validation and JSON serialization.

The field set and allowed values mirror the existing project models
(``models.Variable``) and the operations implemented in
``updaters/explicit_updater.py`` / ``updaters/explicit_variable_updater.py``.
No fields are invented beyond that schema.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, List

__all__ = [
    "VARIABLE_SCHEMA_VERSION",
    "VALID_VARIABLE_ROLES",
    "Variable",
    "validate_variable",
]

VARIABLE_SCHEMA_VERSION = "variable_schema_v1"

# Allowed values for Variable.role.
# Note: "constraint" and "preference" are intentionally excluded.
# A variable is a point-valued dimension of the problem (it states what IS);
# conditions on the solution are Constraints (schemas/constraint.py), and
# preferences/desires are SOFT Constraints — hard constraints define
# feasibility, soft constraints define the objective. One concept, one home.
VALID_VARIABLE_ROLES = {"fact", "unknown"}

# Variable names are immutable snake_case identifiers.
_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass
class Variable:
    """A tracked point-valued attribute conforming to ``variable_schema_v1``.

    A variable's ``value`` is always a concrete point ("Kyoto", "5 days",
    "£3000") or None when role is "unknown" — never a condition ("under
    £3000") or a desire ("splurge"); those belong to Constraints.

    ``required`` refers to the SLOT, not the value: it is True when the goal
    cannot be solved without this attribute being known (e.g. the destination
    of a trip). How binding a stated value is has nothing to do with this
    flag — bindingness is expressed by hard/soft Constraints.

    Identity is the immutable ``variable_id``. The ``name`` is also immutable
    once created; only ``value``, ``role`` and ``required`` may be mutated by
    updates.

    (A ``related`` field existed in v1 to point at a goal VERSION. Goals became
    immutable in goal_schema_v2, so it referenced something that no longer
    exists and was never set to anything but "". Removed; ``from_dict`` ignores
    it in older recorded sessions.)
    """

    variable_id: str
    goal_id: str
    name: str
    value: Any = None
    role: str = "fact"
    required: bool = False
    introduced_turn: str = ""
    last_updated_turn: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict for this variable."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Variable":
        """Construct a Variable from a plain dict (e.g. parsed JSON)."""
        return cls(
            variable_id=data["variable_id"],
            goal_id=data["goal_id"],
            name=data["name"],
            value=data.get("value"),
            role=data.get("role", "fact"),
            required=data.get("required", False),
            introduced_turn=data.get("introduced_turn", ""),
            last_updated_turn=data.get("last_updated_turn", ""),
        )


def validate_variable(variable: Variable) -> List[str]:
    """Validate a Variable against variable_schema_v1.

    Returns a list of human-readable error strings. An empty list means the
    variable is valid.
    """
    errors: List[str] = []

    if not variable.variable_id:
        errors.append("variable_id is required and must be non-empty")

    if not variable.goal_id:
        errors.append("goal_id is required and must be non-empty")

    if not variable.name:
        errors.append("name is required and must be non-empty")
    elif not _SNAKE_CASE_RE.match(variable.name):
        errors.append(f"name {variable.name!r} must be snake_case (no spaces)")

    if variable.role not in VALID_VARIABLE_ROLES:
        errors.append(
            f"role {variable.role!r} is not one of {sorted(VALID_VARIABLE_ROLES)}"
        )

    if not isinstance(variable.required, bool):
        errors.append("required must be a boolean")

    # Cross-field consistency: an "unknown" variable carries no value yet.
    if variable.role == "unknown" and variable.value is not None:
        errors.append("a variable with role 'unknown' must have value=None")

    return errors
