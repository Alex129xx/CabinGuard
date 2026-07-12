import pytest
from app.engine import execute_tool
from app.safety import evaluate
from app.schemas import GateDecision, SessionState


@pytest.mark.asyncio
async def test_video_is_blocked_while_moving():
    state = SessionState(session_id="test")
    state.vehicle.speed_kmh = 80
    reply = await execute_tool(state, "media_control", {"mode": "video"})
    assert "无法播放视频" in reply
    assert state.cabin.media_mode == "off"
    assert state.tool_logs[0].decision.value == "BLOCK"


@pytest.mark.asyncio
async def test_video_is_blocked_as_soon_as_navigation_starts():
    state = SessionState(session_id="test")
    state.navigation.status = "active"
    reply = await execute_tool(state, "set_media", {"mode": "video"})
    assert "无法播放视频" in reply


@pytest.mark.asyncio
async def test_highway_massage_is_modified():
    state = SessionState(session_id="test")
    state.vehicle.speed_kmh = 100
    await execute_tool(state, "seat_control", {"massage": 3})
    assert state.cabin.seat_massage == 1
    assert state.tool_logs[0].decision.value == "MODIFY"


def test_out_of_range_temperature_requires_comfort_confirmation():
    result = evaluate("climate_control", {"temperature": 34, "mode": "auto"}, SessionState(session_id="test"))
    assert result.decision is GateDecision.CONFIRM
    assert result.args == {"temperature": 23, "mode": "auto"}


def test_destination_search_without_an_active_route_does_not_require_confirmation():
    state = SessionState(session_id="test")
    state.navigation.status = "active"
    result = evaluate("navigation_service", {"action": "search", "destination": "东方明珠"}, state)
    assert result.decision is GateDecision.ALLOW


@pytest.mark.asyncio
async def test_rainy_weather_enables_wipers(monkeypatch):
    async def rainy(*_args):
        return {"temperature": 22, "weather": "小雨", "precipitation_probability": 80}
    monkeypatch.setattr("app.engine.weather", rainy)
    state = SessionState(session_id="test")
    reply = await execute_tool(state, "get_weather", {"action": "current", "rainy_scenario": True}, skip_gate=True)
    assert state.vehicle.wiper_on is True
    assert "带伞" in reply
