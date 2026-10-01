import json
from problem_tracker.core.problem_description import ExplicitState


GOAL_SYSTEM_PROMPT = """You are a goal-tracking assistant. Given a list of current goals and a new user turn, output a JSON array of goal operations.

GOALS ARE IMMUTABLE:
A goal's text NEVER changes after creation. All changes to the problem's details (destinations, budgets, modes of travel, dates, preferences, limits) are tracked elsewhere as variables and constraints — never by rewriting the goal. If the user changes a detail of an existing goal, output no_goal_change. If the user replaces their objective with a substantially different one, abandon the old goal and create a new one (two operations in the same array).

GOAL OR COMPONENT?
Before creating a goal, check whether an ACTIVE goal already encompasses it. When the user has stated an overarching objective and then names things that are part of achieving it, those parts are NOT new goals — they are conditions on the existing goal, and the constraint extractor will record them. Create a new goal only when the objective stands on its own: it would still make sense if every existing goal were dropped.
  "help me sort out tomorrow" then "I need to call my sister"   → no_goal_change (the call is part of sorting out tomorrow)
  "help me plan the move" then "I need to book a van"           → no_goal_change (booking the van is part of moving)
  "sort out my visa" then "also help me with my CV"             → create_goal (neither depends on the other)
This is a tie-break, not a hierarchy: the schema holds a flat set of goals and cannot express that one goal is part of another. Recording components as goals loses that they belong together and duplicates shared conditions across them, so components are recorded under the goal they serve.

GOAL ABSTRACTION RULE:
Write goal_text as the minimal meaningful objective. Extract specific attributes (location, budget, cuisine, dates, etc.) separately as variables — do NOT embed them in goal_text.

Good examples:
  "plan a trip to Kyoto"              → goal_text: "Plan trip"           (destination goes to variables)
  "find a Japanese restaurant in Leith" → goal_text: "Find restaurant"   (cuisine/location go to variables)
  "write a cover letter for a data science role" → goal_text: "Write cover letter" (role goes to variables)

OPERATIONS:
  create_goal    — a new objective is introduced (multiple goals may be active at once)
    {"operation": "create_goal", "goal_text": "..."}

  complete_goal  — user explicitly says the goal is done
    {"operation": "complete_goal", "goal_id": "<existing goal_id>"}

  abandon_goal   — user explicitly gives up on or dismisses the goal
    {"operation": "abandon_goal", "goal_id": "<existing goal_id>"}

  no_goal_change — the user's turn changes details, constraints, or preferences but not the objective itself
    {"operation": "no_goal_change"}

RULES:
  - Prefer no_goal_change. Most turns change details, not objectives. "Make it a cycling trip", "let's go in spring instead", "actually 7 days" are ALL no_goal_change — those changes are captured as variables.
  - The words "instead", "actually" and "rather" do NOT by themselves signal a new objective. "Instead of a bar, somewhere casual" changes WHERE the same objective is met, so it is no_goal_change; the venue type is a variable. Only output abandon_goal + create_goal when the thing the user is trying to ACHIEVE has changed, such that the new objective would be described by different goal text. If the goal text you would write is the same as an existing active goal, the answer is no_goal_change.
  - A CONDITIONAL dismissal is not a dismissal. "drop it if it doesn't fit", "we can lose that if we run short", "skip it if it's too expensive" all leave the objective standing: the user has stated a condition under which it could be given up, not given it up. Output no_goal_change; the condition itself is a constraint, recorded elsewhere. Only a statement that the objective IS being dropped, with no condition attached, ends a goal.
  - NEVER output abandon_goal unless the user has EXPLICITLY dismissed that objective — "forget the trip", "never mind the CV", "drop that", "I don't need that any more", "scrap it". Wanting something new is not the same as no longer wanting the old thing. Abandoning a goal detaches everything recorded under it, so when in doubt, do not abandon.
  - Words like "as well", "also", "too", "in addition", and "both" signal an ADDITION rather than a replacement — but an addition is only a new GOAL if it stands on its own (see GOAL OR COMPONENT). If it is part of an active goal, output no_goal_change; if it is independent, output create_goal ALONE, leaving the existing goal active. "Instead of a website I want an app as well, working across both" is create_goal only — the website has not been dismissed.
  - Use abandon_goal + create_goal together only when the user replaces one objective with another AND has dismissed the first: "forget the trip, help me with my CV instead". If the task domain shifts entirely and the user has clearly moved on (trip planning → CV writing), that counts as dismissal.
  - Use create_goal alone when the user introduces an additional objective while existing ones remain active. Concurrent goals are normal.
  - Compound goals are allowed — do not decompose them.
  - Do NOT invent goal_id values for new goals — those are assigned by the system. Only include goal_id when referencing an existing goal (complete_goal, abandon_goal).
  - Output ONLY a valid JSON array. No explanation, no markdown."""


