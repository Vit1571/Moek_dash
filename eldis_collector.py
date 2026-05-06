from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_API_BASE = "https://api.eldis24.ru/api/v1"
HOURLY_ARCHIVE_CODE = 30003
DATE_TYPE = "dateWithTimeBias"
RESOURCE_ENDPOINTS = {
    "heat": {
        "endpoint": "tv/heatInfoList",
        "list_key": "heatInfoList",
        "resource": "ТС",
        "data_keys": ("heat",),
    },
    "gvs": {
        "endpoint": "tv/gvsInfoList",
        "list_key": "gvsInfoList",
        "resource": "ГВС",
        "data_keys": ("hotWater", "heat"),
    },
}
POINT_CSV_COLUMNS = [
    "account",
    "resource",
    "tv_id",
    "object_id",
    "object",
    "object_name",
    "point",
    "meter_model",
    "serial_number",
    "device_id",
    "modem_id",
    "scheme",
    "resource_code",
    "auto_polling",
    "time_on_device",
    "last_hour_archive",
    "last_connection",
]


@dataclass
class EldisAccount:
    name: str
    api_key: str
    access_token: str = ""
    login: str = ""
    password: str = ""


@dataclass(frozen=True)
class EldisConfig:
    api_base: str
    accounts: list[EldisAccount]
    timeout: int
    pause_seconds: float


class EldisAuthError(RuntimeError):
    pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect Eldis24 hourly archive data into MOEK dashboard report format."
    )
    parser.add_argument("--env", default=".env", help="Path to local .env file.")
    parser.add_argument("--discover", action="store_true", help="Only discover available points.")
    parser.add_argument("--collect", action="store_true", help="Collect normalized hourly archive data.")
    parser.add_argument("--auth-check", action="store_true", help="Only check Eldis login for configured accounts.")
    parser.add_argument("--resources", default="heat,gvs", help="Comma-separated: heat,gvs.")
    parser.add_argument("--limit", type=int, default=500, help="Page size for Eldis list endpoints.")
    parser.add_argument("--months", type=int, help="Lookback period in months.")
    parser.add_argument("--days", type=int, help="Lookback period in days. Overrides --months.")
    parser.add_argument("--start", help="Start date/time: YYYY-MM-DD or DD.MM.YYYY[ HH:MM:SS].")
    parser.add_argument("--end", help="End date/time: YYYY-MM-DD or DD.MM.YYYY[ HH:MM:SS].")
    parser.add_argument("--output", help="Reports JSON output. Defaults to ELDIS_OUTPUT_REPORTS.")
    parser.add_argument("--points-output", help="Discovered points CSV output.")
    parser.add_argument("--points-json", help="Optional discovered points JSON output.")
    parser.add_argument("--points-file", help="Use an existing points CSV instead of discovery.")
    parser.add_argument("--include-inactive", action="store_true", help="Include inactive points.")
    parser.add_argument("--include-archive", action="store_true", help="Include archival points.")
    parser.add_argument("--raw-dir", help="Optional folder to save raw Eldis API responses.")
    parser.add_argument("--timeout", type=int, help="HTTP timeout in seconds.")
    args = parser.parse_args()

    load_env_file(Path(args.env))
    config = load_config(args)
    if args.auth_check:
        check_auth(config)
        return

    resource_names = parse_resources(args.resources)
    points_output = Path(args.points_output or os.getenv("ELDIS_POINTS_OUTPUT", "eldis_points.csv"))
    points_json = Path(args.points_json) if args.points_json else None

    try:
        if args.points_file:
            points = read_points_csv(Path(args.points_file))
        else:
            points = discover_points(
                config,
                resource_names=resource_names,
                limit=min(max(args.limit, 1), 500),
                include_inactive=args.include_inactive,
                include_archive=args.include_archive,
                raw_dir=Path(args.raw_dir) if args.raw_dir else None,
            )
            write_points_csv(points_output, points)
            if points_json:
                points_json.write_text(json.dumps(points, ensure_ascii=False, indent=2), encoding="utf-8")
    except EldisAuthError as exc:
        raise SystemExit(str(exc)) from exc

    print(f"Accounts: {len(config.accounts)}")
    print(f"Points: {len(points)}")
    print(f"Points CSV: {points_output.resolve()}")

    if args.discover and not args.collect:
        return

    start_at, end_at = resolve_period(args)
    output = Path(args.output or os.getenv("ELDIS_OUTPUT_REPORTS", "eldis_reports.json"))
    try:
        reports = collect_reports(
            config,
            points=points,
            start_at=start_at,
            end_at=end_at,
            raw_dir=Path(args.raw_dir) if args.raw_dir else None,
        )
    except EldisAuthError as exc:
        raise SystemExit(str(exc)) from exc
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "eldis24",
        "api_base": config.api_base,
        "typeDataCode": HOURLY_ARCHIVE_CODE,
        "dateType": DATE_TYPE,
        "period_start": start_at.isoformat(timespec="seconds"),
        "period_end": end_at.isoformat(timespec="seconds"),
        "accounts": [account.name for account in config.accounts],
        "points_count": len(points),
        "reports_count": len(reports),
        "reports": reports,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Reports: {len(reports)}")
    print(f"Reports JSON: {output.resolve()}")


