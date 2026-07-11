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
