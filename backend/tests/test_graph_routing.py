from app.graph import router


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
    result = router({"event": "message", "text": "苏州天气怎么样", "messages": [], "navigation": {}, "cabin": {}})
    assert result.goto == "safety"
    assert result.update["planned_calls"] == [{"name": "get_weather", "arguments": {"action": "location", "location": "苏州"}}]


def test_navigation_replan_requires_search_before_candidates():
    result = router({"event": "message", "text": "带我去东方明珠", "messages": [], "navigation": {"status": "active"}, "cabin": {}})
    assert result.goto == "safety"
    assert result.update["planned_calls"] == [{"name": "search_poi", "arguments": {"query": "东方明珠"}}]


def test_seat_heating_replaces_ventilation():
    result = router({"event": "message", "text": "座椅还是加热吧", "messages": [], "navigation": {}, "cabin": {"seat_heating": 0, "seat_ventilation": 2, "seat_massage": 0}})
    assert result.update["planned_calls"] == [{"name": "set_seat", "arguments": {"heating": 2, "ventilation": 0, "massage": 0}}]
