from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Literal, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .agent import candidate_prompt, resolve_candidate_with_llm, select_candidate
from .config import settings
from .engine import execute_tool
from .preferences import delete_preference, save_preference
from .schemas import SessionState
from .services import now_iso

logger = logging.getLogger("cabinguard")


class CabinGuardState(TypedDict, total=False):
    session_id: str
    profile_id: str | None
    vehicle: dict[str, Any]
    driver: dict[str, Any]
    cabin: dict[str, Any]
    navigation: dict[str, Any]
    messages: list[dict[str, str]]
    pending_action: dict[str, Any] | None
    tool_logs: list[dict[str, Any]]
    active_alert: str | None
    weather: dict[str, Any] | None
    trigger_state: dict[str, Any]
    provider_status: dict[str, Any]
    execution_trace: list[dict[str, Any]]
    agent_status: str
    final_response: str | None
    response_source: str | None
    user_preferences: dict[str, str]
    event: str
    text: str
    candidate_id: str | None
    event_payload: dict[str, Any]
    planned_calls: list[dict[str, Any]]
    planner_reply: str | None
    approval_accepted: bool | None


TOOL_NAMES = {
    "search_poi", "plan_route", "start_navigation", "stop_navigation", "set_temperature",
    "set_window", "set_seat", "set_media", "get_weather", "get_vehicle_status",
    "emit_safety_alert", "save_user_preference", "delete_user_preference",
}


def _domain(state: CabinGuardState) -> SessionState:
    return SessionState(**{key: value for key, value in state.items() if key in SessionState.model_fields})


def _domain_update(domain: SessionState) -> dict[str, Any]:
    return domain.model_dump(mode="json", exclude={"session_id", "profile_id"})


def _trace(state: CabinGuardState, node: str, detail: str = "") -> list[dict[str, Any]]:
    trace = [*state.get("execution_trace", []), {"node": node, "detail": detail, "timestamp": now_iso()}]
    return trace[-30:]


def _final(state: CabinGuardState, text: str, source: str) -> dict[str, Any]:
    if state.get("event") == "proactive" and not text:
        return {"final_response": None, "agent_status": "idle", "execution_trace": _trace(state, "response_builder", "no_notification")}
    messages = [*state.get("messages", []), {"role": "assistant", "content": text}][-20:]
    return {"messages": messages, "final_response": text, "response_source": source, "agent_status": "completed", "execution_trace": _trace(state, "response_builder", source)}


def _is_yes(text: str) -> bool:
    return any(word in text for word in ("确认", "可以", "好的", "同意", "是", "开始"))


def _is_no(text: str) -> bool:
    return any(word in text for word in ("取消", "不要", "算了", "否"))


def _weather_arguments(state: CabinGuardState, text: str) -> dict[str, Any]:
    if state.get("navigation", {}).get("destination") and any(word in text for word in ("目的地", "那里", "那边")):
        return {"action": "destination"}
    location = re.sub(r"(帮我|请|查询|看看|一下|天气预报|天气怎么样|天气如何|的天气|天气|下雨|温度)", "", text).strip()
    return {"action": "location", "location": location} if len(location) >= 2 and location not in {"当前", "这里", "现在"} else {"action": "current"}


def _seat_arguments(state: CabinGuardState, text: str) -> dict[str, Any]:
    cabin = state.get("cabin", {})
    if "关闭座椅" in text or "关闭加热" in text or "关闭按摩" in text or "全关" in text:
        return {"heating": 0, "massage": 0}
    heating = 1 if "加热" in text and any(word in text for word in ("太烫", "热了", "低一点", "一档")) else 2 if "加热" in text else cabin.get("seat_heating", 0)
    massage = 3 if any(word in text for word in ("最大", "最强", "三档")) else 1 if "按摩" in text else cabin.get("seat_massage", 0)
    return {"heating": heating, "massage": massage}