def load_config(args: argparse.Namespace) -> EldisConfig:
    accounts = load_accounts()
    if not accounts:
        raise SystemExit(
            "Eldis API key is missing. Set ELDIS_ACCOUNT_1_KEY in .env "
            "or use ELDIS_API_KEY for a single account."
        )

    return EldisConfig(
        api_base=os.getenv("ELDIS_API_BASE", DEFAULT_API_BASE).rstrip("/"),
        accounts=accounts,
        timeout=args.timeout or int(os.getenv("ELDIS_TIMEOUT", "60")),
        pause_seconds=float(os.getenv("ELDIS_REQUEST_PAUSE_SECONDS", "0")),
    )


def load_accounts() -> list[EldisAccount]:
    accounts: list[EldisAccount] = []
    for index in range(1, 51):
        key = os.getenv(f"ELDIS_ACCOUNT_{index}_KEY", "").strip()
        if not key:
            continue
        name = os.getenv(f"ELDIS_ACCOUNT_{index}_NAME", f"account_{index}").strip()
        access_token = os.getenv(f"ELDIS_ACCOUNT_{index}_ACCESS_TOKEN", "").strip()
        login = os.getenv(f"ELDIS_ACCOUNT_{index}_LOGIN", "").strip()
        password = os.getenv(f"ELDIS_ACCOUNT_{index}_PASSWORD", "").strip()
        accounts.append(
            EldisAccount(
                name=name or f"account_{index}",
                api_key=key,
                access_token=access_token,
                login=login,
                password=password,
            )
        )

    single_key = os.getenv("ELDIS_API_KEY", "").strip()
    if single_key:
        single_name = os.getenv("ELDIS_ACCOUNT_NAME", "account_1").strip() or "account_1"
        access_token = os.getenv("ELDIS_ACCESS_TOKEN", "").strip()
        login = os.getenv("ELDIS_LOGIN", "").strip()
        password = os.getenv("ELDIS_PASSWORD", "").strip()
        if all(account.api_key != single_key for account in accounts):
            accounts.append(
                EldisAccount(
                    name=single_name,
                    api_key=single_key,
                    access_token=access_token,
                    login=login,
                    password=password,
                )
            )

    key_list = split_csv(os.getenv("ELDIS_API_KEYS", ""))
    name_list = split_csv(os.getenv("ELDIS_ACCOUNT_NAMES", ""))
    token_list = split_csv(os.getenv("ELDIS_ACCESS_TOKENS", ""))
    login_list = split_csv(os.getenv("ELDIS_LOGINS", ""))
    password_list = split_csv(os.getenv("ELDIS_PASSWORDS", ""))
    for index, key in enumerate(key_list, start=1):
        if any(account.api_key == key for account in accounts):
            continue
        name = name_list[index - 1] if index <= len(name_list) else f"account_{index}"
        access_token = token_list[index - 1] if index <= len(token_list) else ""
        login = login_list[index - 1] if index <= len(login_list) else ""
        password = password_list[index - 1] if index <= len(password_list) else ""
        accounts.append(
            EldisAccount(
                name=name,
                api_key=key,
                access_token=access_token,
                login=login,
                password=password,
            )
        )

    return accounts


