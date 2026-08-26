"""Firebase Firestore logger for participant data.

Isolated from the main server so the original disk-saving logic is untouched.
All functions are no-ops when Firebase is not configured (GOOGLE_APPLICATION_CREDENTIALS
env var or FIREBASE_SERVICE_ACCOUNT_JSON env var not set), so the server starts
normally in local dev without any credentials.

Firestore structure:
  participants/{participantId}/
    meta          (document)  – app_mode, app_type, app_level, connected_at, …
    logs          (collection) – one document per interaction (response_list entries)
    priors        (collection) – one document per attribute prior belief
"""

import json
import os
import numpy as np

_db = None  # module-level Firestore client; None = not initialised


def _get_db():
    global _db
    if _db is not None:
        return _db

    try:
        import firebase_admin
        from firebase_admin import credentials, firestore

        # Support two ways to supply credentials:
        # 1. GOOGLE_APPLICATION_CREDENTIALS pointing to a JSON key file (standard ADC)
        # 2. FIREBASE_SERVICE_ACCOUNT_JSON containing the JSON content as a string
        #    (convenient for Heroku config vars where you can't upload files)
        sa_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
        if sa_json:
            cred = credentials.Certificate(json.loads(sa_json))
        elif os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            cred = credentials.ApplicationDefault()
        else:
            print("[firebase_logger] No credentials found – Firebase logging disabled.")
            return None

        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)

        _db = firestore.client()
        print("[firebase_logger] Connected to Firestore.")
        return _db

    except Exception as e:
        print(f"[firebase_logger] Init failed, Firebase logging disabled: {e}")
        return None


class _Encoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def _sanitize(obj):
    """Force everything to plain Python types via JSON round-trip."""
    return json.loads(json.dumps(obj, cls=_Encoder))


def _participant_ref(db, pid):
    return db.collection("participants").document(pid)


def save_meta(pid: str, client_record: dict):
    """Upsert top-level participant metadata."""
    db = _get_db()
    if db is None:
        return
    try:
        fields = {k: client_record.get(k) for k in
                  ("participant_id", "app_mode", "app_type", "app_level",
                   "connected_at", "disconnected_at", "participant_id_source",
                   "priors_committed_at")}
        _participant_ref(db, pid).set(fields, merge=True)
    except Exception as e:
        print(f"[firebase_logger] save_meta error: {e}")


def save_logs(pid: str, response_list: list):
    """Write all interaction log entries as individual Firestore documents."""
    db = _get_db()
    if db is None:
        return
    try:
        logs_ref = _participant_ref(db, pid).collection("logs")
        batch = db.batch()
        for entry in response_list:
            doc = logs_ref.document()
            batch.set(doc, _sanitize(entry))
        batch.commit()
        print(f"[firebase_logger] Saved {len(response_list)} log entries for {pid}.")
    except Exception as e:
        print(f"[firebase_logger] save_logs error: {e}")


def save_selected_subjects(pid: str, subjects: list):
    """Upsert the participant's selected subjects list."""
    db = _get_db()
    if db is None:
        return
    try:
        _participant_ref(db, pid).set({"selected_subjects": _sanitize(subjects)}, merge=True)
        print(f"[firebase_logger] Saved {len(subjects)} selected subjects for {pid}.")
    except Exception as e:
        print(f"[firebase_logger] save_selected_subjects error: {e}")


