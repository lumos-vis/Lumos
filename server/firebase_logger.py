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
                   "connected_at", "disconnected_at", "participant_id_source")}
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


def save_priors(pid: str, priors: dict):
    """Write each prior belief as a Firestore document keyed by attribute name."""
    db = _get_db()
    if db is None:
        return
    if not priors:
        return
    try:
        priors_ref = _participant_ref(db, pid).collection("priors")
        batch = db.batch()
        for attribute, belief in priors.items():
            doc = priors_ref.document(attribute)
            batch.set(doc, _sanitize(belief))
        batch.commit()
        print(f"[firebase_logger] Saved {len(priors)} priors for {pid}.")
    except Exception as e:
        print(f"[firebase_logger] save_priors error: {e}")
