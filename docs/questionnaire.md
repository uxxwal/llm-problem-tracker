# Study questionnaire

Constructs and item wording, with the source each item is adapted from.

## Literature validation (verified 31 Jul via web search)

**The headline defence, if pushed on why THESE items:** the PARADISE framework
(Walker et al. 1997) evaluated task-oriented dialogue by regressing user
satisfaction on candidate predictors, and found that **task success and the
number of repetitions were the significant predictors of user satisfaction**.
A1 (repetition) and A4 (task progress) are therefore not intuitions — they are
the two measures the dialogue-evaluation literature empirically identified as
carrying satisfaction. arXiv: cmp-lg/9704004.

Per-construct anchors, all verified to exist:

- **A1, A2 (repetition / grounding persistence):** Clark & Brennan (1991),
  "Grounding in communication" — repair of grounding failure is costly in time
  and effort; PARADISE operationalises repetition count as a dialogue cost.
- **A3 (constraint adherence):** Xie et al. (2024), TravelPlanner — constraint
  satisfaction as the documented failure mode of language agents (0.6% final
  pass rate). Already in the bibliography.
- **A4 (task progress), A5 (effort), A6 (frustration):** NASA-TLX subscales
  (Hart & Staveland 1988) — performance, effort, frustration. Widely adapted
  in HCI, including for conversational agents.
- **A6, item style generally:** SASSI (Hone & Graham 2000), the validated
  questionnaire for speech/dialogue interfaces (34 items, six factors) — its
  **Annoyance** factor is defined by users rating the system as *repetitive,
  boring, irritating, frustrating*, and its System Response Accuracy factor
  covers whether the system does what the user intended. Natural Language
  Engineering 6(3–4).
- **B1 (paired forced choice):** standard paired-comparison design; analysed
  with sign/binomial test (nonparametric, valid at small N — Siegel 1956).
- **C-block wording (faults not agreement):** acquiescence/yea-saying bias is
  established survey methodology (Schriesheim & Hill 1981; GESIS survey
  guidelines). NOTE an honest nuance: deliberately reverse-wording items to
  fight acquiescence has mixed evidence (Sonderen et al. 2013, PLOS ONE —
  reversed items can add noise via inattention). A1/A5/A6 are negatively
  worded because their constructs are inherently negative (repetition, effort,
  frustration are the natural phrasings, matching TLX and SASSI), NOT as
  artificial reversals — say it that way if asked.
- **Free-text analysis:** Braun & Clarke (2006) thematic analysis. (Standard;
  verify the exact reference when citing.)

**Adaptation caveat stands:** items are adapted from these instruments, not
administered as the instruments; phrase as "adapted from" and do not claim the
source's psychometric validation for the adapted form.

**Two standing rules.**
1. No mentalistic language. The supervisor stopped a draft item containing
   "which assistant understood your problem better" — there is no such thing as
   understanding here, and it is not measurable. Every item below describes
   *observable behaviour* or the participant's *own experience*.
2. Items are ADAPTED, not administered. Say "adapted from" in the methodology;
   do not claim the validated properties of the source instrument.

---

## Block A — after each conversation (asked twice, once per assistant)

Seven-point Likert, "Strongly disagree" to "Strongly agree". Order fixed across
both arms so the pairing holds. Items 1, 5 and 6 are negatively worded on
purpose, to break agreement bias.

| # | Item | Construct | Adapted from |
|---|------|-----------|--------------|
| A1 | I had to repeat or restate things I had already told the assistant. | **Repair burden** — the central prediction: an explicit representation should reduce re-grounding | grounding / repair in communication (Clark & Brennan) |
| A2 | Information I gave earlier appeared in the assistant's later replies. | **Grounding persistence** (behavioural, not mentalistic) | common ground maintenance |
| A3 | The assistant's suggestions respected the limits I had set. | **Constraint adherence** — directly comparable to the constraint-satisfaction failures reported for language agents | TravelPlanner-style constraint satisfaction (Xie et al.) |
| A4 | By the end of the conversation I was closer to a plan I could act on. | **Perceived task progress** | perceived task success / performance (NASA-TLX performance subscale) |
| A5 | I had to work hard to keep the conversation on track. | **Effort** | NASA-TLX effort subscale (Hart & Staveland) |
| A6 | I felt frustrated during this conversation. | **Frustration** | NASA-TLX frustration subscale (Hart & Staveland) |

Then one open item:

> **A7.** What, if anything, did this assistant handle badly? *(free text)*

Six Likert items plus one open is roughly two minutes — acceptable twice.

---

## Block B — after both conversations (asked once)

**B1 (PRIMARY OUTCOME, forced choice, no neutral option):**
> If you had a task like this to do for real, which assistant would you rather
> use?  ☐ Assistant 1  ☐ Assistant 2

**B2:** Why? *(free text — this carries the explanation; B1 only gives direction)*

**B3 (forced choice):** Which assistant did you have to repeat yourself to less?
☐ Assistant 1 ☐ Assistant 2

**B4 (free text):** Was there anything either assistant forgot, or got wrong,
that you had already told it?

---

## Block C — after the representation is revealed (schema session only)

Shown on the app's end screen. Wording matters: "do you agree?" invites
agreement (acquiescence bias), and a vague summary is impossible to disagree
with (Barnum effect). Ask for faults instead, and prompt separately for
omissions, because people notice wrong items far more readily than missing ones.

> **C1.** Looking at this summary, is anything **wrong** — something it says that
> you did not say, or that misstates what you meant? *(free text)*
>
> **C2.** Is anything **missing** — something you told the assistant that does
> not appear here? *(free text)*
>
> **C3.** Overall, how well does this summary match the problem you were actually
> working on? *(1 = not at all, 7 = completely)*

C1 and C2 are coded into counts of errors and omissions per participant, giving
a per-element accuracy measure for RQ2 that needs no hand-written gold standard.

---

## How each block is analysed

| RQ | Measure | Test |
|----|---------|------|
| RQ3 (utility) | B1 forced-choice preference | sign / binomial test on paired preferences — valid at N ≈ 20 |
| RQ3 | A1–A6, paired within participant | Wilcoxon signed-rank per item; report descriptively, no correction claims at this N |
| RQ3 | A7, B2, B4 free text | thematic coding |
| RQ2 (maintenance) | C1, C2 counts; C3 rating | descriptive; triangulated against the transcript |
| RQ2 | failure events in the STG (extraction_failed, validation_skip, overlap_detected) | rates computed directly from recorded sessions |

Paired non-parametric tests are the right family here: they make no normality
assumption and remain legitimate at this sample size, whereas comparing group
means between different people would not be.

---

## Pre-declared before data collection

- **Primary outcome:** B1 preference. Everything else is secondary.
- **Scenario-wise comparison** will be reported only if N ≥ 16; below that the
  cells are too thin to support a claim.
- Declaring this in advance is what separates dropping an underpowered analysis
  from selecting the flattering one after seeing the data.

---

## Deliberately NOT asked

- Anything phrased as the assistant "understanding", "knowing", or "caring".
- Whether the participant *noticed* a difference in mechanism — the study tests
  whether the representation helps, not whether people can detect it.
- General chatbot usability (SUS, BUS-11 and similar). Both arms use the same
  model and interface, so usability is held constant by design and measuring it
  spends participant patience for nothing.
