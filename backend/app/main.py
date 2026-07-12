from __future__ import annotations

import asyncio
import logging
import random
import time
import math
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .schemas import MessageIn, ResumeIn, SessionCreateIn, SimulationPatch
from .store import store

logger = logging.getLogger("cabinguard")
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class Hub:
    def __init__(self) -> None:
        self.connections: dict[str, list[WebSocket]] = {}

    async def connect(self, session_id: str, socket: WebSocket) -> None:
        await socket.accept()
        self.connections.setdefault(session_id, []).append(socket)

    def disconnect(self, session_id: str, socket: WebSocket) -> None:
        if socket in self.connections.get(session_id, []):
            self.connections[session_id].remove(socket)

    async def publish(self, session_id: str) -> None:
        state = await store.get(session_id)
        if not state:
            return
        payload = {"type": "state", "state": state.model_dump(mode="json")}
        for socket in list(self.connections.get(session_id, [])):
            try:
                await asyncio.wait_for(socket.send_json(payload), timeout=.5)
            except Exception:
                self.disconnect(session_id, socket)


hub = Hub()


def position_on_route(polyline: list[list[float]], progress: float) -> tuple[float, float]:
    """Return an interpolated route coordinate; simulation deliberately does not use it."""
    if not polyline:
        raise ValueError("路线缺少坐标")
    if len(polyline) == 1:
        return tuple(polyline[0])  # type: ignore[return-value]
    distances, total = [], 0.0
    for start, end in zip(polyline, polyline[1:]):
        distance = math.hypot((end[0] - start[0]) * math.cos(math.radians((start[1] + end[1]) / 2)), end[1] - start[1])
        distances.append(distance); total += distance
    target, walked = total * min(1, max(0, progress)), 0.0
    for start, end, distance in zip(polyline, polyline[1:], distances):
        if walked + distance >= target and distance:
            ratio = (target - walked) / distance
            return start[0] + (end[0] - start[0]) * ratio, start[1] + (end[1] - start[1]) * ratio
        walked += distance
    return tuple(polyline[-1])  # type: ignore[return-value]


@asynccontextmanager
async def lifespan(_: FastAPI):
    await store.start()
    yield
    await store.stop()