def check_auth(config: EldisConfig) -> None:
    failed = False
    for account in config.accounts:
        print(f"Checking Eldis account '{account.name}'...", flush=True)
        if not account.login or not account.password:
            print("  SKIP: ELDIS_ACCOUNT_*_LOGIN/PASSWORD are not set.")
            failed = True
            continue
        try:
            account.access_token = ""
            ensure_access_token(config, account)
        except EldisAuthError as exc:
            print(f"  FAIL: {exc}")
            failed = True
            continue
        print("  OK: login accepted, access_token received.")

    if failed:
        raise SystemExit(1)


def parse_resources(value: str) -> list[str]:
    resources = []
    for item in split_csv(value):
        normalized = item.lower()
        if normalized not in RESOURCE_ENDPOINTS:
            raise SystemExit(f"Unknown Eldis resource '{item}'. Supported: heat,gvs")
        resources.append(normalized)
    return resources or ["heat", "gvs"]


def discover_points(
    config: EldisConfig,
    resource_names: list[str],
    limit: int,
    include_inactive: bool,
    include_archive: bool,
    raw_dir: Path | None = None,
) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for account in config.accounts:
        for resource_name in resource_names:
            resource_config = RESOURCE_ENDPOINTS[resource_name]
            page = 1
            while True:
                payload = request_json(
                    config,
                    account,
                    resource_config["endpoint"],
                    method="POST",
                    query={
                        "limit": limit,
                        "page": page,
                        "typeDataCode": HOURLY_ARCHIVE_CODE,
                    },
                    form={
                        "state": "" if include_inactive else "0",
                        "archive": "true" if include_archive else "false",
                    },
                )
                save_raw(raw_dir, account.name, f"{resource_name}_points_page_{page}", payload)
                tv_payload = payload.get("response", {}).get("tv", {})
                items = tv_payload.get(resource_config["list_key"], [])
                if not isinstance(items, list):
                    items = []
                for item in items:
                    if not isinstance(item, dict) or not item.get("id"):
                        continue
                    key = (account.name, str(item["id"]))
                    if key in seen:
                        continue
                    seen.add(key)
                    points.append(point_from_item(account, item, resource_config["resource"]))

                pagination = tv_payload.get("pagination") or {}
                page_count = int_or_none(pagination.get("numberOfPages")) or page
                if page >= page_count or not items:
                    break
                page += 1
                pause(config)

    return points


def point_from_item(account: EldisAccount, item: dict[str, Any], resource: str) -> dict[str, Any]:
    object_text = first_text(
        item.get("addressObject"),
        item.get("objects"),
        item.get("objectName"),
    )
    point_text = first_text(
        item.get("measurePointName"),
        item.get("tvName"),
        item.get("title"),
        item.get("name"),
    )
    return {
        "account": account.name,
        "resource": resource,
        "tv_id": item.get("id"),
        "object_id": item.get("objectID"),
        "object": object_text,
        "object_name": item.get("objectName"),
        "point": point_text,
        "meter_model": item.get("deviceModelName"),
        "serial_number": first_text(
            item.get("deviceSN"),
            item.get("deviceIdentifier"),
            item.get("deviceName"),
        ),
        "device_id": item.get("deviceID"),
        "modem_id": item.get("modemID"),
        "scheme": first_text(
            item.get("schemeName"),
            item.get("heatConnectionSchemeName"),
            item.get("schemeGVSName"),
            item.get("typeSchemeName"),
        ),
        "resource_code": item.get("resourceCode"),
        "auto_polling": item.get("autoPolling"),
        "time_on_device": first_text(item.get("timeOnDevice"), item.get("time_on_device")),
        "last_hour_archive": normalize_datetime_value(item.get("lastDateHourArchive")),
        "last_connection": normalize_datetime_value(item.get("lastConnection")),
        "system_heat": first_text(item.get("systemHeatName"), item.get("systemHeat")),
        "system_gvs": first_text(item.get("systemGVSName"), item.get("systemGVS")),
    }


