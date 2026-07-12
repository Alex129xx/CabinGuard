from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import httpx

from .config import settings


class AmapServiceError(RuntimeError):
    """A user-safe failure returned by a high-map Web service."""


class WeatherServiceError(RuntimeError):
    """A user-safe weather provider failure."""


_weather_cache: dict[tuple[float, float], tuple[float, dict[str, Any]]] = {}


async def weather(latitude: float, longitude: float) -> dict[str, Any]:
    key = (round(latitude, 2), round(longitude, 2))
    now = datetime.now(timezone.utc).timestamp()
    cached = _weather_cache.get(key)
    if cached and now - cached[0] < settings.weather_cache_seconds:
        return {**cached[1], "cached": True}
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
        result = {
            "temperature": round(current["temperature_2m"]),
            "apparent_temperature": round(current["apparent_temperature"]),
            "weather": weather_label(current["weather_code"]),
            "precipitation_probability": probability,
            "wind_speed": round(current["wind_speed_10m"]),
            "source": "open-meteo",
            "cached": False,
            "fetched_at": now_iso(),
        }
        _weather_cache[key] = (now, result)
        return result
    except Exception as exc:
        raise WeatherServiceError("当前无法获取天气信息") from exc


async def geocode_location(name: str) -> dict[str, Any]:
    """Resolve an explicit weather location instead of silently using the car position."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5, connect=3)) as client:
            response = await client.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": name, "count": 1, "language": "zh", "format": "json"})
            response.raise_for_status()
            result = response.json().get("results", [])
        if not result:
            raise WeatherServiceError(f"未找到“{name}”的位置")
        place = result[0]
        return {"name": place.get("name", name), "lat": place["latitude"], "lng": place["longitude"]}
    except WeatherServiceError:
        raise
    except Exception as exc:
        raise WeatherServiceError(f"当前无法获取“{name}”的天气") from exc


def weather_label(code: int) -> str:
    if code in {51, 53, 55, 61, 63, 65, 80, 81, 82}:
        return "小雨" if code in {51, 53, 61, 80} else "降雨"
    if code in {71, 73, 75, 85, 86}:
        return "降雪"
    if code in {0, 1}:
        return "晴"
    return "多云"


async def search_poi(query: str) -> list[dict[str, Any]]:
    if not settings.amap_enabled:
        raise AmapServiceError("未配置高德 Web 服务 Key")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(6, connect=3), trust_env=False) as client:
            response = await client.get("https://restapi.amap.com/v5/place/text", params={
                "key": settings.amap_web_service_key, "keywords": query, "city": "上海", "show_fields": "business",
            })
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        raise AmapServiceError("高德地点搜索网络不可用") from exc
    if data.get("status") != "1":
        raise AmapServiceError(f"高德地点搜索失败：{data.get('info', '未知错误')}")
    pois = [poi for poi in data.get("pois", []) if poi.get("location")]
    if not pois:
        return []
    return [{
        "name": poi.get("name", "未命名地点"), "address": poi.get("address") or "",
        "lat": float(poi["location"].split(",")[1]), "lng": float(poi["location"].split(",")[0]),
        "id": poi.get("id"), "typecode": poi.get("typecode"),
    } for poi in pois[:5]]


async def driving_route(origin_lng: float, origin_lat: float, destination: dict[str, Any]) -> dict[str, Any]:
    if not settings.amap_enabled:
        raise AmapServiceError("未配置高德 Web 服务 Key")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=3), trust_env=False) as client:
            response = await client.get("https://restapi.amap.com/v5/direction/driving", params={
                "key": settings.amap_web_service_key,
                "origin": f"{origin_lng:.6f},{origin_lat:.6f}",
                "destination": f"{destination['lng']:.6f},{destination['lat']:.6f}",
                "destination_id": destination.get("id") or "",
                "strategy": 32,
                "show_fields": "cost,navi,polyline",
            })
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        raise AmapServiceError("高德驾车路径规划网络不可用") from exc
    if data.get("status") != "1":
        raise AmapServiceError(f"高德路径规划失败：{data.get('info', '未知错误')}")
    paths = data.get("route", {}).get("paths", [])
    if not paths:
        raise AmapServiceError("高德未返回可用驾车路线")
    path = paths[0]
    duration_seconds = int(path.get("cost", {}).get("duration") or path.get("duration") or 0)
    distance_meters = int(path.get("distance") or 0)
    steps = path.get("steps", [])
    instructions = [step.get("instruction") or "沿规划路线行驶" for step in steps[:8]]
    polyline: list[list[float]] = []
    for step in steps:
        for point in (step.get("polyline") or "").split(";"):
            if not point:
                continue
            lng, lat = point.split(",")
            polyline.append([float(lng), float(lat)])
    return {
        "distance_km": round(distance_meters / 1000, 1),
        "duration_minutes": max(1, round(duration_seconds / 60)),
        "steps": instructions or ["沿规划路线行驶"],
        "polyline": polyline or [[origin_lng, origin_lat], [destination["lng"], destination["lat"]]],
        "source": "amap-web-service",
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
