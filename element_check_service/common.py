import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import os


class ServiceError(Exception):
    def __init__(self, status, code, message=None, **details):
        self.status, self.code = status, code
        self.message = message or code
        self.details = details
        super().__init__(self.message)


def stamp(value):
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def now():
    return datetime.now(timezone.utc)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def fingerprint(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def sync_dir(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_json(path, value):
    path = Path(path)
    with path.open("wb") as stream:
        stream.write(encoded(value))
        stream.flush()
        os.fsync(stream.fileno())
    sync_dir(path.parent)