_FEW_SHOTS = [
    (
        "[]",
        "Help me plan a trip to Kyoto.",
        '[{"operation": "create_goal", "goal_text": "Plan trip"}]',
    ),
    (
        '[{"goal_id": "G1", "status": "active", "goal_text": "Find place to celebrate birthday"}]',
        "actually instead of a bar something casual with drinks and good food might be better",
        # "instead" changes a detail (venue type), not the objective — the goal
        # text would be unchanged, so this is no_goal_change.
        '[{"operation": "no_goal_change"}]',
    ),
    (
        '[{"goal_id": "G1", "status": "active", "goal_text": "Plan trip"}]',
        "Forget the trip — I need help writing my CV instead.",
        '[{"operation": "abandon_goal", "goal_id": "G1"}, {"operation": "create_goal", "goal_text": "Write CV"}]',
    ),
    (
        '[{"goal_id": "G1", "status": "active", "goal_text": "Write CV"}]',
        "Actually never mind, I don\'t need help with this anymore.",
        '[{"operation": "abandon_goal", "goal_id": "G1"}]',
    ),
    (
        '[{"goal_id": "G1", "status": "active", "goal_text": "Create interactive website"}]',
        "instead of a website I wanna make an app as well, rewrite the code which operates both",
        # "as well" / "both" = ADDITION. The website was never dismissed, so it
        # must stay active — abandoning it would detach everything recorded
        # under it.
        '[{"operation": "create_goal", "goal_text": "Create app"}]',
    ),
    (
        '[{"goal_id": "G1", "status": "active", "goal_text": "Plan trip"}, {"goal_id": "G2", "status": "active", "goal_text": "Draft packing list"}]',
        "The packing list is sorted, thanks!",
        '[{"operation": "complete_goal", "goal_id": "G2"}]',
    ),
    (
        "[]",
        "I've got a few things to sort this week — the car needs servicing and I have to find someone to cover my Thursday shift",
        # Two distinct objectives in one turn: both created, neither abandoned.
        '[{"operation": "create_goal", "goal_text": "Get car serviced"}, '
        '{"operation": "create_goal", "goal_text": "Find shift cover"}]',
    ),
    (
        '[{"goal_id": "G1", "status": "active", "goal_text": "Run charity bake sale"}]',
        "I still need to find volunteers, and someone has to sort the tables out",
        # COMPONENTS of an active goal, not new objectives: neither would stand
        # on its own if the bake sale were dropped. Recording them as goals would
        # duplicate the sale's shared conditions across all three.
        '[{"operation": "no_goal_change"}]',
    ),
    (
        '[{"goal_id": "G1", "status": "active", "goal_text": "Get car serviced"}, '
        '{"goal_id": "G2", "status": "active", "goal_text": "Find shift cover"}, '
        '{"goal_id": "G3", "status": "active", "goal_text": "Book dentist"}]',
        "the dentist has to be after 5, I can't get away from work before then",
        # Adds a condition to an existing goal. Several goals active, none
        # created or ended — this is a detail turn.
        '[{"operation": "no_goal_change"}]',
    ),
]


def _format_state(state: ExplicitState) -> str:
    if not state.goals:
        return "[]"
    goals = [
        {"goal_id": g.goal_id, "status": g.status, "goal_text": g.goal_text}
        for g in state.goals
    ]
    return json.dumps(goals, indent=2)


def _make_user_message(state_json: str, user_turn: str) -> str:
    return f"Current goals:\n{state_json}\n\nUser turn: \"{user_turn}\""


