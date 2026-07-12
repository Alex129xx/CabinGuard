import pytest

from app.engine import execute_tool
from app.main import position_on_route
from app.schemas import SessionState


def test_position_on_route_interpolates_between_points():
    longitude, latitude = position_on_route([[121.0, 31.0], [122.0, 31.0]], 0.25)
    assert longitude == pytest.approx(121.25)
    assert latitude == pytest.approx(31.0)


def test_position_on_route_preserves_endpoints():
    assert position_on_route([[121.0, 31.0], [122.0, 31.0]], 0) == pytest.approx((121.0, 31.0))
    assert position_on_route([[121.0, 31.0], [122.0, 31.0]], 1) == pytest.approx((122.0, 31.0))


@pytest.mark.asyncio
async def test_stop_navigation_clears_all_route_data():
    state = SessionState(session_id="test")
    state.navigation.status = "active"
    state.navigation.route = {"distance_km": 3.2}
    state.navigation.destination = {"name": "虹桥站"}
    state.navigation.candidates = [{"name": "虹桥站"}]
    await execute_tool(state, "stop_navigation", {}, skip_gate=True)
    assert state.navigation.status == "idle"
    assert state.navigation.route is None
    assert state.navigation.destination is None
    assert state.navigation.candidates == []
