"""tests/test_study.py — the user-study harness.

The critical property under test is that the two arms differ in EXACTLY one
respect: the problem description appears in the reply prompt of one and not
the other. If that fails, RQ3 measures nothing.
"""

from pathlib import Path

from problem_tracker.core.problem_description import ProblemDescription
from problem_tracker.schemas.constraint import Constraint
from problem_tracker.schemas.variable import Variable
from problem_tracker.study.reply import ARMS, ReplyGenerator, render_problem_description
from problem_tracker.study.scenarios import SCENARIOS, TURN_BUDGET, assign, get_scenario
from tests.conftest import make_goal, make_pd_with_goal


class _CapturingClient:
    """Records the messages it was asked to send."""
    model = "capture"

    def __init__(self):
        self.last_messages = None

    def chat(self, messages):
        self.last_messages = messages
        return "ok"


def _pd_with_everything():
    pd = make_pd_with_goal(goal_id="G1", text="Plan trip")
    pd.explicit.variables.append(
        Variable(variable_id="V1", goal_id="G1", name="destination", value="Kyoto", role="fact", required=True, introduced_turn="t1", last_updated_turn="t1")
    )
    pd.explicit.variables.append(
        Variable(variable_id="V2", goal_id="G1", name="travel_month", value=None, role="unknown", required=False, introduced_turn="t2", last_updated_turn="t2")
    )
    pd.explicit.constraints.append(
        Constraint("C1", "G1", "budget_ceiling", "must stay under £3000", "hard", "t2", "t2")
    )
    pd.explicit.constraints.append(
        Constraint("C2", "G1", "budget_target", "around £2000", "soft", "t2", "t2")
    )
    return pd


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def test_render_includes_all_element_kinds():
    text = render_problem_description(_pd_with_everything())
    assert "Plan trip" in text
    assert "destination" in text and "Kyoto" in text
    assert "not yet known" in text            # unknown variable rendered without a value
    assert "under £3000" in text and "around £2000" in text
    assert "hard constraints" in text and "soft constraints" in text


def test_render_excludes_inactive_goals():
    """Abandoned/completed goals are historical record and must not steer the reply."""
    pd = make_pd_with_goal(goal_id="G1", text="Plan trip")
    pd.explicit.goals[0].status = "abandoned"
    pd.explicit.goals.append(make_goal(goal_id="G2", text="Write CV", turn="t3"))
    text = render_problem_description(pd)
    assert "Write CV" in text
    assert "Plan trip" not in text


def test_render_empty_state():
    assert "no goals" in render_problem_description(ProblemDescription()).lower()


# ---------------------------------------------------------------------------
# The arms differ in exactly one way
# ---------------------------------------------------------------------------

def test_baseline_prompt_contains_no_problem_description():
    client = _CapturingClient()
    gen = ReplyGenerator(client=client)
    history = [{"role": "user", "content": "help me plan a trip"}]
    gen.reply(history, "baseline")
    blob = " ".join(m["content"] for m in client.last_messages)
    assert "Kyoto" not in blob and "GOAL" not in blob
    assert client.last_messages[-1]["content"] == "help me plan a trip"


def test_problem_description_arm_injects_state():
    client = _CapturingClient()
    gen = ReplyGenerator(client=client)
    history = [{"role": "user", "content": "help me plan a trip"}]
    gen.reply(history, "problem_description", _pd_with_everything())
    blob = " ".join(m["content"] for m in client.last_messages)
    assert "Kyoto" in blob and "under £3000" in blob


def test_arms_differ_only_by_the_injected_block():
    """Same system prompt, same history, one extra system message — nothing else."""
    c1, c2 = _CapturingClient(), _CapturingClient()
    history = [{"role": "user", "content": "plan a trip"},
               {"role": "assistant", "content": "sure"},
               {"role": "user", "content": "under £3000"}]
    ReplyGenerator(client=c1).reply(history, "baseline")
    ReplyGenerator(client=c2).reply(history, "problem_description", _pd_with_everything())

    base, pdarm = c1.last_messages, c2.last_messages
    assert len(pdarm) == len(base) + 1
    assert base[0] == pdarm[0]                      # identical system prompt
    assert base[1:] == pdarm[2:]                    # identical history
    assert pdarm[1]["role"] == "system"             # the one difference


def test_reply_prompt_forbids_claiming_tool_use():
    """Observed in pilot: the assistant twice said it would 'look up the menu'
    and 'get back to you shortly', burning two of seven turns on an action it
    cannot perform. Both arms share this system prompt, so the instruction must
    be present in it."""
    from problem_tracker.study.reply import REPLY_SYSTEM_PROMPT
    lowered = REPLY_SYSTEM_PROMPT.lower()
    assert "no tools" in lowered
    assert "get back to them" in lowered or "get back to you" in lowered
    for banned_promise in ("shortly", "hold on"):
        assert banned_promise in lowered, f"prompt should name {banned_promise!r} as forbidden"

    # The no-tools rule must be scoped to external ACTIONS only. Left unscoped it
    # caused a false refusal in pilot ("I can't create the app directly for you"),
    # burning two of seven turns.
    assert "does not limit what you can produce" in lowered


