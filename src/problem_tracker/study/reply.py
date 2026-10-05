"""study/reply.py

ReplyGenerator — produces the assistant's conversational reply.

This is the component the user study manipulates. Both arms use the same model,
the same system prompt, and the same conversation history; the ONLY difference
is that the ``problem_description`` arm additionally injects the maintained
problem description into the prompt. Any difference in the interaction is
therefore attributable to the representation and nothing else.

Note this is a distinct concern from extraction: the updaters read a turn and
maintain the state; this reads the state (in one arm) and talks to the user.
"""

from __future__ import annotations

import logging
import os

from problem_tracker.core.problem_description import ProblemDescription
from problem_tracker.llm_client import ChatClient, LLMClient

__all__ = ["ARMS", "ReplyGenerator", "render_problem_description"]

logger = logging.getLogger(__name__)

# Internal arm identifiers. Participant-facing labels never use these.
ARMS = ("baseline", "problem_description")

REPLY_SYSTEM_PROMPT = """You are a helpful assistant working with a user on a problem they are trying to solve.

Work with them conversationally, one step at a time:
- Ask AT MOST ONE question per reply, and only when you genuinely need the answer to make progress. Never present a list or checklist of questions.
- Make concrete suggestions based on what you already know, rather than waiting until you know everything.
- Take account of everything the user has told you so far, and do not ask again for something they have already given you.
- Do not restate the whole problem back to them.

LENGTH — this is a conversation, not an article. KEEP EVERY REPLY UNDER 80 WORDS. Do not use headings, bold labels, or numbered sections. Use at most three short bullets, and only when genuinely offering alternatives. Give the one or two things that move the problem forward and stop; a reply that surveys a topic is a worse reply, not a more helpful one.

THE ONE EXCEPTION: when the user explicitly asks you to produce something — a draft, an outline, a full plan, a piece of code, or a summary pulling everything together — write it out properly and at whatever length it needs. When they ask you to pull it all together, account for EVERYTHING they have established over the conversation, not just the most recent turns, and say plainly if two things they asked for cannot both hold.

YOU HAVE NO TOOLS AND NO INTERNET ACCESS. This is ONLY about actions in the outside world: you cannot look anything up, check availability, open a menu, search, book, or verify a current fact, and there is no later moment in which you could — the user replies immediately after you, and nothing happens in between.

It does NOT limit what you can produce in your reply. You can and should write code, drafts, plans, lists, outlines and worked examples in full when asked. Never say you are unable to make or build something you could simply write out; just write it. Therefore:
- NEVER say you will check, look up, confirm, find out, or get back to them, and never say "shortly", "hold on", "one moment", or "I'll update you". A reply that promises future action wastes the user's turn and delivers nothing.
- Answer now with what you already know, and be explicit about its status: say that a detail would need checking, and if useful say how they could check it, rather than offering to do it yourself.
- Where you are drawing on general knowledge that may be out of date or wrong — opening hours, prices, whether somewhere still exists — say so plainly rather than stating it as fact.

The conversation is short, so make each reply count."""

_PD_PREAMBLE = """The following is a structured description of the user's problem, maintained automatically across the conversation so far. Use it as a summary of what the user has established: respect it in your reply, do not contradict a hard constraint, and do not ask again for something already recorded here.

It is derived from the conversation and can be incomplete or mistaken. Where it disagrees with what the user actually said, THE CONVERSATION IS AUTHORITATIVE — follow the user's own words and ignore the entry that conflicts with them."""


def _states_value(description: str, value: str) -> bool:
    """True when ``description`` already contains ``value`` as a run of tokens.

    Same normalisation as the duplicate check in the updater: case, punctuation
    and determiners stripped, so "4pm" matches "must pick up my kid by 4pm".
    """
    from problem_tracker.updaters.explicit_updater import _norm
    d, v = _norm(description).split(), _norm(value).split()
    if not v:
        return False
    return any(d[i:i + len(v)] == v for i in range(len(d) - len(v) + 1))


