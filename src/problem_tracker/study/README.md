# Running the user study

## Remote / asynchronous deployment

The app is a single standard-library server, so it runs on any host with
Python. Two routes:

**Quick (link alive only while your laptop runs):** Cloudflare quick tunnel —
no account needed.

```bash
brew install cloudflared
python study/app.py --host 127.0.0.1 &      # with .env configured
cloudflared tunnel --url http://127.0.0.1:8000
```

`cloudflared` prints a public `https://….trycloudflare.com` URL — that's what
you send participants. Laptop must stay awake and online; the URL changes every
restart. Good for a scheduled batch, bad for "do it whenever this week".

**Proper (always on): Render free tier.**

1. Push the repo to GitHub (private). `.env` and `sessions/` are gitignored —
   check `git status` shows neither.
2. render.com → New → Web Service → connect the repo.
   - Runtime: Python. Build command: (leave empty / `pip install openai`)
   - Start command: `python study/app.py --host 0.0.0.0`
3. In the service's Environment tab set: `LLM_BASE_URL`, `LLM_API_KEY`,
   `LLM_MODEL`, `STUDY_ACCESS_CODE`, `ADMIN_CODE`.
4. Send participants the Render URL + their participant number + the study
   code. Warn them the first page load can take ~1 minute (free tier sleeps
   when idle).

**Render's disk is ephemeral — session files vanish on every redeploy or
restart.** Download the data after every participant (or at least daily):

```
https://<your-app>.onrender.com/export?code=<ADMIN_CODE>
```

That returns every recorded session as one JSON bundle. Keep the exports; they
are your evaluation corpus.

### Async protections built in

- `STUDY_ACCESS_CODE` — participants must enter it; strangers can't burn the
  API key.
- Autosave — every turn is written to disk as it happens, so a server restart
  mid-conversation loses nothing already said.
- `/export?code=<ADMIN_CODE>` — pull all data off the host.

### Async invitation must carry

participant number (assign 1, 2, 3, … in order), the study code, the app URL,
and the questionnaire link — with consent as the form's first page, before the
app link appears.


## Rehearse (no LLM calls, no cost)

```bash
python study/app.py --dry-run
```

Open <http://127.0.0.1:8000>, enter participant number `1`, and walk the whole
flow. Do this at least once before a real participant.

## Run for real

```bash
export LLM_BASE_URL="<ELM endpoint>"
export LLM_API_KEY="<key>"
export LLM_MODEL="<model name>"
python study/app.py
```

Sessions are written to `sessions/<session_id>.json`. Inspect one with:

```bash
python tools/view_stg.py sessions/<file>.json
```

## Per participant

1. Give them the participant information sheet and consent form. **Record the
   participant number yourself** — it drives condition assignment.
2. Open the app, enter the number, hand over the keyboard.
3. They read the scenario and talk to Assistant 1 (10 messages).
4. At the end they get a **completion code** — they enter it in the
   questionnaire, which links their form responses to the transcript.
5. They complete the questionnaire for Assistant 1.
6. Click through to Assistant 2, repeat.
7. Comparison questions.
8. For whichever session was the schema arm, the app displays the maintained
   problem description on the end screen: ask them what is **wrong, missing, or
   never said**. (Do not ask "do you agree" — that just gets agreement.)

## Conditions

Assignment is deterministic from the participant number, cycling through four
groups so that each scenario is seen with each arm equally often, and each arm
appears first equally often. Just number participants 1, 2, 3, … in order.

| pid mod 4 | Session 1 | Session 2 |
|---|---|---|
| 1 | S1 baseline | S2 schema |
| 2 | S1 schema | S2 baseline |
| 3 | S2 baseline | S1 schema |
| 0 | S2 schema | S1 baseline |

Participants only ever see "Assistant 1" and "Assistant 2". The arm never
appears in the interface, the page source, or the URL.

## What differs between arms

Exactly one thing: in the schema arm the maintained problem description is
injected into the reply prompt as an extra system message. Same model, same
system prompt, same conversation history otherwise — verified by
`tests/test_study.py::test_arms_differ_only_by_the_injected_block`.

The baseline arm still records a transcript (so both sessions are analysable),
but runs no extraction and maintains no state.

## Files

- `study/app.py` — the server and UI (standard library only)
- `study/reply.py` — reply generation and the state-injection rendering
- `study/scenarios.py` — the two scenarios and the assignment rotation