def _climate_arguments(state: CabinGuardState, text: str) -> dict[str, Any]:
    if any(word in text for word in ("关闭空调", "关空调", "空调关", "空调关闭")):
        return {"mode": "off"}
    match = re.search(r"(\d{1,2})\s*度", text)
    return {"temperature": int(match.group(1)) if match else state.get("cabin", {}).get("temperature", 23), "mode": "auto"}


def router(state: CabinGuardState) -> Command[Literal["candidate", "planner", "safety", "approval", "proactive", "finish"]]:
    event = state.get("event", "message")
    text = state.get("text", "").strip()
    if event == "proactive":
        return Command(goto="proactive", update={"agent_status": "checking", "execution_trace": _trace(state, "input_router", "proactive")})
    if state.get("pending_action"):
        if event == "resume" or _is_yes(text) or _is_no(text):
            return Command(goto="approval", update={"approval_accepted": True if event == "resume" and state.get("event_payload", {}).get("approved") else _is_yes(text), "agent_status": "awaiting_confirmation"})
        return Command(goto="finish", update=_final(state, state["pending_action"]["prompt"], "rule"))
    if event == "message":
        messages = [*state.get("messages", []), {"role": "user", "content": text}][-20:]
        base = {"messages": messages, "agent_status": "understanding", "execution_trace": _trace(state, "input_router", "message")}
        if any(word in text.replace(" ", "") for word in ("取消导航", "结束导航", "停止导航", "不导航了")):
            return Command(goto="safety", update={**base, "planned_calls": [{"name": "stop_navigation", "arguments": {}}], "planner_reply": None, "response_source": "rule"})
        if state.get("navigation", {}).get("status") == "selecting":
            return Command(goto="candidate", update=base)
        return Command(goto="planner", update=base)
    return Command(goto="finish", update={"agent_status": "idle"})


async def candidate(state: CabinGuardState) -> dict[str, Any]:
    candidates = state.get("navigation", {}).get("candidates", [])
    selected = next((item for item in candidates if item.get("id") == state.get("candidate_id")), None)
    if not selected:
        selected = select_candidate(candidates, state.get("text", ""))
    if not selected:
        selected = await resolve_candidate_with_llm(candidates, state.get("text", ""))
    if selected:
        return {"planned_calls": [{"name": "plan_route", "arguments": {"destination": selected}}], "planner_reply": None, "execution_trace": _trace(state, "candidate_resolver", "selected")}
    return {"planned_calls": [], "planner_reply": candidate_prompt(candidates), "execution_trace": _trace(state, "candidate_resolver", "unresolved")}


async def _deepseek_plan(state: CabinGuardState, text: str) -> dict[str, Any] | None:
    if not settings.llm_enabled:
        return None
    prompt = {
        "role": "system", "content": """你是 CabinGuard 的语义规划器。只输出 JSON：{intent,reply,tool_calls:[{name,arguments}]}。理解自然中文、同义表达、上下文和省略信息；车控、天气和导航必须生成工具调用，不要只用文字承诺。
工具参数：search_poi {query}；plan_route {destination}；start_navigation {}；stop_navigation {}；get_weather {action: current|destination|location, location?: 地名}；set_temperature {mode: off|auto|cool|heat|fan, temperature?: 整数}；set_seat {heating: 0|1|2|3, massage: 0|1|2|3}（只支持加热和按摩；“太烫/热了/低一点”对应加热 1 档，关闭对应 0）；set_media {mode: music|podcast|video|off, volume?: 0-100}；set_window {open_percent: 0-100}；get_vehicle_status {}；emit_safety_alert {action,level,message}；save_user_preference {key,value}；delete_user_preference {key}。新的导航请求必须先调用 search_poi，等待用户从候选地点中选择后才调用 plan_route，预览路线后才调用 start_navigation。工具名只能是：""" + ",".join(sorted(TOOL_NAMES)),
    }
    context = [prompt, {"role": "system", "content": json.dumps({"vehicle": state.get("vehicle"), "navigation": state.get("navigation"), "preferences": state.get("user_preferences", {})}, ensure_ascii=False)}, {"role": "user", "content": text}]
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=3), trust_env=settings.deepseek_use_env_proxy) as client:
            response = await client.post(f"{settings.deepseek_base_url.rstrip('/')}/chat/completions", headers={"Authorization": f"Bearer {settings.deepseek_api_key}"}, json={"model": settings.deepseek_model, "messages": context, "temperature": 0.1, "response_format": {"type": "json_object"}})
            response.raise_for_status()
        result = json.loads(response.json()["choices"][0]["message"].get("content") or "{}")
        calls = result.get("tool_calls", [])
        if not isinstance(calls, list) or any(not isinstance(call, dict) or call.get("name") not in TOOL_NAMES for call in calls):
            raise ValueError("invalid planner tool call")
        return result
    except Exception as exc:
        logger.warning("DeepSeek planner failed (%s)", type(exc).__name__)
        return None
    finally:
        status = state.get("provider_status", {})
        status["deepseek"] = {"available": settings.llm_enabled, "latency_ms": round((time.monotonic() - started) * 1000)}


