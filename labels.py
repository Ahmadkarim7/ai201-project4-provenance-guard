"""
Transparency label generation for Provenance Guard.

Exact label text as specified in planning.md (Milestone 2, Question 3).
These strings are the canonical wording — do not paraphrase them elsewhere.
"""

LABEL_HIGH_CONFIDENCE_AI = "This content is likely AI-generated (confidence: {score})."
LABEL_HIGH_CONFIDENCE_HUMAN = "This content appears to be human-written (confidence: {score})."
LABEL_UNCERTAIN = (
    "We couldn't confidently determine whether this content is AI-generated "
    "or human-written (confidence: {score}). Treat this result as inconclusive."
)


def generate_label(confidence: float, attribution: str) -> str:
    """
    Maps a confidence score + attribution verdict to the exact transparency
    label text a reader would see, with the confidence score formatted as
    a whole-number percentage for non-technical readability
    (e.g. 0.78 -> "78%").
    """
    score_str = f"{round(confidence * 100)}%"

    if attribution == "likely_ai":
        return LABEL_HIGH_CONFIDENCE_AI.format(score=score_str)
    elif attribution == "likely_human":
        return LABEL_HIGH_CONFIDENCE_HUMAN.format(score=score_str)
    else:
        return LABEL_UNCERTAIN.format(score=score_str)
