# Provenance Guard

A backend system that classifies submitted text as likely AI-generated,
likely human-written, or uncertain — with confidence scoring, a transparency
label, an appeals workflow, rate limiting, and a structured audit log. Built
for a hypothetical creative-writing platform that wants to give readers
honest context about authorship, without pretending AI detection is a solved
problem.

## Architecture overview

When a piece of writing is submitted to `POST /submit`, it first passes
through a rate limiter — if the sender has gone over the limit, the request
is rejected with a 429 and nothing else happens. Otherwise, a unique
`content_id` is generated and the text is run through two independent
detection signals: an LLM classifier (Groq) that reads the text holistically
for semantic and stylistic cues, and a set of stylometric heuristics (pure
Python) that measure structural properties like sentence length variance,
vocabulary diversity, and punctuation density.

The two signal scores are combined into a single confidence score. If the
signals disagree by more than 0.35, the combined score is pulled toward 0.5
rather than averaged normally, since strong disagreement between a semantic
read and a structural read is itself a sign the system doesn't confidently
know what it's looking at. That confidence score maps to one of three
transparency labels, which is what the reader on the platform actually sees.
Every decision — both signal scores, the combined confidence, and the label —
is written to a structured audit log before the response is returned.

If a creator disagrees with their classification, they can call `POST
/appeal` with their `content_id` and their own reasoning. The system updates
that content's status to `under_review` and appends the appeal to the same
audit log entry as the original decision, so a human reviewer can see the
full history in one place. No automatic re-classification happens on appeal —
the whole point of an appeal is that the creator doesn't trust the automated
result, so a person needs to look at it.

## Detection signals

**Signal 1 — LLM Classifier (Groq, llama-3.3-70b-versatile).** The submitted
text is sent to the model with a prompt asking it to judge, holistically,
whether the writing reads as human or AI-generated, returned as a float
between 0 and 1. This signal is good at picking up on semantic coherence,
tone, and specificity of detail — the kind of judgment that comes from
actually reading the content. Its blind spot is that it can be swayed by how
the text is framed, and it doesn't look at structural patterns at all.

**Signal 2 — Stylometric heuristics (pure Python).** Three measurable
properties are computed and averaged into a single structural score: sentence
length variance (AI text tends to be more uniform), type-token ratio (a proxy
for vocabulary diversity), and punctuation density. This signal's blind spot
is the mirror image of the first one — it can't read meaning at all, and it
will sometimes misread a human writer who happens to write in a very
consistent, controlled style.

I chose this pairing specifically because the two signals fail in different
places. Where the LLM signal is blind (structure), the heuristics usually
aren't, and vice versa.

**Combining them:**
```
combined_score = (0.6 × llm_score) + (0.4 × stylometric_score)
```
weighted toward the LLM signal since it's actually reading the content. If
the two signals disagree by more than 0.35, the result is pulled toward 0.5
instead of averaged normally.

## Confidence scoring

Score bands:
- **0.00–0.35** → likely human
- **0.35–0.65** → uncertain
- **0.65–1.00** → likely AI

To validate that the scoring produces meaningful variation rather than a
constant, I ran it against the four calibration inputs from the project spec,
using the real Groq model (not a placeholder):

