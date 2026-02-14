# core/db.py
import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "midjab_v2")
USE_MOCK = os.getenv("USE_MOCK", "0") == "1"

_client = None

def get_db():
    """
    Return a pymongo-like database object.
    Uses mongomock if USE_MOCK=1, otherwise a real MongoClient.
    Singleton-style: reuse _client for the process.
    """
    global _client
    if _client is None:
        if USE_MOCK:
            try:
                import mongomock
            except ImportError as e:
                raise RuntimeError("USE_MOCK=1 but mongomock not installed. pip install mongomock") from e
            _client = mongomock.MongoClient()
        else:
            from pymongo import MongoClient
            # Fail fast if server unreachable
            _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    return _client[MONGO_DB]

def init_indexes(background: bool = True):
    """
    Create required indexes for the jobs collection:
      - fingerprint (unique)
      - status (non-unique, queried frequently)
      - match_score (non-unique, queried/sorted frequently)

    background=True is recommended for non-unique indexes to avoid long blocking operations
    on large collections. Creating a unique index on fingerprint must be done carefully if
    the collection already has duplicates — in production ensure duplicates were cleaned or
    create the unique index with the 'dropDups' migration pattern (manual process).
    """
    db = get_db()
    jobs = db.jobs

    # Unique index for deduplication (fingerprint)
    try:
        jobs.create_index([("fingerprint", 1)], unique=True)
    except Exception as e:
        # We surface the exception so deploy-time tooling can log it.
        # In many envs this will be a no-op if index already exists.
        print("Warning creating fingerprint unique index:", e)

    # Non-unique indexes: create in background to avoid locking for large collections
    try:
        jobs.create_index([("status", 1)], background=background)
        jobs.create_index([("match_score", 1)], background=background)
    except TypeError:
        # Some backends (e.g., mongomock) may not accept background arg — fall back gracefully.
        jobs.create_index([("status", 1)])
        jobs.create_index([("match_score", 1)])

def test_connection():
    """
    Quick check that connection and simple write work.
    """
    db = get_db()
    print("Using DB:", db.name)
    try:
        if not USE_MOCK:
            info = _client.server_info()
            print("MongoDB server version:", info.get("version"))
    except Exception as e:
        print("Warning: could not fetch server_info:", repr(e))

    print("Collections before:", db.list_collection_names())
    res = db.test.insert_one({"ping": 1})
    print("Inserted test id:", res.inserted_id)
    db.test.delete_one({"_id": res.inserted_id})
    print("Connection and write test successful")
