"""updaters/explicit_constraint_updater.py

ExplicitConstraintUpdater — per-entity pipeline for constraints, composing:

    ConstraintExtractor  (LLM boundary: proposes raw items, may fail)
    ConstraintApplicator (deterministic: identity resolution, validation, apply)

Mirrors the formal update U_C(S_t, u_t) = A(S_t, E(S_t, u_t)) scoped to the
goal in focus. Extraction failures are recorded as log entries rather than
raised — this method never raises.

All log entries use the key ``turn_id`` (a string such as ``"t1"``).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from problem_tracker.core.problem_description import ProblemDescription
from problem_tracker.llm_client import LLMClient
from problem_tracker.updaters.errors import ExtractionError
from problem_tracker.updaters.extractors.constraint_extractor import ConstraintExtractor
from problem_tracker.updaters.applicators.constraint_applicator import ConstraintApplicator

__all__ = ["ExplicitConstraintUpdater"]

logger = logging.getLogger(__name__)


class ExplicitConstraintUpdater:
    def __init__(self, client: Optional[LLMClient] = None):
        self.extractor = ConstraintExtractor(client=client)
        self.applicator = ConstraintApplicator()

    def update(
        self,
        pd: ProblemDescription,
        user_turn: str,
        active_goal_id: str,
        turn_id: str,
    ) -> Tuple[ProblemDescription, List[Dict[str, Any]]]:
        existing = [c for c in pd.explicit.constraints if c.goal_id == active_goal_id]
        goal = pd.get_goal(active_goal_id)
        goal_text = goal.goal_text if goal else ""

        # E: extraction (LLM boundary)
        try:
            raw = self.extractor.extract(user_turn, active_goal_id, existing, goal_text=goal_text)
        except ExtractionError:
            return pd, [{"operation": "extraction_failed", "component": "constraint", "turn_id": turn_id}]

        # A: deterministic application
        ops = self.applicator.apply(pd, raw, active_goal_id, turn_id)
        return pd, ops