def collect_reports(
    config: EldisConfig,
    points: list[dict[str, Any]],
    start_at: datetime,
    end_at: datetime,
    raw_dir: Path | None = None,
) -> list[dict[str, Any]]:
    accounts_by_name = {account.name: account for account in config.accounts}
    reports: list[dict[str, Any]] = []

    for index, point in enumerate(points, start=1):
        account = accounts_by_name.get(str(point.get("account") or ""))
        tv_id = point.get("tv_id")
        if not account or not tv_id:
            continue

        payload = request_json(
            config,
            account,
            "data/normalized",
            method="GET",
            query={
                "id": tv_id,
                "typeDataCode": HOURLY_ARCHIVE_CODE,
                "startDate": format_eldis_datetime(start_at),
                "endDate": format_eldis_datetime(end_at),
                "dateType": DATE_TYPE,
                "showEmptyRows": "true",
                "sort": "ASC",
            },
        )
        save_raw(raw_dir, account.name, f"normalized_{index}_{tv_id}", payload)
        rows = normalized_rows(payload, str(point.get("resource") or ""))
        report = report_from_rows(point, rows, start_at, end_at)
        reports.append(report)
        print(
            f"[{index}/{len(points)}] {account.name} {point.get('resource')} "
            f"{point.get('point') or tv_id}: {len(report['readings'])} rows",
            flush=True,
        )
        pause(config)

    return reports


def normalized_rows(payload: dict[str, Any], resource: str) -> list[dict[str, Any]]:
    normalized = payload.get("response", {}).get("data", {}).get("normalized", [])
    data_keys: tuple[str, ...]
    if resource == "ТС":
        data_keys = RESOURCE_ENDPOINTS["heat"]["data_keys"]
    elif resource == "ГВС":
        data_keys = RESOURCE_ENDPOINTS["gvs"]["data_keys"]
    else:
        data_keys = ("heat", "hotWater")

    rows: list[dict[str, Any]] = []
    for block in ensure_list(normalized):
        if isinstance(block, dict):
            block_rows: list[dict[str, Any]] = []
            for key in data_keys:
                block_rows.extend(row for row in ensure_list(block.get(key)) if isinstance(row, dict))
            if block_rows:
                rows.extend(block_rows)
            elif looks_like_data_row(block):
                rows.append(block)
        elif isinstance(block, list):
            rows.extend(row for row in block if isinstance(row, dict))
    return rows


def report_from_rows(
    point: dict[str, Any],
    rows: list[dict[str, Any]],
    start_at: datetime,
    end_at: datetime,
) -> dict[str, Any]:
    readings = [reading_from_row(row, point) for row in rows]
    readings = [row for row in readings if row.get("timestamp")]
    readings.sort(key=lambda item: item.get("timestamp") or "")
    columns = sorted({key for row in readings for key in row if key not in {"raw"}})
    period_start = readings[0]["timestamp"] if readings else start_at.isoformat(timespec="seconds")
    period_end = readings[-1]["timestamp"] if readings else end_at.isoformat(timespec="seconds")

    return {
        "id": f"eldis:{point.get('account')}:{point.get('tv_id')}:{period_start}:{period_end}",
        "source": "eldis24",
        "source_file": f"eldis:{point.get('account')}:{point.get('tv_id')}",
        "pages": [],
        "section_index": 1,
        "columns": columns,
        "readings": readings,
        "object": point.get("object"),
        "meter_model": point.get("meter_model"),
        "serial_number": point.get("serial_number"),
        "point": point.get("point"),
        "resource": point.get("resource"),
        "scheme": point.get("scheme"),
        "period_start": period_start,
        "period_end": period_end,
        "eldis": point,
        "checks": {
            "total_rows": len(readings),
            "missing_rows": sum(1 for row in readings if row.get("is_missing")),
        },
    }