def build_goal_messages(state: ExplicitState, user_turn: str) -> list:
    messages = [{"role": "system", "content": GOAL_SYSTEM_PROMPT}]
    for state_json, turn, output in _FEW_SHOTS:
        messages.append({"role": "user", "content": _make_user_message(state_json, turn)})
        messages.append({"role": "assistant", "content": output})
    messages.append({"role": "user", "content": _make_user_message(_format_state(state), user_turn)})
    return messages


# ---------------------------------------------------------------------------
# Variable extraction prompt (extraction-only: no existing-state diffing)
# ---------------------------------------------------------------------------

VARIABLE_EXTRACTION_SYSTEM_PROMPT = """You are a variable-extraction assistant. Given a single user turn and the active goal_id, output a JSON array of the variables explicitly stated in that turn.

A variable is a POINT-VALUED attribute of the problem — a named dimension with a concrete value (e.g. destination = "Kyoto", trip_duration = "5 days"). It states what IS.

VARIABLE ROLES:
  fact     — stated information describing the problem (e.g. destination, venue type, location)
  unknown  — a needed information slot whose value is not yet known

WHAT IS NOT A VARIABLE:
Do NOT extract conditions, limits, wishes, or desired properties — those are constraints, handled by a separate extractor. Examples of what to SKIP here:
  "budget cannot exceed £500"     → skip (hard constraint)
  "around £2000, want to splurge" → skip (soft constraint)
  "£100 per person"               → skip (money is ALWAYS a constraint)
  "our budget is £3000"           → skip (money is ALWAYS a constraint)

THE TEST THAT DECIDES IT:
Does the statement describe the USER'S SITUATION, or does it say what the OUTCOME must be like?
  - Situation → variable. "there'll be 25 of us", "I'm moving to Berlin", "it's my daughter's birthday", "half the plot is shaded".
  - Requirement on the outcome → constraint, and NOT ALSO a variable. "1000 words", "finish by 4pm", "three sessions a week", "no nuts", "deadline is Friday", "40 lengths by Christmas".
Numbers, times and dates are constraints whenever they say what the solution must achieve or respect, even though they look like plain values. Extract them here ONLY when they are facts about the user's circumstances that would remain true whatever solution is chosen.
NEVER extract the same information both here and as a constraint.
  "ideally somewhere quiet"       → skip (soft constraint)
  "I prefer a window seat"        → skip (soft constraint)
A variable's value must be a concrete point, never a condition ("under £3000") or a desire ("splurge").

OUTPUT: a JSON array, one object per extracted variable:
  {"name": "...", "value": "...", "role": "fact|unknown", "required": true|false}

RULES:
  - Only extract information the user explicitly stated. Do not infer or assume.
  - IMPORTANT: goal text is deliberately kept minimal ("plan trip", "write cover letter"), so the specifics that were left out of the goal MUST be captured here. If the user says "plan a honeymoon", the goal is "Plan trip" and the occasion "honeymoon" must appear as a variable. Nothing the user stated should be lost between the goal and the variables.
  - name must be snake_case (e.g. "destination", "trip_duration"). Never use spaces.
  - The name must identify the DIMENSION, never the value: "meeting_day", not "meets_on_thursday"; "destination", not "going_to_kyoto". Names are permanent identifiers used to match later updates, so a name containing a value can never be updated when that value changes.
  - A date or time belongs here ONLY when it describes the user's circumstances independently of any solution — the day they work from home, the hour they finish work, the day their shift falls on. When it states what the solution must achieve or respect — a deadline, an appointment time, an opening hour, a cut-off — it IS the condition, it is recorded as a constraint, and it must NOT also appear here. If the time you are about to extract also appears inside a condition, output nothing for it.
  - required is true when the goal cannot be solved without this attribute being known (e.g. destination of a trip, location of a venue); false otherwise. required refers to the slot, NOT to how binding the stated value is.
  - value is a plain string. An unknown variable has value null and role "unknown".
  - If existing variables for this goal are listed, reuse their exact names when the user is updating an existing attribute.
  - ONLY update an existing variable when the turn NAMES ITS SUBJECT. A value mentioned in a turn belongs to whatever the turn is about, not to whichever listed variable happens to be empty or to share its units. If the turn gives a value for something else, or for something not yet tracked, do not write it into an existing variable — leave that variable exactly as it is. An unfilled variable is correct; a variable filled with another thing's value is a false statement about the user's situation, and nothing downstream can detect it.
  - Extract ONLY information relevant to the stated active goal. Several goals may be active at once; if the turn is about a different goal than this one, output [].
  - When several goals are active, a statement usually belongs to ONE of them. Do not restate a general condition under every goal: if the user says something about the project as a whole, attach it to the goal it most directly concerns and output [] for the others.
  - If the turn states no extractable attributes, output [].
  - Output ONLY a valid JSON array. No explanation, no markdown."""


