"""公网 OCR 简易限流。"""

from __future__ import annotations

import os
import time

_hits: dict[str, list[float]] = {}


def ocr_rate_limit_per_hour() -> int:
    return int(os.environ.get("FIREMIND_OCR_RATE_LIMIT_PER_HOUR", "48"))


def allow_ocr(client_key: str) -> tuple[bool, int]:
    limit = ocr_rate_limit_per_hour()
    now = time.time()
    bucket = _hits.setdefault(client_key, [])
    bucket[:] = [t for t in bucket if now - t < 3600.0]
    remaining = max(0, limit - len(bucket))
    if len(bucket) >= limit:
        return False, 0
    bucket.append(now)
    return True, remaining - 1
