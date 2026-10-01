"""updaters/explicit_goal_updater.py

ExplicitGoalUpdater — per-entity pipeline for goals, composing:

    GoalExtractor  (LLM boundary: proposes operations, may fail)
    GoalApplicator (deterministic: validates and applies, logs before/after)

This composition mirrors the formal update U_G(S_t, u_t) = A(S_t, V(E(S_t, u_t))):
extraction is stochastic and untrusted; validation and application are code.
Failures at every stage (LLM call, validation, apply) are recorded as log
entries so failure rates are measurable — this method never raises.

All log entries use the key ``turn_id`` (a string such as ``"t1"``).
"""

from __future__ import annotations

import logging
from typing import Any

from problem_tracker.core.problem_description import ProblemDescription
from problem_tracker.llm_client import LLMClient
from problem_tracker.updaters.applicators.goal_applicator import GoalApplicator
from problem_tracker.updaters.errors import ExtractionError
from problem_tracker.updaters.extractors.goal_extractor import GoalExtractor

__all__ = ["ExplicitGoalUpdater"]

logger = logging.getLogger(__name__)


class ExplicitGoalUpdater:
    def __init__(self, client: LLMClient | None = None):
        self.extractor = GoalExtractor(client=client)
        self.applicator = GoalApplicator()

    def update(
        self,
        pd: ProblemDescription,
        user_turn: str,
        turn_id: str,
    ) -> tuple[ProblemDescription, list[dict[str, Any]]]:
        """Run the full goal-update pipeline for one user turn.

        Returns (pd, operation_log). Every failure is appended to the log
        rather than being silently dropped or raising to the caller.
        """
        operation_log: list[dict[str, Any]] = []

        # E: extraction (LLM boundary)
        try:
            operations = self.extractor.propose(pd, user_turn)
        except ExtractionError:
            operation_log.append({
                "operation": "extraction_failed",
                "component": "goal",
                "turn_id": turn_id,
            })
            return pd, operation_log

        # V: shape validation against the fixed operation vocabulary
        try:
            self.applicator.validate_operations(operations)
        except ValueError as e:
            logger.warning("[ExplicitGoalUpdater] validation failed (%s), no_goal_change", e)
            operation_log.append({
                "operation": "validation_failed",
                "component": "goal",
                "turn_id": turn_id,
                "reason": str(e),
            })
            return pd, operation_log

        # A: deterministic application, per-operation isolation
        for op in operations:
            try:
                entry = self.applicator.apply_operation(pd, op, turn_id)
                if entry is not None:
                    operation_log.append(entry)
            except ValueError as e:
                logger.warning("[ExplicitGoalUpdater] apply failed (%s)", e)
                operation_log.append({
                    "operation": "apply_failed",
                    "component": "goal",
                    "turn_id": turn_id,
                    "reason": str(e),
                })

        return pd, operation_log
