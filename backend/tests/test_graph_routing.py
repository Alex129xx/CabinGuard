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
