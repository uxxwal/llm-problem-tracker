"""study/probes.py — adversarial probe conversations for stress-testing extraction.

Each probe targets specific failure modes, several of which have already been
observed in piloting. They are deliberately harder than the study scenarios:
the point is to find where the prompts break, not to look good.

Run one:      python tools/replay.py --probe cascade
Run all:      python tools/replay.py --probe all
Consistency:  python tools/replay.py --probe cascade --runs 3

WHAT TO LOOK FOR is listed with each probe. Read the final state against the
conversation and ask: could someone reconstruct the problem from this alone?
"""

PROBES = {

    # ------------------------------------------------------------------
    "cascade": {
        "why": "Revisions that depend on earlier revisions. Checks that updates "
               "land on the SAME element rather than creating duplicates, and "
               "that the final state reflects the last word, not the first.",
        "watch": [
            "guest_count updated three times, never duplicated",
            "the final budget reflects £600 total, not £400 or £900",
            "no constraint description narrating a change ('increased to')",
        ],
        "turns": [
            "I need to organise a leaving do for someone at work",
            "there'll be about 20 of us, budget is 400 quid",
            "actually make that 30 people",
            "hmm if it's 30 we'll need more money, say 600 total",
            "and it has to be somewhere that does food, not just drinks",
            "wait, 30 was optimistic. 25 realistically",
            "keep the 600 though, we can spend a bit more each",
        ],
    },

    # ------------------------------------------------------------------
    "no_money": {
        "why": "Deliberately contains NO budget or price of any kind. Every "
               "pilot so far has been money-dominated, so constraint handling "
               "has only really been tested on one dimension. Here the "
               "constraints are physical limits, time and compatibility.",
        "watch": [
            "constraints extracted at all, given nothing is priced",
            "the shoulder injury must be HARD, the 'prefer mornings' SOFT",
            "does the retraction at turn 6 update the existing element or add a new one?",
        ],
        "turns": [
            "I want to get back into swimming after a long time off",
            "my left shoulder is dodgy so anything overhead is out",
            "I can realistically manage three sessions a week",
            "I'd prefer mornings but I can be flexible",
            "the pool near me only has lanes before 9am and after 6",
            "actually the shoulder is fine for backstroke, just not front crawl",
            "I want to be able to do 40 lengths without stopping by Christmas",
        ],
    },

    # ------------------------------------------------------------------
    "concurrent": {
        "why": "Three genuinely separate objectives in one conversation, "
               "interleaved. Checks that goals stay concurrent (no spurious "
               "abandon) and that details route to the RIGHT goal.",
        "watch": [
            "ZERO abandon_goal — whatever the goal count, nothing should be dismissed",
            ("how many goals? the subsumption tie-break says visa/flat/furniture are "
            "COMPONENTS of 'plan the move', so one goal is now the intended answer — "
            "but check what is LOST by collapsing them (grouping, per-component scoping)"),
            ("if they do collapse: are the visa deadline and the €900 rent still "
            "distinguishable in one flat constraint list?"),
            "'the cheaper option' at the end — where does it land?",
        ],
        "turns": [
            "I'm moving to Berlin in October, help me plan it",
            "first thing is I need to sort out a visa",
            "also I need to find somewhere to live before I get there",
            "and I need to work out what to do with my furniture",
            "the visa application has to be in six weeks before I travel",
            "for the flat, max 900 euros a month and it needs to allow pets",
            ("shipping the furniture is either 800 quid or I sell it all, "
             "I'm leaning towards the cheaper option"),
        ],
    },

    # ------------------------------------------------------------------
    "relative": {
        "why": "Values expressed relative to earlier ones, and unit changes. "
               "Checks whether the system resolves references or stores them "
               "uninterpretably.",
        "watch": [
            "does 'double that' resolve to a number, or get stored as text?",
            "is the final word count 2000 or still 1000?",
            "'about a third of it' — resolved, or meaningless in isolation?",
        ],
        "turns": [
            "I'm writing a blog post about sustainable fashion",
            "aiming for around 1000 words",
            "actually let's double that",
            "about a third of it should be case studies",
            "deadline is a week on Friday",
            "bring the deadline forward by two days",
            "and keep the reading level fairly accessible, not academic",
        ],
    },

    # ------------------------------------------------------------------
    "addition_vs_pivot": {
        "why": "The failure observed in pilot: additive language read as "
               "replacement, abandoning a goal and detaching everything under "
               "it. Deliberately set in a domain unlike any few-shot. Every "
               "turn is additive or a detail change; NOTHING should be "
               "abandoned.",
        "watch": [
            "ZERO abandon_goal operations",
            "no elements_detached events",
            "'rather than' at turn 5 must not trigger a pivot",
        ],
        "turns": [
            "I've taken on an allotment and I don't know where to start",
            "I want to grow vegetables I'd actually cook with",
            "I'd like some fruit as well, a couple of trees maybe",
            "half of it is in shade most of the afternoon",
            "rather than digging the whole thing over, no-dig beds",
            "and somewhere to sit at the far end",
            "I can get down there twice a week at most",
        ],
    },

    # ------------------------------------------------------------------
    "ambiguous": {
        "why": "Pronouns and vague reference. Checks whether the system "
               "invents structure it cannot justify, or correctly extracts "
               "little. Extracting NOTHING is often the right answer here.",
        "watch": [
            "does 'make that one bigger' create a phantom element?",
            "are unresolvable references left out, or guessed at?",
            "role=unknown used appropriately, or phantom unknowns invented?",
        ],
        "turns": [
            "I need help redesigning my kitchen",
            "the layout doesn't work, especially near the window",
            "make that bit bigger if you can",
            "the other thing is storage, there's never enough",
            "it should feel lighter than it does now",
            "same budget as before, whatever we said",
            "do the second option but with the first one's colours",
        ],
    },

    # ------------------------------------------------------------------
    "scheduling": {
        "why": "The candidate study scenario, and a direct test of a claim that "
               "has so far only been asserted: that ORDERING relations ('after "
               "the meeting', 'before I leave') are usable when stored as prose "
               "inside a constraint description. The consumer is a language "
               "model, not a solver, so relational text may be perfectly "
               "serviceable — or it may be recorded in a form that cannot "
               "survive the referent moving. Turn 6 moves a referent on purpose.",
        "watch": [
            "is 'after the mechanic' recorded at all, and as what — constraint, variable, or dropped?",
            "does the description keep the relation ('after X') or flatten it to a clock time?",
            "t6 moves the mechanic appointment — does the dependent element still read correctly afterwards?",
            "is the 4pm/5pm squeeze at t5 detectable from the final state, or only from the reply?",
            "do 5 tasks in 7 turns fit, or does the state thin out?",
        ],
        "turns": [
            "tomorrow's going to be busy, help me work out how to fit everything in",
            "I've got two work meetings, one at 11 and one I can move",
            "the car needs to go to the mechanic and they only take it in the morning",
            "I want to get to the gym after the mechanic, and I owe my sister a phone call",
            "I have to pick my kid up at 4 and that isn't moving",
            "actually the mechanic can't do the morning, it'll have to be 2pm",
            "and I've only really got until 5, drop the gym if it doesn't fit",
        ],
    },

    # ------------------------------------------------------------------
    "research": {
        "why": "Constraint dimensions nothing else touches: PROVENANCE and "
               "quality (peer-reviewed, recency, source type) rather than money, "
               "time or physical limits. Also introduces a revision type never "
               "tested — SCOPE NARROWING, where the user shrinks the problem "
               "rather than changing a value.",
        "watch": [
            "are provenance requirements typed hard (peer-reviewed) vs soft (recent)?",
            "does 'actually just the UK' NARROW an existing element, or add a second one?",
            "'not blog posts' is an exclusion — is a negative condition representable at all?",
            "does the turn-6 reversal update the turn-2 requirement, or contradict it silently?",
        ],
        "turns": [
            "I need to get up to speed on how people measure trust in automation",
            "it's for a literature review so peer-reviewed sources only, no blog posts",
            "nothing older than about 2015 ideally",
            "actually just narrow it to healthcare settings",
            "I've got until the end of next week",
            "hmm, older foundational papers are fine after all if they're heavily cited",
            "and I need enough to write about 3000 words",
        ],
    },

    # ------------------------------------------------------------------
    "conditional": {
        "why": "Conditional and hypothetical statements. A condition on a "
               "condition is not a constraint on the solution; checks the "
               "system does not flatten hypotheticals into commitments.",
        "watch": [
            "is 'if it rains' stored as a hard constraint? (it should not be)",
            "does 'we might' become a commitment?",
            "the firm requirement (finish by 4pm) must be hard",
        ],
        "turns": [
            "planning a birthday picnic for my daughter",
            "if the weather is bad we'd need an indoor backup",
            "we might do a bouncy castle but I'm not sure yet",
            "it definitely has to finish by 4pm, she has swimming",
            "assuming it stays dry, somewhere with a bit of shade",
            "10 kids, maybe 12",
            "no nuts, one of them is allergic",
        ],
    },
}


def get(name):
    if name not in PROBES:
        raise KeyError(f"Unknown probe {name!r}. Available: {', '.join(sorted(PROBES))}")
    return PROBES[name]


def names():
    return sorted(PROBES)
