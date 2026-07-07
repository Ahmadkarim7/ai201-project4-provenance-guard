"""
Simple JSON-file-backed storage for Provenance Guard.

Two things live here:
- content_store: one record per submission, keyed by content_id (needed by
  the appeal endpoint to look up what's being appealed)
- audit_log: an append-only structured log of every decision and appeal

Using plain JSON files instead of SQLite for now to keep this readable and
easy to inspect by hand during development. Documented as a deliberate
choice, not an oversight.
"""

import json
import os
import threading

_LOCK = threading.Lock()

CONTENT_STORE_PATH = os.path.join(os.path.dirname(__file__), "content_store.json")
AUDIT_LOG_PATH = os.path.join(os.path.dirname(__file__), "audit_log.json")


def _read_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)


def _write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def save_content_record(content_id: str, record: dict):
    with _LOCK:
        store = _read_json(CONTENT_STORE_PATH)
        store[content_id] = record
        _write_json(CONTENT_STORE_PATH, store)


def get_content_record(content_id: str):
    store = _read_json(CONTENT_STORE_PATH)
    return store.get(content_id)


def update_content_record(content_id: str, updates: dict):
    with _LOCK:
        store = _read_json(CONTENT_STORE_PATH)
        if content_id not in store:
            return None
        store[content_id].update(updates)
        _write_json(CONTENT_STORE_PATH, store)
        return store[content_id]


def append_log_entry(entry: dict):
    with _LOCK:
        log = _read_json(AUDIT_LOG_PATH)
        entries = log.get("entries", [])
        entries.append(entry)
        _write_json(AUDIT_LOG_PATH, {"entries": entries})


def get_log_entries(limit: int = 50):
    log = _read_json(AUDIT_LOG_PATH)
    entries = log.get("entries", [])
    return entries[-limit:]
