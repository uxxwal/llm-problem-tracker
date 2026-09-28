import json
from problem_tracker.core.problem_description import ProblemDescription
from problem_tracker.core.session import Session
from problem_tracker.llm_client import LLMClient
from problem_tracker.updaters import ExplicitUpdater

# Operations that indicate something went wrong during the turn rather than a
# state change — surfaced as warnings so silent degradation is visible.
_FAILURE_OPS = {
    "extraction_failed",
    "validation_failed",
    "apply_failed",
    "validation_skip",
    "state_invariant_violation",
    "overlap_detected",
    "elements_detached",
    "cross_goal_duplicate",
}


def print_state(pd: ProblemDescription):
    print("\n  GOALS:")
    if not pd.explicit.goals:
        print("    (none)")
    for g in pd.explicit.goals:
        print(f"    [{g.goal_id}] \"{g.goal_text}\"  status={g.status}")

    print("\n  VARIABLES:")
    if not pd.explicit.variables:
        print("    (none)")
    for v in pd.explicit.variables:
        print(f"    [{v.variable_id}] {v.name} = {v.value!r}  role={v.role}  goal={v.goal_id}  required={v.required}")

    print("\n  CONSTRAINTS:")
    if not pd.explicit.constraints:
        print("    (none)")
    for c in pd.explicit.constraints:
        print(f"    [{c.constraint_id}] {c.name}: \"{c.description}\"  type={c.constraint_type}  goal={c.goal_id}")


def print_ops(ops):
    print("\n  OPERATIONS:")
    if not ops:
        print("    (none)")
    for op in ops:
        op_display = {k: v for k, v in op.items() if k not in ("turn_id", "before", "after")}
        print(f"    {json.dumps(op_display)}")


def print_warnings(ops):
    failures = [op for op in ops if op.get("operation") in _FAILURE_OPS]
    if not failures:
        return
    print("\n  WARNINGS:")
    for op in failures:
        component = op.get("component", "state")
        reason = op.get("reason") or op.get("errors") or ""
        detail = f" — {reason}" if reason else ""
        print(f"    !! {op['operation']} ({component}){detail}")


def main():
    client = LLMClient()
    updater = ExplicitUpdater(client=client)
    session = Session(model=client.model)

    print("Interactive problem tracker. Type a message each turn. Enter 'quit' or 'exit' to stop.")
    print(f"Session: {session.session_id} (saved to sessions/ on exit)\n")

    try:
        while True:
            try:
                user_input = input(f"Turn {session._turn_counter + 1}> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting.")
                break

            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit"):
                break

            result = session.process_turn(updater, user_input)
            print_ops(result["operation_log"])
            print_warnings(result["operation_log"])
            print_state(session.pd)
            print()
    finally:
        if session.stg:
            path = session.save()
            print(f"\nSession saved: {path}")
            print(f"View it with:  python tools/view_stg.py {path}")
        else:
            print("\nNo turns recorded; nothing saved.")


if __name__ == "__main__":
    main()
