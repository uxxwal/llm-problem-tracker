"""updaters/extractors/variable_extractor.py

VariableExtractor — the LLM boundary for variable extraction.

Extraction function E for variables: given a user turn and the goal in focus,
propose raw variable items (name/value/role/required). Never mutates state;
identity resolution and application belong to the VariableApplicator.

Raises ExtractionError on LLM failure so the caller can log it and apply the
identity update.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from problem_tracker.llm_client import LLMClient
from problem_tracker.schemas.variable import Variable
from problem_tracker.prompt_specs import build_variable_extraction_messages
from problem_tracker.updaters.errors import ExtractionError

__all__ = ["VariableExtractor"]

logger = logging.getLogger(__name__)


class VariableExtractor:
    def __init__(self, client: Optional[LLMClient] = None):
        self.client = client or LLMClient()

    def extract(
        self,
        user_turn: str,
        active_goal_id: str,
        existing: Optional[List[Variable]] = None,
        goal_text: str = "",
    ) -> List[Dict[str, Any]]:
        """Extract raw variable items from one user turn.

        ``existing`` (variables already tracked for this goal) is passed to the
        prompt so the model reuses established names on updates. ``goal_text``
        is included in the goal descriptor so the model knows WHICH problem it
        is extracting for when several goals are active concurrently.

        Raises ExtractionError if the LLM call fails or returns a non-list.
        """
        descriptor = f'{active_goal_id} ("{goal_text}")' if goal_text else active_goal_id
        existing_dicts = (
            [{"variable_id": v.variable_id, "name": v.name, "value": v.value, "role": v.role}
             for v in existing]
            if existing else None
        )
        messages = build_variable_extraction_messages(user_turn, descriptor, existing_dicts)
        try:
            raw = self.client.chat_json(messages)
        except Exception as e:
            logger.warning("[VariableExtractor] LLM call failed (%s)", e)
            raise ExtractionError(f"variable extraction LLM call failed: {e}") from e
        if not isinstance(raw, list):
            logger.warning("[VariableExtractor] non-list LLM response: %r", type(raw).__name__)
            raise ExtractionError(
                f"variable extraction returned {type(raw).__name__}, expected list"
            )
        return raw
