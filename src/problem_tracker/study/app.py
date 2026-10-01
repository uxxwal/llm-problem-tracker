#!/usr/bin/env python3
"""study/app.py — the user-study chat application.

Runs both study arms behind one visually identical interface. The only
difference a participant could detect is the assistant's behaviour; nothing on
screen, in the page source, or in the URL names the condition.

Standard library only (plus whatever LLMClient needs), so it runs without
installing anything: no framework, no build step.

Usage
-----
    python study/app.py                 # http://127.0.0.1:8000
    python study/app.py --port 8080
    python study/app.py --dry-run       # canned replies, no LLM calls (for rehearsal)

Then open the URL, enter the participant number, and hand over the keyboard.
Sessions are written to sessions/ as JSON (schema arm records the full STG).
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from problem_tracker.config import load_env, require_llm_config
from problem_tracker.core.session import Session
from problem_tracker.llm_client import LLMClient
from problem_tracker.study.reply import (
    ReplyGenerator,
    render_problem_description,
)
from problem_tracker.study.scenarios import (
    TURN_BUDGET,
    assign,
    get_scenario,
)
from problem_tracker.updaters import ExplicitUpdater

logger = logging.getLogger(__name__)

SESSION_DIR = "sessions"

def _use_sessions_dir(path: str) -> None:
    """Write sessions to an arbitrary directory instead of ./sessions.

    Intended for a folder synced to University storage (OneDrive, SharePoint,
    DataShare), so participant transcripts arrive in approved storage as they
    are collected rather than being copied there afterwards — a copy step is a
    step that gets forgotten, and an unsynced laptop folder is the wrong place
    for the data to spend even one night.

    Hosting the application itself on a third-party platform would be the wrong
    answer to the same problem: it introduces a data processor the ethics
    application does not name.
    """
    global SESSION_DIR, _COUNTER_FILE
    SESSION_DIR = os.path.expanduser(path)
    _COUNTER_FILE = os.path.join(SESSION_DIR, "participant_counter.txt")


def _use_pilot_dir() -> None:
    """Redirect all state to sessions/pilot/ for rehearsal runs.

    Rehearsing in the live directory is a real hazard: rehearsal sessions land
    in the study corpus, and — worse — they consume participant numbers, so the
    first real participant is not participant 1 and the counterbalancing is
    silently offset. Keeping rehearsals in their own directory with their own
    counter removes the need to remember to clean up.
    """
    global SESSION_DIR, _COUNTER_FILE
    SESSION_DIR = os.path.join(SESSION_DIR, "pilot")
    _COUNTER_FILE = os.path.join(SESSION_DIR, "participant_counter.txt")

# Optional protections for public deployment, both set via environment:
#   STUDY_ACCESS_CODE — participants must enter this to start (put it in the
#                       invitation email). Stops strangers burning the API key.
#   ADMIN_CODE        — enables GET /export?code=<ADMIN_CODE>, which returns
#                       every recorded session as one JSON bundle. Needed on
#                       hosts with ephemeral disks (Render, Fly), where files
#                       vanish on redeploy: export after every participant.
_lock = threading.Lock()
_runs: dict[str, Run] = {}

# Participant numbers that have cleared the access code at /start. /begin and
# /send check membership, because they are the endpoints that spend money and
# were previously reachable by anyone who guessed the URL — gating /start alone
# gated nothing.
_authorised: set = set()

# Participant numbers are issued by the server, not chosen by participants.
# With a shared link, self-chosen numbers collide — two people in the same
# condition, and the second overwrites the first. The counter is persisted so
# it survives a restart mid-study.
_COUNTER_FILE = os.path.join(SESSION_DIR, "participant_counter.txt")


def _issue_participant_number() -> int:
    """Return the next participant number, atomically and durably."""
    with _lock:
        os.makedirs(SESSION_DIR, exist_ok=True)
        try:
            with open(_COUNTER_FILE, encoding="utf-8") as f:
                last = int(f.read().strip() or 0)
        except (OSError, ValueError):
            last = 0
        nxt = last + 1
        with open(_COUNTER_FILE, "w", encoding="utf-8") as f:
            f.write(str(nxt))
        return nxt


class _DryRunClient:
    """Canned client for rehearsing the flow without touching the LLM."""

    model = "dry-run"

    def chat(self, messages):
        return "Thanks — noted. What else should I take into account?"

    def chat_json(self, messages):
        system = messages[0]["content"] if messages else ""
        if "goal-tracking" in system:
            return [{"operation": "no_goal_change"}]
        return []


def _rehydrate(pid: str, session_no: str, client, updater, replier) -> Run | None:
    """Rebuild an in-progress Run from its autosaved session file.

    ONLY POSSIBLE FOR SESSIONS THAT STORED THEIR CONTENT. Study sessions do not:
    the utterances and replies exist in memory alone, so a restart mid-study
    genuinely loses that participant's conversation and it cannot be recovered.
    That is the direct cost of not retaining transcripts, and it is why the
    server must not be restarted while anyone is mid-session.

    Returns None when there is nothing recoverable.
    """
    marker = f"pid={pid} session={session_no} "
    if not os.path.isdir(SESSION_DIR):
        return None
    for name in sorted(os.listdir(SESSION_DIR), reverse=True):
        if not name.endswith(".json"):
            continue
        try:
            session = Session.load(os.path.join(SESSION_DIR, name))
        except Exception:
            continue
        if not session.notes.startswith(marker):
            continue
        if not session.store_content:
            logger.warning("[app] %s:%s cannot be resumed — the conversation was "
                           "not retained by design", pid, session_no)
            return None
        spec = assign(int(pid))[int(session_no) - 1]
        run = Run(pid, spec, client, updater, replier)
        run.session = session
        run.turns = len(session.stg)
        for entry in session.stg:
            run.history.append({"role": "user", "content": entry["user_turn"]})
            if entry.get("assistant_reply"):
                run.history.append({"role": "assistant", "content": entry["assistant_reply"]})
        run.finished = run.turns >= TURN_BUDGET
        with _lock:
            _runs[run.key] = run
        logger.warning("[app] rehydrated %s from %s (%d turns)", run.key, name, run.turns)
        return run
    return None


class Run:
    """One participant's session with one assistant."""

    def __init__(self, pid: str, spec: dict[str, str], client, updater, replier):
        self.pid = pid
        self.spec = spec
        self.arm = spec["arm"]
        self.scenario = get_scenario(spec["scenario"])
        self.history: list[dict[str, str]] = []
        self.turns = 0
        self.finished = False
        self.updater = updater
        self.replier = replier
        # Serialises turns within ONE conversation: a participant double-clicking
        # Send during a slow model call must not interleave two turns.
        self.turn_lock = threading.Lock()
        self.session = Session(
            model=getattr(client, "model", "?"),
            decode=client.describe() if hasattr(client, "describe") else None,
            notes=f'pid={pid} session={spec["session"]} scenario={spec["scenario"]} arm={self.arm}',
            # HARD-CODED, not a flag: participant conversations are never
            # written to disk. What is recorded per turn is the operation and
            # event TYPES and the size of the state — enough for failure rates,
            # and containing nothing the participant typed.
            store_content=False,
        )

    @property
    def key(self) -> str:
        return f'{self.pid}:{self.spec["session"]}'

    @property
    def completion_code(self) -> str:
        return f'{self.pid}-{self.spec["session"]}{self.turns:02d}'

    def send(self, text: str) -> str | None:
        with self.turn_lock:
            if self.finished:
                return None
            # Ignore an immediate repeat of the same message (double submit).
            if self.history and self.history[-2:-1] and \
                    self.history[-2]["role"] == "user" and self.history[-2]["content"] == text:
                return None
            return self._send_locked(text)

    def _send_locked(self, text: str) -> str:
        self.history.append({"role": "user", "content": text})
        self.turns += 1

        if self.arm == "problem_description":
            # maintain the state, then reply with it in the prompt
            result = self.session.process_turn(self.updater, text)
            pd = result["state"]
            reply = self.replier.reply(self.history, self.arm, pd)
        else:
            # baseline: record the turn for the transcript, no state maintained
            turn_id = self.session.next_turn_id()
            self.session.record_turn(turn_id, text, [], self.session.pd)
            reply = self.replier.reply(self.history, self.arm)

        self.history.append({"role": "assistant", "content": reply})
        # The reply is NOT attached to the recorded turn: it is generated from
        # what the participant said and is therefore withheld along with the
        # rest of the conversation. It lives only in memory, for the duration of
        # the session, because the model needs it as context.

        if self.turns >= TURN_BUDGET:
            self.finished = True

        # Autosave every turn: on a remote host a restart mid-conversation
        # must not cost the participant's transcript. A failure here is silent
        # to the participant by design — but it must not be silent to the
        # researcher, since it means data is being lost as it is collected.
        try:
            self.save()
        except Exception as e:
            logger.error("[app] AUTOSAVE FAILED for %s: %s — data is not being "
                         "written to disk", self.key, e)
        return reply

    def save(self) -> str:
        return self.session.save(directory=SESSION_DIR)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Study</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 :root {{ --line:#d8d8d8; --muted:#666; }}
 * {{ box-sizing:border-box; }}
 body {{ font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
        margin:0; background:#fafafa; color:#111; }}
 .wrap {{ max-width:760px; margin:0 auto; padding:24px 20px 40px; }}
 .card {{ background:#fff; border:1px solid var(--line); border-radius:10px; padding:20px; }}
 h1 {{ font-size:20px; margin:0 0 4px; }}
 h2 {{ font-size:16px; margin:24px 0 8px; }}
 .sub {{ color:var(--muted); font-size:14px; margin-bottom:20px; }}
 .brief {{ background:#f4f6f8; border:1px solid var(--line); border-radius:8px;
           padding:14px 16px; white-space:pre-wrap; font-size:15px; margin-bottom:20px; }}
 .msgs {{ display:flex; flex-direction:column; gap:12px; margin-bottom:18px; }}
 .m {{ padding:10px 14px; border-radius:12px; max-width:82%; white-space:pre-wrap; }}
 .u {{ align-self:flex-end; background:#0b5fff; color:#fff; border-bottom-right-radius:3px; }}
 .a {{ align-self:flex-start; background:#eef0f3; border-bottom-left-radius:3px; }}
 form {{ display:flex; gap:8px; }}
 input[type=text], textarea {{ flex:1; padding:11px 12px; border:1px solid var(--line);
        border-radius:8px; font:inherit; }}
 button {{ padding:11px 18px; border:0; border-radius:8px; background:#111; color:#fff;
           font:inherit; cursor:pointer; }}
 button:disabled {{ background:#999; cursor:default; }}
 .count {{ color:var(--muted); font-size:13px; margin-top:10px; }}
 .code {{ font:15px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace; background:#f4f6f8;
          border:1px solid var(--line); border-radius:8px; padding:12px 14px;
          white-space:pre-wrap; }}
 .big {{ font-size:22px; letter-spacing:1px; text-align:center; padding:16px; }}
 ul {{ margin:8px 0 0 18px; padding:0; }} li {{ margin:4px 0; }}
</style></head>
<body><div class="wrap"><div class="card">{body}</div></div></body></html>
"""

def _start_body(error: str = "") -> str:
    code_field = ""
    if os.getenv("STUDY_ACCESS_CODE"):
        code_field = ('<input type="text" name="code" placeholder="Study code (from your invitation)" '
                      'required autofocus>')
    msg = f'<div class="count">{html.escape(error)}</div>' if error else ""
    return f"""<h1>Before we begin</h1>
<div class="sub">You will be given a participant number on the next screen. Please
make sure you have finished the consent section of the questionnaire first.</div>
<form method="post" action="/start" style="flex-direction:column;display:flex;gap:8px">
  {code_field}
  <button type="submit">Start</button>
</form>{msg}"""


def _issued_body(pid: str, spec: dict[str, str]) -> str:
    return (
        '<h1>Your participant number</h1>'
        f'<div class="code big">{html.escape(pid)}</div>'
        '<div class="sub" style="margin-top:14px">Write this down, and enter it in the '
        'questionnaire. You will need it again at the end, so please keep this tab open.</div>'
        + _next_body(pid, spec)
        + _details_panel(pid)
    )


def _page(body: str) -> bytes:
    return _PAGE.format(body=body).encode("utf-8")


def _messages_block(run: Run) -> str:
    return "".join(
        f'<div class="m {"u" if m["role"] == "user" else "a"}">{html.escape(m["content"])}</div>'
        for m in run.history
    )


def _chat_body(run: Run) -> str:
    msgs = _messages_block(run)
    left = TURN_BUDGET - run.turns
    form = (
        f'<form method="post" action="/send" id="f" onsubmit="return lock()">'
        f'<input type="hidden" name="key" value="{html.escape(run.key)}">'
        f'<input type="text" id="t" name="text" placeholder="Type your message…" autofocus required autocomplete="off">'
        f'<button type="submit" id="b">Send</button></form>'
        f'<div class="count" id="c">{left} message{"s" if left != 1 else ""} remaining</div>'
        '<script>var sent=false;function lock(){if(sent)return false;'
        'if(!document.getElementById("t").value.trim())return false;sent=true;'
        'var b=document.getElementById("b");b.disabled=true;b.textContent="Sending…";'
        'document.getElementById("t").readOnly=true;'
        'document.getElementById("c").textContent="Thinking… this can take a few seconds.";'
        'return true;}</script>'
    )
    return (
        f'<h1>{html.escape(run.spec["label"])}</h1>'
        f'<div class="sub">{html.escape(run.scenario["title"])}</div>'
        f'<div class="brief">{html.escape(run.scenario["brief"])}</div>'
        f'<div class="msgs">{msgs}</div>{form}'
    )


def _end_body(run: Run, path: str) -> str:
    """End of one conversation.

    Deliberately IDENTICAL for both arms: the maintained problem description is
    NOT shown here. Revealing it at the end of the schema session would tell the
    participant the two assistants differ, before they answer the comparison
    questions that are the study's primary outcome. The reveal happens at
    /review, which the questionnaire sends them to only after that block.
    """
    # The conversation, INCLUDING the reply to the participant's last message.
    # Without this the final turn's answer was generated and never shown: the
    # page flipped to the completion code the moment the turn budget ran out.
    # That answer is the consolidated plan the last message asks for, so it is
    # the one reply the study most depends on the participant having read.
    parts = [f'<h1>{html.escape(run.spec["label"])} — finished</h1>',
             f'<div class="msgs">{_messages_block(run)}</div>',
             '<div class="sub">That is the end of this conversation — you have used all '
             f'{TURN_BUDGET} messages.</div>',
             '<h2>Your completion code</h2>']
    parts.append(f'<div class="code big">{html.escape(run.completion_code)}</div>')

    if run.spec["session"] == "1":
        parts.append('<div class="count">Enter this code in the questionnaire, answer the '
                     'questions about this assistant, and then come back here.</div>')
    else:
        parts.append('<h2>What now</h2>')
        parts.append('<div class="sub">Go back to the questionnaire and enter this code, then '
                     'keep answering. <strong>Leave this tab open</strong> — near the end the '
                     'questionnaire will ask you to come back here and press the button '
                     'below.</div>')
        # The reveal lives behind a button on the page the participant already
        # has open, so no second URL is needed (the tunnel address is not
        # stable) and no navigation is required. The questionnaire controls
        # WHEN it is pressed, which keeps the reveal after the comparison
        # questions.
        parts.append(
            f'<form method="post" action="/review" style="margin-top:18px">'
            f'<input type="hidden" name="pid" value="{html.escape(run.pid)}">'
            f'<button type="submit">Show the summary (only when the questionnaire says so)</button>'
            f'</form>'
        )

    parts.append(_details_panel(run.pid))
    parts.append(f'<div class="count" style="margin-top:14px">saved: '
                 f'{html.escape(os.path.basename(path))}</div>')
    return "".join(parts)


def _details_panel(pid: str) -> str:
    """Everything the participant must carry to the questionnaire, repeated on
    every screen after they have it. Losing a completion code means a form
    response that cannot be linked to its transcript."""
    if not pid.isdigit():
        return ""
    rows = [f'<tr><td style="padding:4px 14px 4px 0">Participant number</td>'
            f'<td><strong>{html.escape(pid)}</strong></td></tr>']
    with _lock:
        for spec in assign(int(pid)):
            run = _runs.get(f'{pid}:{spec["session"]}')
            label = html.escape(spec["label"])
            if run is not None and run.finished:
                rows.append(f'<tr><td style="padding:4px 14px 4px 0">{label} code</td>'
                            f'<td><strong>{html.escape(run.completion_code)}</strong></td></tr>')
            else:
                rows.append(f'<tr><td style="padding:4px 14px 4px 0">{label} code</td>'
                            f'<td style="color:#888">not yet</td></tr>')
    return ('<div style="margin-top:22px;padding:14px 16px;background:#f4f6f8;'
            'border:1px solid #d8d8d8;border-radius:8px">'
            '<div style="font-size:13px;color:#666;margin-bottom:8px">'
            'KEEP THESE — you need them in the questionnaire</div>'
            f'<table style="font-size:15px">{"".join(rows)}</table></div>')


def _review_body(pid: str) -> str:
    """The RQ2 reveal: the maintained problem description, shown only after the
    questionnaire's comparison block."""
    if not pid.isdigit():
        return ('<h1>Review</h1><div class="sub">Enter your participant number.</div>'
                '<form method="post" action="/review">'
                '<input type="text" name="pid" placeholder="Participant number" autofocus required>'
                '<button type="submit">Show</button></form>')

    specs = assign(int(pid))
    schema_session = next(s for s in specs if s["arm"] == "problem_description")
    with _lock:
        run = _runs.get(f'{pid}:{schema_session["session"]}')
    if run is None:
        # Same crash-recovery path as /send: the transcript is on disk even when
        # the in-memory run is gone, and this page is the last step of the study.
        run = _rehydrate(pid, schema_session["session"],
                         Handler.client, Handler.updater, Handler.replier)
    if run is None:
        return ('<h1>Review</h1><div class="sub">No completed conversation found for that '
                'participant number. Please check it and try again.</div>'
                '<form method="post" action="/review">'
                '<input type="text" name="pid" placeholder="Participant number" autofocus required>'
                '<button type="submit">Show</button></form>')

    # WHICH assistant this summary belongs to varies by participant: the schema
    # arm is the first conversation for half of them and the second for the
    # other half. Saying only "one of the assistants" left participants
    # answering about the wrong conversation, or about both — which would
    # produce complaints of inaccuracy that say nothing about the
    # representation. Name it, and name the task it belongs to.
    label = html.escape(schema_session["label"])
    scenario_title = html.escape(get_scenario(schema_session["scenario"])["title"])
    ordinal = "second" if schema_session["session"] == "2" else "first"
    return ('<h1>One last thing</h1>'
            f'<div class="sub">The following summary is from your conversation with '
            f'<strong>{label}</strong>, your {ordinal} conversation. Please read it, then '
            'answer the final questions in the questionnaire — we want to know anything '
            'that is <strong>wrong</strong>, or <strong>missing</strong>.</div>'
            f'<div class="code">{html.escape(render_problem_description(run.session.pd))}</div>'
            + _details_panel(pid))


def _next_body(pid: str, spec: dict[str, str]) -> str:
    return (
        f'<h1>Ready for {html.escape(spec["label"])}</h1>'
        '<div class="sub">Answer the questionnaire section for the previous assistant '
        'first, then come back and press this.</div>'
        f'<form method="post" action="/begin">'
        f'<input type="hidden" name="pid" value="{html.escape(pid)}">'
        f'<input type="hidden" name="session" value="{html.escape(spec["session"])}">'
        f'<button type="submit">Begin {html.escape(spec["label"])}</button></form>'
    )


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    client: _DryRunClient | LLMClient | None = None
    updater: ExplicitUpdater | None = None
    replier: ReplyGenerator | None = None

    def log_message(self, fmt, *args):  # quieter console
        pass

    def _respond(self, body: bytes, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _authorised(pid: str) -> bool:
        """True when this participant cleared the access code in this process.

        With no STUDY_ACCESS_CODE configured the study is open anyway, so the
        check is a no-op — it exists to stop a leaked tunnel URL being used to
        spend the API budget.
        """
        if not os.getenv("STUDY_ACCESS_CODE"):
            return True
        with _lock:
            return pid in _authorised

    def _form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            self._respond(_page(_start_body()))
        elif path == "/review":
            # GET fallback: if the participant lost the tab, they can reach the
            # reveal directly. The normal route is the button on the session-2
            # end screen, which needs no URL.
            self._respond(_page(_review_body((parse_qs(parsed.query).get("pid") or [""])[0].strip())))
        elif path == "/export":
            admin = os.getenv("ADMIN_CODE")
            supplied = (parse_qs(parsed.query).get("code") or [""])[0]
            if not admin or supplied != admin:
                self._respond(_page('<h1>Not found</h1>'), 404)
                return
            bundle = []
            if os.path.isdir(SESSION_DIR):
                for name in sorted(os.listdir(SESSION_DIR)):
                    if name.endswith(".json"):
                        with open(os.path.join(SESSION_DIR, name), encoding="utf-8") as f:
                            bundle.append(json.load(f))
            payload = json.dumps({"sessions": bundle}, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="study_sessions.json"')
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self._respond(_page('<h1>Not found</h1>'), 404)

    def do_POST(self):
        path = urlparse(self.path).path
        data = self._form()

        if path == "/start":
            required_code = os.getenv("STUDY_ACCESS_CODE")
            if required_code and (data.get("code") or "").strip() != required_code:
                self._respond(_page(_start_body(
                    "That study code is not right — check your invitation.")))
                return
            # A number supplied explicitly is a researcher override (testing,
            # or resuming someone who lost their tab); otherwise issue a new one.
            supplied = (data.get("pid") or "").strip()
            pid = supplied if supplied.isdigit() else str(_issue_participant_number())
            with _lock:
                _authorised.add(pid)
            spec = assign(int(pid))[0]
            self._respond(_page(_issued_body(pid, spec) if not supplied
                                else _next_body(pid, spec)))
            return

        if path == "/begin":
            pid = (data.get("pid") or "").strip()
            session_raw = (data.get("session") or "").strip()
            if not pid.isdigit() or int(pid) < 1 or session_raw not in ("1", "2"):
                self._respond(_page(_start_body("Something went wrong — please start again.")))
                return
            if not self._authorised(pid):
                self._respond(_page(_start_body(
                    "Please start from the beginning and enter your study code.")))
                return
            session_no = int(session_raw)
            spec = assign(int(pid))[session_no - 1]
            key = f"{pid}:{session_no}"
            with _lock:
                existing = _runs.get(key)
                if existing is not None and not existing.finished:
                    # Resume rather than clobber: covers page refresh and the
                    # back button, and prevents a duplicated participant number
                    # from destroying someone's in-progress conversation.
                    run = existing
                else:
                    run = Run(pid, spec, self.client, self.updater, self.replier)
                    _runs[key] = run
            self._respond(_page(_chat_body(run)))
            return

        if path == "/review":
            # POSTed from the button on the session-2 end screen.
            self._respond(_page(_review_body((data.get("pid") or "").strip())))
            return

        if path == "/send":
            key = (data.get("key") or "").strip()
            pid, _, session_raw = key.partition(":")
            if not pid.isdigit() or int(pid) < 1 or session_raw not in ("1", "2"):
                self._respond(_page('<h1>Session expired</h1>'), 404)
                return
            if not self._authorised(pid):
                self._respond(_page(_start_body(
                    "Please start from the beginning and enter your study code.")))
                return
            text = (data.get("text") or "").strip()
            with _lock:
                found = _runs.get(key)
            if found is None: 
                # Rebuild from the autosaved transcript rather than losing the
                # conversation: _runs is in-memory, so any restart of the server
                # would otherwise strand every participant mid-session with work
                # that is sitting on disk.
                found = _rehydrate(pid, session_raw, self.client, self.updater, self.replier)
            if found is None:
                self._respond(_page('<h1>Session expired</h1>'), 404)
                return
            run = found
            if text:
                run.send(text)   # no-ops safely if finished or a duplicate submit
            if run.finished:
                saved = run.save()
                body = _end_body(run, saved)
                specs = assign(int(run.pid))
                if run.spec["session"] == "1":
                    body += "<hr style='margin:24px 0;border:0;border-top:1px solid #d8d8d8'>"
                    body += _next_body(run.pid, specs[1])
                self._respond(_page(body))
            else:
                self._respond(_page(_chat_body(run)))
            return

        self._respond(_page('<h1>Not found</h1>'), 404)


def _lan_ip() -> str | None:
    """Best-effort LAN address of this machine (no packets are actually sent)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    ap.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    ap.add_argument("--dry-run", action="store_true",
                    help="canned replies, no LLM calls — for rehearsing the flow")
    ap.add_argument("--pilot", action="store_true",
                    help="rehearsal mode: sessions and the participant counter go to "
                         "sessions/pilot/, leaving the study corpus and numbering untouched")
    ap.add_argument("--sessions-dir", default=os.getenv("SESSIONS_DIR"),
                    help="where to write session files. Point this at a synced "
                         "University storage folder (OneDrive/SharePoint) so transcripts "
                         "land in approved storage as they are collected, rather than "
                         "being copied there afterwards")
    args = ap.parse_args()

    if args.sessions_dir:
        _use_sessions_dir(args.sessions_dir)
    if args.pilot or args.dry_run:
        _use_pilot_dir()

    if not args.dry_run:
        # Fail loudly at start-up rather than silently falling back to a local
        # default and returning error text to a participant mid-session.
        load_env( ".env")
        require_llm_config()

    client = _DryRunClient() if args.dry_run else LLMClient()
    Handler.client = client
    Handler.updater = ExplicitUpdater(client=client)
    # Deliberately NOT the extraction client: extraction runs at temperature 0,
    # conversation does not. Sharing one client silently applied the extraction
    # temperature to participant-facing replies.
    Handler.replier = ReplyGenerator(client=client if args.dry_run else None)

    os.makedirs(SESSION_DIR, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    mode = "DRY RUN (no LLM calls)" if args.dry_run else f"model={getattr(client, 'model', '?')}"
    print(f"Study app running — {mode}")

    if args.host in ("0.0.0.0", "::"):
        print(f"  this machine:      http://127.0.0.1:{args.port}")
        lan = _lan_ip()
        if lan:
            print(f"  same network:      http://{lan}:{args.port}   <- give participants this")
        else:
            print("  same network:      (could not determine LAN address)")
        print("  NOTE: reachable by anyone on this network. Stop the server when not in use.")
    else:
        print(f"  http://{args.host}:{args.port}   (this machine only)")
        print("  for participants on other devices, restart with:  --host 0.0.0.0")

    print(f"  sessions saved to ./{SESSION_DIR}/   (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
