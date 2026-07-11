from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import httpx

from .config import settings
from .schemas import SessionState


DEMO_POIS = [
    {"name": "上海虹桥火车站", "address": "上海市闵行区申贵路1500号", "lat": 31.1942, "lng": 121.3217},
    {"name": "上海虹桥机场 T2", "address": "上海市闵行区申达一路", "lat": 31.1978, "lng": 121.3380},
    {"name": "杭州西湖", "address": "杭州市西湖区", "lat": 30.2310, "lng": 120.1480},
]


async def weather(latitude: float, longitude: float) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": latitude, "longitude": longitude,
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                "hourly": "precipitation_probability", "forecast_days": 1,
            })
            response.raise_for_status()
            data = response.json()
        current = data["current"]
        probability = max(data.get("hourly", {}).get("precipitation_probability", [0])[:3], default=0)
        return {
            "temperature": round(current["temperature_2m"]),
            "apparent_temperature": round(current["apparent_temperature"]),
            "weather": weather_label(current["weather_code"]),
            "precipitation_probability": probability,
            "wind_speed": round(current["wind_speed_10m"]),
            "source": "open-meteo",
        }
    except Exception:
        return {"temperature": 26, "apparent_temperature": 27, "weather": "小雨", "precipitation_probability": 70, "wind_speed": 12, "source": "demo-cache"}


def weather_label(code: int) -> str:
    if code in {51, 53, 55, 61, 63, 65, 80, 81, 82}:
        return "小雨" if code in {51, 53, 61, 80} else "降雨"
    if code in {71, 73, 75, 85, 86}:
        return "降雪"
    if code in {0, 1}:
        return "晴"
    return "多云"


async def search_poi(query: str) -> list[dict[str, Any]]:
    if settings.amap_enabled:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                response = await client.get("https://restapi.amap.com/v5/place/text", params={"key": settings.amap_web_service_key, "keywords": query, "city": "上海", "show_fields": "business"})
                data = response.json()
                pois = data.get("pois", [])
                if pois:
                    return [{"name": p.get("name"), "address": p.get("address"), "lat": float(p["location"].split(",")[1]), "lng": float(p["location"].split(",")[0])} for p in pois[:5] if p.get("location")]
        except Exception:
            pass
    q = query.replace("站", "")
    return [poi for poi in DEMO_POIS if q in poi["name"] or query in poi["name"]] or DEMO_POIS[:2]


def demo_route(state: SessionState, destination: dict[str, Any]) -> dict[str, Any]:
    km = round(abs(destination["lat"] - state.vehicle.latitude) * 111 + abs(destination["lng"] - state.vehicle.longitude) * 92, 1)
    return {"distance_km": max(km, 3.2), "duration_minutes": max(int(km * 1.4), 18), "steps": ["沿当前道路直行", "前方路口右转", "到达目的地"], "polyline": [[state.vehicle.longitude, state.vehicle.latitude], [destination["lng"], destination["lat"]],], "source": "demo"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
