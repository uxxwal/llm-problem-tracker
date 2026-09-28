"""updaters/extractors/constraint_extractor.py

ConstraintExtractor — the LLM boundary for constraint extraction.

Extraction function E for constraints: given a user turn and the goal in
focus, propose raw constraint items (name/description/constraint_type).
Never mutates state; identity resolution and application belong to the
ConstraintApplicator.

Raises ExtractionError on LLM failure so the caller can log it and apply the
identity update.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from problem_tracker.llm_client import LLMClient
from problem_tracker.schemas.constraint import Constraint
from problem_tracker.prompt_specs import build_constraint_extraction_messages
from problem_tracker.updaters.errors import ExtractionError

__all__ = ["ConstraintExtractor"]

logger = logging.getLogger(__name__)


class ConstraintExtractor:
    def __init__(self, client: Optional[LLMClient] = None):
        self.client = client or LLMClient()

    def extract(
        self,
        user_turn: str,
        active_goal_id: str,
        existing: Optional[List[Constraint]] = None,
        goal_text: str = "",
    ) -> List[Dict[str, Any]]:
        """Extract raw constraint items from one user turn.

        ``existing`` (constraints already tracked for this goal) is passed to
        the prompt so the model reuses established names when the user restates
        or tightens a constraint. ``goal_text`` is included in the goal
        descriptor so the model knows WHICH problem it is extracting for when
        several goals are active concurrently.

        Raises ExtractionError if the LLM call fails or returns a non-list.
        """
        existing_dicts = (
            [
                {
                    "constraint_id": c.constraint_id,
                    "name": c.name,
                    "description": c.description,
                    "constraint_type": c.constraint_type,
                }
                for c in existing
            ]
            if existing
            else None
        )
        descriptor = f'{active_goal_id} ("{goal_text}")' if goal_text else active_goal_id
        messages = build_constraint_extraction_messages(user_turn, descriptor, existing_dicts)
        try:
            raw = self.client.chat_json(messages)
        except Exception as e:
            logger.warning("[ConstraintExtractor] LLM call failed (%s)", e)
            raise ExtractionError(f"constraint extraction LLM call failed: {e}") from e
        if not isinstance(raw, list):
            logger.warning("[ConstraintExtractor] non-list LLM response: %r", type(raw).__name__)
            raise ExtractionError(
                f"constraint extraction returned {type(raw).__name__}, expected list"
            )
        return raw