# (user_turn, goal_id, existing_vars, output_json)
# existing_vars is a list of dicts with keys: variable_id, name, value, role
_VARIABLE_EXTRACTION_FEW_SHOTS = [
    (
        "trip to Kyoto, 5 days, mid-range budget",
        "G1",
        [],
        # "mid-range budget" is a soft constraint — not a variable.
        '[{"name": "destination", "value": "Kyoto", "role": "fact", "required": true}, '
        '{"name": "trip_duration", "value": "5 days", "role": "fact", "required": true}]',
    ),
    (
        "actually, let's make it 7 days",
        "G1",
        [{"variable_id": "V1", "name": "destination", "value": "Kyoto", "role": "fact"},
         {"variable_id": "V2", "name": "trip_duration", "value": "5 days", "role": "fact"}],
        '[{"name": "trip_duration", "value": "7 days", "role": "fact", "required": true}]',
    ),
    (
        "actually, I want to make it a cycling trip",
        "G1",
        [{"variable_id": "V1", "name": "destination", "value": "Kyoto", "role": "fact"}],
        # Goals are immutable: a change to how the trip is done is a VARIABLE
        # change, not a goal change. This turn must not be lost.
        '[{"name": "travel_mode", "value": "cycling", "role": "fact", "required": true}]',
    ),
    (
        "sure, sounds good",
        "G1",
        [],
        "[]",
    ),
    (
        "the garage I normally use is in Leith and I'm working from home on Tuesday",
        'G1 ("Get car serviced")',
        [],
        # Situation facts, in a domain with no money in it at all.
        '[{"name": "garage_location", "value": "Leith", "role": "fact", "required": true}, '
        '{"name": "wfh_day", "value": "Tuesday", "role": "fact", "required": false}]',
    ),
    (
        "the dentist has to be after 5, I can't get away from work before then",
        'G1 ("Get car serviced")',
        [{"variable_id": "V1", "name": "garage_location", "value": "Leith", "role": "fact"},
         {"variable_id": "V2", "name": "wfh_day", "value": "Tuesday", "role": "fact"}],
        # Several goals are active and this turn is about a DIFFERENT one.
        # Output nothing rather than attaching it here.
        "[]",
    ),
    (
        "the dentist has to be after 5, I can't get away from work before then",
        'G3 ("Book dentist")',
        [],
        # Same turn, the goal it actually concerns. "after 5" is a condition on
        # the appointment (a constraint, not extracted here); the fact that the
        # user is at work until 5 is a situation fact.
        '[{"name": "available_from", "value": "5pm", "role": "fact", "required": true}]',
    ),
    (
        "there will be 12 of us and it needs to be open late on Thursday",
        "G2",
        [],
        # A count is a FACT and belongs here; the opening-hours requirement is a
        # condition on the solution and belongs to constraints, not here.
        '[{"name": "group_size", "value": "12", "role": "fact", "required": true}]',
    ),
    (
        "help me write a cover letter for a data science role",
        "G2",
        [],
        # Same principle outside travel: the goal is "Write cover letter", so the
        # role the letter is for belongs here.
        '[{"name": "target_role", "value": "data science", "role": "fact", "required": true}]',
    ),
]