def render_problem_description(pd: ProblemDescription) -> str:
    """Render the explicit state as compact text for injection into a prompt.

    Only ACTIVE goals and their elements are rendered: abandoned and completed
    goals are historical record and must not steer the current reply.
    """
    active = [g for g in pd.explicit.goals if g.status == "active"]
    if not active:
        return "(no goals established yet)"

    blocks: list[str] = []
    for goal in active:
        lines = [f'GOAL {goal.goal_id}: {goal.goal_text}']

        constraints = [c for c in pd.explicit.constraints if c.goal_id == goal.goal_id]

        variables = [v for v in pd.explicit.variables if v.goal_id == goal.goal_id]
        # A variable whose value is already stated inside one of this goal's
        # constraints is not shown: "must pick up my kid by 4pm" says everything
        # `pickup_time = "4pm"` says, and names its subject too. Roughly a third
        # of a rendered block was the same fact twice.
        #
        # Suppressed at RENDER time, not removed from the state. Deleting these
        # variables during extraction was tried and reverted — a later update
        # then had no slot to land in and overwrote an unrelated variable
        # instead. The state keeps them; the prompt does not have to carry them.
        #
        # A variable sharing a constraint's NAME exactly is also suppressed, and
        # for a sharper reason: the two describe the same dimension, and only one
        # of them gets maintained. `scheduling` t6 moves the mechanic to 2pm,
        # updates the constraint, and leaves the variable reading "morning" — so
        # the block would state the appointment twice, contradicting itself. The
        # constraint is the condition that must hold; the stale twin is noise.
        constraint_names = {c.name for c in constraints}
        variables = [
            v for v in variables
            if v.name not in constraint_names
            and not (v.value and any(_states_value(c.description, str(v.value))
                                     for c in constraints))
        ]
        if variables:
            lines.append("  Known details:")
            for v in variables:
                name = v.name.replace("_", " ")
                if v.role == "unknown":
                    lines.append(f"    - {name}: (not yet known)")
                else:
                    lines.append(f"    - {name}: {v.value}")

        if constraints:
            hard = [c for c in constraints if c.constraint_type == "hard"]
            soft = [c for c in constraints if c.constraint_type == "soft"]

            def _line(c):
                # The NAME carries the subject the description often assumes.
                # Rendering the description alone produced states like
                # "must be scheduled for 2 PM" with no indication of what — an
                # unreadable record, and one participants are asked to validate.
                subject = c.name.replace("_", " ")
                return f"    - {subject}: {c.description}"

            if hard:
                lines.append("  Must hold (hard constraints):")
                lines.extend(_line(c) for c in hard)
            if soft:
                lines.append("  Preferred (soft constraints):")
                lines.extend(_line(c) for c in soft)

        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


class ReplyGenerator:
    def __init__(self, client: ChatClient | None = None):
        # Extraction runs at temperature 0 (one intended answer). Reply
        # generation is conversational, so it is configured separately and
        # LEFT AT THE API DEFAULT unless set — lowering it would reduce noise in
        # the paired comparison but makes replies flatter and more repetitive,
        # which is an ecological-validity cost. Whatever is chosen applies
        # identically to both arms, so it cannot favour either.
        self.client = client or LLMClient(
            temperature=float(os.getenv("LLM_REPLY_TEMPERATURE", "1.0"))
        )

    def reply(
        self,
        history: list[dict[str, str]],
        arm: str,
        pd: ProblemDescription | None = None,
    ) -> str:
        """Generate the assistant's next reply.

        ``history`` is the alternating user/assistant message list, ending with
        the user's latest turn. ``arm`` selects the condition; in the
        ``problem_description`` arm the rendered state is injected as an
        additional system message.
        """
        if arm not in ARMS:
            raise ValueError(f"Unknown arm {arm!r}; expected one of {ARMS}")

        messages: list[dict[str, str]] = [{"role": "system", "content": REPLY_SYSTEM_PROMPT}]

        if arm == "problem_description":
            if pd is None:
                raise ValueError("problem_description arm requires a ProblemDescription")
            messages.append({
                "role": "system",
                "content": f"{_PD_PREAMBLE}\n\n{render_problem_description(pd)}",
            })

        messages.extend(history)

        try:
            return (self.client.chat(messages) or "").strip()
        except Exception:
            logger.exception("[ReplyGenerator] reply call failed")
            # Never crash a participant's session on a transport error.
            return ("Sorry — I had trouble responding just then. "
                    "Could you say that again?")
