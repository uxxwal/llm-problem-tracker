"""updaters.applicators — deterministic state-transition modules (the application function A).

Applicators validate proposed operations/items and apply them to the
ProblemDescription, emitting log entries with before/after snapshots.
No LLM involvement; IDs are assigned here, never by the model.
"""

from problem_tracker.updaters.applicators.goal_applicator import GoalApplicator
from problem_tracker.updaters.applicators.variable_applicator import VariableApplicator
from problem_tracker.updaters.applicators.constraint_applicator import ConstraintApplicator

__all__ = ["GoalApplicator", "VariableApplicator", "ConstraintApplicator"]
