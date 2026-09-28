# Known Issues

---

## Variable Quality

### 1. Role inconsistency — same data typed differently across runs

The model sometimes assigns `role=preference` to an explicitly stated fact (e.g. France
as a company location) and `role=fact` to another (e.g. Kyoto as a destination). The
role rules in the extraction prompt are not reliably followed by `llama3.1`. This is a
model-capability issue — expected to improve significantly with a stronger model (GPT-4,
Claude) for user experiments.

### 2. `required` field is heuristic

The extraction prompt instructs the model to set `required=true` when an attribute is
essential to the goal. In practice the model is inconsistent — the same type of attribute
gets `required=true` in one run and `false` in another. Acceptable for now; can be
addressed with a code-level rule (e.g. all `fact` variables auto-set to `required=true`)
if reliability becomes critical.

### 3. `unknown` slot inference can be overly aggressive

The extraction prompt allows `role=unknown` for needed slots whose value is not yet
stated. The model sometimes infers slots that are not actually needed (e.g. `career_goal`
as unknown after "help me write my CV"). These phantom unknowns clutter the variable list
but do not break correctness — they represent things the model thinks are worth asking about.

---

## Variable Fate on Goal Supersede

### (Design Decision) Variables from superseded goals are preserved as historical record

When `supersede_goal` fires, the old goal's variables remain in `pd.explicit.variables`
with their original `goal_id`. The variable updater scopes exclusively to the active goal's
`goal_id`, so these variables are invisible to all future extraction passes — they cannot
be touched or repurposed.

**Decision:** this is intentional. Old-goal variables serve as an audit trail of what was
known at the time, and carrying them over to a new goal would risk contaminating it with
attributes that no longer apply. If a user re-states an attribute for the new goal, the
variable updater creates a fresh variable scoped to the new goal. No LLM re-evaluation of
old variables is performed.

**Implication for the STG:** when replaying the state transition graph, variables from
superseded goals appear in the full `variables` list but are distinguishable by their
`goal_id` pointing to a superseded goal. A viewer/pretty-printer should filter by
`active_goal_id` when showing "current" state.

---

## Goal Quality

### 4. Goal abstraction violated on `refine_goal` and `supersede_goal`

The GOAL ABSTRACTION RULE (location/budget/etc. must not appear in `goal_text`) is
followed more reliably on `create_goal` than on refinements or supersessions. A
few-shot example for refinement was added and helped, but the model still slips
occasionally (e.g. `"Plan cycling trip"` embeds mode of transport, which arguably
belongs as a variable).

### 5. `refine_goal` vs `supersede_goal` boundary is fuzzy

"Actually I want to import products" after "I want to open a company" is ambiguous — the
model sometimes chooses `refine_goal`, sometimes `supersede_goal`. Both are defensible.

A domain-shift heuristic has been added to `GOAL_SYSTEM_PROMPT`: if the task domain
changes entirely (e.g. trip planning → CV writing, renting → buying property), the prompt
now explicitly instructs `supersede_goal`. This reduces but does not eliminate ambiguity.

No fully reliable fix via prompting alone; the residual ambiguity is a known limitation.
A confidence signal or explicit user cue (e.g. "forget X, now I want Y") is the most
robust signal — the few-shots already cover the clear-cut phrasing case. The problematic
cases are implicit pivots (same message, no explicit dismissal of the prior goal).

---

## Infrastructure

### 6. `llama3.1` flakiness

The local model occasionally returns empty responses, non-JSON output, or invented
operation names (e.g. `add_property`). Mitigated by:
- Retry logic in `LLMClient.chat_json()` (up to 3 attempts)
- Robust JSON extraction (`raw_decode` + find-first-bracket fallback)
- Type check after `chat_json` — non-list result falls back to no-op
- `ValueError` from goal validation caught in orchestrator — falls back to `no_goal_change`

Flakiness will likely not be an issue with the UoE API model.

### 7. No persistence across sessions

