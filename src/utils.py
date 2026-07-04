"""Misc helpers."""
from __future__ import annotations

import secrets
import string


def gen_referral_code(length: int = 8) -> str:
    """Short alphanumeric, lowercase. Telegram start_param accepts a–z, 0–9 and _."""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def gen_api_token(length: int = 40) -> str:
    return secrets.token_urlsafe(length)[:length]
