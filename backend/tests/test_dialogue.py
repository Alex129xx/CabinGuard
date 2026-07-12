import pytest
from app.agent import handle_message
from app.schemas import SessionState


@pytest.mark.asyncio
async def test_navigation_disambiguation_flow(monkeypatch):
    async def no_llm(*_args, **_kwargs):
        return None
    monkeypatch.setattr("app.agent.try_llm_tools", no_llm)
    async def poi_search(_query):
        return [
            {"name": "上海虹桥火车站", "address": "", "lat": 31.1942, "lng": 121.3217, "id": "poi-1"},
            {"name": "上海虹桥机场 T2", "address": "", "lat": 31.1978, "lng": 121.3380, "id": "poi-2"},
        ]
    async def route(*_args):
        return {"distance_km": 32.4, "duration_minutes": 42, "steps": ["直行"], "polyline": [[121.47, 31.23], [121.32, 31.19]], "source": "amap-web-service"}
    monkeypatch.setattr("app.engine.search_poi", poi_search)
    monkeypatch.setattr("app.engine.driving_route", route)
    state = SessionState(session_id="test")
    first = await handle_message(state, "带我去虹桥站")
    assert "找到" in first
    second = await handle_message(state, "火车站")
    assert "预计" in second
    third = await handle_message(state, "开始吧")
    assert "导航已开始" in third


@pytest.mark.asyncio
async def test_candidate_can_be_selected_by_branch_name(monkeypatch):
    async def no_llm(*_args, **_kwargs):
        return None
    monkeypatch.setattr("app.agent.try_llm_tools", no_llm)
    async def route(*_args):
        return {"distance_km": 8.2, "duration_minutes": 16, "steps": ["直行"], "polyline": [[121.47, 31.23], [121.32, 31.19]], "source": "amap-web-service"}
    monkeypatch.setattr("app.engine.driving_route", route)
    state = SessionState(session_id="test")
    state.navigation.status = "selecting"
    state.navigation.candidates = [
        {"name": "上海虹桥站", "address": "", "lat": 31.19, "lng": 121.32},
        {"name": "虹桥火车站(地铁站)", "address": "", "lat": 31.19, "lng": 121.32},
        {"name": "上海虹桥站(北进站口)", "address": "", "lat": 31.19, "lng": 121.32},
    ]
    reply = await handle_message(state, "上海虹桥站（北进站口）")
    assert "北进站口" in reply
    assert state.navigation.status == "preview"


@pytest.mark.asyncio
async def test_unmatched_candidate_reprompts_without_llm(monkeypatch):
    async def should_not_run(*_args, **_kwargs):
        raise AssertionError("LLM should not run while selecting a destination")
    monkeypatch.setattr("app.agent.try_llm_tools", should_not_run)
    async def unresolved(*_args, **_kwargs):
        return None
    monkeypatch.setattr("app.agent.resolve_candidate_with_llm", unresolved)
    state = SessionState(session_id="test")
    state.navigation.status = "selecting"
    state.navigation.candidates = [{"name": "上海虹桥站", "address": "", "lat": 31.19, "lng": 121.32}]
    reply = await handle_message(state, "我不知道")
    assert "第一个" in reply


@pytest.mark.asyncio
async def test_chinese_ordinal_selects_navigation_candidate(monkeypatch):
    async def route(*_args):
        return {"distance_km": 5.2, "duration_minutes": 12, "steps": ["直行"], "polyline": [[121.47, 31.23], [121.32, 31.19]], "source": "amap-web-service"}
    monkeypatch.setattr("app.engine.driving_route", route)
    state = SessionState(session_id="test")
    state.navigation.status = "selecting"
    state.navigation.candidates = [
        {"name": "上海虹桥站", "address": "", "lat": 31.19, "lng": 121.32},
        {"name": "虹桥火车站(地铁站)", "address": "", "lat": 31.19, "lng": 121.32},
        {"name": "上海虹桥站(北进站口)", "address": "", "lat": 31.19, "lng": 121.32},
    ]
    reply = await handle_message(state, "第三个")
    assert "北进站口" in reply


@pytest.mark.asyncio
async def test_low_confidence_uses_constrained_deepseek_candidate_resolution(monkeypatch):
    async def resolved(candidates, _text):
        return candidates[1]
    async def route(*_args):
        return {"distance_km": 5.2, "duration_minutes": 12, "steps": ["直行"], "polyline": [[121.47, 31.23], [121.32, 31.19]], "source": "amap-web-service"}
    monkeypatch.setattr("app.agent.resolve_candidate_with_llm", resolved)
    monkeypatch.setattr("app.engine.driving_route", route)
    state = SessionState(session_id="test")
    state.navigation.status = "selecting"
    state.navigation.candidates = [
        {"name": "上海虹桥站", "address": "", "lat": 31.19, "lng": 121.32},
        {"name": "虹桥火车站(地铁站)", "address": "", "lat": 31.19, "lng": 121.32},
    ]
    reply = await handle_message(state, "火车站，不是机场")
    assert "地铁站" in reply


@pytest.mark.asyncio
async def test_greetings_use_deepseek_when_available(monkeypatch):
    async def deepseek_reply(*_args, **_kwargs):
        return "您好，我是来自 DeepSeek 的 CabinGuard。"
    monkeypatch.setattr("app.agent.try_llm_tools", deepseek_reply)
    state = SessionState(session_id="test")
    assert "DeepSeek" in await handle_message(state, "你好")
    assert "DeepSeek" in await handle_message(state, "早上好")