def _fallback_plan(state: CabinGuardState, text: str) -> dict[str, Any]:
    normalized = text.replace("。", "").replace("，", "")
    if state.get("navigation", {}).get("status") == "preview" and any(word in normalized for word in ("开始", "出发", "导航")):
        return {"reply": None, "tool_calls": [{"name": "start_navigation", "arguments": {}}]}
    if any(word in normalized for word in ("结束导航", "取消导航", "停止导航")):
        return {"reply": None, "tool_calls": [{"name": "stop_navigation", "arguments": {}}]}
    if any(word in normalized for word in ("天气", "下雨", "温度")):
        return {"reply": None, "tool_calls": [{"name": "get_weather", "arguments": _weather_arguments(state, normalized)}]}
    if any(word in normalized for word in ("带我去", "导航到", "去")):
        destination = re.sub(r".*?(带我去|导航到|去)", "", normalized).strip()
        return {"reply": None, "tool_calls": [{"name": "search_poi", "arguments": {"query": destination or "虹桥站"}}]}
    match = re.search(r"(1[89]|2[0-8])\s*度", normalized)
    if match or "空调" in normalized:
        return {"reply": None, "tool_calls": [{"name": "set_temperature", "arguments": _climate_arguments(state, normalized)}]}
    if "座椅" in normalized and any(word in normalized for word in ("加热", "按摩", "关闭", "关掉", "全关")):
        return {"reply": None, "tool_calls": [{"name": "set_seat", "arguments": _seat_arguments(state, normalized)}]}
    if "视频" in normalized or "电影" in normalized:
        return {"reply": None, "tool_calls": [{"name": "set_media", "arguments": {"mode": "video"}}]}
    if "音乐" in normalized:
        return {"reply": None, "tool_calls": [{"name": "set_media", "arguments": {"mode": "music"}}]}
    return {"reply": "我在。您可以让我查询天气、规划导航，或调节空调、媒体和座椅。", "tool_calls": []}


async def planner(state: CabinGuardState) -> dict[str, Any]:
    plan = await _deepseek_plan(state, state.get("text", ""))
    source = "deepseek" if plan else "fallback"
    plan = plan or _fallback_plan(state, state.get("text", ""))
    return {"planned_calls": plan.get("tool_calls", []), "planner_reply": plan.get("reply"), "response_source": source, "execution_trace": _trace(state, "planner", source)}


