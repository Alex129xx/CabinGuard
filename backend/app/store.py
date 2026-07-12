from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from .graph import build_graph
from .preferences import load_preferences, setup_preferences
from .schemas import SessionState


class GraphStore:
    """Application-owned LangGraph lifecycle and per-session serialization."""

    def __init__(self) -> None:
        self.db_path = Path(__file__).resolve().parents[1] / "data" / "cabinguard.db"
        self.checkpointer: AsyncSqliteSaver | None = None
        self.graph = None
        self._saver_context = None
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def start(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        await setup_preferences(str(self.db_path))
        self._saver_context = AsyncSqliteSaver.from_conn_string(str(self.db_path))
        self.checkpointer = await self._saver_context.__aenter__()
        await self.checkpointer.setup()
        self.graph = build_graph(self.checkpointer)

    async def stop(self) -> None:
        if self._saver_context:
            await self._saver_context.__aexit__(None, None, None)
        self.checkpointer = self.graph = self._saver_context = None

    @staticmethod
    def config(session_id: str) -> dict:
        return {"configurable": {"thread_id": session_id}}

    def _require_graph(self):
        if not self.graph:
            raise RuntimeError("CabinGuard graph has not started")
        return self.graph

    @staticmethod
    def as_state(values: dict | None) -> SessionState | None:
        if not values or not values.get("session_id"):
            return None
        fields = {key: value for key, value in values.items() if key in SessionState.model_fields}
        return SessionState(**fields)

    async def create(self, profile_id: str | None = None) -> SessionState:
        session_id = str(uuid4())
        state = SessionState(session_id=session_id, profile_id=profile_id)
        payload = state.model_dump(mode="json")
        payload.update({"event": "bootstrap", "user_preferences": await load_preferences(str(self.db_path), profile_id)})
        async with self._locks[session_id]:
            await self._require_graph().ainvoke(payload, self.config(session_id))
            return await self.get(session_id)  # type: ignore[return-value]

    async def get(self, session_id: str) -> SessionState | None:
        snapshot = await self._require_graph().aget_state(self.config(session_id))
        return self.as_state(snapshot.values)

    async def run(self, session_id: str, payload: dict) -> tuple[SessionState | None, str | None, list]:
        async with self._locks[session_id]:
            graph = self._require_graph()
            snapshot = await graph.aget_state(self.config(session_id))
            current = self.as_state(snapshot.values)
            if not current:
                return None, None, []
            text = str(payload.get("text", ""))
            awaiting = bool(snapshot.tasks and any(task.interrupts for task in snapshot.tasks))
            approved = payload.get("approved")
            if awaiting and approved is None:
                yes = any(word in text for word in ("确认", "可以", "好的", "同意", "是", "开始"))
                no = any(word in text for word in ("取消", "不要", "算了", "否"))
                if not (yes or no):
                    return current, current.pending_action.prompt if current.pending_action else "当前有待确认操作。", list(snapshot.tasks[0].interrupts)
                approved = yes
            if awaiting:
                if payload.get("action_id") and current.pending_action and payload["action_id"] != current.pending_action.id:
                    return current, "确认操作已过期，请按当前提示操作。", list(snapshot.tasks[0].interrupts)
                output = await graph.ainvoke(Command(resume={"approved": bool(approved)}), self.config(session_id))
            else:
                output = await graph.ainvoke(payload, self.config(session_id))
            state = self.as_state(output)
            return state, state.final_response if state else None, list(output.get("__interrupt__", []))

    async def reset(self, session_id: str) -> SessionState | None:
        async with self._locks[session_id]:
            current = await self.get(session_id)
            if not current:
                return None
            await self.checkpointer.adelete_thread(session_id)  # type: ignore[union-attr]
            state = SessionState(session_id=session_id, profile_id=current.profile_id)
            payload = state.model_dump(mode="json")
            payload.update({"event": "bootstrap", "user_preferences": await load_preferences(str(self.db_path), current.profile_id)})
            await self._require_graph().ainvoke(payload, self.config(session_id))
            return await self.get(session_id)


store = GraphStore()
