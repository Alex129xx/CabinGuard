from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from .schemas import GateDecision, SessionState


@dataclass
class GateResult:
    decision: GateDecision
    args: dict
    message: str


def evaluate(tool: str, args: dict, state: SessionState, proactive: bool = False) -> GateResult:
    effective = deepcopy(args)
    speed = state.vehicle.speed_kmh
    driver = state.driver

    if tool == "media_control" and effective.get("mode") == "video" and (speed > 0 or state.navigation.status == "active"):
        return GateResult(GateDecision.BLOCK, effective, "行驶过程中无法播放视频。")
    if tool == "seat_control" and speed > 80 and int(effective.get("massage", 0)) > 1:
        effective["massage"] = 1
        return GateResult(GateDecision.MODIFY, effective, "高速行驶时已将按摩强度调整为低档。")
    if tool == "climate_control" and "temperature" in effective:
        requested = int(effective["temperature"])
        adjusted = max(18, min(28, requested))
        if adjusted != requested:
            effective["temperature"] = adjusted
            return GateResult(GateDecision.MODIFY, effective, f"温度已调整到安全范围内的 {adjusted}℃。")
    if tool == "navigation_service" and effective.get("action") == "start":
        if state.navigation.destination is None:
            return GateResult(GateDecision.BLOCK, effective, "请先选择明确的目的地。")
        if state.navigation.status != "preview":
            return GateResult(GateDecision.CONFIRM, effective, "路线尚未预览，是否先为您规划路线？")
    if tool == "navigation_service" and effective.get("action") == "preview" and state.navigation.status == "active":
        return GateResult(GateDecision.CONFIRM, effective, "当前正在导航，确定要更换目的地吗？")
    if tool == "navigation_service" and effective.get("action") == "search" and state.navigation.status == "active":
        return GateResult(GateDecision.CONFIRM, effective, "当前正在导航，确定要更换目的地吗？")
    if tool in {"climate_control", "media_control", "seat_control"} and proactive:
        return GateResult(GateDecision.CONFIRM, effective, "我可以为您执行这项舒适性调节，是否确认？")
    if driver.fatigue_level >= 0.8 and speed >= 80 and tool == "safety_service":
        return GateResult(GateDecision.ALLOW, effective, "检测到明显疲劳风险，正在发出安全提醒。")
    return GateResult(GateDecision.ALLOW, effective, "操作已通过安全检查。")
