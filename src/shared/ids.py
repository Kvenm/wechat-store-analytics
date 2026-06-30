from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any


def stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )


def fingerprint(*parts: Any) -> str:
    payload = stable_json(parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
