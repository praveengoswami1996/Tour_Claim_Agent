"""Identifies a bill by its bytes, so the same file is never sent to the OCR twice, whatever it is called.

Port of Tour_React src/lib/claimChatbot/fileHash.ts. The browser returned null where it had no crypto.subtle or
could not read the bytes; such a file cannot be compared, so it is treated as new. Here None comes back when the
bytes are missing or cannot be hashed.
"""

from __future__ import annotations

import hashlib


def hash_file(data: bytes | bytearray | memoryview | None) -> str | None:
    """fileHash.ts:hashFile: lowercase hex SHA-256 of the bytes, or None."""
    try:
        if data is None:
            return None
        return hashlib.sha256(bytes(data)).hexdigest()
    except Exception:
        return None
