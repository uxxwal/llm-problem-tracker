# ProblemDescription Tracker

MSc dissertation project. Converts multi-turn user–LLM conversations into an
explicit, inspectable `ProblemDescription` — a structured representation that
tracks goals, variables and constraints as they evolve across turns.

---

## What It Does

A user talks to an LLM assistant across multiple turns. Instead of the
conversation staying as raw text, this system extracts and maintains a live
structured representation of what the user is trying to achieve and what
defines their problem.

```
Turn 1: "Help me plan a trip to Kyoto."
Turn 2: "I want to stay for 5 days, and it has to be under £3000."
Turn 3: "Forget the trip — I need help writing my CV instead."
```

Produces:

```
Goals:
  G1  "Plan trip"        status=abandoned   (t3)
  G2  "Write CV"         status=active      (t3)

Variables:
  V1  destination = "Kyoto"    role=fact  goal=G1
  V2  duration    = "5 days"   role=fact  goal=G1

Constraints:
  C1  total_budget  "must not exceed £3000"   type=hard  goal=G1
```

---

## Formal Model

Each turn is one application of

```
S_{t+1} = A( S_t , V( E(S_t, u_t) ) )
```

| Symbol | Role | Where |
|---|---|---|
| `E` | **Extraction** — the LLM boundary. Proposes operations from a user turn. | `updaters/extractors/` |
| `V` | **Validation** — shape and schema checks on what came back. | applicator `validate_operations()` |
| `A` | **Application** — deterministic state transition, emits a log `L_t`. | `updaters/applicators/` |

The split matters: `E` is the only non-deterministic component. Everything
after it is pure code, so a bad extraction can be rejected rather than
corrupting state, and every accepted transition is reproducible from the log.

```
┌──────────────────────────────────────────────────────────────┐
│                        Entry Points                          │
│   run_interactive.py    run_batch.py    study/app.py         │
└───────────────────────────┬──────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│              ExplicitUpdater  (per-turn orchestrator)        │
│                 updaters/explicit_updater.py                 │
│                                                              │
│      goals ──► variables ──► constraints ──► invariant check │
│                                                              │
│   each stage:   Extractor (LLM)  ──►  Applicator (pure code) │
└───────────────────────────┬──────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│                     ProblemDescription                       │
│                core/problem_description.py                   │
│                                                              │
│   pd.explicit: ExplicitState                                 │
│   ├── goals:       List[Goal]                                │
│   ├── variables:   List[Variable]                            │
│   └── constraints: List[Constraint]                          │
│                                                              │
│   pd.implicit: Dict   (placeholder — not yet implemented)    │
└───────────────────────────┬──────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│                    Session  /  STG                           │
│                    core/session.py                           │
│   (S_0, (u_1, L_1, S_1), (u_2, L_2, S_2), ...)               │
│   one node per turn — including turns that changed nothing   │
└──────────────────────────────────────────────────────────────┘
```

---

## Data Model

```
ProblemDescription
│
└── explicit: ExplicitState
    │
    ├── goals: List[Goal]                    # goal_schema_v2
    │     goal_id            "G1"            (immutable, assigned by code)
    │     goal_text          "Plan trip"     (IMMUTABLE after creation)
    │     status             active | completed | abandoned
    │     introduced_turn    "t1"
    │     last_updated_turn  "t3"
    │
    ├── variables: List[Variable]            # variable_schema_v1
    │     variable_id        "V1"            (immutable, assigned by code)
    │     goal_id            "G1"
    │     name               "destination"   (immutable once created, snake_case)
    │     value              "Kyoto"
    │     role               fact | unknown
    │     required           bool            (is the SLOT essential?)
    │     introduced_turn / last_updated_turn
    │
    └── constraints: List[Constraint]        # constraint_schema_v1
          constraint_id      "C1"            (immutable, assigned by code)
          goal_id            "G1"
          name               "total_budget"  (immutable once created)
          description        "must not exceed £3000"
          constraint_type    hard | soft
          introduced_turn / last_updated_turn
```

### Variables vs Constraints

This distinction is the central design decision of the current schema:

- A **Variable** is a point-valued dimension of the problem — it states what **is**
  (`destination = "Kyoto"`).
- A **Constraint** is a predicate over the solution — it states what must **hold**
  (`total cost ≤ £3000`).

Hard constraints define feasibility; soft constraints define the objective.
Preferences and desires are therefore **soft constraints**, not variables — which
is why the `preference` variable role was removed. `VALID_VARIABLE_ROLES` is now
`{fact, unknown}`.

`Variable.required` refers to the **slot** (the goal is unsolvable without this
attribute being known), *not* to how binding the stated value is. Bindingness is
constraint hardness.

### Goals are immutable

Goal text never changes after creation. The only mutation is a lifecycle status
transition. Replacing an objective is expressed as `abandon_goal` + `create_goal`
in the same turn, and multiple goals may be active concurrently.

This replaced an earlier versioned design (`refine_goal` / `supersede_goal` with
per-goal `GoalVersion` chains). Versioning made the ambiguity worse, not better:
the model could not reliably distinguish "refine" from "supersede" on implicit
pivots, and every misclassification silently rewrote history. Abandon+create is
lossless — the abandoned goal and its variables stay in the record.

---

## Operations

| Goal | When |
|---|---|
| `create_goal` | A goal is introduced |
| `complete_goal` | User says the goal is done |
| `abandon_goal` | User gives up on, or replaces, the goal |
| `no_goal_change` | Turn adds detail but no goal-level change |

| Variable | When |
|---|---|
| `create_variable` | New attribute mentioned for the first time |
| `update_variable` | Existing attribute gets a new value or role |

| Constraint | When |
|---|---|
| `create_constraint` | New condition on the solution |
| `update_constraint` | Existing condition's description or hardness changes |

Failure events (`extraction_failed`, `validation_skip`, `apply_failed`,
`overlap_detected`, `state_invariant_violation`) are recorded in the operation
log alongside successes, so failure rates are measurable directly from saved
sessions rather than having to be re-derived.

---

## Repo Layout

```
problem-tracker/
├── src/problem_tracker/
│   ├── core/            # ProblemDescription, Session / STG
│   ├── schemas/         # Goal, Variable, Constraint
│   ├── updaters/        # orchestrator, extractors (LLM), applicators (pure code)
│   ├── study/           # user study app, scenarios, probes, replies
│   ├── prompt_specs.py  # system prompts + few-shot examples
│   ├── llm_client.py    # OpenAI-compatible wrapper
│   └── config.py        # .env loading
├── scripts/             # run_interactive, run_batch, replay, view_stg, check_llm
├── tests/               # 142 tests
└── docs/                # known issues, questionnaire
```

---

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/<your-username>/problem-tracker.git
cd problem-tracker
uv sync
cp .env.example .env    # then add your real key to .env
```

The system talks to any OpenAI-compatible endpoint. For a local Ollama:

```bash
ollama serve
ollama pull llama3.1
uv run python scripts/check_llm.py     # verify connectivity first
```

Configuration is by environment variable (or `.env`):

| Variable | Default |
|---|---|
| `LLM_BASE_URL` | `http://localhost:11434/v1` |
| `LLM_API_KEY` | `ollama` |
| `LLM_MODEL` | `llama3.1` |

---

## Running

```bash
uv run python scripts/run_interactive.py                  # REPL
uv run python scripts/run_batch.py                        # scripted run
uv run python -m problem_tracker.study.app                # user-study server on :8000
uv run python -m problem_tracker.study.app --dry-run      # canned replies, no LLM calls
uv run python scripts/view_stg.py <session.json>
uv run pytest -q
```

---