def test_reply_prompt_constrains_length_but_exempts_asked_for_output():
    """Pilot: both arms produced 150-250 word surveys of the topic ending in
    'Would you like...'. When both arms lecture, there is nothing for the
    representation to differentiate, and the interaction stops being
    collaborative problem solving. The exemption matters as much as the limit —
    the synthesis turn must be allowed to run long."""
    from problem_tracker.study.reply import REPLY_SYSTEM_PROMPT
    lowered = REPLY_SYSTEM_PROMPT.lower()
    assert "under 80 words" in lowered
    assert "headings" in lowered
    # ...but an explicitly requested plan/draft/outline is exempt
    assert "the one exception" in lowered
    assert "pull everything together" in lowered or "pulling everything together" in lowered
    assert "write code" in lowered

    # and it must reach BOTH arms, since it lives in the shared system prompt
    c1, c2 = _CapturingClient(), _CapturingClient()
    history = [{"role": "user", "content": "check if they are open late"}]
    ReplyGenerator(client=c1).reply(history, "baseline")
    ReplyGenerator(client=c2).reply(history, "problem_description", _pd_with_everything())
    assert "no tools" in c1.last_messages[0]["content"].lower()
    assert c1.last_messages[0] == c2.last_messages[0]


def test_unknown_arm_rejected():
    try:
        ReplyGenerator(client=_CapturingClient()).reply([], "sneaky")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "sneaky" in str(e)


def test_pd_arm_requires_state():
    try:
        ReplyGenerator(client=_CapturingClient()).reply([], "problem_description")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_reply_failure_does_not_crash_session():
    class _Dead:
        model = "dead"
        def chat(self, messages):
            raise RuntimeError("transport down")
    out = ReplyGenerator(client=_Dead()).reply([{"role": "user", "content": "hi"}], "baseline")
    assert isinstance(out, str) and out


# ---------------------------------------------------------------------------
# Assignment is properly counterbalanced
# ---------------------------------------------------------------------------

def test_each_participant_gets_both_arms_and_both_scenarios():
    for n in range(1, 25):
        sessions = assign(n)
        assert len(sessions) == 2
        assert {s["arm"] for s in sessions} == set(ARMS)
        assert {s["scenario"] for s in sessions} == set(SCENARIOS)


def test_scenario_and_arm_stay_crossed():
    """Across four participants each (scenario, arm) cell must appear exactly
    twice, so a scenario main effect still cancels in the paired comparison.
    This is the property the design must keep after fixing the arm order."""
    pairs, scen_order = [], []
    for n in range(1, 5):
        for s in assign(n):
            pairs.append((s["scenario"], s["arm"]))
            scen_order.append((s["scenario"], s["session"]))

    for scenario in SCENARIOS:
        for arm in ARMS:
            assert pairs.count((scenario, arm)) == 2, (scenario, arm, pairs)
    for scenario in SCENARIOS:
        assert scen_order.count((scenario, "1")) == 2, scenario


def test_schema_arm_is_always_the_second_conversation():
    """DELIBERATE, and it is the one thing about this design that is not
    counterbalanced. The C-block must follow the B-block comparison (or the
    reveal discloses the condition), so putting the schema arm second means the
    representation is always reviewed immediately after the conversation it
    came from, rather than from memory of an earlier one.

    The cost: ORDER IS CONFOUNDED WITH ARM. Any advantage of going second
    accrues entirely to the problem_description arm. This test exists so the
    confound cannot be reintroduced or removed by accident — changing it is a
    change to the experimental design and must be recorded as one."""
    for n in range(1, 13):
        first, second = assign(n)
        assert first["arm"] == "baseline", (n, first)
        assert second["arm"] == "problem_description", (n, second)


def test_assignment_cycles_and_is_deterministic():
    assert assign(1) == assign(5) == assign(9)      # 4-group rotation
    assert assign(3) != assign(4)
    assert assign(7) == assign(7)                    # no randomness


def test_labels_do_not_leak_the_condition():
    for n in range(1, 9):
        for s in assign(n):
            assert s["arm"] not in s["label"]
            assert s["label"] in ("Assistant 1", "Assistant 2")


def test_scenarios_are_comparable_in_shape():
    for sid in SCENARIOS:
        brief = get_scenario(sid)["brief"].lower()
        assert "limit" in brief          # a hard constraint to state
        assert "change your mind" in brief   # a revision, which is the point of the study
        assert len(brief) < 900          # readable inside the session
        # The synthesis turn is what gives the representation anything to do.
        assert "last message" in brief


def test_briefs_do_not_supply_utterances_from_the_probes():
    """Lexical entrainment: a brief that names the probe's own items would make
    participants echo them, flattering extraction and removing the between-
    participant variation the study depends on."""
    leaked = ("mechanic", "the gym", "my sister", "peer-reviewed", "2015")
    for sid in SCENARIOS:
        brief = get_scenario(sid)["brief"].lower()
        for term in leaked:
            assert term not in brief, f"{sid} brief leaks probe wording: {term!r}"


