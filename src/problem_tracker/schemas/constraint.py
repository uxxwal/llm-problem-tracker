"""schemas/constraint.py

Authoritative representation of ``constraint_schema_v1`` for the dissertation
project. Defines the ``Constraint`` dataclass, the allowed type enumeration,
and helpers for validation and JSON serialization.

A Constraint is a condition that must (hard) or should (soft) hold over the
solution to a goal. It is distinct from a Variable: a Variable is an attribute
with a value (destination = "Kyoto"); a Constraint is a condition on the
solution space ("must arrive before noon", "budget cannot exceed £500").

Identity is the immutable ``constraint_id``. The ``name`` is also immutable
once created; only ``description`` and ``constraint_type`` may be updated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, List

__all__ = [
    "CONSTRAINT_SCHEMA_VERSION",
    "VALID_CONSTRAINT_TYPES",
    "Constraint",
    "validate_constraint",
]

CONSTRAINT_SCHEMA_VERSION = "constraint_schema_v1"

# A hard constraint must be satisfied for a solution to be valid.
# A soft constraint should be satisfied but may be traded off.
VALID_CONSTRAINT_TYPES = {"hard", "soft"}

_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass
class Constraint:
    """A tracked constraint conforming to ``constraint_schema_v1``.

    Fields
    ------
    constraint_id : str
        Immutable system-assigned identifier (C1, C2, ...).
    goal_id : str
        The goal this constraint is scoped to.
    name : str
        Immutable snake_case label for identity resolution across turns
        (e.g. ``diet_restriction``, ``budget_ceiling``, ``arrival_deadline``).
    description : str
        Natural-language statement of the constraint
        (e.g. ``"must be vegetarian"``, ``"budget cannot exceed £500"``).
    constraint_type : str
        ``"hard"`` if the constraint must be satisfied for the solution to be
        valid; ``"soft"`` if it should be satisfied but may be traded off.
    introduced_turn : str
        Turn ID when this constraint was first stated.
    last_updated_turn : str
        Turn ID of the most recent update to description or type.
    """

    constraint_id: str
    goal_id: str
    name: str
    description: str
    constraint_type: str = "hard"
    introduced_turn: str = ""
    last_updated_turn: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Constraint":
        return cls(
            constraint_id=data["constraint_id"],
            goal_id=data["goal_id"],
            name=data["name"],
            description=data["description"],
            constraint_type=data.get("constraint_type", "hard"),
            introduced_turn=data.get("introduced_turn", ""),
            last_updated_turn=data.get("last_updated_turn", ""),
        )


def validate_constraint(constraint: Constraint) -> List[str]:
    """Validate a Constraint against constraint_schema_v1.

    Returns a list of human-readable error strings. Empty list means valid.
    """
    errors: List[str] = []

    if not constraint.constraint_id:
        errors.append("constraint_id is required and must be non-empty")

    if not constraint.goal_id:
        errors.append("goal_id is required and must be non-empty")

    if not constraint.name:
        errors.append("name is required and must be non-empty")
    elif not _SNAKE_CASE_RE.match(constraint.name):
        errors.append(f"name {constraint.name!r} must be snake_case (no spaces)")

    if not constraint.description:
        errors.append("description is required and must be non-empty")

    if constraint.constraint_type not in VALID_CONSTRAINT_TYPES:
        errors.append(
            f"constraint_type {constraint.constraint_type!r} is not one of "
            f"{sorted(VALID_CONSTRAINT_TYPES)}"
        )

    return errors
