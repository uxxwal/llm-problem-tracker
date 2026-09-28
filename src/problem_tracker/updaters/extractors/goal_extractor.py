"""updaters/goal_extractor.py

GoalExtractor — the LLM boundary for goal operations.

This is the extraction function E of the formal model: E(S_t, u_t) -> Ω, a set
of proposed goal operations. It is the ONLY place a language model is consulted
about goals. It never mutates state; interpreting and applying the proposed
operations is the GoalApplicator's job.

On any LLM failure (transport error, non-JSON, non-list output) it raises
ExtractionError rather than guessing, so the caller can log the failure and
apply the identity update.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from problem_tracker.core.problem_description import ProblemDescription
from problem_tracker.llm_client import LLMClient
from problem_tracker.prompt_specs import GOAL_SYSTEM_PROMPT, _FEW_SHOTS, _make_user_message
from problem_tracker.updaters.errors import ExtractionError

__all__ = ["GoalExtractor"]

logger = logging.getLogger(__name__)


class GoalExtractor:
    def __init__(self, client: Optional[LLMClient] = None):
        self.client = client or LLMClient()

    def propose(self, pd: ProblemDescription, user_turn: str) -> List[Dict[str, Any]]:
        """Propose goal operations for one user turn.

        Returns the raw operation list from the LLM (unvalidated — shape and
        precondition checks belong to the GoalApplicator).

        Raises ExtractionError if the LLM call fails or returns a non-list.
        """
        messages = self._build_messages(pd, user_turn)
        try:
            operations = self.client.chat_json(messages)
        except Exception as e:
            logger.warning("[GoalExtractor] LLM call failed (%s)", e)
            raise ExtractionError(f"goal extraction LLM call failed: {e}") from e
        if not isinstance(operations, list):
            logger.warning("[GoalExtractor] non-list LLM response: %r", type(operations).__name__)
            raise ExtractionError(
                f"goal extraction returned {type(operations).__name__}, expected list"
            )
        return operations

    # ------------------------------------------------------------------
    # Prompt assembly
    # ------------------------------------------------------------------

    def _build_messages(self, pd: ProblemDescription, user_turn: str) -> List[Dict[str, str]]:
        messages = [{"role": "system", "content": GOAL_SYSTEM_PROMPT}]
        for state_json, turn, output in _FEW_SHOTS:
            messages.append({"role": "user", "content": _make_user_message(state_json, turn)})
            messages.append({"role": "assistant", "content": output})
        messages.append(
            {"role": "user", "content": _make_user_message(self._format_goals(pd), user_turn)}
        )
        return messages

    def _format_goals(self, pd: ProblemDescription) -> str:
        if not pd.explicit.goals:
            return "[]"
        return json.dumps(
            [
                {"goal_id": g.goal_id, "status": g.status, "goal_text": g.goal_text}
                for g in pd.explicit.goals
            ],
            indent=2,
        )
