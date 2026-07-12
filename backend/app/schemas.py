from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field


class GateDecision(str, Enum):
    ALLOW = "ALLOW"
    MODIFY = "MODIFY"
    CONFIRM = "CONFIRM"
    BLOCK = "BLOCK"


class DriverState(BaseModel):
    fatigue_level: float = Field(default=0.2, ge=0, le=1)
    attention_level: float = Field(default=0.9, ge=0, le=1)
    stress_level: float = Field(default=0.2, ge=0, le=1)
    mood: str = "normal"
    driving_duration_minutes: float = Field(default=0, ge=0)


class VehicleState(BaseModel):
    speed_kmh: float = Field(default=0, ge=0)
    ignition_on: bool = False
    latitude: float = 31.2304
    longitude: float = 121.4737
    wiper_on: bool = False


class CabinState(BaseModel):
    temperature: int = Field(default=25, ge=16, le=32)
    climate_mode: str = "auto"
    media_mode: str = "off"
    volume: int = Field(default=25, ge=0, le=100)
    seat_heating: int = Field(default=0, ge=0, le=3)
    seat_ventilation: int = Field(default=0, ge=0, le=3)
    seat_massage: int = Field(default=0, ge=0, le=3)
    window_open_percent: int = Field(default=0, ge=0, le=100)


class NavigationState(BaseModel):
    status: Literal["idle", "selecting", "preview", "active"] = "idle"
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    destination: dict[str, Any] | None = None
    route: dict[str, Any] | None = None
    progress: float = Field(default=0, ge=0, le=1)
    remaining_distance_km: float = Field(default=0, ge=0)
    simulated_speed_kmh: float = Field(default=0, ge=0)
    simulated_elapsed_minutes: float = Field(default=0, ge=0)


class PendingAction(BaseModel):
    id: str
    tool: str
    args: dict[str, Any]
    prompt: str
    created_at: str | None = None


class ToolLog(BaseModel):
    tool: str
    args: dict[str, Any]
    decision: GateDecision
    message: str
    timestamp: str


class SessionState(BaseModel):
    session_id: str
    profile_id: str | None = None
    vehicle: VehicleState = Field(default_factory=VehicleState)
    driver: DriverState = Field(default_factory=DriverState)
    cabin: CabinState = Field(default_factory=CabinState)
    navigation: NavigationState = Field(default_factory=NavigationState)
    messages: list[dict[str, str]] = Field(default_factory=list)
    pending_action: PendingAction | None = None
    tool_logs: list[ToolLog] = Field(default_factory=list)
    active_alert: str | None = None
    weather: dict[str, Any] | None = None
    trigger_state: dict[str, Any] = Field(default_factory=dict)
    provider_status: dict[str, Any] = Field(default_factory=dict)
    execution_trace: list[dict[str, Any]] = Field(default_factory=list)
    agent_status: str = "idle"
    final_response: str | None = None
    response_source: str | None = None


class MessageIn(BaseModel):
    text: str = Field(default="", max_length=1000)
    candidate_id: str | None = None


class SimulationPatch(BaseModel):
    vehicle: VehicleState | None = None
    driver: DriverState | None = None


class SessionCreateIn(BaseModel):
    profile_id: str | None = Field(default=None, max_length=128)


class ResumeIn(BaseModel):
    action_id: str
    approved: bool