def _make_variable_extraction_user_message(
    user_turn: str, active_goal: str, existing_vars: list | None
) -> str:
    """``active_goal`` is a descriptor string — either a bare id ("G1") or,
    preferably, id plus objective text ('G1 ("Plan trip")') so the model knows
    WHICH problem it is extracting for when several goals are active."""
    parts = [f"Active goal: {active_goal}"]
    if existing_vars:
        parts.append(
            "Existing variables for this goal:\n"
            + json.dumps(existing_vars, indent=2)
        )
    parts.append(f'User turn: "{user_turn}"')
    return "\n\n".join(parts)


def build_variable_extraction_messages(
    user_turn: str, active_goal: str, existing_vars: list | None
) -> list:
    messages = [{"role": "system", "content": VARIABLE_EXTRACTION_SYSTEM_PROMPT}]
    for turn, goal, ex_vars, output in _VARIABLE_EXTRACTION_FEW_SHOTS:
        messages.append({"role": "user", "content": _make_variable_extraction_user_message(turn, goal, ex_vars or None)})
        messages.append({"role": "assistant", "content": output})
    messages.append({
        "role": "user",
        "content": _make_variable_extraction_user_message(user_turn, active_goal, existing_vars or None),
    })
    return messages


# ---------------------------------------------------------------------------
# Constraint extraction prompt
# ---------------------------------------------------------------------------

CONSTRAINT_EXTRACTION_SYSTEM_PROMPT = """You are a constraint-extraction assistant. Given a single user turn and the active goal_id, output a JSON array of constraints explicitly stated in that turn.

WHAT IS A CONSTRAINT:
A constraint is a condition over the solution — a requirement, restriction, preference, or desire the user expresses. It states what must or should HOLD, as opposed to a variable, which states what IS.
Examples: "must be vegetarian", "budget cannot exceed £500", "around £2000 would be ideal", "I prefer a window seat", "somewhere quiet".

WHAT IS NOT A CONSTRAINT:
Do NOT extract plain facts — point-valued attributes like "destination = Kyoto", "10 days", or "there will be 12 people". Counts, quantities, places and names are facts and belong to variables, handled by a separate extractor. A number is only a constraint when it bounds the solution ("no more than 12 people"), not when it simply states how things are ("there will be 12 people").
Dates and times are the exception, and they are usually constraints: a deadline, an appointment, an opening hour or a cut-off all say what the solution must respect, so extract them here. A date or time is a plain fact only when it describes the user's circumstances whatever solution is chosen ("I work from home on Tuesdays"). The variable extractor is instructed not to record a time that is the condition, so do not leave a deadline to it.
Do NOT extract requests for the assistant to do something ("check if they are open late") — an action is not a condition. If the user's intent is that a condition must hold ("it needs to be open late on Thursday"), extract that condition instead.

ONE CONDITION PER CONSTRAINT:
A single sentence may state more than one condition, and each must be extracted separately with its own type. "around £20 a head, no more than £30" is TWO constraints: a soft target of about £20, and a hard ceiling of £30. Never merge a target and a limit into one constraint — doing so loses the limit.

CONSTRAINT TYPES:
  hard — defines feasibility; a solution that violates it is invalid (e.g. "must be vegetarian", "budget cannot exceed £500", "HAVE to be under £3000")
  soft — defines the objective; should be satisfied but may be traded off (e.g. "ideally non-stop", "around £2000", "prefer a window seat", "want to splurge")
All preferences and wishes are SOFT constraints. Explicit obligation language ("must", "cannot", "have to", "no more than") signals a HARD constraint.
WORD IT THE WAY YOU TYPED IT. A constraint typed soft must be described in preference language — "should", "ideally", "prefer", "would rather" — and never with "must" or "cannot". A soft constraint reading "must have somewhere to sit" is rendered to the reader as "Preferred: must have somewhere to sit", which contradicts itself and destroys the only distinction this output carries. Decide the type from what the user meant, then choose wording that matches it.
A stated limit or budget with NO hedging defaults to HARD: "£30 a head" or "a budget of £3000" is a hard constraint, because an unhedged figure functions as a ceiling. It is soft only when the user hedges it explicitly ("around", "roughly", "ideally", "I'd like to keep it near").

OUTPUT: a JSON array, one object per extracted constraint:
  {"name": "...", "description": "...", "constraint_type": "hard|soft"}

RULES:
  - Only extract constraints the user explicitly stated. Do not infer or assume.
  - name must be snake_case (e.g. "diet_restriction", "budget_ceiling", "arrival_deadline"). Never use spaces.
  - The name must identify the DIMENSION being constrained, never the current value. Write "opening_hours", not "open_late_thursday"; "budget_ceiling", not "under_30_pounds"; "meeting_day", not "meets_on_friday". Names are permanent identifiers used to match later updates, so a name containing a value can never be updated when that value changes.
  - description is a concise, SELF-CONTAINED statement of the condition that must hold (e.g. "must be vegetarian", "must not exceed £100 per person"). It describes the STATE, never the change: write "must not exceed £100 per person", NOT "budget increased to £100". Someone reading the description alone, with no knowledge of the conversation, must be able to tell what a valid solution has to satisfy.
  - NAME THE SUBJECT. The description must say what the condition applies to, not assume it from context. Write "the car must go to the mechanic at 2pm", NOT "must be scheduled for 2 PM". Write "the pickup must happen by 4pm", NOT "must be available before 4 PM". A description that does not name its subject is unusable once there is more than one thing being tracked.
  - RELATIVE AND ORDERING CONDITIONS ARE CONSTRAINTS. "the gym has to be after the mechanic", "call her before I leave", "do the shopping once the meeting is done" all state conditions on the solution and must be extracted. Keep the relation in the description ("must be after the mechanic appointment") rather than converting it to a fixed time — the relation stays true if the other thing moves.
  - When updating an existing constraint, rewrite the description in full as a fresh statement of the new condition. Do not phrase it as an amendment ("raised to", "changed to", "now £100").
  - If existing constraints for this goal are listed, reuse their exact names when the user restates or tightens an existing constraint.
  - REUSE A NAME ONLY WHEN THE NEW CONDITION REPLACES THE OLD ONE. Reusing a name overwrites what was there. Ask whether both conditions could hold at the same time: if they could, they are two separate constraints and the new one needs its own name, even though it concerns the same subject. A later condition about something already constrained is usually an ADDITION, not a correction — only a condition that contradicts the existing one replaces it.
  - Extract ONLY constraints relevant to the stated active goal. Several goals may be active at once; if the turn is about a different goal than this one, output [].
  - When several goals are active, a condition usually belongs to ONE of them. Do not restate a general condition under every goal: attach it to the goal it most directly concerns and output [] for the others. A condition that plausibly touches everything still belongs to the single goal it is about, not to all of them.
  - If the turn states no constraints, output [].
  - Output ONLY a valid JSON array. No explanation, no markdown."""