def safety(state: CabinGuardState) -> dict[str, Any]:
    calls = state.get("planned_calls", [])
    if not calls:
        return {"execution_trace": _trace(state, "safety_gate", "no_tool")}
    domain = _domain(state)
    from .safety import evaluate
    call = calls[0]
    # evaluate through the legacy service name so the existing rules remain authoritative.
    name = call["name"]
    aliases = {"start_navigation": "navigation_service", "stop_navigation": "navigation_service", "plan_route": "navigation_service", "search_poi": "navigation_service", "set_temperature": "climate_control", "set_media": "media_control", "set_seat": "seat_control", "get_weather": "weather_service", "emit_safety_alert": "safety_service"}
    args = call.get("arguments", {})
    legacy_args = args
    if name == "start_navigation": legacy_args = {"action": "start"}
    elif name == "stop_navigation": legacy_args = {"action": "cancel"}
    elif name == "plan_route": legacy_args = {"action": "preview", "destination": args.get("destination")}
    elif name == "search_poi": legacy_args = {"action": "search", "destination": args.get("query", "")}
    gate = evaluate(aliases.get(name, name), legacy_args, domain, proactive=state.get("event") == "proactive")
    if gate.decision.value == "CONFIRM":
        action = {"id": f"{state['session_id']}:{len(state.get('tool_logs', []))}", "tool": name, "args": gate.args, "prompt": gate.message, "created_at": now_iso()}
        return {"pending_action": action, "agent_status": "awaiting_confirmation", "execution_trace": _trace(state, "safety_gate", "confirm")}
    if gate.decision.value == "BLOCK":
        return {"planned_calls": [], "planner_reply": gate.message, "execution_trace": _trace(state, "safety_gate", "deny")}
    if gate.decision.value == "MODIFY":
        effective = dict(args)
        if name == "set_temperature":
            effective = gate.args
        elif name == "search_poi":
            effective["query"] = gate.args.get("destination", effective.get("query", ""))
        elif name == "plan_route":
            effective["destination"] = gate.args.get("destination")
        calls = [{**call, "arguments": effective}, *calls[1:]]
        return {"planned_calls": calls, "planner_reply": gate.message, "execution_trace": _trace(state, "safety_gate", "modify")}
    return {"execution_trace": _trace(state, "safety_gate", gate.decision.value.lower())}


def next_after_safety(state: CabinGuardState) -> str:
    if state.get("pending_action"):
        return "approval"
    return "executor" if state.get("planned_calls") else "finish"


def approval(state: CabinGuardState) -> dict[str, Any]:
    action = state.get("pending_action")
    approved = state.get("approval_accepted")
    if approved is None:
        approved = interrupt({"action_id": action["id"], "prompt": action["prompt"]})
    if isinstance(approved, dict):
        approved = approved.get("approved", False)
    if not approved:
        return {"pending_action": None, "planned_calls": [], "planner_reply": "好的，已取消该操作。", "execution_trace": _trace(state, "approval_interrupt", "rejected")}
    return {"pending_action": None, "planned_calls": [{"name": action["tool"], "arguments": action["args"]}], "execution_trace": _trace(state, "approval_interrupt", "accepted")}


async def executor(state: CabinGuardState) -> dict[str, Any]:
    domain = _domain(state)
    replies: list[str] = []
    for call in state.get("planned_calls", []):
        name, arguments = call["name"], call.get("arguments", {})
        if name == "save_user_preference" and state.get("profile_id"):
            await save_preference(str(Path(__file__).resolve().parents[1] / "data" / "cabinguard.db"), state["profile_id"], arguments["key"], arguments["value"], now_iso())
            replies.append("已记住您的偏好。")
        elif name == "delete_user_preference" and state.get("profile_id"):
            await delete_preference(str(Path(__file__).resolve().parents[1] / "data" / "cabinguard.db"), state["profile_id"], arguments["key"])
            replies.append("已删除该偏好。")
        else:
            replies.append(await execute_tool(domain, name, arguments, skip_gate=True))
    update = _domain_update(domain)
    update.update({"planner_reply": " ".join(replies), "planned_calls": [], "execution_trace": _trace(state, "tool_executor", "executed")})
    return update


