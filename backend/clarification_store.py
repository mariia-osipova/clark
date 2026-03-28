"""
Ephemeral clarification state for multi-turn ambiguous choices.
"""

from __future__ import annotations

import time
from typing import Any


_TTL_SECONDS = 15 * 60
_PENDING: dict[str, dict[str, dict[str, Any]]] = {}


class ClarificationStateError(ValueError):
    """Raised when a clarification response cannot be resolved safely."""


def _now() -> float:
    return time.time()


def _purge_expired(session_token: str) -> None:
    session_entries = _PENDING.get(session_token)
    if not session_entries:
        return

    cutoff = _now() - _TTL_SECONDS
    expired_ids = [
        pending_id
        for pending_id, record in session_entries.items()
        if record.get("created_at", 0.0) < cutoff
    ]
    for pending_id in expired_ids:
        session_entries.pop(pending_id, None)

    if not session_entries:
        _PENDING.pop(session_token, None)


def store_pending_clarification(session_token: str | None, clarification: dict | None) -> None:
    """Persist a clarification payload for later resolution in the same session."""
    if not session_token or not clarification:
        return

    pending_request_id = clarification.get("pending_request_id")
    if not pending_request_id:
        return

    _purge_expired(session_token)
    session_entries = _PENDING.setdefault(session_token, {})
    session_entries[pending_request_id] = {
        "pending_request_id": pending_request_id,
        "question": clarification.get("question", ""),
        "options": clarification.get("options", []),
        "created_at": _now(),
    }


def get_pending_clarification(session_token: str | None, pending_request_id: str | None) -> dict[str, Any]:
    """Return the stored clarification record or raise a safe user-facing error."""
    if not session_token:
        raise ClarificationStateError(
            "Falta la sesión del chat para resolver la aclaración. Probá de nuevo."
        )
    if not pending_request_id:
        raise ClarificationStateError("La aclaración no tiene un identificador válido. Probá de nuevo.")

    _purge_expired(session_token)
    record = _PENDING.get(session_token, {}).get(pending_request_id)
    if not record:
        raise ClarificationStateError(
            "La aclaración venció o ya no está disponible. Volvé a hacer el pedido."
        )
    return record


def resolve_clarification_option(
    session_token: str | None,
    pending_request_id: str | None,
    chosen_option_id: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve a user's selection against server-side pending clarification state."""
    record = get_pending_clarification(session_token, pending_request_id)
    option = next(
        (candidate for candidate in record.get("options", []) if candidate.get("id") == chosen_option_id),
        None,
    )
    if not option:
        raise ClarificationStateError("La opción elegida ya no está disponible. Elegí otra vez.")
    return record, option


def clear_pending_clarification(session_token: str | None, pending_request_id: str | None) -> None:
    """Remove a resolved clarification record."""
    if not session_token or not pending_request_id:
        return

    session_entries = _PENDING.get(session_token)
    if not session_entries:
        return

    session_entries.pop(pending_request_id, None)
    if not session_entries:
        _PENDING.pop(session_token, None)


def _reset_pending_clarifications() -> None:
    """Testing helper."""
    _PENDING.clear()