**High-confidence case — clearly human-written text** ("ok so i finally
tried that new ramen place downtown and honestly? underwhelming...")
- `llm_score: 0.2`, `stylometric_score: 0.257`
- **Combined confidence: 0.223 → likely_human**

**Lower-confidence case — clearly AI-generated text** ("Artificial
intelligence represents a transformative paradigm shift in modern
society...")
- `llm_score: 0.8`, `stylometric_score: 0.36`
- **Combined confidence: 0.562 → uncertain**

These two examples alone show the scoring is doing real work — the casual
human text and the formal AI-flavored text land almost 0.34 apart, in the
directions you'd expect. The second example also surfaces a genuine
limitation, discussed below.

## Transparency label

| Variant | Exact label text |
|---|---|
| High-confidence AI | "This content is likely AI-generated (confidence: {score})." |
| High-confidence human | "This content appears to be human-written (confidence: {score})." |
| Uncertain | "We couldn't confidently determine whether this content is AI-generated or human-written (confidence: {score}). Treat this result as inconclusive." |

`{score}` is filled in as a rounded percentage (e.g. `56%`) so a
non-technical reader sees how confident the system actually is, not just a
category. Real examples pulled directly from the running system:

- `"This content appears to be human-written (confidence: 22%)."`
- `"We couldn't confidently determine whether this content is AI-generated or human-written (confidence: 56%). Treat this result as inconclusive."`

## Appeals workflow

Any creator can appeal their own content by submitting `content_id` +
`creator_reasoning`. Real end-to-end test:

**Request:**
```json
POST /appeal
{"content_id": "4a56a2f6-53a6-4717-b2e8-8b930bf08234", "creator_reasoning": "I wrote this myself."}
```

**Resulting audit log entry:**
```json
{
  "content_id": "4a56a2f6-53a6-4717-b2e8-8b930bf08234",
  "creator_id": "test-user-1",
  "timestamp": "2026-07-07T02:17:14.788006+00:00",
  "status": "under_review",
  "appeal_reasoning": "I wrote this myself.",
  "original_attribution": "uncertain",
  "original_confidence": 0.514
}
```

The status updated to `under_review`, and the reasoning was logged alongside
the original decision, giving a reviewer everything they'd need in one place.

## Rate limiting

`/submit` is limited to **10 requests per minute** per IP address, via
Flask-Limiter with in-memory storage. I chose this by thinking about
realistic usage: a writer submitting their own work isn't going to hit
`/submit` more than a handful of times in a minute, even if they're
resubmitting edited drafts. 10/minute comfortably covers that while making it
impractical for a script to flood the endpoint. Real captured evidence,
sending 12 rapid requests in a row:

```
200
200
200
200
200
200
200
200
200
200
429
429
```

Exactly 10 succeeded before the limiter kicked in, matching the configured
limit.

## Audit log

Every submission and appeal writes a structured JSON entry (see `audit_log.json`,
served via `GET /log`). Real entries pulled from the running system:

```json
{
  "attribution": "likely_human",
  "confidence": 0.223,
  "content_id": "6723af0b-3020-4161-8785-c0df376a92ea",
  "creator_id": "calibration-2",
  "llm_score": 0.2,
  "signal_disagreement": false,
  "status": "classified",
  "stylometric_score": 0.257,
  "timestamp": "2026-07-07T02:28:14.913981+00:00"
},
{
  "attribution": "uncertain",
  "confidence": 0.562,
  "content_id": "efda00f6-f717-49eb-a954-8e307d6b4673",
  "creator_id": "calibration-1",
  "llm_score": 0.8,
  "signal_disagreement": true,
  "status": "classified",
  "stylometric_score": 0.36,
  "timestamp": "2026-07-07T02:28:04.466858+00:00"
},
{
  "content_id": "4a56a2f6-53a6-4717-b2e8-8b930bf08234",
  "creator_id": "test-user-1",
  "timestamp": "2026-07-07T02:17:14.788006+00:00",
  "status": "under_review",
  "appeal_reasoning": "I wrote this myself.",
  "original_attribution": "uncertain",
  "original_confidence": 0.514
}
```

## Known limitations

**Clearly AI-generated text can still land as "uncertain" instead of "likely
AI," if the stylometric signal disagrees with the LLM signal by enough
margin.** This is a real result from testing, not a hypothetical: on the
clearly-AI calibration text, the LLM scored it at 0.8 (fairly confident it's
AI), but the stylometric heuristics only scored it at 0.36, because that
particular text didn't happen to have very uniform sentence lengths. Because
the two signals disagreed by more than 0.35, the disagreement rule pulled the
combined score down to 0.562 — "uncertain" — even though the LLM alone was
fairly confident. The disagreement rule exists specifically to protect
against false positives, but this shows it has a real cost: it can also soften
a correct high-confidence call. I'd rather have that tradeoff than the
alternative, but it's a genuine limitation worth naming.

**Very short submissions (roughly under 50 words) make the stylometric signal
unreliable.** Sentence length variance in particular needs multiple sentences
to mean anything; on a two- or three-sentence submission, that metric is
essentially noise, which pushes the whole pipeline to lean more heavily on
the LLM signal than intended.

## Spec reflection

Writing `planning.md` before any code — specifically deciding the exact label
text and the confidence thresholds up front — genuinely paid off. When I sat
down to write the label-generation function in Milestone 5, there was no
ambiguity to resolve; I just implemented what I'd already committed to in
Milestone 2, which made that part of the build fast and mistake-free.

Where implementation diverged from the spec: I originally designed the
punctuation-density heuristic around a "typical band" of values, on the
theory that both unusually high and unusually low density would signal
human writing. When I tested it against the four calibration inputs, I found
it was compressing nearly all real text into the same score regardless of
direction, which hid the signal instead of revealing it. I rewrote it as a
simple linear scale instead. The spec didn't dictate the internal shape of
that heuristic, so this was a case of the plan being right at the level it
specified (three stylometric properties, combined into one score) while an
implementation detail underneath it needed correction once tested against
real data.

## AI usage

1. **Punctuation heuristic bug.** I asked the AI tool to generate the
   stylometric scoring functions from the Detection Signals section of
   `planning.md`. The first version of the punctuation-density function used
   a "typical band" scoring approach that looked reasonable but, when tested
   against the four calibration inputs, produced nearly identical scores for
   clearly-AI and clearly-human text — it was hiding the signal instead of
   surfacing it. I had the AI tool rewrite it as a simple linear scale
   instead, and re-tested to confirm the scores actually separated in the
   right direction before moving on.

2. **Label and appeal endpoint generation.** For Milestone 5, I gave the AI
   tool the Transparency Label Design and Appeals Workflow sections of
   `planning.md` and asked it to generate the label-mapping function and the
   `/appeal` endpoint. I checked the generated label function against the
   exact strings written in `planning.md` (not paraphrased versions of them),
   and tested the appeal endpoint end-to-end — submitting content, appealing
   it, then checking `/log` to confirm the status change and reasoning were
   actually recorded — before considering it done.