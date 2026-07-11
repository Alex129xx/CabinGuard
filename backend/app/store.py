from __future__ import annotations

from uuid import uuid4
from .schemas import SessionState


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    def create(self) -> SessionState:
        state = SessionState(session_id=str(uuid4()))
        self._sessions[state.session_id] = state
        return state

    def get(self, session_id: str) -> SessionState | None:
        return self._sessions.get(session_id)


store = SessionStore()