`ProblemDescription` lives in memory only. Each run starts fresh. No serialisation to
disk, so there is no way to resume a session or compare runs.

### 8. No STG / history log

The `operation_log` returned by `ExplicitUpdater.update()` captures operations per turn
but is not persisted anywhere. A State Transition Graph (turn-by-turn audit trail with
before/after snapshots) has not been implemented.

---

## Resolved

- ~~Two parallel variable updater interfaces~~ — old `propose_operations` path retired;
  extraction-based `update()` path is now the only interface. Variable identity resolved
  by name in code, not by LLM picking IDs.
- ~~Variable repurposing on goal change~~ — fixed by scoping extraction to active goal only.
  Variables from superseded/abandoned goals are invisible to the updater and cannot be touched.
- ~~`models.py` dead code~~ — moved to `_archive/`. All schemas now live in `schemas/`.

---

## Design Decision (2026-07-09): Variable/Constraint Separation

**Decision (pending supervisor sign-off):** a Variable is a point-valued dimension
of the problem (states what IS: destination = "France"); a Constraint is a predicate
over variables/the solution (states what must HOLD: total cost ≤ £3000). Hard
constraints define feasibility; soft constraints define the objective. All
preferences and desires are therefore SOFT CONSTRAINTS — the `preference` variable
role was removed (`VALID_VARIABLE_ROLES = {"fact", "unknown"}`).

`Variable.required` refers to the SLOT (goal unsolvable without this attribute
known), not to how binding the stated value is — bindingness is constraint hardness.

Motivating failure (honeymoon transcript, 2026-07-09): "HAVE to be under 3000"
overwrote variable `budget` with predicate text `'under £3000'` while the same
information also landed as constraint `budget_ceiling` — same fact, two stores,
diverging contents. Also observed: identical information carved differently across
runs (constraint-only vs variable+constraint) — inter-run consistency is a
candidate evaluation metric; transcripts saved.

Implementation: extraction prompts rewritten with complementary few-shots (same
utterances, split outputs), orchestrator logs `overlap_detected` when a same-turn
variable and constraint share a name token. Open (needs supervisor): whether
Constraint gains a `scope: [variable_name]` field formally linking predicates to
the variables they range over.

- ~~No persistence across sessions (#7)~~ — resolved by `core/session.py`: Session
  owns the ProblemDescription, saves/loads JSON under `sessions/`, restores the
  turn counter on load. run_interactive auto-saves on exit/Ctrl-C.
- ~~No STG / history log (#8)~~ — resolved: Session records one STG entry per turn
  (turn-based nodes per the formal model), storing turn_id, timestamp, the raw
  user utterance, the full operation log INCLUDING failure events, and a decoupled
  post-turn state snapshot. `tools/view_stg.py` renders a per-turn diff view.

---

## Design Decision (30 Jul): Immutable Goals (goal_schema_v2)

Goals are immutable individuators of a solution space: goal_text never changes
after creation; the only mutation is a lifecycle status transition
(active -> completed | abandoned). refine_goal and supersede_goal are REMOVED.
Detail changes are variable/constraint updates ("make it a cycling trip" ->
travel_mode=cycling); objective replacement is abandon_goal + create_goal in
one turn. "superseded" status removed (replacement is visible as abandon+create
in the same turn's log). Version history removed; v1 snapshots remain loadable
(from_dict adopts the active version's text, maps superseded -> abandoned).

Concurrent goals are first-class: a turn's focal goals are every goal created
that turn, else ALL active goals — content-based routing is delegated to
extraction, which now receives each goal's text in the prompt and is instructed
to return [] for goals the turn does not concern. (Recency-based routing was
tried and misassigned detail turns under concurrency.)

Known risk (logged for evaluation): without refine as a shock absorber, a
model that misreads a detail change as a pivot strands the old goal's
variables/constraints. Mitigations: prompt biases strongly to no_goal_change,
duplicate-create guard, all events measurable from the STG.
