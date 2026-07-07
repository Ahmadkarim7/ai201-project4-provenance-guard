# Provenance Guard — Planning

> Status: Milestones 1 and 2 complete. Implementation begins in Milestone 3.

## 1. Detection Signals

I'm using two signals, and I picked them specifically because they fail in
different places — where one is blind, the other usually isn't.

**Signal 1: LLM Classifier (Groq, llama-3.3-70b-versatile).** I send the
submitted text to the model and ask it to judge, as a whole, whether the
writing reads as human or AI-generated, and to return that judgment as a
single float between 0 and 1 (0 = confidently human, 1 = confidently AI). This
is the "semantic" signal — it's reading for things like coherence, tone, and
whether the ideas actually hang together the way a person's would. Its output
is just a plain 0–1 float.

**Signal 2: Stylometric heuristics (pure Python, no external libraries).**
This one doesn't read meaning at all — it just measures the shape of the
writing. I'm computing three things:
- sentence length variance (AI text tends to keep sentences a pretty
  consistent length; people don't)
- type-token ratio, i.e. unique words divided by total words (a rough stand-in
  for vocabulary diversity)
- punctuation density (AI output tends to punctuate "correctly" and evenly;
  human writing is messier)

Each of those three gets normalized to a 0–1 scale and averaged together into
one structural score, also 0–1.

**Combining them.** I weight the LLM score higher than the stylometric score,
since it's actually reading the content rather than just its shape:

```
combined_score = (0.6 × llm_score) + (0.4 × stylometric_score)
```

The one adjustment I'm making on top of that: if the two signals disagree by
more than 0.35, I don't just average them — I pull the combined score toward
0.5 instead. My reasoning is that when a "reads like a person" judgment and a
"looks statistically uniform" judgment flatly contradict each other, that
disagreement is itself a signal that the system doesn't actually know what
it's looking at, and averaging it away would hide that instead of surfacing
it.

## 2. Uncertainty Representation

I'm using three bands for the combined score:

- **0.00–0.35** → likely human
- **0.35–0.65** → uncertain
- **0.65–1.00** → likely AI

So a score of 0.6 means the system is leaning toward "this might be
AI-generated" but not by enough margin to say so with any real confidence —
it should be treated as inconclusive, not as a soft version of an accusation.
That's actually the main design goal here: I don't want 0.51 and 0.95 to read
as basically the same "AI" verdict just because they're both above some
halfway line, so the uncertain band exists specifically to catch that middle
ground and label it honestly instead of forcing a binary call.

Since both signals already output values on a 0–1 scale, I'm not doing any
separate rescaling step beyond the weighted combination described above — the
main place calibration actually happens is the disagreement rule, which pulls
contradictory readings toward the uncertain middle rather than letting them
cancel out into a falsely confident average.

## 3. Transparency Label Design

Here's the exact text for each of the three variants. I wanted the wording to
stay calm and factual rather than accusatory, especially for the AI-leaning
label, since a false positive there could genuinely upset someone who wrote
the piece themselves.

| Variant | Exact label text |
|---|---|
| High-confidence AI | "This content is likely AI-generated (confidence: {score})." |
| High-confidence human | "This content appears to be human-written (confidence: {score})." |
| Uncertain | "We couldn't confidently determine whether this content is AI-generated or human-written (confidence: {score}). Treat this result as inconclusive." |

The `{score}` placeholder gets filled in with the actual combined confidence
value so a reader isn't just told a category — they can see how sure the
system actually is. The uncertain variant is deliberately the longest and
most hedged of the three, since that's the case where I most want to avoid
the label reading as a verdict.

## 4. Appeals Workflow

Any creator can appeal a classification on their own content by submitting
their `content_id` along with free-text reasoning explaining why they think
the result is wrong. There's no separate approval step to submit an appeal —
the bar for filing one should be low, since the whole point is giving people
a way to push back on a system that we already know isn't perfect.

When an appeal comes in, the system does three things: it changes that
content's status to `"under_review"`, it appends the appeal — the reasoning
text plus a timestamp — to the same audit log entry that holds the original
classification, and it sends back a confirmation to the creator that the
appeal was received.

If a human reviewer opened the appeal queue, what they'd see for each pending
appeal is: the original submitted text, both individual signal scores, the
combined confidence score, the label that was shown to the creator, and the
creator's own stated reasoning for the appeal, all in one place. I'm
deliberately not re-running detection automatically when an appeal comes in —
the whole reason someone is appealing is that they don't trust the automated
result, so re-running the same pipeline and getting the same answer wouldn't
actually resolve anything. A person needs to look at it.

## 5. Anticipated Edge Cases

Two specific cases I expect this system to handle badly:

1. **Formal, controlled human writing** — think academic or technical prose,
   or just a writer with a very disciplined, consistent style. The
   stylometric signal is going to read that consistency as a red flag, since
   it's exactly the kind of statistical uniformity that AI text also
   produces. This is the scenario I walked through in the architecture
   section — if the LLM signal doesn't push back hard enough in the other
   direction, this kind of writer could get an unfairly AI-leaning score.

2. **Very short submissions**, roughly under 50 words. Sentence length
   variance in particular needs multiple sentences to mean anything — on a
   two- or three-sentence submission, that metric is essentially noise, which
   makes the whole stylometric signal unreliable at that length. Short
   submissions are going to end up leaning more heavily on the LLM signal
   whether I intend that or not, just because the other signal has nothing
   real to measure.

## Architecture

### How a submission flows through the system

Here's what happens, step by step, when someone submits a piece of writing.

A creator sends their text to `POST /submit`, along with their creator ID. The
first thing the Flask app does is check whether they've hit the rate limit —
if they've submitted too many times too quickly, they get rejected right away
with a 429 and nothing else happens. If they're within the limit, the app
generates a unique `content_id` for this submission so it can be tracked and
referenced later (this is what the appeal endpoint will need).

The text then goes through two independent detection signals. The first is an
LLM classifier — we send the text to Groq and ask it to judge, holistically,
whether the writing reads as human or AI-generated. This signal is good at
picking up on things like semantic coherence and tone, but it can be swayed by
how the text is framed, and it doesn't look at structural patterns at all. The
second signal is a set of stylometric heuristics, computed with plain Python —
things like sentence length variance, vocabulary diversity, and punctuation
density. AI-generated text tends to be more uniform in these measures; human
writing tends to be messier and more variable. This signal has the opposite
blind spot: it can't understand meaning at all, and it will sometimes misread
a human writer who happens to write in a very consistent, controlled style.

Once both signals have produced a score, the confidence scorer combines them
into a single number between 0 and 1, along with an attribution verdict. That
combined score is handed to the label generator, which maps it to one of
three transparency labels — the label is what the reader on the platform
actually sees, so it needs to be honest about how confident the system really
is, not just a flat "AI" or "human" stamp.

Before anything is returned to the creator, the audit logger writes a
structured entry recording the content ID, both individual signal scores, the
combined confidence score, the label, and a timestamp. Only after that's
written does the app respond to the creator with the content ID, the
attribution result, the confidence score, and the label text.

### How an appeal flows through the system

If a creator believes they were misclassified, they submit a `POST /appeal`
with the `content_id` from their original submission and their own reasoning
for why they think the classification was wrong. The app looks up that
content ID, changes its status to `"under_review"`, and appends the appeal —
including the creator's reasoning — to the same audit log entry as the
original decision, so a reviewer can see the full history in one place. The
creator then gets back a confirmation that their appeal was received. We are
not automatically re-running detection on an appeal; a human is expected to
review it.

### Diagram

```
SUBMISSION FLOW
───────────────
Creator
  │  POST /submit { text, creator_id }
  ▼
Flask app ──── rate limiter check ────► [429 if exceeded, flow stops here]
  │  (ok) generates content_id
  ▼
┌─────────────────────┬──────────────────────────┐
│  Signal 1:           │  Signal 2:                │
│  LLM Classifier       │  Stylometric Heuristics   │
│  (Groq)                │  (pure Python)             │
│  → semantic score      │  → structural score        │
└──────────┬───────────┴───────────┬──────────────┘
           │                       │
           ▼                       ▼
        Confidence Scorer (combines both → single score + verdict)
                       │
                       ▼
              Label Generator (score → label text)
                       │
                       ▼
                Audit Logger (writes structured entry)
                       │
                       ▼
      Response to creator: { content_id, attribution, confidence, label }


APPEAL FLOW
───────────
Creator
  │  POST /appeal { content_id, creator_reasoning }
  ▼
Flask app looks up content_id
  │
  ▼
Status updated → "under_review"
  │
  ▼
Audit Logger appends appeal (reasoning + status change) to original entry
  │
  ▼
Response to creator: appeal received confirmation
```

### API surface (sketch)

- `POST /submit` — accepts `{text, creator_id}`, returns `{content_id, attribution, confidence, label}`
- `POST /appeal` — accepts `{content_id, creator_reasoning}`, returns a confirmation and the new status
- `GET /log` — returns the most recent structured audit log entries, for review and grading visibility

### Walking through a false positive

Say a human writer submits a piece with a very formal, controlled tone — no
slang, consistent sentence lengths, careful punctuation. The stylometric
signal might score this as AI-leaning, since it's reading uniformity as a red
flag. If the LLM classifier disagrees and reads the content as clearly human
(because it's picking up on specific personal detail or voice the heuristics
can't see), the combined score should land somewhere in the uncertain middle
range rather than confidently flagging it as AI — the two signals disagreeing
with each other is itself informative and should pull the score toward
uncertainty rather than being averaged away. The label the creator sees in
that case should be the "uncertain" variant, not "likely AI," and it should be
worded in a way that doesn't feel accusatory. If the creator still feels
misclassified, the appeal endpoint gives them a way to flag it for human
review rather than being stuck with the machine's verdict.

## AI Tool Plan

**Milestone 3 (submission endpoint + first signal).** I'll give the AI tool
the Detection Signals section above plus the architecture diagram, and ask it
to generate the Flask app skeleton with a `POST /submit` route stub, and
separately the LLM classifier function. Before wiring anything into the
endpoint, I'll call the classifier function directly with a few sample texts
and check the output is actually a 0–1 float that moves in a sensible
direction, not just trust that it works because it ran without errors.

**Milestone 4 (second signal + confidence scoring).** I'll provide the
Detection Signals section, the Uncertainty Representation section, and the
diagram, and ask for the stylometric function plus the scoring logic that
combines both signals. The important check here is making sure the generated
scoring function actually implements the 0.6/0.4 weighting and the
disagreement rule as written above, not some plausible-looking approximation
of it — I'll test it against the four calibration inputs from the spec and
confirm the thresholds line up with what I specified before trusting it.

**Milestone 5 (production layer).** I'll hand over the Transparency Label
Design section, the Appeals Workflow section, and the diagram, and ask for
the label-generation function plus the `/appeal` endpoint. I'll verify the
label function reproduces all three label strings exactly as written above
(not paraphrased versions of them), and I'll test the appeal endpoint
end-to-end — submit something, appeal it, then check `/log` to confirm the
status actually changed and the reasoning actually got recorded.