async def proactive(state: CabinGuardState) -> dict[str, Any]:
    domain = _domain(state)
    trigger = dict(state.get("trigger_state", {}))
    now = time.time()
    calls: list[dict[str, Any]] = []
    messages: list[str] = []
    def due(key: str, seconds: int = 600) -> bool:
        return now - float(trigger.get(key, 0)) >= seconds
    scenario = state.get("event_payload", {}).get("scenario_id")
    if scenario in {"commute", "rainy"}:
        calls.append({"name": "get_weather", "arguments": {"action": "current", "rainy_scenario": scenario == "rainy"}})
        messages.append("雨天出行场景已加载，正在查询当前位置的真实天气。" if scenario == "rainy" else "正常通勤场景已加载，正在查询当前位置的真实天气。")
    elif domain.driver.fatigue_level >= .8 and due("fatigue_critical", 60):
        trigger["fatigue_critical"] = now
        calls.append({"name": "emit_safety_alert", "arguments": {"action": "alert", "level": "critical", "message": "检测到明显疲劳风险，请尽快进入休息区休息。"}})
    elif domain.driver.driving_duration_minutes >= 120 and due("rest"):
        trigger["rest"] = now
        calls.append({"name": "emit_safety_alert", "arguments": {"action": "alert", "level": "warning", "message": "您已连续驾驶较长时间，建议尽快休息。"}})
    elif domain.driver.attention_level <= .4 and due("attention", 300):
        trigger["attention"] = now
        message = "检测到注意力较低，请集中注意力并注意前方路况。"
        if domain.cabin.media_mode in {"music", "podcast"}:
            calls.append({"name": "set_media", "arguments": {"mode": domain.cabin.media_mode, "volume": min(100, domain.cabin.volume + 10)}})
            message += " 我可以将当前媒体音量稍微提高。"
        return {"trigger_state": trigger, "active_alert": message, "planned_calls": calls, "planner_reply": message, "execution_trace": _trace(state, "proactive_evaluator", "attention")}
    elif domain.vehicle.ignition_on and not trigger.get("ignition"):
        trigger["ignition"] = now
        calls.append({"name": "get_weather", "arguments": {"action": "current"}})
        messages.append("车辆已点火，正在检查当前天气和车辆状态。")
    elif domain.cabin.climate_mode == "off" and (domain.cabin.temperature > 28 or domain.cabin.temperature < 18) and due("climate"):
        trigger["climate"] = now
        calls.append({"name": "set_temperature", "arguments": {"temperature": 23, "mode": "auto"}})
    return {"trigger_state": trigger, "planned_calls": calls, "planner_reply": " ".join(messages) or None, "execution_trace": _trace(state, "proactive_evaluator", str(len(calls)))}


def finish(state: CabinGuardState) -> dict[str, Any]:
    reply = state.get("planner_reply") or (None if state.get("event") == "proactive" else state.get("final_response")) or ""
    return _final(state, reply, state.get("response_source", "rule"))


def build_graph(checkpointer: Any):
    builder = StateGraph(CabinGuardState)
    builder.add_node("router", router)
    builder.add_node("candidate", candidate)
    builder.add_node("planner", planner)
    builder.add_node("safety", safety)
    builder.add_node("approval", approval)
    builder.add_node("executor", executor)
    builder.add_node("proactive", proactive)
    builder.add_node("finish", finish)
    builder.add_edge(START, "router")
    builder.add_edge("candidate", "safety")
    builder.add_edge("planner", "safety")
    builder.add_conditional_edges("safety", next_after_safety, {"approval": "approval", "executor": "executor", "finish": "finish"})
    builder.add_conditional_edges("approval", next_after_safety, {"approval": "approval", "executor": "executor", "finish": "finish"})
    builder.add_edge("executor", "finish")
    builder.add_edge("proactive", "safety")
    builder.add_edge("finish", END)
    return builder.compile(checkpointer=checkpointer)
