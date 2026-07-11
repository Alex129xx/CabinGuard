from __future__ import annotations

import json
import re
from openai import AsyncOpenAI

from .config import settings
from .engine import confirm, execute_tool
from .schemas import SessionState


TOOLS = [
    {"type": "function", "function": {"name": "weather_service", "description": "查询当前位置或目的地天气", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["current", "destination", "forecast"]}}, "required": ["action"]}}},
    {"type": "function", "function": {"name": "navigation_service", "description": "搜索、预览、开始或取消导航", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["search", "preview", "start", "cancel"]}, "destination": {"type": "string"}}, "required": ["action"]}}},
    {"type": "function", "function": {"name": "climate_control", "description": "调节座舱空调", "parameters": {"type": "object", "properties": {"temperature": {"type": "integer"}, "mode": {"type": "string", "enum": ["off", "auto", "cool", "heat", "fan"]}}}}},
    {"type": "function", "function": {"name": "media_control", "description": "控制媒体与音量", "parameters": {"type": "object", "properties": {"mode": {"type": "string", "enum": ["music", "podcast", "video", "off"]}, "volume": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "seat_control", "description": "控制座椅加热、通风和按摩", "parameters": {"type": "object", "properties": {"heating": {"type": "integer"}, "ventilation": {"type": "integer"}, "massage": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "safety_service", "description": "发出安全提醒或建议休息", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["alert", "suggest_rest_stop"]}, "level": {"type": "string", "enum": ["info", "warning", "critical"]}, "message": {"type": "string"}}, "required": ["action", "message"]}}},
]

SYSTEM_PROMPT = """你是 CabinGuard 智能座舱助手。使用中文，回答简洁。需要外部数据或车控时必须调用工具；绝不绕过 Safety Gate。导航目的地有歧义时先搜索，不要自行猜测。"""


async def handle_message(state: SessionState, text: str) -> str:
    state.messages.append({"role": "user", "content": text})
    if state.pending_action:
        if any(word in text for word in ("确认", "可以", "好的", "开始", "同意", "是")):
            response = await confirm(state, True)
            return remember(state, response)
        if any(word in text for word in ("取消", "不要", "算了", "否")):
            response = await confirm(state, False)
            return remember(state, response)

    # Candidate selection should remain deterministic to make the demo stable.
    if state.navigation.status == "selecting" and state.navigation.candidates:
        selected = next((p for p in state.navigation.candidates if any(token in p["name"] for token in (text, "火车站" if "火车" in text else "__"))), None)
        if selected:
            response = await execute_tool(state, "navigation_service", {"action": "preview", "destination": selected})
            return remember(state, response)

    response = await try_llm_tools(state, text) if settings.llm_enabled else None
    if not response:
        response = await deterministic_response(state, text)
    return remember(state, response)


def remember(state: SessionState, response: str) -> str:
    state.messages.append({"role": "assistant", "content": response})
    state.messages = state.messages[-20:]
    return response


async def try_llm_tools(state: SessionState, text: str) -> str | None:
    try:
        client = AsyncOpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)
        context = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": f"车辆速度 {state.vehicle.speed_kmh}km/h；疲劳 {state.driver.fatigue_level:.2f}；导航状态 {state.navigation.status}。"},
            *state.messages[-8:],
        ]
        completion = await client.chat.completions.create(model=settings.deepseek_model, messages=context, tools=TOOLS, tool_choice="auto", temperature=0.2)
        message = completion.choices[0].message
        if not message.tool_calls:
            return message.content
        results = []
        for call in message.tool_calls:
            args = json.loads(call.function.arguments or "{}")
            results.append(await execute_tool(state, call.function.name, args))
        return " ".join(results)
    except Exception:
        return None


async def deterministic_response(state: SessionState, text: str) -> str:
    normalized = text.replace("。", "").replace("，", "")
    if state.navigation.status == "preview" and any(word in normalized for word in ("开始", "出发", "导航")):
        return await execute_tool(state, "navigation_service", {"action": "start"})
    if any(word in normalized for word in ("天气", "下雨", "温度")):
        action = "destination" if state.navigation.destination and any(word in normalized for word in ("那里", "目的地", "那边")) else "current"
        return await execute_tool(state, "weather_service", {"action": action})
    if any(word in normalized for word in ("导航", "带我去", "去", "路线")):
        if any(word in normalized for word in ("开始", "出发")):
            return await execute_tool(state, "navigation_service", {"action": "start"})
        destination = re.sub(r".*?(带我去|去|导航到)", "", normalized).strip() or "虹桥站"
        return await execute_tool(state, "navigation_service", {"action": "search", "destination": destination})
    if "电影" in normalized or "视频" in normalized:
        return await execute_tool(state, "media_control", {"mode": "video"})
    if "音乐" in normalized or "播客" in normalized:
        return await execute_tool(state, "media_control", {"mode": "music" if "音乐" in normalized else "podcast"})
    if "按摩" in normalized or "通风" in normalized or "加热" in normalized:
        massage = 3 if any(word in normalized for word in ("最大", "最强", "三档")) else 1 if "按摩" in normalized else state.cabin.seat_massage
        ventilation = 2 if "通风" in normalized else state.cabin.seat_ventilation
        heating = 2 if "加热" in normalized else state.cabin.seat_heating
        return await execute_tool(state, "seat_control", {"massage": massage, "ventilation": ventilation, "heating": heating})
    match = re.search(r"(1[89]|2[0-8])\s*度", normalized)
    if match or "空调" in normalized:
        return await execute_tool(state, "climate_control", {"temperature": int(match.group(1)) if match else 23, "mode": "auto"})
    if any(word in normalized for word in ("困", "疲劳", "累")):
        return await execute_tool(state, "safety_service", {"action": "suggest_rest_stop", "level": "warning", "message": "我理解您现在很疲惫。建议尽快在前方休息区休息，我也可以为您开启低强度按摩。"})
    return "我在。您可以让我查询天气、规划导航，或调节空调、媒体和座椅。"
