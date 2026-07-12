import pytest
from app.engine import execute_tool
from app.schemas import SessionState


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
