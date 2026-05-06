from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from telegram_bot import main as run_bot


ROOT = Path(__file__).resolve().parent
REFRESH_SECONDS = int(os.getenv("BOT_REFRESH_SECONDS", "1800"))
REPORTS_PATH = ROOT / "eldis_reports.json"
NEXT_JSON_PATH = ROOT / "parsed_reports.next.json"
JSON_PATH = ROOT / "parsed_reports.json"
WEATHER_PATH = ROOT / "weather_moscow_hourly.json"


def main() -> None:
    wait_for_initial_refresh()
    refresher = threading.Thread(target=refresh_loop, name="eldis-refresh", daemon=True)
    refresher.start()
    run_bot()


def wait_for_initial_refresh() -> None:
    while True:
        try:
            refresh_once()
            return
        except Exception as exc:
            print(f"Initial refresh failed: {exc}", flush=True)
            time.sleep(60)


def refresh_loop() -> None:
    while True:
        time.sleep(REFRESH_SECONDS)
        try:
            refresh_once()
        except Exception as exc:
            print(f"Refresh failed: {exc}", flush=True)


def refresh_once() -> None:
    print("Refreshing Eldis data...", flush=True)
    run([sys.executable, "eldis_collector.py", "--collect", "--output", str(REPORTS_PATH)])
    run(
        [
            sys.executable,
            "build_dashboard.py",
            "--reports-json",
            str(REPORTS_PATH),
            "--json-output",
            str(NEXT_JSON_PATH),
            "--weather-cache",
            str(WEATHER_PATH),
        ]
    )
    os.replace(NEXT_JSON_PATH, JSON_PATH)
    print("Eldis data refreshed.", flush=True)


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
