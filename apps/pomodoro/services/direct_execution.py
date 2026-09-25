import hashlib
import json


def payload_hash(payload: dict) -> str:
    """Stable hash shared by every direct-execution idempotency contract."""
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(encoded).hexdigest()
