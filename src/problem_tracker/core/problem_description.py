from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from problem_tracker.schemas.constraint import Constraint, validate_constraint
from problem_tracker.schemas.goal import (  # goal_schema_v2: immutable goals
    Goal,
    validate_goal,
)
from problem_tracker.schemas.variable import Variable, validate_variable

__all__ = ["ExplicitState", "ProblemDescription"]


@dataclass
class ExplicitState:
    goals: list[Goal] = field(default_factory=list)
    variables: list[Variable] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)


@dataclass
class ProblemDescription:
    explicit: ExplicitState = field(default_factory=ExplicitState)
    implicit: dict[str, Any] = field(default_factory=dict)

    def get_goal(self, goal_id: str) -> Goal | None:
        return next((g for g in self.explicit.goals if g.goal_id == goal_id), None)

    def get_variable(self, variable_id: str) -> Variable | None:
        return next((v for v in self.explicit.variables if v.variable_id == variable_id), None)

    def get_constraint(self, constraint_id: str) -> Constraint | None:
        return next((c for c in self.explicit.constraints if c.constraint_id == constraint_id), None)

    def constraints_for_goal(self, goal_id: str) -> list[Constraint]:
        return [c for c in self.explicit.constraints if c.goal_id == goal_id]

    def active_goals(self) -> list[Goal]:
        return [g for g in self.explicit.goals if g.status == "active"]

    def variables_for_goal(self, goal_id: str) -> list[Variable]:
        return [v for v in self.explicit.variables if v.goal_id == goal_id]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProblemDescription:
        explicit_data = data.get("explicit", {})
        return cls(
            explicit=ExplicitState(
                goals=[Goal.from_dict(g) for g in explicit_data.get("goals", [])],
                variables=[Variable.from_dict(v) for v in explicit_data.get("variables", [])],
                constraints=[Constraint.from_dict(c) for c in explicit_data.get("constraints", [])],
            ),
            implicit=dict(data.get("implicit", {})),
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        goal_ids = {g.goal_id for g in self.explicit.goals}

        seen_goal_ids: set[str] = set()
        for goal in self.explicit.goals:
            if goal.goal_id in seen_goal_ids:
                errors.append(f"duplicate goal_id {goal.goal_id!r}")
            seen_goal_ids.add(goal.goal_id)
            errors.extend(f"goal {goal.goal_id!r}: {e}" for e in validate_goal(goal))

        seen_var_ids: set[str] = set()
        for var in self.explicit.variables:
            if var.variable_id in seen_var_ids:
                errors.append(f"duplicate variable_id {var.variable_id!r}")
            seen_var_ids.add(var.variable_id)
            errors.extend(f"variable {var.variable_id!r}: {e}" for e in validate_variable(var))
            if var.goal_id not in goal_ids:
                errors.append(
                    f"variable {var.variable_id!r}: goal_id {var.goal_id!r} does not reference an existing goal"
                )

        seen_con_ids: set[str] = set()
        for con in self.explicit.constraints:
            if con.constraint_id in seen_con_ids:
                errors.append(f"duplicate constraint_id {con.constraint_id!r}")
            seen_con_ids.add(con.constraint_id)
            errors.extend(f"constraint {con.constraint_id!r}: {e}" for e in validate_constraint(con))
            if con.goal_id not in goal_ids:
                errors.append(
                    f"constraint {con.constraint_id!r}: goal_id {con.goal_id!r} does not reference an existing goal"
                )

        return errors

    def is_valid(self) -> bool:
        return not self.validate()
