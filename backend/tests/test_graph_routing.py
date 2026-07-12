import pytest

from app.graph import _fallback_plan, proactive, router


def test_navigation_cancel_bypasses_planner():
    result = router({
        "event": "message",
        "text": "结束导航",
        "messages": [],
        "navigation": {"status": "active"},
    })
    assert result.goto == "safety"
    assert result.update["planned_calls"] == [{"name": "stop_navigation", "arguments": {}}]


def test_location_weather_uses_explicit_location():
    result = _fallback_plan({"navigation": {}, "cabin": {}}, "苏州天气怎么样")
    assert result["tool_calls"] == [{"name": "get_weather", "arguments": {"action": "location", "location": "苏州"}}]


def test_navigation_replan_goes_to_semantic_planner():
    result = router({"event": "message", "text": "带我去东方明珠", "messages": [], "navigation": {"status": "active"}, "cabin": {}})
    assert result.goto == "planner"


def test_seat_heating_replaces_ventilation():
    result = _fallback_plan({"navigation": {}, "cabin": {"seat_heating": 0, "seat_massage": 0}}, "座椅还是加热吧")
    assert result["tool_calls"] == [{"name": "set_seat", "arguments": {"heating": 2, "massage": 0}}]


@pytest.mark.asyncio
async def test_low_attention_suggests_a_small_media_volume_increase():
    state = {
        "vehicle": {"ignition_on": True},
        "driver": {"fatigue_level": .2, "attention_level": .3, "stress_level": .2, "mood": "normal", "driving_duration_minutes": 30},
        "cabin": {"temperature": 24, "climate_mode": "auto", "media_mode": "music", "volume": 25, "seat_heating": 0, "seat_ventilation": 0, "seat_massage": 0, "window_open_percent": 0},
        "navigation": {"status": "idle", "candidates": [], "destination": None, "route": None, "progress": 0, "remaining_distance_km": 0, "simulated_speed_kmh": 0, "simulated_elapsed_minutes": 0},
        "session_id": "test", "trigger_state": {}, "event_payload": {}, "execution_trace": [],
    }
    result = await proactive(state)
    assert result["planned_calls"] == [{"name": "set_media", "arguments": {"mode": "music", "volume": 35}}]