app = FastAPI(title="CabinGuard V3 API", version="3.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


async def state_or_404(session_id: str):
    state = await store.get(session_id)
    if not state:
        raise HTTPException(404, "会话不存在")
    return state


@app.get("/api/health")
async def health():
    return {"status": "ok", "providers": {"deepseek": settings.llm_enabled, "amap": settings.amap_enabled, "weather": True, "database": store.graph is not None}}


@app.post("/api/sessions")
async def create_session(payload: SessionCreateIn | None = None):
    return (await store.create(payload.profile_id if payload else None)).model_dump(mode="json")


@app.get("/api/sessions/{session_id}/state")
async def get_state(session_id: str):
    return (await state_or_404(session_id)).model_dump(mode="json")


@app.post("/api/sessions/{session_id}/messages")
async def post_message(session_id: str, payload: MessageIn):
    if not payload.text and not payload.candidate_id:
        raise HTTPException(422, "消息不能为空")
    started = time.monotonic()
    try:
        state, response, interrupts = await asyncio.wait_for(store.run(session_id, {"event": "message", "text": payload.text, "candidate_id": payload.candidate_id}), timeout=15)
    except TimeoutError:
        raise HTTPException(504, "Agent 处理超时，请重试")
    if not state:
        raise HTTPException(404, "会话不存在")
    asyncio.create_task(hub.publish(session_id))
    logger.info("graph message processed in %.2fs for %s", time.monotonic() - started, session_id)
    return {"response": response, "state": state.model_dump(mode="json"), "run_status": state.agent_status, "interrupt": bool(interrupts)}


@app.post("/api/sessions/{session_id}/resume")
async def resume(session_id: str, payload: ResumeIn):
    state, response, interrupts = await store.run(session_id, {"event": "resume", "action_id": payload.action_id, "approved": payload.approved})
    if not state:
        raise HTTPException(404, "会话不存在")
    asyncio.create_task(hub.publish(session_id))
    return {"response": response, "state": state.model_dump(mode="json"), "run_status": state.agent_status, "interrupt": bool(interrupts)}


@app.post("/api/sessions/{session_id}/reset")
async def reset_session(session_id: str):
    state = await store.reset(session_id)
    if not state:
        raise HTTPException(404, "会话不存在")
    asyncio.create_task(hub.publish(session_id))
    return state.model_dump(mode="json")


@app.patch("/api/sessions/{session_id}/simulation")
async def patch_simulation(session_id: str, patch: SimulationPatch):
    state = await state_or_404(session_id)
    vehicle = patch.vehicle.model_dump(mode="json") if patch.vehicle else state.vehicle.model_dump(mode="json")
    if state.navigation.status == "active" and patch.vehicle:
        raise HTTPException(409, "导航中不能手动修改车辆位置")
    driver = patch.driver.model_dump(mode="json") if patch.driver else state.driver.model_dump(mode="json")
    result, _, _ = await store.run(session_id, {"event": "proactive", "vehicle": vehicle, "driver": driver})
    asyncio.create_task(hub.publish(session_id))
    return {"messages": [result.final_response] if result and result.final_response else [], "state": result.model_dump(mode="json") if result else state.model_dump(mode="json")}


@app.post("/api/sessions/{session_id}/scenarios/{scenario_id}")
async def scenario(session_id: str, scenario_id: str):
    state = await state_or_404(session_id)
    vehicle, driver, cabin = state.vehicle.model_dump(mode="json"), state.driver.model_dump(mode="json"), state.cabin.model_dump(mode="json")
    if scenario_id == "commute":
        vehicle.update({"ignition_on": True, "speed_kmh": 0}); driver["fatigue_level"] = .2; cabin["temperature"] = 28
    elif scenario_id == "rainy":
        vehicle["ignition_on"] = True; cabin["temperature"] = 26
    elif scenario_id == "fatigue":
        vehicle.update({"ignition_on": True, "speed_kmh": 105}); driver.update({"fatigue_level": .9, "attention_level": .38, "driving_duration_minutes": 145})
    else:
        raise HTTPException(404, "未知场景")
    result, _, _ = await store.run(session_id, {"event": "proactive", "vehicle": vehicle, "driver": driver, "cabin": cabin, "event_payload": {"scenario_id": scenario_id}})
    asyncio.create_task(hub.publish(session_id))
    return {"messages": [result.final_response] if result and result.final_response else [], "state": result.model_dump(mode="json") if result else state.model_dump(mode="json")}


@app.post("/api/sessions/{session_id}/navigation/advance")
async def advance_navigation(session_id: str):
    state = await state_or_404(session_id)
    if state.navigation.status != "active" or not state.navigation.route:
        raise HTTPException(400, "当前未开始导航")
    vehicle, driver, navigation = state.vehicle.model_dump(mode="json"), state.driver.model_dump(mode="json"), state.navigation.model_dump(mode="json")
    speed = round(random.uniform(56, 64), 1)
    vehicle["speed_kmh"] = speed
    driver["driving_duration_minutes"] += 1 / 60
    navigation["simulated_speed_kmh"] = speed
    navigation["simulated_elapsed_minutes"] += 1 / 60
    result, _, _ = await store.run(session_id, {"event": "proactive", "vehicle": vehicle, "driver": driver, "navigation": navigation})
    asyncio.create_task(hub.publish(session_id))
    return result.model_dump(mode="json") if result else state.model_dump(mode="json")


@app.post("/api/sessions/{session_id}/navigation/cancel")
async def cancel_navigation(session_id: str):
    state, response, _ = await store.run(session_id, {"event": "message", "text": "取消导航"})
    if not state:
        raise HTTPException(404, "会话不存在")
    asyncio.create_task(hub.publish(session_id))
    return {"response": response, "state": state.model_dump(mode="json")}


@app.websocket("/ws/sessions/{session_id}")
async def websocket(session_id: str, socket: WebSocket):
    if not await store.get(session_id):
        await socket.close(code=4404)
        return
    await hub.connect(session_id, socket)
    await hub.publish(session_id)
    try:
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(session_id, socket)


@app.get("/")
async def root():
    if (FRONTEND_DIST / "index.html").exists():
        return FileResponse(FRONTEND_DIST / "index.html")
    return {"name": "CabinGuard V3", "docs": "/docs"}


if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")
