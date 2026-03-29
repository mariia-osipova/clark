"""
Loader for the single hardcoded demo user profile.

Reads data/user_profile.json once and caches it.
Graph nodes and tools can import get_user_profile() to access user context.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = ROOT / "data" / "user_profile.json"

_profile_cache: dict[str, Any] | None = None
_profile_mtime: float = 0.0


def get_user_profile() -> dict[str, Any]:
    """Return the cached user profile, reloading if the file changed."""
    global _profile_cache, _profile_mtime

    try:
        mtime = PROFILE_PATH.stat().st_mtime
    except FileNotFoundError:
        return _empty_profile()

    if _profile_cache is None or mtime != _profile_mtime:
        with open(PROFILE_PATH) as f:
            _profile_cache = json.load(f)
        _profile_mtime = mtime

    return _profile_cache


def _empty_profile() -> dict[str, Any]:
    """Fallback when user_profile.json is missing."""
    return {
        "identity": {"name": "", "locale": "es-AR"},
        "household": {
            "size": 1,
            "has_children": False,
            "has_pets": False,
            "dietary_restrictions": [],
        },
        "preferences": {
            "preferred_brands": {},
            "excluded_brands": [],
            "excluded_categories": [],
            "budget_monthly": None,
            "strict_brand": False,
        },
        "purchase_history": {
            "frequent_items": [],
            "last_order_date": None,
            "avg_cart_size": None,
            "shopping_frequency_days": None,
        },
        "interaction": {
            "auto_pick_suggestions": False,
            "verbosity": "normal",
        },
        "cart_profiles": {},
    }


def reset_profile_cache() -> None:
    """Clear the cache (useful for tests)."""
    global _profile_cache, _profile_mtime
    _profile_cache = None
    _profile_mtime = 0.0


def get_cart_profiles() -> dict[str, list[dict]]:
    """Return the cart_profiles section of the user profile, or {} if absent."""
    return get_user_profile().get("cart_profiles", {})


def save_cart_profiles(profiles: dict[str, list[dict]]) -> None:
    """Atomically write the cart_profiles section to user_profile.json.

    Reads the current file, updates the cart_profiles key, then writes to a
    temp file and renames it into place (atomic on Linux/macOS).
    Invalidates the mtime-based cache so the next get_user_profile() reloads.
    """
    try:
        with open(PROFILE_PATH) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = _empty_profile()
    data["cart_profiles"] = profiles
    tmp = PROFILE_PATH.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(PROFILE_PATH)
    reset_profile_cache()
