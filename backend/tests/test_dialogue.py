import pytest
from app.agent import handle_message
from app.schemas import SessionState


@pytest.mark.asyncio
async def test_navigation_disambiguation_flow(monkeypatch):
    async def no_llm(*_args, **_kwargs):
        return None
    monkeypatch.setattr("app.agent.try_llm_tools", no_llm)
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
    state = SessionState(session_id="test")
    state.navigation.status = "selecting"
    state.navigation.candidates = [{"name": "上海虹桥站", "address": "", "lat": 31.19, "lng": 121.32}]
    reply = await handle_message(state, "随便一个")
    assert "回复地点全名或序号" in reply


@pytest.mark.asyncio
async def test_greetings_use_deepseek_when_available(monkeypatch):
    async def deepseek_reply(*_args, **_kwargs):
        return "您好，我是来自 DeepSeek 的 CabinGuard。"
    monkeypatch.setattr("app.agent.try_llm_tools", deepseek_reply)
    state = SessionState(session_id="test")
    assert "DeepSeek" in await handle_message(state, "你好")
    assert "DeepSeek" in await handle_message(state, "早上好")
