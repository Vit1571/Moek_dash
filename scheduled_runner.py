from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

from mail_collector import collect_mailru_pdfs, load_config, load_env_file, subtract_months


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check Mail.ru every 30 minutes and rebuild dashboard when new PDFs arrive."
    )
    parser.add_argument("--env", default=".env", help="Path to local .env file.")
    parser.add_argument("--interval-minutes", type=float, default=30.0)
    parser.add_argument("--once", action="store_true", help="Run one check and exit.")
    parser.add_argument(
        "--force-build",
        action="store_true",
        help="Rebuild dashboard even if no new PDFs were downloaded.",
    )
    parser.add_argument("--history-dir", default="reports_history")
    args = parser.parse_args()

    while True:
        try:
            run_once(args)
        except Exception as exc:
            print(f"[{now()}] ERROR: {exc}", flush=True)

        if args.once:
            return

        sleep_seconds = max(60, int(args.interval_minutes * 60))
        print(f"[{now()}] Sleeping {sleep_seconds // 60} min", flush=True)
        time.sleep(sleep_seconds)


def run_once(args: argparse.Namespace) -> None:
    load_env_file(Path(args.env))
    config = load_config(
        SimpleNamespace(
            folder=None,
            months=None,
            output_dir=None,
        )
    )
    since = subtract_months(date.today(), config.lookback_months)

    print(f"[{now()}] Checking {config.email_address}/{config.folder} since {since}", flush=True)
    result = collect_mailru_pdfs(config, since=since)
    print(
        f"[{now()}] Messages={result['messages']} pdf={result['pdf_attachments']} "
        f"downloaded={result['downloaded']} duplicates={result['duplicates']}",
        flush=True,
    )

    dashboard_path = Path("dashboard.html")
    should_build = args.force_build or result["downloaded"] > 0 or not dashboard_path.exists()
    if not should_build:
        print(f"[{now()}] No new files, dashboard unchanged", flush=True)
        return

    command = [
        sys.executable,
        "build_dashboard.py",
        "--pdf-dir",
        str(config.output_dir),
        "--history-dir",
        args.history_dir,
    ]
    print(f"[{now()}] Building dashboard", flush=True)
    subprocess.run(command, check=True)


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


if __name__ == "__main__":
    main()
