from __future__ import annotations

from uuid import uuid4
from .safety import evaluate
from .schemas import GateDecision, PendingAction, SessionState, ToolLog
from .services import AmapServiceError, WeatherServiceError, driving_route, now_iso, search_poi, weather


async def execute_tool(state: SessionState, tool: str, args: dict, proactive: bool = False, skip_gate: bool = False) -> str:
    aliases = {
        "search_poi": ("navigation_service", {"action": "search", "destination": args.get("query", args.get("destination", ""))}),
        "plan_route": ("navigation_service", {"action": "preview", "destination": args.get("destination")}),
        "start_navigation": ("navigation_service", {"action": "start"}),
        "stop_navigation": ("navigation_service", {"action": "cancel"}),
        "set_temperature": ("climate_control", args),
        "set_seat": ("seat_control", args),
        "set_media": ("media_control", args),
        "get_weather": ("weather_service", args),
        "emit_safety_alert": ("safety_service", args),
    }
    if tool == "set_window":
        state.cabin.window_open_percent = max(0, min(100, int(args.get("open_percent", 0))))
        return f"车窗已调整为开启 {state.cabin.window_open_percent}%。"
    if tool == "get_vehicle_status":
        return f"当前车速 {state.vehicle.speed_kmh:.1f} km/h，驾驶时长 {state.driver.driving_duration_minutes:.1f} 分钟。"
    tool, args = aliases.get(tool, (tool, args))
    gate = evaluate(tool, args, state, proactive) if not skip_gate else None
    decision = gate.decision if gate else GateDecision.ALLOW
    effective = gate.args if gate else args
    message = gate.message if gate else "已确认执行。"
    state.tool_logs.insert(0, ToolLog(tool=tool, args=effective, decision=decision, message=message, timestamp=now_iso()))
    state.tool_logs = state.tool_logs[:20]
    if decision == GateDecision.BLOCK:
        return message
    if decision == GateDecision.CONFIRM:
        state.pending_action = PendingAction(id=str(uuid4()), tool=tool, args=effective, prompt=message)
        return f"{message}"

    if tool == "weather_service":
        target = state.navigation.destination if effective.get("action") == "destination" and state.navigation.destination else {"lat": state.vehicle.latitude, "lng": state.vehicle.longitude, "name": "当前位置"}
        try:
            state.weather = await weather(target["lat"], target["lng"])
        except WeatherServiceError as exc:
            return str(exc)
        return f"{target.get('name', '当前位置')} {state.weather['temperature']}℃，{state.weather['weather']}，降水概率 {state.weather['precipitation_probability']}%。"
    if tool == "navigation_service":
        action = effective.get("action")
        if action == "search":
            try:
                state.navigation.candidates = await search_poi(effective.get("destination", ""))
            except AmapServiceError as exc:
                return str(exc)
            if not state.navigation.candidates:
                return "高德未找到匹配地点，请换一个更完整的地点名称。"
            state.navigation.status = "selecting"
            names = "、".join(p["name"] for p in state.navigation.candidates[:3])
            return f"我找到了：{names}。请告诉我您要去哪里。"
        if action == "preview":
            destination = effective.get("destination") or (state.navigation.candidates[0] if state.navigation.candidates else None)
            if isinstance(destination, str):
                destination = next((p for p in state.navigation.candidates if destination in p["name"]), state.navigation.candidates[0] if state.navigation.candidates else None)
            if not destination:
                return "请先告诉我目的地。"
            try:
                route = await driving_route(state.vehicle.longitude, state.vehicle.latitude, destination)
            except AmapServiceError as exc:
                return str(exc)
            state.navigation.destination = destination
            state.navigation.route = route
            state.navigation.status = "preview"
            state.navigation.progress = 0
            state.navigation.remaining_distance_km = route["distance_km"]
            state.navigation.simulated_speed_kmh = 0
            state.navigation.simulated_elapsed_minutes = 0
            return f"已找到{destination['name']}，全程约 {route['distance_km']} 公里，预计 {route['duration_minutes']} 分钟。现在开始导航吗？"
        if action == "start":
            state.navigation.status = "active"
            state.vehicle.speed_kmh = 0
            return f"导航已开始，预计 {state.navigation.route['duration_minutes']} 分钟到达。"
        if action == "cancel":
            state.navigation.status = "idle"; state.navigation.route = None; state.navigation.destination = None
            state.navigation.progress = 0; state.navigation.remaining_distance_km = 0; state.navigation.simulated_speed_kmh = 0; state.navigation.simulated_elapsed_minutes = 0
            state.vehicle.speed_kmh = 0; state.driver.driving_duration_minutes = 0
            return "已取消导航。"
    if tool == "climate_control":
        state.cabin.temperature = int(effective.get("temperature", state.cabin.temperature)); state.cabin.climate_mode = effective.get("mode", state.cabin.climate_mode)
        return f"已将座舱温度调至 {state.cabin.temperature}℃，模式为 {state.cabin.climate_mode}。"
    if tool == "media_control":
        state.cabin.media_mode = effective.get("mode", state.cabin.media_mode); state.cabin.volume = int(effective.get("volume", state.cabin.volume))
        return f"媒体已切换为{state.cabin.media_mode}，音量 {state.cabin.volume}。"
    if tool == "seat_control":
        state.cabin.seat_heating = int(effective.get("heating", state.cabin.seat_heating)); state.cabin.seat_ventilation = int(effective.get("ventilation", state.cabin.seat_ventilation)); state.cabin.seat_massage = int(effective.get("massage", state.cabin.seat_massage))
        return f"座椅已更新：加热 {state.cabin.seat_heating} 档，通风 {state.cabin.seat_ventilation} 档，按摩 {state.cabin.seat_massage} 档。"
    if tool == "safety_service":
        state.active_alert = effective.get("message", "请注意驾驶安全。")
        return state.active_alert
    return message


async def confirm(state: SessionState, accepted: bool) -> str:
    action = state.pending_action
    if not action:
        return "当前没有需要确认的操作。"
    state.pending_action = None
    if not accepted:
        return "好的，已取消该操作。"
    return await execute_tool(state, action.tool, action.args, skip_gate=True)


async def proactive_check(state: SessionState) -> list[str]:
    messages: list[str] = []
    if state.vehicle.ignition_on and not state.weather:
        messages.append(await execute_tool(state, "weather_service", {"action": "current"}))
    if state.driver.fatigue_level >= 0.8 or state.driver.driving_duration_minutes >= 120:
        messages.append(await execute_tool(state, "safety_service", {"action": "alert", "level": "critical", "message": "检测到明显疲劳风险，请尽快进入休息区休息。"}))
    elif state.cabin.temperature >= 27:
        messages.append(await execute_tool(state, "climate_control", {"temperature": 23, "mode": "auto"}, proactive=True))
    return messages
