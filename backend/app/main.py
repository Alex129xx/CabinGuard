from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .agent import handle_message
from .config import settings
from .engine import proactive_check
from .schemas import MessageIn, SimulationPatch
from .store import store

app = FastAPI(title="CabinGuard V2 API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class Hub:
    def __init__(self) -> None:
        self.connections: dict[str, list[WebSocket]] = {}

    async def connect(self, session_id: str, socket: WebSocket) -> None:
        await socket.accept(); self.connections.setdefault(session_id, []).append(socket)

    def disconnect(self, session_id: str, socket: WebSocket) -> None:
        if socket in self.connections.get(session_id, []): self.connections[session_id].remove(socket)

    async def publish(self, session_id: str) -> None:
        state = store.get(session_id)
        if not state: return
        dead = []
        for socket in self.connections.get(session_id, []):
            try: await socket.send_json({"type": "state", "state": state.model_dump(mode="json")})
            except Exception: dead.append(socket)
        for socket in dead: self.disconnect(session_id, socket)


hub = Hub()


class SynthesisIn(MessageIn):
    style: str = "general"


def speech_config(speechsdk):
    if settings.azure_speech_endpoint:
        return speechsdk.SpeechConfig(subscription=settings.azure_speech_key, endpoint=settings.azure_speech_endpoint)
    return speechsdk.SpeechConfig(subscription=settings.azure_speech_key, region=settings.azure_speech_region)


def state_or_404(session_id: str):
    state = store.get(session_id)
    if not state: raise HTTPException(404, "会话不存在")
    return state


@app.get("/api/health")
async def health():
    return {"status": "ok", "providers": {"deepseek": settings.llm_enabled, "amap": settings.amap_enabled, "azure": settings.azure_enabled}}


@app.post("/api/sessions")
async def create_session():
    state = store.create()
    return state.model_dump(mode="json")


@app.get("/api/sessions/{session_id}/state")
async def get_state(session_id: str):
    return state_or_404(session_id).model_dump(mode="json")


@app.post("/api/sessions/{session_id}/messages")
async def post_message(session_id: str, payload: MessageIn):
    state = state_or_404(session_id)
    response = await handle_message(state, payload.text)
    await hub.publish(session_id)
    return {"response": response, "state": state.model_dump(mode="json")}


@app.patch("/api/sessions/{session_id}/simulation")
async def patch_simulation(session_id: str, patch: SimulationPatch):
    state = state_or_404(session_id)
    if patch.vehicle: state.vehicle = patch.vehicle
    if patch.driver: state.driver = patch.driver
    messages = await proactive_check(state)
    await hub.publish(session_id)
    return {"messages": messages, "state": state.model_dump(mode="json")}


@app.post("/api/sessions/{session_id}/scenarios/{scenario_id}")
async def scenario(session_id: str, scenario_id: str):
    state = state_or_404(session_id)
    if scenario_id == "commute":
        state.vehicle.ignition_on = True; state.vehicle.speed_kmh = 0; state.driver.fatigue_level = .2; state.cabin.temperature = 28
    elif scenario_id == "rainy":
        state.vehicle.ignition_on = True; state.cabin.temperature = 26
    elif scenario_id == "fatigue":
        state.vehicle.ignition_on = True; state.vehicle.speed_kmh = 105; state.driver.fatigue_level = .9; state.driver.attention_level = .38; state.driver.driving_duration_minutes = 145
    else:
        raise HTTPException(404, "未知场景")
    messages = await proactive_check(state)
    await hub.publish(session_id)
    return {"messages": messages, "state": state.model_dump(mode="json")}


@app.post("/api/sessions/{session_id}/navigation/advance")
async def advance_navigation(session_id: str):
    state = state_or_404(session_id)
    if state.navigation.status != "active": raise HTTPException(400, "当前未开始导航")
    state.navigation.progress = min(1, state.navigation.progress + .25)
    if state.navigation.progress >= 1:
        state.navigation.status = "idle"; state.active_alert = "已到达目的地。"
    await hub.publish(session_id)
    return state.model_dump(mode="json")


@app.websocket("/ws/sessions/{session_id}")
async def websocket(session_id: str, socket: WebSocket):
    if not store.get(session_id):
        await socket.close(code=4404); return
    await hub.connect(session_id, socket)
    await hub.publish(session_id)
    try:
        while True: await socket.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(session_id, socket)


@app.post("/api/speech/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    if not settings.azure_enabled:
        return {"text": "", "provider": "unavailable", "error": "Azure Speech 尚未配置，请使用键盘输入。"}
    try:
        import azure.cognitiveservices.speech as speechsdk
        suffix = Path(audio.filename or "recording.wav").suffix or ".wav"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp:
            temp.write(await audio.read()); filename = temp.name
        def recognize():
            config = speech_config(speechsdk)
            config.speech_recognition_language = "zh-CN"
            result = speechsdk.SpeechRecognizer(speech_config=config, audio_config=speechsdk.audio.AudioConfig(filename=filename)).recognize_once_async().get()
            return result.text if result.reason == speechsdk.ResultReason.RecognizedSpeech else ""
        text = await asyncio.to_thread(recognize)
        Path(filename).unlink(missing_ok=True)
        return {"text": text, "provider": "azure"}
    except Exception as exc:
        return {"text": "", "provider": "unavailable", "error": str(exc)}


@app.post("/api/speech/synthesize")
async def synthesize(payload: SynthesisIn, background: BackgroundTasks):
    if not settings.azure_enabled:
        raise HTTPException(503, "Azure Speech 尚未配置")
    try:
        import azure.cognitiveservices.speech as speechsdk
        filename = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
        def create_audio():
            config = speech_config(speechsdk)
            config.speech_synthesis_language = "zh-CN"
            config.speech_synthesis_voice_name = "zh-CN-XiaoxiaoNeural"
            output = speechsdk.audio.AudioOutputConfig(filename=filename)
            result = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=output).speak_text_async(payload.text).get()
            return result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted
        success = await asyncio.to_thread(create_audio)
        if not success:
            Path(filename).unlink(missing_ok=True)
            raise HTTPException(502, "Azure TTS 未能生成音频")
        background.add_task(Path(filename).unlink, missing_ok=True)
        return FileResponse(filename, media_type="audio/wav", filename="cabinguard-reply.wav")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"TTS 暂时不可用：{exc}")


@app.get("/")
async def root():
    if (FRONTEND_DIST / "index.html").exists():
        return FileResponse(FRONTEND_DIST / "index.html")
    return {"name": "CabinGuard V2", "docs": "/docs"}


if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")