def save_task_submission(pid: str, verification_code: str, subjects: list, submitted_at=None,
                          elicitation_started_at=None, elicitation_ended_at=None,
                          elicitation_duration_ms=None, elicitation_engagement=None,
                          main_task_started_at=None, main_task_ended_at=None,
                          main_task_duration_ms=None, main_engagement=None):
    """Record that the participant submitted the main task, with their verification code.

    main_engagement / elicitation_engagement are "low" (spent under the
    MIN_MAIN_TASK_MS/MIN_ELICITATION_MS threshold) or "satisfactory" (met it) --
    replaces the old opaque insufficient_duration boolean.
    """
    db = _get_db()
    if db is None:
        return
    try:
        fields = {
            "task_submitted": True,
            "verification_code": verification_code,
            "submitted_subjects": _sanitize(subjects),
            "submitted_at": submitted_at,
            "main_task_started_at": main_task_started_at,
            "main_task_ended_at": main_task_ended_at,
            "main_task_duration_ms": main_task_duration_ms,
            "main_engagement": main_engagement,
        }
        # Only set when this submission actually measured it (combined-flow
        # sessions, where elicitation happened right before the main task in
        # the same visit) -- a /main-only visit has no elicitation phase and
        # would otherwise overwrite the values save_elicitation_submission
        # wrote from an earlier, separate /elicitation visit.
        if elicitation_duration_ms is not None:
            fields["elicitation_started_at"] = elicitation_started_at
            fields["elicitation_ended_at"] = elicitation_ended_at
            fields["elicitation_duration_ms"] = elicitation_duration_ms
            fields["elicitation_engagement"] = elicitation_engagement
        _participant_ref(db, pid).set(fields, merge=True)
        print(f"[firebase_logger] Saved task submission for {pid}: {verification_code}")
    except Exception as e:
        print(f"[firebase_logger] save_task_submission error: {e}")


def save_elicitation_submission(pid: str, verification_code: str, submitted_at=None,
                                 elicitation_started_at=None, elicitation_ended_at=None,
                                 elicitation_duration_ms=None, elicitation_engagement=None):
    """Record that the participant finished the elicitation-only phase, with its own
    verification code -- kept separate from save_task_submission's (main-task) fields
    so a participant who does /elicitation and /main as two visits gets both recorded
    on the same participant document."""
    db = _get_db()
    if db is None:
        return
    try:
        _participant_ref(db, pid).set({
            "elicitation_submitted": True,
            "elicitation_verification_code": verification_code,
            "elicitation_submitted_at": submitted_at,
            "elicitation_started_at": elicitation_started_at,
            "elicitation_ended_at": elicitation_ended_at,
            "elicitation_duration_ms": elicitation_duration_ms,
            "elicitation_engagement": elicitation_engagement,
        }, merge=True)
        print(f"[firebase_logger] Saved elicitation submission for {pid}: {verification_code}")
    except Exception as e:
        print(f"[firebase_logger] save_elicitation_submission error: {e}")


def save_refresh_event(pid: str, timestamp):
    """Append a page-refresh event for the participant."""
    db = _get_db()
    if db is None:
        return
    try:
        _participant_ref(db, pid).collection("refresh_events").add({"timestamp": timestamp})
        print(f"[firebase_logger] Logged refresh event for {pid} at {timestamp}")
    except Exception as e:
        print(f"[firebase_logger] save_refresh_event error: {e}")


def save_priors(pid: str, priors: dict):
    """Write each prior belief as its own Firestore document."""
    db = _get_db()
    if db is None:
        return
    if not priors:
        return
    try:
        priors_ref = _participant_ref(db, pid).collection("priors")
        for key, belief in priors.items():
            doc_id = key.replace("/", "_").replace(":", "_")
            print(f"[firebase_logger] Writing prior '{doc_id}' for {pid}")
            priors_ref.document(doc_id).set(_sanitize(belief))
        print(f"[firebase_logger] Saved {len(priors)} prior(s) for {pid}.")
    except Exception as e:
        print(f"[firebase_logger] save_priors error: {e}")


def load_priors(pid: str) -> dict:
    """Read this participant's previously-committed prior beliefs back out of
    Firestore, keyed the same way as CLIENTS[pid]["priors"] in server.py
    ("{attribute}::{condition}" -> belief dict). Used to restore that state
    for a session that starts in a different visit/server process than the
    one where elicitation actually happened (the /elicitation + /main split
    flow) -- without this, a /main-only session has no priors and dc_map is
    never computed."""
    db = _get_db()
    if db is None:
        return {}
    try:
        priors = {}
        for doc in _participant_ref(db, pid).collection("priors").stream():
            belief = doc.to_dict()
            attribute = belief.get("attribute")
            condition = belief.get("condition", "default")
            if attribute:
                priors[f"{attribute}::{condition}"] = belief
        if priors:
            print(f"[firebase_logger] Loaded {len(priors)} prior(s) for {pid} from Firestore.")
        return priors
    except Exception as e:
        print(f"[firebase_logger] load_priors error: {e}")
        return {}
