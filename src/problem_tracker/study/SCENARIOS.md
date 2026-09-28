# Scenario design

Working notes and drafts. **The wording is Ujjwal's to finalise** — read each as
a participant would and rewrite in your own voice before freezing.

## Design principles (from the literature)

1. **No single correct answer.** The standard definition of a study scenario in
   the Wizard-of-Oz literature: a task whose solution requires the system but
   which has no one right answer, and which places constraints on both parties.
   A scenario with a lookup-able answer tests retrieval, not collaboration.
2. **Describe the SITUATION, never the SENTENCE.** Participants echo the wording
   they are given (lexical entrainment / alignment, well documented in dialogue
   research). If the brief says "you must not spend more than £250", people type
   that phrase and extraction becomes trivial — you would be measuring how well
   the pipeline parses your own phrasing. "Money's tight; £250 each is really all
   you have" yields twenty different utterances instead.
3. **Never use the schema's vocabulary.** No "goal", "constraint", "variable",
   "requirement", "preference", "must", "limit". Those words prime exactly the
   structures being extracted.
4. **Stage the information.** Give an opening line plus facts held in reserve.
   If everything is stated upfront the participant dumps it in turn one, nothing
   evolves across turns, and both arms become equivalent — which nulls RQ3.
5. **Build in one change of mind.** Revision is the phenomenon under study; if
   nobody revises anything, the transition record has nothing to show.
6. **Comparable, not equal.** Both scenarios must contain the same KINDS of
   element (an objective, several facts, one firm limit, one soft preference,
   one revision). Difficulty need not match — assignment is counterbalanced, so
   a scenario main effect cancels.
7. **Planning tasks** are the standard domain for conversational-agent studies
   (travel, gifts, recipes, learning plans), realistic and resolvable in a few
   turns without specialist knowledge.

## What each scenario must elicit (comparability check)

| Element | S1 | S2 |
|---|---|---|
| Objective | plan a weekend away | arrange a meal out |
| Facts (→ variables) | who with, when, from where | how many people, city, occasion |
| Firm limit (→ hard constraint) | money per person | money per head |
| Practical requirement (→ hard) | no flying | someone doesn't eat meat |
| Soft preference (→ soft constraint) | somewhere walkable | somewhere you can talk |
| Revision | one thing changes | one thing changes |

---

## S1 — draft

**The situation**

> A friend of yours is moving abroad next month, and the two of you want to get
> away for a long weekend before they go. Nothing is booked and nothing is
> decided.
>
> **Open with just that** — don't explain everything at once.

**Things that are true, which you can mention as they come up**

> - Neither of you wants to fly. Trains or driving are fine.
> - Money is tight. £250 each is genuinely all you have for the whole thing.
> - You'd both rather be somewhere you can walk around all day than somewhere
>   you need to drive between places.
> - You're free the second weekend of next month.

**Partway through**, change your mind about one of the things you've already
told the assistant. Anything — it's your trip.

**You're finished when** you'd be willing to actually go on the trip you've
ended up with.

---

## S2 — draft

**The situation**

> A friend has just got a new job and you're organising a meal out in Edinburgh
> to celebrate. There will be eight of you.
>
> **Open with just that** — don't explain everything at once.

**Things that are true, which you can mention as they come up**

> - One of the group doesn't eat meat.
> - Nobody can really afford more than £25 a head.
> - It's the sort of group that wants to sit and talk, so somewhere deafening
>   would be a bad choice.
> - You're thinking of a Thursday evening.

**Partway through**, change your mind about one of the things you've already
told the assistant. Anything — it's your evening.

**You're finished when** you'd be happy to send the details to the group.

---

## Before freezing — checklist

- [ ] Read each aloud. Does it sound like a situation, or like a specification?
- [ ] Search both for: goal, constraint, variable, requirement, preference,
      limit, must, budget. Rewrite any hit.
- [ ] Would two different people phrase the money point differently? If not, the
      wording is too prescriptive.
- [ ] Can it plausibly be worked through in 7 messages?
- [ ] Does it avoid anything needing real-world lookup (opening times, prices,
      availability)? The assistant has no tools and will otherwise invent facts.
- [ ] Pilot both on someone who is not you.

## Note on S2 and hallucination

The pilot showed the assistant naming real Edinburgh venues and quoting prices
it cannot know. That is a property of the task (choosing a real place), not of
either arm, so it affects both equally — but expect participants to mention it,
and say so in the limitations. If it proves too distracting, S2 can be softened
to "work out what kind of place and what to look for" rather than "pick a
venue".
