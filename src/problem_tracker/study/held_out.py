"""study/held_out.py — HELD-OUT test conversations.

These exist for one reason: the nine conversations in `probes.py` were used
during development to find failures and decide prompt and code changes. Reporting
performance on them would be reporting performance on the data the system was
tuned against. They are development history, not a test.

The four conversations below were written after the configuration was frozen
(prompt fingerprint 583048853227, gpt-4o-mini, extraction temperature 0) and have
never been run. They are the test set.

THE RULE, and it is the whole point:

    Run them. Report what happens. Do NOT change a prompt, a guard, a few-shot
    or a threshold in response to anything they show. The moment the system is
    adjusted to improve a result here, this file becomes development data too
    and there is no held-out set left.

If something looks bad, that is a finding for the evaluation chapter and, if
worth fixing, future work.

Two of the four mirror the shape of the study scenarios, so results transfer to
the user study: `held_instrument` is a single objective progressively narrowed
with a requirement relaxed late, and `held_dinner` is a set of parallel items
with ordering between them. The other two probe design questions never tested:
whether an objective replacement is handled cleanly, and whether the schema can
express whose constraint a constraint is.

    python tools/replay.py --probe held_instrument --runs 3 --save
"""

HELD_OUT = {

    # ------------------------------------------------------------------
    "held_instrument": {
        "why": "Single objective, progressively narrowed, with a requirement "
               "RELAXED rather than replaced at t6 — the same shape as study "
               "scenario S1. Tests whether a later qualification narrows the "
               "existing condition or overwrites it, on content never seen.",
        "watch": [
            "at t6, does the practice-time limit get RELAXED or REPLACED? the earlier limit should survive in some form",
            "is 'no sheet music' representable at all, or does the exclusion vanish?",
            "does the goal stay singular across all seven turns?",
            "is the teacher's availability a variable, a constraint, or lost?",
        ],
        "turns": [
            "I want to finally learn the piano properly this year",
            "I can only really practise in the evenings after eight",
            "twenty minutes a day is all I can commit to",
            "I'd rather learn by ear, no sheet music",
            "the only teacher near me is free on Thursdays",
            "actually on weekends I could stretch to an hour if I need to",
            "and I want to be able to play one whole song by Christmas",
        ],
    },

    # ------------------------------------------------------------------
    "held_dinner": {
        "why": "Parallel items with ordering between them, the same shape as "
               "study scenario S2. Deliberately includes ONE ITEM WITH NO "
               "CONDITION attached (the wine) — the case that vanished in "
               "development, where an item has no time and no requirement and "
               "the schema has no slot for a bare task.",
        "watch": [
            "is the wine recorded AT ALL? it has no time and no condition attached",
            "does 'after the bread is out' survive t6 moving the oven time?",
            "how many goals? the meal is one objective with several parts",
            "does the guest count end up a variable, a constraint, or both?",
        ],
        "turns": [
            "I'm cooking for people on Saturday and I need to work out the order",
            "six of us, one doesn't eat fish",
            "the bread has to go in first, it needs the oven at full heat",
            "the pie can only go in after the bread is out, and I still need to buy wine",
            "people arrive at seven and I don't want to be cooking when they do",
            "actually I can't get the oven on until five now",
            "and the pie takes about forty minutes",
        ],
    },

    # ------------------------------------------------------------------
    "held_pivot": {
        "why": "A genuine objective replacement, explicitly dismissed by the "
               "user at t5, after four turns of detail have accumulated under "
               "the original goal. Tests whether abandonment is recognised only "
               "when stated, and what happens to the elements attached to the "
               "goal that ends.",
        "watch": [
            "ZERO abandon_goal before t5 — the first four turns are all detail",
            "at t5 the old goal SHOULD end; is it, and is elements_detached logged?",
            "does anything from the first goal leak into the second?",
            "is the £400 restated under the new goal, or silently inherited, or lost?",
        ],
        "turns": [
            "I need to sort out a birthday present for my brother",
            "he's into cycling, budget about forty quid",
            "it should be something he wouldn't buy himself",
            "nothing that needs a size, I never get those right",
            "forget the present, he's said he wants us to go away for a weekend instead",
            "somewhere we can both get to by train, two nights",
            "keep it under four hundred for the two of us",
        ],
    },

    # ------------------------------------------------------------------
    "held_thirdparty": {
        "why": "Constraints that belong to OTHER PEOPLE, a dimension never "
               "tested. The schema records what must hold, but has no field for "
               "whose requirement it is. Checks whether the owner survives in "
               "the description, disappears, or is invented.",
        "watch": [
            "is it recoverable WHOSE constraint each one is, or do they flatten into anonymous conditions?",
            "at t6 one person's requirement is withdrawn — does the right one get removed?",
            "does 'my sister can't do Tuesdays' become a constraint, a variable, or a goal?",
            "does the system invent an owner for a constraint that has none?",
        ],
        "turns": [
            "I'm trying to find a date for a family meal, four of us",
            "my sister can't do Tuesdays or Wednesdays, she works late",
            "Dad won't travel after dark so it needs to be early",
            "my brother is away until the 20th",
            "somewhere quiet, Dad's hearing isn't great",
            "actually my sister says Wednesdays are fine now",
            "and it has to be a weekday, the weekend is out for everyone",
        ],
    },
}


def get(name):
    if name not in HELD_OUT:
        raise KeyError(f"Unknown held-out probe {name!r}. "
                       f"Available: {', '.join(sorted(HELD_OUT))}")
    return HELD_OUT[name]


def names():
    return sorted(HELD_OUT)