def reading_from_row(row: dict[str, Any], point: dict[str, Any]) -> dict[str, Any]:
    corrected_time = normalize_datetime_value(row.get("dateWithTimeBias"))
    archive_end_time = normalize_datetime_value(row.get("dateOnEndOfArchive"))
    device_time = normalize_datetime_value(row.get("date"))
    timestamp_source = "dateWithTimeBias"

    if corrected_time:
        timestamp = corrected_time
    else:
        timestamp_source = "timeOnDevice" if point.get("time_on_device") else "dateOnEndOfArchive"
        timestamp = corrected_timestamp_from_time_on_device(device_time, point.get("time_on_device"))
        if not timestamp:
            timestamp = archive_end_time or device_time
            timestamp_source = "dateOnEndOfArchive" if archive_end_time else "date"

    q_value = value_as_float(row.get("Q"))
    q1 = value_as_float(row.get("Q1"))
    q2 = value_as_float(row.get("Q2"))
    if q_value is None and q1 is not None and q2 is not None:
        q_value = q1 - q2
    elif q_value is None:
        q_value = q1

    reading = {
        "timestamp": timestamp,
        "device_timestamp": device_time,
        "archive_end_timestamp": archive_end_time,
        "corrected_timestamp": corrected_time or timestamp,
        "timestamp_source": timestamp_source,
        "t1": value_as_float(row.get("t1")),
        "t2": value_as_float(row.get("t2")),
        "t3": value_as_float(row.get("t3")),
        "dt": value_as_float(row.get("dt")),
        "ta": value_as_float(row.get("ta")),
        "V1": value_as_float(row.get("V1")),
        "V2": value_as_float(row.get("V2")),
        "V3": value_as_float(row.get("V3")),
        "M1": value_as_float(row.get("M1")),
        "M2": value_as_float(row.get("M2")),
        "M3": value_as_float(row.get("M3")),
        "P1": value_as_float(row.get("P1")),
        "P2": value_as_float(row.get("P2")),
        "P3": value_as_float(row.get("P3")),
        "Q": q_value,
        "runtime_hours": value_as_float(row.get("QntHIP")),
        "QntP": value_as_float(row.get("QntP")),
        "ns": row.get("ns"),
        "is_missing": is_missing_row(row),
        "raw": f"eldis:{point.get('account')}:{point.get('tv_id')}:{timestamp or 'no-date'}",
    }
    if reading["dt"] is None and reading["t1"] is not None and reading["t2"] is not None:
        reading["dt"] = reading["t1"] - reading["t2"]
    return reading


def is_missing_row(row: dict[str, Any]) -> bool:
    if bool(row.get("empty")):
        return True
    important = ("t1", "t2", "V1", "V2", "M1", "M2", "Q", "Q1", "QntHIP")
    return all(value_as_float(row.get(key)) is None for key in important)


def request_json(
    config: EldisConfig,
    account: EldisAccount,
    endpoint: str,
    method: str,
    query: dict[str, Any] | None = None,
    form: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{config.api_base}/{endpoint.lstrip('/')}"
    query_string = urllib.parse.urlencode(clean_params(query or {}), doseq=True)
    if query_string:
        url = f"{url}?{query_string}"

    ensure_access_token(config, account)

    body = None
    headers = {
        "key": account.api_key,
        "Accept": "application/json",
    }
    if account.access_token:
        headers["Cookie"] = build_cookie_header(account.access_token)
    if method.upper() == "POST":
        body = urllib.parse.urlencode(clean_params(form or {}), doseq=True).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    request = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
    try:
        raw = open_request(request, config.timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Eldis HTTP {exc.code} for {endpoint}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Eldis request failed for {endpoint}: {exc}") from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Eldis returned non-JSON response for {endpoint}") from exc

    messages = payload.get("response", {}).get("messages") or payload.get("messages") or []
    bad_messages = [
        message
        for message in ensure_list(messages)
        if isinstance(message, dict)
        and int_or_none(message.get("httpStatusCode")) not in (None, 200)
    ]
    if bad_messages:
        status_codes = {int_or_none(message.get("httpStatusCode")) for message in bad_messages}
        if 401 in status_codes:
            if account.login and account.password:
                account.access_token = ""
                ensure_access_token(config, account)
                retry_payload = retry_request_json(config, account, url, method, body, headers)
                if retry_payload is not None:
                    return retry_payload
            fallback_payload = try_api_key_fallbacks(
                config,
                account,
                endpoint,
                method,
                url,
                body,
                headers,
            )
            if fallback_payload is not None:
                return fallback_payload
            raise EldisAuthError(
                f"Eldis authorization failed for account '{account.name}' on {endpoint}. "
                f"API returned: {compact_messages(bad_messages)}\n"
                f"The request used ELDIS_ACCOUNT_*_KEY as header 'key'. "
                f"If you have a login and password, set ELDIS_ACCOUNT_*_LOGIN and "
                f"ELDIS_ACCOUNT_*_PASSWORD so the collector can call /users/login "
                f"and receive access_token automatically.\n"
                f"If those are already set, ask Eldis support to verify that this user "
                f"and API key have access to API v1 and {endpoint}."
            )
        raise RuntimeError(
            f"Eldis API returned error for account '{account.name}' on {endpoint}: "
            f"{compact_messages(bad_messages)}"
        )

    return payload


def ensure_access_token(config: EldisConfig, account: EldisAccount) -> None:
    if not account.login or not account.password:
        return
    if account.access_token and account.access_token != account.api_key:
        return
    account.access_token = login_account(config, account)


def login_account(config: EldisConfig, account: EldisAccount) -> str:
    url = f"{config.api_base}/users/login"
    body = urllib.parse.urlencode(
        {
            "login": account.login,
            "password": account.password,
        }
    ).encode("utf-8")
    headers = {
        "key": account.api_key,
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        "Accept": "application/json",
    }
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=config.timeout) as response:
            raw = response.read()
            cookie_header = response.headers.get("Set-Cookie", "")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise EldisAuthError(
            f"Eldis login failed for account '{account.name}': HTTP {exc.code} {detail[:300]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Eldis login request failed for account '{account.name}': {exc}") from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise EldisAuthError(
            f"Eldis login returned non-JSON response for account '{account.name}'."
        ) from exc

    result = payload.get("response", {}).get("users", {}).get("login", {}).get("result")
    if result is not True:
        messages = payload.get("response", {}).get("messages") or payload.get("messages") or []
        raise EldisAuthError(
            f"Eldis login failed for account '{account.name}': "
            f"{compact_messages(ensure_list(messages)) or 'login result is false'}"
        )

    match = re.search(r"access_token=([^;]+)", cookie_header)
    if not match:
        raise EldisAuthError(
            f"Eldis login succeeded for account '{account.name}', but Set-Cookie "
            f"does not contain access_token."
        )

    print(f"Eldis login OK for account '{account.name}'", flush=True)
    return match.group(1)