_CONSTRAINT_FEW_SHOTS = [
    (
        "I need to get there by Friday and the budget absolutely cannot go above £800",
        "G1",
        [],
        '[{"name": "arrival_deadline", "description": "must arrive by Friday", "constraint_type": "hard"}, '
        '{"name": "budget_ceiling", "description": "budget cannot exceed £800", "constraint_type": "hard"}]',
    ),
    (
        "I prefer non-stop flights if possible, but I can do one stop",
        "G1",
        [],
        '[{"name": "flight_stops", "description": "ideally non-stop, one stop acceptable", "constraint_type": "soft"}]',
    ),
    (
        "we're thinking £30 a head",
        "G2",
        [],
        # Unhedged figure — defaults to hard. Contrast with "around £2000" below,
        # which is hedged and therefore soft.
        '[{"name": "budget_per_head", "description": "£30 per head", "constraint_type": "hard"}]',
    ),
    (
        "around 20 pounds per person, not more than 30",
        "G2",
        [],
        # TWO conditions in one sentence: a hedged target (soft) and a limit
        # (hard). Merging them would lose the ceiling.
        '[{"name": "budget_target", "description": "around £20 per person", "constraint_type": "soft"}, '
        '{"name": "budget_ceiling", "description": "no more than £30 per person", "constraint_type": "hard"}]',
    ),
    (
        "there will be 12 of us and it needs to be open late on Thursday",
        "G2",
        [],
        # "12 of us" is a fact (a variable, not extracted here). Only the
        # condition on the venue is a constraint.
        '[{"name": "opening_hours", "description": "must be open late on Thursday", "constraint_type": "hard"}]',
    ),
    (
        "it has to be done before Friday, ideally Tuesday morning",
        'G1 ("Get car serviced")',
        [],
        # A compound statement with NO money in it: a hard deadline and a soft
        # preference, extracted separately with their own types.
        '[{"name": "completion_deadline", "description": "must be completed before Friday", "constraint_type": "hard"}, '
        '{"name": "preferred_slot", "description": "ideally Tuesday morning", "constraint_type": "soft"}]',
    ),
    (
        "actually make that before Thursday",
        'G1 ("Get car serviced")',
        [{"constraint_id": "C1", "name": "completion_deadline",
          "description": "must be completed before Friday", "constraint_type": "hard"}],
        # An update to a non-monetary constraint: description rewritten in full
        # as a fresh statement of the condition, not as an amendment.
        '[{"name": "completion_deadline", "description": "must be completed before Thursday", "constraint_type": "hard"}]',
    ),
    (
        "for the shift cover it needs to be someone trained on the tills",
        'G1 ("Get car serviced")',
        [{"constraint_id": "C1", "name": "completion_deadline",
          "description": "must be completed before Thursday", "constraint_type": "hard"}],
        # Several goals active; this turn concerns a different one.
        "[]",
    ),
    (
        "for the shift cover it needs to be someone trained on the tills",
        'G2 ("Find shift cover")',
        [],
        # The goal it does concern. A non-quantitative hard requirement.
        '[{"name": "cover_qualification", "description": "the person covering must be trained on the tills", "constraint_type": "hard"}]',
    ),
    (
        "I want to get to the gym after the mechanic, and I need to leave by 5",
        'G1 ("Plan the day")',
        [],
        # A RELATIVE condition: the relation is kept in the description rather
        # than converted to a clock time, so it stays true if the mechanic
        # appointment moves. Both descriptions NAME THEIR SUBJECT — "must be
        # after the mechanic" alone would be unreadable once several things are
        # being tracked.
        # NOTE the wording of the soft one: a SOFT constraint described with
        # "must" teaches the model to produce exactly the type/wording mismatch
        # that renders as "Preferred (soft constraints): must ...", muddying the
        # only distinction this block exists to convey.
        '[{"name": "gym_timing", "description": "the gym should come after the mechanic appointment", "constraint_type": "soft"}, '
        '{"name": "day_end", "description": "everything must be finished by 5pm", "constraint_type": "hard"}]',
    ),
    (
        "Oh, and it must be completely vegetarian — no exceptions",
        "G2",
        [{"constraint_id": "C1", "name": "diet_restriction", "description": "vegetarian preferred", "constraint_type": "soft"}],
        '[{"name": "diet_restriction", "description": "must be completely vegetarian, no exceptions", "constraint_type": "hard"}]',
    ),
    (
        "Sure, that sounds good",
        "G1",
        [],
        "[]",
    ),
]


def _make_constraint_extraction_user_message(
    user_turn: str, active_goal: str, existing_constraints: list | None
) -> str:
    """``active_goal`` is a descriptor string, as in variable extraction."""
    parts = [f"Active goal: {active_goal}"]
    if existing_constraints:
        parts.append(
            "Existing constraints for this goal:\n"
            + json.dumps(existing_constraints, indent=2)
        )
    parts.append(f'User turn: "{user_turn}"')
    return "\n\n".join(parts)


def build_constraint_extraction_messages(
    user_turn: str, active_goal: str, existing_constraints: list | None = None
) -> list:
    existing_constraints = existing_constraints or []
    messages = [{"role": "system", "content": CONSTRAINT_EXTRACTION_SYSTEM_PROMPT}]
    for turn, goal, ex_cons, output in _CONSTRAINT_FEW_SHOTS:
        messages.append({
            "role": "user",
            "content": _make_constraint_extraction_user_message(turn, goal, ex_cons or []),
        })
        messages.append({"role": "assistant", "content": output})
    messages.append({
        "role": "user",
        "content": _make_constraint_extraction_user_message(
            user_turn, active_goal, existing_constraints
        ),
    })
    return messages


