import os
import uuid
from datetime import datetime, timezone

from flask import Flask, request, jsonify

from signals import get_llm_score, get_llm_score_dev_fallback, get_stylometric_score, combine_signals
from labels import generate_label
import rate_limit
import storage

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv not installed in this environment — fine for local
    # testing, just means .env won't be auto-loaded. Make sure it's
    # installed per requirements.txt for real use.
    pass

app = Flask(__name__)

# Rate limiting on /submit: 10 per minute, 100 per day, per remote address.
# See README.md for reasoning behind these specific numbers.
if rate_limit.FLASK_LIMITER_AVAILABLE:
    limiter = rate_limit.build_limiter(app)
    SUBMIT_RATE_LIMIT_DECORATOR = limiter.limit("10 per minute;100 per day")
else:
    SUBMIT_RATE_LIMIT_DECORATOR = rate_limit.dev_fallback_rate_limit(
        lambda req: req.remote_addr
    )

# If no real Groq key is configured, fall back to the dev placeholder signal
# so the rest of the pipeline can still be exercised locally. This is loudly
# logged so it's never mistaken for real detection output.
USE_DEV_FALLBACK = not os.environ.get("GROQ_API_KEY")
if USE_DEV_FALLBACK:
    print("WARNING: GROQ_API_KEY not set — using dev fallback signal, NOT real detection.")


@app.route("/submit", methods=["POST"])
@SUBMIT_RATE_LIMIT_DECORATOR
def submit():
    data = request.get_json(force=True)
    text = data.get("text")
    creator_id = data.get("creator_id")

    if not text or not creator_id:
        return jsonify({"error": "text and creator_id are required"}), 400

    content_id = str(uuid.uuid4())

    # Signal 1: LLM classifier (or dev fallback if no API key configured)
    if USE_DEV_FALLBACK:
        llm_result = get_llm_score_dev_fallback(text)
    else:
        try:
            llm_result = get_llm_score(text)
        except RuntimeError as e:
            return jsonify({"error": f"Detection signal failed: {e}"}), 502

    llm_score = llm_result["score"]

    # Signal 2: stylometric heuristics
    style_result = get_stylometric_score(text)
    stylometric_score = style_result["score"]

    # Combine both signals into a real confidence score + attribution
    scoring = combine_signals(llm_score, stylometric_score)
    confidence = scoring["confidence"]
    attribution = scoring["attribution"]

    # Real transparency label text, per planning.md
    label = generate_label(confidence, attribution)

    timestamp = datetime.now(timezone.utc).isoformat()

    record = {
        "content_id": content_id,
        "creator_id": creator_id,
        "text": text,
        "timestamp": timestamp,
        "llm_score": llm_score,
        "llm_reasoning": llm_result.get("reasoning", ""),
        "stylometric_score": round(stylometric_score, 3),
        "stylometric_components": style_result["components"],
        "signal_disagreement": scoring["disagreement"],
        "confidence": confidence,
        "attribution": attribution,
        "label": label,
        "status": "classified",
        "appeal": None,
    }
    storage.save_content_record(content_id, record)

    storage.append_log_entry({
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": timestamp,
        "attribution": attribution,
        "confidence": confidence,
        "llm_score": llm_score,
        "stylometric_score": round(stylometric_score, 3),
        "signal_disagreement": scoring["disagreement"],
        "status": "classified",
    })

    return jsonify({
        "content_id": content_id,
        "attribution": attribution,
        "confidence": confidence,
        "label": label,
    })


@app.route("/appeal", methods=["POST"])
def appeal():
    data = request.get_json(force=True)
    content_id = data.get("content_id")
    creator_reasoning = data.get("creator_reasoning")

    if not content_id or not creator_reasoning:
        return jsonify({"error": "content_id and creator_reasoning are required"}), 400

    record = storage.get_content_record(content_id)
    if record is None:
        return jsonify({"error": f"No content found with content_id {content_id}"}), 404

    timestamp = datetime.now(timezone.utc).isoformat()

    appeal_data = {
        "creator_reasoning": creator_reasoning,
        "timestamp": timestamp,
    }

    storage.update_content_record(content_id, {
        "status": "under_review",
        "appeal": appeal_data,
    })

    # Append the appeal to the audit log alongside the original decision,
    # so a reviewer can see the full history for this content_id.
    storage.append_log_entry({
        "content_id": content_id,
        "creator_id": record["creator_id"],
        "timestamp": timestamp,
        "status": "under_review",
        "appeal_reasoning": creator_reasoning,
        "original_attribution": record["attribution"],
        "original_confidence": record["confidence"],
    })

    return jsonify({
        "content_id": content_id,
        "status": "under_review",
        "message": "Appeal received and logged for human review."
    })


@app.route("/log", methods=["GET"])
def get_log():
    return jsonify({"entries": storage.get_log_entries()})


if __name__ == "__main__":
    app.run(debug=True, port=5050)