from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect Eldis data every 30 minutes and rebuild dashboard."
    )
    parser.add_argument("--interval-minutes", type=float, default=30.0)
    parser.add_argument("--once", action="store_true", help="Run one Eldis update and exit.")
    parser.add_argument("--env", default=".env", help="Path to local .env file.")
    parser.add_argument("--history-dir", default="reports_history")
    parser.add_argument("--reports-json", default="eldis_reports.json")
    parser.add_argument("--resources", default="heat,gvs")
    parser.add_argument("--months", type=int, help="Lookback months for Eldis collection.")
    parser.add_argument("--days", type=int, help="Lookback days for Eldis collection.")
    parser.add_argument("--no-weather-fetch", action="store_true")
    args = parser.parse_args()

    while True:
        run_once(args)
        if args.once:
            return

        sleep_seconds = max(60, int(args.interval_minutes * 60))
        print(f"[{now()}] Sleeping {sleep_seconds // 60} min", flush=True)
        time.sleep(sleep_seconds)


def run_once(args: argparse.Namespace) -> None:
    try:
        collect_eldis(args)
        build_dashboard(args)
        print(f"[{now()}] Eldis dashboard update finished", flush=True)
    except subprocess.CalledProcessError as exc:
        print(f"[{now()}] ERROR: command failed with exit code {exc.returncode}", flush=True)
    except Exception as exc:
        print(f"[{now()}] ERROR: {exc}", flush=True)


def collect_eldis(args: argparse.Namespace) -> None:
    command = [
        sys.executable,
        "eldis_collector.py",
        "--collect",
        "--env",
        args.env,
        "--resources",
        args.resources,
        "--output",
        args.reports_json,
    ]
    if args.months is not None:
        command.extend(["--months", str(args.months)])
    if args.days is not None:
        command.extend(["--days", str(args.days)])

    print(f"[{now()}] Collecting Eldis data", flush=True)
    subprocess.run(command, check=True)


def build_dashboard(args: argparse.Namespace) -> None:
    command = [
        sys.executable,
        "build_dashboard.py",
        "--reports-json",
        args.reports_json,
        "--history-dir",
        args.history_dir,
    ]
    if args.no_weather_fetch:
        command.append("--no-weather-fetch")

    print(f"[{now()}] Building dashboard from {Path(args.reports_json).resolve()}", flush=True)
    subprocess.run(command, check=True)


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


if __name__ == "__main__":
    main()
