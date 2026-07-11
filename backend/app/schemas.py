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
    driving_duration_minutes: int = Field(default=0, ge=0)


class VehicleState(BaseModel):
    speed_kmh: float = Field(default=0, ge=0)
    ignition_on: bool = False
    latitude: float = 31.2304
    longitude: float = 121.4737


class CabinState(BaseModel):
    temperature: int = Field(default=25, ge=16, le=32)
    climate_mode: str = "auto"
    media_mode: str = "off"
    volume: int = Field(default=25, ge=0, le=100)
    seat_heating: int = Field(default=0, ge=0, le=3)
    seat_ventilation: int = Field(default=0, ge=0, le=3)
    seat_massage: int = Field(default=0, ge=0, le=3)


class NavigationState(BaseModel):
    status: Literal["idle", "selecting", "preview", "active"] = "idle"
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    destination: dict[str, Any] | None = None
    route: dict[str, Any] | None = None
    progress: float = Field(default=0, ge=0, le=1)


class PendingAction(BaseModel):
    id: str
    tool: str
    args: dict[str, Any]
    prompt: str


class ToolLog(BaseModel):
    tool: str
    args: dict[str, Any]
    decision: GateDecision
    message: str
    timestamp: str


class SessionState(BaseModel):
    session_id: str
    vehicle: VehicleState = Field(default_factory=VehicleState)
    driver: DriverState = Field(default_factory=DriverState)
    cabin: CabinState = Field(default_factory=CabinState)
    navigation: NavigationState = Field(default_factory=NavigationState)
    messages: list[dict[str, str]] = Field(default_factory=list)
    pending_action: PendingAction | None = None
    tool_logs: list[ToolLog] = Field(default_factory=list)
    active_alert: str | None = None
    weather: dict[str, Any] | None = None


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class SimulationPatch(BaseModel):
    vehicle: VehicleState | None = None
    driver: DriverState | None = None

