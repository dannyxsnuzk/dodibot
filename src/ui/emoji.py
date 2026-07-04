"""Premium emoji helper.

Two places use the registry in ``assets/premium_emojis.json``:

* HTML message bodies, via the ``<tg-emoji emoji-id="...">😀</tg-emoji>`` span
  produced by :func:`pe` / :func:`pe_custom`.
* Inline keyboard button icons, via the ``icon_custom_emoji_id`` field added
  to ``InlineKeyboardButton`` in Bot API 9.4. ``keyboards.py`` calls
  :func:`emoji_id` to look up the raw ID for that field.

When the user's client cannot render the premium emoji it falls back to the
unicode glyph, so messages degrade gracefully. Both features additionally
require the bot owner to have a Telegram Premium subscription — if they do
not, set ``PREMIUM_BUTTON_ICONS=false`` to keep buttons on plain unicode.

Use ``/getemoji`` admin command to capture IDs for any premium emoji and write
them into ``assets/premium_emojis.json``.
"""
from __future__ import annotations

import json
from functools import lru_cache
from html import escape
from pathlib import Path

from ..config import get_settings


@lru_cache(maxsize=1)
def _load_map() -> dict[str, dict]:
    path: Path = get_settings().project_root / "assets" / "premium_emojis.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("emojis", {})


def reload_map() -> None:
    _load_map.cache_clear()
    _build_fallback_index.cache_clear()


def pe(name: str) -> str:
    """Render a premium emoji span for HTML messages.

    If no custom_emoji_id is configured for ``name`` we just return the unicode
    fallback. Otherwise we wrap it in ``<tg-emoji>`` so Telegram renders the
    premium asset.
    """
    spec = _load_map().get(name)
    if spec is None:
        return ""
    fallback = spec.get("fallback", "")
    eid = spec.get("id")
    if not eid:
        return fallback
    return f'<tg-emoji emoji-id="{escape(str(eid))}">{escape(fallback)}</tg-emoji>'


def fb(name: str) -> str:
    """Get just the unicode fallback (for use in keyboard button labels)."""
    spec = _load_map().get(name)
    if spec is None:
        return ""
    return spec.get("fallback", "")


def emoji_id(name: str) -> str | None:
    """Return the premium ``custom_emoji_id`` registered for ``name``.

    Used as the ``icon_custom_emoji_id`` field on ``InlineKeyboardButton``
    so Telegram renders the premium asset to the left of the label. Returns
    ``None`` when no ID is configured, so callers can fall back to the
    unicode glyph from :func:`fb`.
    """
    spec = _load_map().get(name)
    if spec is None:
        return None
    eid = spec.get("id")
    return str(eid) if eid else None


@lru_cache(maxsize=1)
def _build_fallback_index() -> dict[str, str]:
    """Reverse-index from unicode fallback glyph -> premium custom_emoji_id.

    Built lazily from the registry so :func:`find_emoji_id` can resolve a
    product's emoji to a premium ID even when the per-row
    ``products.emoji_id`` column has not been set yet.

    When several entries share the same unicode glyph (e.g. chrome ``link``
    and ``product_link`` both use ``\U0001f517``), entries whose key starts
    with ``product_`` win. ``find_emoji_id`` is only called for product
    lookups, so this gives admins a way to ship a product-specific premium
    variant alongside a chrome variant.
    """
    out: dict[str, str] = {}
    product_glyphs: set[str] = set()
    for key, spec in _load_map().items():
        if not isinstance(spec, dict):
            continue
        fallback = spec.get("fallback")
        eid = spec.get("id")
        if not (fallback and eid):
            continue
        is_product = key.startswith("product_")
        if fallback in product_glyphs and not is_product:
            continue
        out[fallback] = str(eid)
        if is_product:
            product_glyphs.add(fallback)
    return out


def find_emoji_id(fallback: str | None) -> str | None:
    """Look up a premium ``custom_emoji_id`` by its unicode fallback glyph.

    Useful for products where the admin populated
    ``assets/premium_emojis.json`` (with ``product_*`` entries keyed by
    fallback glyph) but never ran ``/setemoji <slug> <id>`` to write the
    ID into the ``products.emoji_id`` column. Returns the first matching
    ID or ``None`` if no entry uses that fallback.
    """
    if not fallback:
        return None
    return _build_fallback_index().get(fallback)


def pe_custom(emoji_id: str | None, fallback: str) -> str:
    """Render an arbitrary premium emoji span for HTML messages.

    Use this for per-row data (e.g. a product's ``emoji_id``) that isn't in
    the ``premium_emojis.json`` registry. If ``emoji_id`` is empty/None, the
    plain ``fallback`` unicode glyph is returned.
    """
    if not emoji_id:
        return escape(fallback or "", quote=False)
    return f'<tg-emoji emoji-id="{escape(str(emoji_id))}">{escape(fallback or "")}</tg-emoji>'


def list_loaded_keys() -> list[str]:
    """Return the list of emoji keys currently loaded from the registry."""
    return list(_load_map().keys())


def validate_registry() -> list[str]:
    """Validate the loaded registry and return a list of warning strings.

    Does lightweight checks only: each entry should be a dict with a
    non-empty string "fallback" and an "id" that looks numeric.
    """
    warnings: list[str] = []
    data = _load_map()
    if not isinstance(data, dict):
        warnings.append("premium_emojis.json: 'emojis' root is not an object")
        return warnings
    for key, spec in data.items():
        if not isinstance(spec, dict):
            warnings.append(f"{key}: entry is not an object")
            continue
        fb = spec.get("fallback")
        if not fb or not isinstance(fb, str):
            warnings.append(f"{key}: missing or invalid 'fallback'")
        eid = spec.get("id")
        if not eid:
            warnings.append(f"{key}: missing 'id'")
        else:
            # allow either numeric strings or numbers, but ensure digits
            if not str(eid).strip().isdigit():
                warnings.append(f"{key}: 'id' is not numeric: {eid}")
    return warnings
