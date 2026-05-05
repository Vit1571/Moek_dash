from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any


MOSCOW_LATITUDE = 55.7558
MOSCOW_LONGITUDE = 37.6173
MOSCOW_TIMEZONE = "Europe/Moscow"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def update_moscow_weather_cache(
    start_date: date,
    end_date: date,
    cache_path: str | Path = "weather_moscow_hourly.json",
    timeout: int = 12,
) -> dict[str, Any]:
    cache_file = Path(cache_path)
    cache = load_weather_cache(cache_file)

    if cache_is_complete(cache, start_date, end_date):
        return cache

    params = {
        "latitude": MOSCOW_LATITUDE,
        "longitude": MOSCOW_LONGITUDE,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "hourly": "temperature_2m",
        "timezone": MOSCOW_TIMEZONE,
    }
    url = f"{OPEN_METEO_ARCHIVE_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    hourly = payload.get("hourly", {})
    times = hourly.get("time", [])
    temperatures = hourly.get("temperature_2m", [])
    values = cache.setdefault("hourly_temperature_2m", {})
    for timestamp, temperature in zip(times, temperatures):
        if temperature is not None:
            values[timestamp] = float(temperature)

    cache.update(
        {
            "source": "Open-Meteo Historical Weather API",
            "latitude": MOSCOW_LATITUDE,
            "longitude": MOSCOW_LONGITUDE,
            "timezone": MOSCOW_TIMEZONE,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    cache_file.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    return cache


def load_weather_cache(path: str | Path) -> dict[str, Any]:
    cache_file = Path(path)
    if not cache_file.exists():
        return {"hourly_temperature_2m": {}}
    return json.loads(cache_file.read_text(encoding="utf-8"))


def cache_is_complete(cache: dict[str, Any], start_date: date, end_date: date) -> bool:
    values = cache.get("hourly_temperature_2m", {})
    if not values:
        return False
    dates = {timestamp[:10] for timestamp in values}
    current = start_date
    while current <= end_date:
        if current.isoformat() not in dates:
            return False
        current = date.fromordinal(current.toordinal() + 1)
    return True
