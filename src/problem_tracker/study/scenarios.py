"""study/scenarios.py

Scenarios and counterbalanced condition assignment.

Two scenarios, comparable rather than calibrated-equal: both are multi-element
problems, resolvable within the turn budget, needing no specialist knowledge,
and both contain the same *kinds* of thing to track (an objective, several
attributes, a hard limit, a soft preference, and a mid-conversation change of
mind). Exact difficulty need not match, because assignment is BALANCED across
participants: any main effect of scenario difficulty falls on both arms equally
and cancels in the paired comparison.

They are deliberately chosen to stress DIFFERENT things, so that a result is
interpretable rather than an average over two unlike problems:

  S1 (research to a deadline) — ONE objective, progressively specified. This is
     the class the schema was designed for and the class it is structurally
     stable on. Its known weakness is relational: a later qualification tends to
     OVERWRITE the rule it qualifies rather than narrowing it (probe `research`,
     t6). So S1 stresses rule/exception handling.

  S2 (fitting a day together) — several parallel items with ordering relations
     between them. The schema has no notion of an item and no relations, so this
     stresses the structural boundary (probe `scheduling`).

THE FINAL TURN IS A SYNTHESIS TURN, and it is load-bearing. A pilot showed the
two arms producing near-identical replies: at seven turns every question was
answerable from the immediately preceding turn, so the maintained state had no
opportunity either to help or to mislead — the transcript was never a
constraint. The representation can only matter where the assistant must hold
EVERYTHING at once. Both briefs therefore end by asking the participant to
request a consolidated plan, which is the one turn requiring simultaneous
recall of every constraint established. It also yields a scoreable outcome
independent of the questionnaire: constraint coverage in the final answer,
counted per arm.

BRIEFS DESCRIBE SITUATIONS, NOT SENTENCES. Lexical entrainment: if the brief
supplies phrasings, participants read them aloud and every session converges on
the same utterances, which would make extraction look better than it is and
destroy between-participant variation. Briefs therefore state what to cover, not
how to say it, and neither brief names the specific items used in the probes.

ASSIGNMENT — the schema arm is ALWAYS the second conversation.

    group = (n - 1) % 2
    0: S1 with baseline, then S2 with problem_description
    1: S2 with baseline, then S1 with problem_description

Scenario and arm remain fully crossed: each scenario receives each arm equally
often across participants, so a scenario main effect still cancels. What is NOT
counterbalanced is ORDER: the maintained representation is always in the second
conversation.

WHY, and what it costs — state both in the methodology.

The reason is measurement of RQ2. The C-block asks the participant to find
errors and omissions in the representation, and it must come after the B-block
comparison, or seeing the representation reveals which arm was which and
invalidates the preference they have just given. With the arm counterbalanced,
half of participants would be recalling a conversation held before an
intervening task, and reporting faults from degraded memory. Fixing the schema
arm second means the reveal always concerns the conversation just finished.

The cost is that ORDER IS NOW CONFOUNDED WITH ARM. Any effect of going second —
practice with the interface, having already thought about the kind of problem,
or fatigue — falls entirely on the problem_description arm and cannot be
separated from it. If that arm wins, "second conversations go better" is a
live alternative explanation and must be reported as such; if it loses, the
order effect cannot be excused. This is a deliberate trade of RQ3's internal
validity for RQ2's measurement quality, not an oversight.

(The four-group design that counterbalanced order as well is preserved below as
_GROUPS_COUNTERBALANCED, so the decision is reversible before collection
starts. It must NOT be changed once data collection has begun.)
"""

from __future__ import annotations

from typing import Dict, List, Tuple

__all__ = ["SCENARIOS", "TURN_BUDGET", "assign", "get_scenario"]

TURN_BUDGET = 7

SCENARIOS: Dict[str, Dict[str, str]] = {
    "S1": {
        "id": "S1",
        "title": "Getting up to speed on a topic",
        "brief": (
            "You need to get up to speed on a topic you don't know well, for a piece of "
            "written work due soon. Pick any topic you would genuinely find useful — for "
            "work, for study, or out of your own interest.\n\n"
            "Work with the assistant towards a concrete plan for what to read and in what "
            "order. Over the conversation, make sure you cover:\n"
            "  • what you already know, and what you need to come away able to explain\n"
            "  • when it is due, and a limit on how much time you can put into it\n"
            "  • what kinds of source you will and won't accept\n"
            "  • at some point, change your mind about one thing you said earlier — "
            "relax a requirement you set, rather than replacing it\n\n"
            "With your LAST message, ask the assistant to pull the whole thing together "
            "into one plan that takes account of everything you have told it.\n\n"
            "There is no right answer. Treat it as your own piece of work and decide as "
            "you go."
        ),
    },
    "S2": {
        "id": "S2",
        "title": "Fitting everything into a day",
        "brief": (
            "Tomorrow is busier than it should be and you want to work out how to fit "
            "everything in. Use your own commitments or invent some, but end up with at "
            "least five things to fit in — including one that cannot be moved, and one "
            "that can only happen after something else.\n\n"
            "Work with the assistant towards a workable plan for the day. Over the "
            "conversation, make sure you cover:\n"
            "  • when your day starts, and the limit on how late it can run\n"
            "  • which things are fixed and which you could shift\n"
            "  • at some point, change your mind about one thing you said earlier — "
            "move something you had already placed\n\n"
            "With your LAST message, ask the assistant to pull the whole thing together "
            "into one plan for the day that takes account of everything you have told it.\n\n"
            "There is no right answer. Treat it as your own day and decide as you go."
        ),
    },
}

# (scenario_id, arm) pairs, in session order.
# LIVE DESIGN: schema arm always second; scenario alternates.
_GROUPS: List[Tuple[Tuple[str, str], Tuple[str, str]]] = [
    (("S1", "baseline"), ("S2", "problem_description")),
    (("S2", "baseline"), ("S1", "problem_description")),
]

# Kept for the record: the four-group design that also counterbalanced order.
# Swapping _GROUPS to this restores order-counterbalancing at the cost of half
# the participants doing the C-block from memory of an earlier conversation.
_GROUPS_COUNTERBALANCED: List[Tuple[Tuple[str, str], Tuple[str, str]]] = [
    (("S1", "baseline"),             ("S2", "problem_description")),
    (("S1", "problem_description"),  ("S2", "baseline")),
    (("S2", "baseline"),             ("S1", "problem_description")),
    (("S2", "problem_description"),  ("S1", "baseline")),
]


def assign(participant_number: int) -> List[Dict[str, str]]:
    """Return the two sessions for a participant, in the order they run them.

    Each session dict has: session (1 or 2), scenario, arm, label.
    ``label`` is what the participant sees — deliberately neutral and
    order-based, never revealing which condition it is.
    """
    group = (participant_number - 1) % len(_GROUPS)
    return [
        {
            "session": str(i + 1),
            "scenario": scenario_id,
            "arm": arm,
            "label": f"Assistant {i + 1}",
        }
        for i, (scenario_id, arm) in enumerate(_GROUPS[group])
    ]


def get_scenario(scenario_id: str) -> Dict[str, str]:
    if scenario_id not in SCENARIOS:
        raise KeyError(f"Unknown scenario {scenario_id!r}; expected one of {sorted(SCENARIOS)}")
    return SCENARIOS[scenario_id]
