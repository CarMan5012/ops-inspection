"""Persistent per-client login attempt limit.

Use the ASGI client address; this module does not parse forwarded IP headers.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import time

from app.db import connect
from app.settings import settings


WINDOW_SECONDS = 5 * 60
MAX_ATTEMPTS = 8
BLOCK_SECONDS = 5 * 60


def _client_key(client_host: str | None) -> str:
    return hmac.new(settings.secret_key.encode("utf-8"),
                    (client_host or "unknown").encode("utf-8"), hashlib.sha256).hexdigest()


def consume_login_attempt(client_host: str | None) -> int:
    """Reserve an attempt before password verification; return retry seconds if blocked."""
    now = time.time()
    key = _client_key(client_host)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM login_attempts WHERE window_start < ? AND blocked_until < ?",
            (now - WINDOW_SECONDS, now),
        )
        row = conn.execute(
            "SELECT window_start, attempts, blocked_until FROM login_attempts WHERE client_key = ?",
            (key,),
        ).fetchone()
        if row and row[2] > now:
            return max(1, math.ceil(row[2] - now))
        if row and now - row[0] < WINDOW_SECONDS:
            window_start, attempts = row[0], row[1] + 1
        else:
            window_start, attempts = now, 1
        blocked_until = now + BLOCK_SECONDS if attempts > MAX_ATTEMPTS else 0
        if row:
            conn.execute(
                """UPDATE login_attempts
                   SET window_start = ?, attempts = ?, blocked_until = ?
                   WHERE client_key = ?""",
                (window_start, attempts, blocked_until, key),
            )
        else:
            conn.execute(
                """INSERT INTO login_attempts (client_key, window_start, attempts, blocked_until)
                   VALUES (?, ?, ?, ?)""",
                (key, window_start, attempts, blocked_until),
            )
        return BLOCK_SECONDS if blocked_until else 0


def clear_login_attempts(client_host: str | None) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM login_attempts WHERE client_key = ?", (_client_key(client_host),))
