import pytest

from app.main import advance_navigation, position_on_route
from app.store import store


def test_position_on_route_interpolates_between_points():
    longitude, latitude = position_on_route([[121.0, 31.0], [122.0, 31.0]], 0.25)
    assert longitude == pytest.approx(121.25)
    assert latitude == pytest.approx(31.0)


@pytest.mark.asyncio
async def test_navigation_updates_instruments_without_moving_map_position():
    state = store.create()
    state.navigation.status = "active"
    state.navigation.route = {"distance_km": 0.001, "polyline": [[121.0, 31.0], [121.01, 31.0]]}
    state.navigation.remaining_distance_km = 0.001
    initial_position = (state.vehicle.longitude, state.vehicle.latitude)
    result = await advance_navigation(state.session_id)
    assert result["navigation"]["status"] == "active"
    assert 56 <= result["vehicle"]["speed_kmh"] <= 64
    assert result["driver"]["driving_duration_minutes"] == pytest.approx(1 / 60)
    assert (result["vehicle"]["longitude"], result["vehicle"]["latitude"]) == initial_position