def retry_request_json(
    config: EldisConfig,
    account: EldisAccount,
    url: str,
    method: str,
    body: bytes | None,
    headers: dict[str, str],
) -> dict[str, Any] | None:
    retry_headers = dict(headers)
    if account.access_token:
        retry_headers["Cookie"] = build_cookie_header(account.access_token)
    request = urllib.request.Request(url, data=body, headers=retry_headers, method=method.upper())
    try:
        raw = open_request(request, config.timeout)
    except (urllib.error.HTTPError, urllib.error.URLError):
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return None

    messages = payload.get("response", {}).get("messages") or payload.get("messages") or []
    bad_messages = [
        message
        for message in ensure_list(messages)
        if isinstance(message, dict)
        and int_or_none(message.get("httpStatusCode")) not in (None, 200)
    ]
    return None if bad_messages else payload


def open_request(request: urllib.request.Request, timeout: int) -> bytes:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def try_api_key_fallbacks(
    config: EldisConfig,
    account: EldisAccount,
    endpoint: str,
    method: str,
    url: str,
    body: bytes | None,
    base_headers: dict[str, str],
) -> dict[str, Any] | None:
    if account.access_token:
        return None

    variants = [
        {"Cookie": build_cookie_header(account.api_key)},
        {"Authorization": f"Bearer {account.api_key}"},
        {"X-API-Key": account.api_key},
        {"X-Api-Key": account.api_key},
        {"api-key": account.api_key},
    ]
    for extra_headers in variants:
        headers = dict(base_headers)
        headers.update(extra_headers)
        request = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
        try:
            raw = open_request(request, config.timeout)
        except (urllib.error.HTTPError, urllib.error.URLError):
            continue

        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            continue

        messages = payload.get("response", {}).get("messages") or payload.get("messages") or []
        bad_messages = [
            message
            for message in ensure_list(messages)
            if isinstance(message, dict)
            and int_or_none(message.get("httpStatusCode")) not in (None, 200)
        ]
        if not bad_messages:
            print(
                f"Eldis auth fallback worked for account '{account.name}' on {endpoint}: "
                f"{', '.join(extra_headers)}",
                flush=True,
            )
            return payload
    return None


def resolve_period(args: argparse.Namespace) -> tuple[datetime, datetime]:
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    end_at = parse_user_datetime(args.end) if args.end else now
    if args.start:
        start_at = parse_user_datetime(args.start)
    elif args.days:
        start_at = end_at - timedelta(days=args.days)
    else:
        months = args.months or int(os.getenv("ELDIS_LOOKBACK_MONTHS", "2"))
        start_at = datetime.combine(subtract_months(end_at.date(), months), datetime.min.time())
    if start_at >= end_at:
        raise SystemExit("Start date must be earlier than end date.")
    return start_at, end_at


