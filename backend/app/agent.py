from __future__ import annotations

import json
import logging
import re
from difflib import SequenceMatcher
import httpx

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
logger = logging.getLogger("cabinguard")


def normalize_place(value: str) -> str:
    """Normalize POI names so users can omit punctuation or branch decorations."""
    value = value.strip().lower().replace("（", "(").replace("）", ")")
    value = re.sub(r"[\s,，。!！?？、]", "", value)
    return value


CHINESE_INDEX = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "两": 2}


def candidate_aliases(candidate: dict) -> set[str]:
    name = normalize_place(candidate["name"])
    aliases = {name}
    bracket = re.findall(r"\(([^)]+)\)", name)
    aliases.update(bracket)
    aliases.add(re.sub(r"\([^)]*\)", "", name))
    for city in ("上海市", "上海", "北京市", "北京", "杭州市", "杭州"):
        aliases.add(name.removeprefix(city))
    return {alias for alias in aliases if len(alias) >= 2}


def normalized_user_place(text: str) -> str:
    value = normalize_place(text)
    value = re.sub(r"^(我想去|我要去|带我去|去|选|选择|就是|那个|这个|刚才|请导航到)", "", value)
    value = re.sub(r"(那个|这个|吧|呀|啊)$", "", value)
    return value


def select_candidate(candidates: list[dict], text: str) -> dict | None:
    normalized = normalized_user_place(text)
    numeric = re.search(r"(?:第)?([1-5])(?:个|号|项)?", normalized)
    chinese = re.search(r"第?([一二三四五两])(?:个|号|项)?", normalized)
    index = int(numeric.group(1)) if numeric else CHINESE_INDEX.get(chinese.group(1)) if chinese else None
    if index and index <= len(candidates):
        return candidates[index - 1]
    ranked: list[tuple[float, dict]] = []
    for candidate in candidates:
        for alias in candidate_aliases(candidate):
            if normalized == alias or (len(normalized) >= 2 and normalized in alias):
                return candidate
            score = SequenceMatcher(None, normalized, alias).ratio()
            if normalized and alias:
                overlap = len(set(normalized) & set(alias)) / max(1, len(set(normalized)))
                score = max(score, overlap * 0.9)
            ranked.append((score, candidate))
    score, candidate = max(ranked, default=(0, None), key=lambda item: item[0])
    return candidate if score >= 0.78 else None


async def resolve_candidate_with_llm(candidates: list[dict], text: str) -> dict | None:
    """Ask DeepSeek to select one existing POI only; never permit a new destination."""
    if not settings.llm_enabled:
        return None
    options = [{"index": index + 1, "name": candidate["name"], "address": candidate.get("address", "")} for index, candidate in enumerate(candidates)]
    endpoint = f"{settings.deepseek_base_url.rstrip('/')}/chat/completions"
    messages = [
        {"role": "system", "content": "你是导航候选项消歧器。只能从提供的候选项中选一个。若不能确定，返回 {\"index\": null, \"confidence\": 0}。只输出 JSON。"},
        {"role": "user", "content": json.dumps({"user_utterance": text, "candidates": options}, ensure_ascii=False)},
    ]
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=3.0), trust_env=settings.deepseek_use_env_proxy) as client:
            response = await client.post(endpoint, headers={"Authorization": f"Bearer {settings.deepseek_api_key}"}, json={
                "model": settings.deepseek_model, "messages": messages, "temperature": 0, "response_format": {"type": "json_object"},
            })
            response.raise_for_status()
            content = response.json()["choices"][0]["message"].get("content") or "{}"
        decision = json.loads(content)
        index = decision.get("index")
        confidence = float(decision.get("confidence", 0))
        if isinstance(index, int) and 1 <= index <= len(candidates) and confidence >= 0.55:
            logger.info("DeepSeek resolved navigation candidate %s for session input", index)
            return candidates[index - 1]
    except Exception as exc:
        logger.warning("DeepSeek candidate resolution failed (%s)", type(exc).__name__)
    return None


def candidate_prompt(candidates: list[dict]) -> str:
    options = "、".join(f"{index + 1}. {candidate['name']}" for index, candidate in enumerate(candidates[:3]))
    return f"我还不能确认具体目的地。您可以说“第一个”“第三个”或地点中的关键词，例如“北进站口”。候选为：{options}。"


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
        selected = select_candidate(state.navigation.candidates, text)
        if not selected:
            selected = await resolve_candidate_with_llm(state.navigation.candidates, text)
        if selected:
            response = await execute_tool(state, "navigation_service", {"action": "preview", "destination": selected})
            return remember(state, response)
        return remember(state, candidate_prompt(state.navigation.candidates))

    # DeepSeek is the primary reasoning and tool-selection layer for all new turns.
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
        context = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": f"车辆速度 {state.vehicle.speed_kmh}km/h；疲劳 {state.driver.fatigue_level:.2f}；导航状态 {state.navigation.status}。"},
            *state.messages[-8:],
        ]
        endpoint = f"{settings.deepseek_base_url.rstrip('/')}/chat/completions"
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=3.0),
            trust_env=settings.deepseek_use_env_proxy,
        ) as client:
            response = await client.post(endpoint, headers={"Authorization": f"Bearer {settings.deepseek_api_key}"}, json={
                "model": settings.deepseek_model,
                "messages": context,
                "tools": TOOLS,
                "tool_choice": "auto",
                "temperature": 0.2,
            })
            response.raise_for_status()
            message = response.json()["choices"][0]["message"]
        logger.info("DeepSeek completed a turn for session %s", state.session_id)
        if not message.get("tool_calls"):
            return message.get("content")
        results = []
        for call in message["tool_calls"]:
            function = call["function"]
            args = json.loads(function.get("arguments") or "{}")
            results.append(await execute_tool(state, function["name"], args))
        return " ".join(results)
    except Exception as exc:
        logger.warning("DeepSeek request failed (%s); using local fallback", type(exc).__name__)
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