def test_turn_budget_is_sane():
    assert 5 <= TURN_BUDGET <= 15


# ---------------------------------------------------------------------------
# Crash recovery
# ---------------------------------------------------------------------------

def test_study_sessions_store_nothing_the_participant_typed():
    """Participant conversations are never written to disk. What is recorded is
    the operation and event TYPES and the size of the state — enough for the
    log-derived failure-rate measure, and containing nothing they said.

    The consequence, asserted here so it is not discovered during collection: a
    study session cannot be resumed after a restart, because there is nothing to
    resume it from."""
    import json
    import shutil
    import tempfile

    from problem_tracker.study import app
    from problem_tracker.updaters import ExplicitUpdater

    tmp = tempfile.mkdtemp()
    old_dir = app.SESSION_DIR
    try:
        app.SESSION_DIR = tmp

        class _Stub:
            model = "stub"
            def chat(self, messages):
                return "an assistant reply mentioning Ujjwal"
            def chat_json(self, messages):
                if "goal-tracking" in messages[0]["content"]:
                    return [{"operation": "create_goal", "goal_text": "Plan something"}]
                if "variable-extraction" in messages[0]["content"]:
                    return [{"name": "venue", "value": "Ujjwal's flat on Leith Walk",
                             "role": "fact", "required": True}]
                return []

        stub = _Stub()
        # the SCHEMA arm — the baseline arm runs no extraction, so it would
        # have nothing to leak and the test would pass vacuously
        spec = assign(1)[1]
        assert spec["arm"] == "problem_description"
        run = app.Run("1", spec, stub, ExplicitUpdater(client=stub),
                      ReplyGenerator(client=stub))
        run.send("my address is 14 Leith Walk and my name is Ujjwal")
        path = run.save()

        blob = Path(path).read_text(encoding="utf-8")
        for leak in ("Leith Walk", "Ujjwal", "venue", "assistant reply", "my address"):
            assert leak not in blob, f"{leak!r} was written to the session file"

        data = json.loads(blob)
        assert data["stores_content"] is False
        assert "final_state" not in data
        entry = data["stg"][0]
        assert "user_turn" not in entry and "assistant_reply" not in entry
        assert "state_snapshot" not in entry
        # ...but the measurable part survives
        assert [o["operation"] for o in entry["operations"]] == ["create_goal", "create_variable"]
        assert entry["state_size"] == {"goals": 1, "active_goals": 1,
                                       "variables": 1, "constraints": 0}

        app._runs.clear()
        assert app._rehydrate("1", "2", stub, ExplicitUpdater(client=stub),
                              ReplyGenerator(client=stub)) is None
    finally:
        app.SESSION_DIR = old_dir
        app._runs.clear()
        shutil.rmtree(tmp, ignore_errors=True)




def test_injected_block_defers_to_the_transcript_on_conflict():
    """The state is sometimes wrong — fabricated orderings, misrouted values, a
    rule overwritten by its exception. Instructing the model to treat it as
    accurate makes the schema arm propagate those errors and can put it BELOW
    baseline whenever extraction errs, which measures "does trusting a summary
    help", not "does a summary help"."""
    from problem_tracker.study.reply import _PD_PREAMBLE
    lowered = _PD_PREAMBLE.lower()
    assert "conversation is authoritative" in lowered
    assert "incomplete or mistaken" in lowered
    # ...and it must still do the job the study is testing
    assert "do not ask again" in lowered
    assert "hard constraint" in lowered




def test_final_reply_is_shown_before_the_completion_code():
    """The page used to flip to the completion screen the moment the turn budget
    ran out, so the reply to the participant's LAST message was generated and
    never displayed. That message is the one asking the assistant to pull
    everything together, so its answer is the single reply the study most
    depends on them having read."""
    import shutil
    import tempfile

    from problem_tracker.study import app
    from problem_tracker.updaters import ExplicitUpdater

    tmp = tempfile.mkdtemp()
    old_dir = app.SESSION_DIR
    try:
        app.SESSION_DIR = tmp

        class _Stub:
            model = "stub"
            def chat(self, messages):
                return "HERE IS THE CONSOLIDATED PLAN"
            def chat_json(self, messages):
                return [{"operation": "no_goal_change"}] if "goal-tracking" in messages[0]["content"] else []

        stub = _Stub()
        run = app.Run("1", assign(1)[0], stub, ExplicitUpdater(client=stub),
                      ReplyGenerator(client=stub))
        for i in range(TURN_BUDGET):
            run.send(f"message {i + 1}")
        assert run.finished

        page = app._end_body(run, run.save())
        assert "HERE IS THE CONSOLIDATED PLAN" in page
        assert "message 7" in page
        assert run.completion_code in page
        # the reply must come BEFORE the completion code on the page
        assert page.index("HERE IS THE CONSOLIDATED PLAN") < page.index(run.completion_code)
    finally:
        app.SESSION_DIR = old_dir
        app._runs.clear()
        shutil.rmtree(tmp, ignore_errors=True)