def parse_user_datetime(value: str) -> datetime:
    text = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y"):
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt in ("%Y-%m-%d", "%d.%m.%Y"):
                return datetime.combine(parsed.date(), datetime.min.time())
            return parsed
        except ValueError:
            continue
    raise SystemExit(f"Unsupported date format: {value}")


def format_eldis_datetime(value: datetime) -> str:
    return value.strftime("%d.%m.%Y %H:%M:%S")


def normalize_datetime_value(value: Any) -> str | None:
    parsed = parse_eldis_datetime(value)
    return parsed.isoformat(timespec="seconds") if parsed else None


def parse_eldis_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value) / 1000 if abs(float(value)) > 100_000_000_000 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(tzinfo=None)

    text = str(value).strip()
    if not text:
        return None
    timestamp_match = re.fullmatch(r"/Date\((-?\d+)(?:[+-]\d+)?\)/", text)
    if timestamp_match:
        return parse_eldis_datetime(int(timestamp_match.group(1)))

    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        pass

    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def corrected_timestamp_from_time_on_device(
    device_timestamp: str | None,
    time_on_device: Any,
) -> str | None:
    timestamp = parse_eldis_datetime(device_timestamp)
    offset = parse_time_on_device_offset(time_on_device)
    if timestamp is None or offset is None:
        return None
    return (timestamp + offset).isoformat(timespec="seconds")


def parse_time_on_device_offset(value: Any) -> timedelta | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower().replace("ё", "е")
    if not text:
        return None

    sign = 0
    if "спеш" in text or "опереж" in text:
        sign = -1
    elif "отста" in text:
        sign = 1
    if sign == 0:
        return None

    hours = match_time_part(text, r"(\d+)\s*(?:ч|час)")
    minutes = match_time_part(text, r"(\d+)\s*(?:м|мин)")
    seconds = match_time_part(text, r"(\d+)\s*(?:с|сек)")
    if hours == minutes == seconds == 0:
        return None
    return sign * timedelta(hours=hours, minutes=minutes, seconds=seconds)


def match_time_part(text: str, pattern: str) -> int:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else 0


def write_points_csv(path: Path, points: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=POINT_CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for point in points:
            writer.writerow(point)


def read_points_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def save_raw(raw_dir: Path | None, account_name: str, stem: str, payload: dict[str, Any]) -> None:
    if raw_dir is None:
        return
    raw_dir.mkdir(parents=True, exist_ok=True)
    safe_account = re.sub(r"[^A-Za-z0-9_.-]+", "_", account_name)
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem)
    path = raw_dir / f"{safe_account}_{safe_stem}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_params(params: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in params.items() if value not in (None, "")}


def build_cookie_header(access_token: str) -> str:
    token = access_token.strip()
    if ";" in token or "=" in token:
        return token
    return f"access_token={token}"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def subtract_months(value: date, months: int) -> date:
    month_index = value.month - 1 - months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, days_in_month(year, month))
    return date(year, month, day)


def days_in_month(year: int, month: int) -> int:
    if month == 2:
        if year % 400 == 0 or (year % 4 == 0 and year % 100 != 0):
            return 29
        return 28
    return 30 if month in {4, 6, 9, 11} else 31


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def ensure_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def first_present(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def value_as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def looks_like_data_row(value: dict[str, Any]) -> bool:
    return any(key in value for key in ("date", "dateWithTimeBias", "t1", "M1", "QntHIP"))


def compact_messages(messages: list[Any]) -> str:
    parts = []
    for message in messages:
        if not isinstance(message, dict):
            parts.append(str(message))
            continue
        parts.append(
            " ".join(
                part
                for part in (
                    f"http={message.get('httpStatusCode')}",
                    f"code={message.get('messageCode')}",
                    str(message.get("message") or "").strip(),
                )
                if part
            )
        )
    return "; ".join(parts)


def pause(config: EldisConfig) -> None:
    if config.pause_seconds > 0:
        time.sleep(config.pause_seconds)


if __name__ == "__main__":
    main()
