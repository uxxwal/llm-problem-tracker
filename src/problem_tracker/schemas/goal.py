"""schemas/goal.py

Authoritative representation of ``goal_schema_v2`` for the dissertation
project. Defines the ``Goal`` dataclass, the allowed status enumeration, and
helpers for validation and JSON serialization.

DESIGN (v2 — immutable goals): a goal is an IMMUTABLE individuator of a
solution space. Its text never changes after creation; all evolution of the
problem's content is carried by the variables and constraints scoped to the
goal. The only mutation a goal supports is a lifecycle status transition
(active -> completed | abandoned). A user pivoting to a substantially
different objective is represented as abandoning the old goal and creating a
new one — two operations, visible as such in the transition record.

Multiple goals may be active concurrently.

(v1 had per-goal version histories and refine/supersede operations. These were
removed once the variable/constraint separation left refinement with no
meaningful territory: detail changes are variable updates, condition changes
are constraint updates, and objective replacement is abandon+create.
``from_dict`` remains tolerant of v1 snapshots so recorded sessions stay
loadable.)
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List

__all__ = [
    "GOAL_SCHEMA_VERSION",
    "VALID_GOAL_STATUSES",
    "Goal",
    "validate_goal",
]

GOAL_SCHEMA_VERSION = "goal_schema_v2"

# Allowed values for Goal.status. "superseded" (v1) is gone: replacement is
# recorded as abandon + create, both visible in the same turn's log.
VALID_GOAL_STATUSES = {"active", "completed", "abandoned"}


@dataclass
class Goal:
    """A tracked user goal conforming to ``goal_schema_v2``.

    Identity is the immutable ``goal_id``; ``goal_text`` is also immutable
    once created. Only ``status`` and ``last_updated_turn`` may change.
    """

    goal_id: str
    goal_text: str
    status: str = "active"
    introduced_turn: str = ""
    last_updated_turn: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict for this goal."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Goal":
        """Construct a Goal from a plain dict (e.g. parsed JSON).

        Tolerates v1 dicts (version-history format): the active version's text
        is adopted as the immutable goal_text, and a v1 "superseded" status is
        mapped to "abandoned".
        """
        goal_text = data.get("goal_text", "")
        if not goal_text and "versions" in data:
            active_id = data.get("active_version_id", "")
            for v in data.get("versions", []):
                if v.get("version_id") == active_id:
                    goal_text = v.get("goal_text", "")
                    break
            if not goal_text and data.get("versions"):
                goal_text = data["versions"][-1].get("goal_text", "")

        status = data.get("status", "active")
        if status == "superseded":  # v1 compatibility
            status = "abandoned"

        return cls(
            goal_id=data["goal_id"],
            goal_text=goal_text,
            status=status,
            introduced_turn=data.get("introduced_turn", ""),
            last_updated_turn=data.get("last_updated_turn", ""),
        )


def validate_goal(goal: Goal) -> List[str]:
    """Validate a Goal against goal_schema_v2.

    Returns a list of human-readable error strings. An empty list means the
    goal is valid.
    """
    errors: List[str] = []

    if not goal.goal_id:
        errors.append("goal_id is required and must be non-empty")

    if not goal.goal_text:
        errors.append("goal_text is required and must be non-empty")

    if goal.status not in VALID_GOAL_STATUSES:
        errors.append(
            f"status {goal.status!r} is not one of {sorted(VALID_GOAL_STATUSES)}"
        )

    return errors
