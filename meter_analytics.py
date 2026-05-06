from __future__ import annotations

import csv
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


M1_M2_WARNING_PCT = 3.0
M1_M2_CRITICAL_PCT = 4.0
MIN_IMBALANCE_FLOW = 0.1
MIN_DAILY_RUNTIME_HOURS = 23.0
RETURN_TEMP_TOLERANCE = 3.0
SUPPLY_TEMP_TOLERANCE = 7.0
RUNTIME_HOUR_MIN = 0.999
RECENT_ERROR_DAYS = 5
CRITICAL_STATUS_DAYS = 7
NO_DATA_CRITICAL_HOURS = 20
M1_M2_CRITICAL_HOURS_PER_DAY = 2
DEFAULT_CLOCK_TOLERANCE_HOURS = 2


def build_meters(
    reports: list[dict[str, Any]],
    graph_path: str | Path = "moek_temperature_graph.csv",
    default_graph_profile: str = "rts_kts_150_70",
    profile_map_path: str | Path = "meter_graph_profiles.csv",
    weather_path: str | Path = "weather_moscow_hourly.json",
    clock_tolerance_hours: int = DEFAULT_CLOCK_TOLERANCE_HOURS,
) -> list[dict[str, Any]]:
    graph = TemperatureGraph.load(graph_path)
    profile_map = load_meter_profile_map(profile_map_path)
    weather = WeatherData.load(weather_path)
    grouped: dict[str, list[dict[str, Any]]] = {}

    for report in reports:
        grouped.setdefault(meter_key(report), []).append(report)

    meters = []
    for key, meter_reports in grouped.items():
        meter_reports.sort(key=report_sort_key)
        latest_report = meter_reports[-1]
        graph_profile = resolve_graph_profile(latest_report, profile_map, default_graph_profile)
        readings = dedupe_readings(meter_reports)
        annotate_weather(readings, weather, clock_tolerance_hours)
        daily = build_daily_stats(readings)
        checks = analyze_meter(latest_report, readings, daily, graph, graph_profile)

        meters.append(
            {
                "id": key,
                "meter_key": key,
                "object": latest_report.get("object"),
                "meter_model": latest_report.get("meter_model"),
                "serial_number": latest_report.get("serial_number"),
                "point": latest_report.get("point"),
                "resource": latest_report.get("resource"),
                "scheme": latest_report.get("scheme"),
                "source_file": latest_report.get("source_file"),
                "pages": latest_report.get("pages", []),
                "period_start": first_value(readings, "timestamp")
                or latest_report.get("period_start"),
                "period_end": last_value(readings, "timestamp")
                or latest_report.get("period_end"),
                "latest_report_start": latest_report.get("period_start"),
                "latest_report_end": latest_report.get("period_end"),
                "reports_count": len(meter_reports),
                "columns": sorted({column for report in meter_reports for column in report.get("columns", [])}),
                "readings": readings,
                "daily": daily,
                "reports": [report_brief(report) for report in meter_reports[-12:]],
                "graph_profile": graph_profile,
                "weather_source": weather.source,
                "checks": checks,
            }
        )

    meters.sort(key=lambda item: (status_rank(item), item.get("object") or "", item.get("point") or ""))
    return meters


def load_meter_profile_map(path: str | Path) -> list[dict[str, str]]:
    csv_path = Path(path)
    if not csv_path.exists():
        return []

    mappings = []
    with csv_path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(
            row for row in handle if row.strip() and not row.lstrip().startswith("#")
        )
        for row in reader:
            if not row.get("graph_profile"):
                continue
            mappings.append(
                {
                    "serial_number": (row.get("serial_number") or "").strip(),
                    "point": (row.get("point") or "").strip(),
                    "resource": (row.get("resource") or "").strip(),
                    "graph_profile": row["graph_profile"].strip(),
                }
            )
    return mappings


def resolve_graph_profile(
    report: dict[str, Any],
    profile_map: list[dict[str, str]],
    default_graph_profile: str,
) -> str:
    best_match: tuple[int, str] | None = None
    for mapping in profile_map:
        score = 0
        for key in ("serial_number", "point", "resource"):
            expected = mapping.get(key)
            if not expected:
                continue
            if expected != str(report.get(key) or "").strip():
                score = -1
                break
            score += 1
        if score > 0 and (best_match is None or score > best_match[0]):
            best_match = (score, mapping["graph_profile"])
    return best_match[1] if best_match else default_graph_profile


def meter_key(report: dict[str, Any]) -> str:
    serial = normalize_key_part(report.get("serial_number"), "no-serial")
    point = normalize_key_part(report.get("point"), "no-point")
    resource = normalize_key_part(report.get("resource"), "no-resource")
    return f"{serial}::{point}::{resource}"


def normalize_key_part(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text if text else fallback


def report_sort_key(report: dict[str, Any]) -> tuple[str, str, str]:
    return (
        report.get("period_end") or "",
        report.get("period_start") or "",
        report.get("source_file") or "",
    )


def status_rank(meter: dict[str, Any]) -> int:
    return {"Critical": 0, "Warning": 1, "OK": 2, "No Data": 3}.get(
        meter.get("checks", {}).get("status"),
        9,
    )


def dedupe_readings(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_timestamp: dict[str, dict[str, Any]] = {}

    for report in reports:
        for row in report.get("readings", []):
            timestamp = row.get("timestamp")
            if not timestamp:
                continue
            candidate = dict(row)
            candidate["source_file"] = report.get("source_file")
            candidate["report_id"] = report.get("id")
            candidate["_period_end"] = report.get("period_end") or ""

            current = by_timestamp.get(timestamp)
            if current is None or should_replace_row(current, candidate):
                by_timestamp[timestamp] = candidate

    rows = [strip_internal_fields(row) for row in by_timestamp.values()]
    rows.sort(key=lambda item: item.get("timestamp") or "")
    return rows


def should_replace_row(current: dict[str, Any], candidate: dict[str, Any]) -> bool:
    if current.get("is_missing") and not candidate.get("is_missing"):
        return True
    if candidate.get("is_missing") and not current.get("is_missing"):
        return False
    return (candidate.get("_period_end") or "") >= (current.get("_period_end") or "")


def strip_internal_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not key.startswith("_")}


def annotate_weather(
    readings: list[dict[str, Any]],
    weather: "WeatherData",
    clock_tolerance_hours: int,
) -> None:
    for row in readings:
        timestamp = parse_iso(row.get("timestamp"))
        if not timestamp:
            continue

        match = weather.nearest_temperature(timestamp, clock_tolerance_hours)
        if match:
            row["weather_temp"] = match["temperature"]
            row["weather_time"] = match["timestamp"]
            row["weather_delta_hours"] = match["delta_hours"]
            row["outside_temp_used"] = match["temperature"]
            row["outside_temp_source"] = "weather"
            continue


def build_daily_stats(readings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[date, list[dict[str, Any]]] = {}
    for row in readings:
        timestamp = parse_iso(row.get("timestamp"))
        if not timestamp:
            continue
        grouped.setdefault(timestamp.date(), []).append(row)

    if not grouped:
        return []

    latest_day = max(grouped)
    daily = []
    for day, rows in sorted(grouped.items()):
        runtime = sum_numeric(rows, "runtime_hours")
        missing_rows = sum(1 for row in rows if row.get("is_missing"))
        expected_hours = 24 if day < latest_day else max(
            (parse_iso(row.get("timestamp")).hour + 1 for row in rows if parse_iso(row.get("timestamp"))),
            default=len(rows),
        )
        complete_day = expected_hours >= 24 and len(rows) >= 23
        daily.append(
            {
                "date": day.isoformat(),
                "rows": len(rows),
                "expected_hours": expected_hours,
                "runtime_hours": round(runtime, 3) if runtime is not None else None,
                "missing_rows": missing_rows,
                "complete_day": complete_day,
            }
        )
    return daily


def analyze_meter(
    latest_report: dict[str, Any],
    readings: list[dict[str, Any]],
    daily: list[dict[str, Any]],
    graph: "TemperatureGraph",
    graph_profile: str,
) -> dict[str, Any]:
    errors = build_error_events(readings, daily, latest_report, graph, graph_profile)
    latest_error_date = latest_error_or_reading_date(errors, readings)
    recent_cutoff = latest_error_date - timedelta(days=RECENT_ERROR_DAYS - 1) if latest_error_date else None
    critical_cutoff = (
        latest_error_date - timedelta(days=CRITICAL_STATUS_DAYS - 1)
        if latest_error_date
        else None
    )
    recent_errors = [
        error
        for error in errors
        if recent_cutoff is None or parse_date(error["date"]) >= recent_cutoff
    ]
    critical_errors_7_days = [
        error
        for error in errors
        if error["severity"] == "critical"
        and (critical_cutoff is None or parse_date(error["date"]) >= critical_cutoff)
    ]
    noncritical_recent = [
        error
        for error in recent_errors
        if error["severity"] == "warning"
    ]
    issues = summarize_errors(critical_errors_7_days)
    missing_rows = sum(1 for row in latest_report.get("readings", []) if row.get("is_missing"))
    status = "OK"
    if critical_errors_7_days:
        status = "Critical"
    elif noncritical_recent:
        status = "Warning"
    elif not readings:
        status = "No Data"

    latest_completed_day = find_latest_completed_day(daily)
    imbalance = calculate_imbalance([row for row in readings if not row.get("is_missing")])
    temperature_result = summarize_temperature_errors(recent_errors)
    return {
        "status": status,
        "issues": issues,
        "errors": errors,
        "recent_errors": recent_errors,
        "default_errors": [error for error in recent_errors if error["severity"] == "critical"],
        "critical_errors_7_days": critical_errors_7_days,
        "errors_by_day": group_errors_by_day(errors),
        "recent_error_days": RECENT_ERROR_DAYS,
        "critical_status_days": CRITICAL_STATUS_DAYS,
        "recent_error_start": recent_cutoff.isoformat() if recent_cutoff else None,
        "recent_error_end": latest_error_date.isoformat() if latest_error_date else None,
        "critical_error_start": critical_cutoff.isoformat() if critical_cutoff else None,
        "total_rows": len(readings),
        "latest_rows": len(latest_report.get("readings", [])),
        "missing_rows": missing_rows,
        "reports_count": None,
        "max_imbalance_pct": round(imbalance["max"], 3) if imbalance["max"] is not None else None,
        "avg_imbalance_pct": round(imbalance["avg"], 3) if imbalance["avg"] is not None else None,
        "imbalance_hours_over_3": imbalance["count"],
        "latest_completed_day": latest_completed_day,
        "runtime_total": latest_completed_day.get("runtime_hours") if latest_completed_day else None,
        "graph_checked_rows": temperature_result["checked_count"],
        "graph_profile": graph_profile,
        "return_high_count": temperature_result["return_high_count"],
        "supply_out_count": temperature_result["supply_out_count"],
        "first_data_time": first_value(readings, "timestamp"),
        "latest_data_time": last_value([row for row in readings if not row.get("is_missing")], "timestamp"),
    }


def calculate_imbalance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values: list[float] = []
    over_3_count = 0
    for row in rows:
        m1 = numeric(row.get("M1"))
        m2 = numeric(row.get("M2"))
        if m1 is None or m2 is None or max(abs(m1), abs(m2)) < MIN_IMBALANCE_FLOW:
            continue
        value = abs(m1 - m2) / max(abs(m1), abs(m2)) * 100
        values.append(value)
        if value > M1_M2_WARNING_PCT:
            over_3_count += 1

    return {
        "max": max(values, default=None),
        "avg": sum(values) / len(values) if values else None,
        "count": over_3_count,
    }


def build_error_events(
    readings: list[dict[str, Any]],
    daily: list[dict[str, Any]],
    latest_report: dict[str, Any],
    graph: "TemperatureGraph",
    graph_profile: str,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    resource = latest_report.get("resource")

    for row in readings:
        timestamp = parse_iso(row.get("timestamp"))
        if not timestamp:
            continue
        day = timestamp.date().isoformat()

        if row.get("is_missing"):
            events.append(
                make_error(
                    timestamp=row.get("timestamp"),
                    date_value=day,
                    priority=9,
                    severity="info",
                    kind="missing",
                    title="Пропуск данных",
                    detail="В часовой строке прочерки; не считается критичной ошибкой.",
                )
            )
            continue

        runtime = numeric(row.get("runtime_hours"))
        if runtime is not None and runtime < RUNTIME_HOUR_MIN:
            events.append(
                make_error(
                    timestamp=row.get("timestamp"),
                    date_value=day,
                    priority=1,
                    severity="warning",
                    kind="runtime_hour",
                    title="Наработка меньше 1 ч",
                    detail=f"За час наработка {runtime:.2f} ч.",
                    value=runtime,
                )
            )

        imbalance = row_imbalance_pct(row)
        if imbalance is not None and imbalance > M1_M2_WARNING_PCT:
            events.append(
                make_error(
                    timestamp=row.get("timestamp"),
                    date_value=day,
                    priority=2,
                    severity="warning",
                    kind="m1_m2",
                    title="M1/M2 > 3%",
                    detail=f"Разность M1/M2 {imbalance:.1f}%.",
                    value=imbalance,
                )
            )

        if resource == "ТС":
            target = graph_target_for_row(row, graph, graph_profile)
            if target:
                t1 = numeric(row.get("t1"))
                t2 = numeric(row.get("t2"))
                outside = numeric(row.get("outside_temp_used"))
                source = row.get("outside_temp_source") or "unknown"
                if t2 is not None and abs(t2 - target["t2_max"]) > RETURN_TEMP_TOLERANCE:
                    delta = t2 - target["t2_max"]
                    events.append(
                        make_error(
                            timestamp=row.get("timestamp"),
                            date_value=day,
                            priority=4,
                            severity="warning",
                            kind="t2_high",
                            title="t2 вне графика МОЭК",
                            detail=(
                                f"t2={t2:.1f} °C, график={target['t2_max']:.1f} °C, "
                                f"отклонение={delta:+.1f} °C, "
                                f"наружная={outside:.1f} °C ({source})."
                            ),
                            value=delta,
                        )
                    )
                if t1 is not None and abs(t1 - target["t1_target"]) > SUPPLY_TEMP_TOLERANCE:
                    delta = t1 - target["t1_target"]
                    events.append(
                        make_error(
                            timestamp=row.get("timestamp"),
                            date_value=day,
                            priority=5,
                            severity="warning",
                            kind="t1_graph",
                            title="t1 вне графика МОЭК",
                            detail=(
                                f"t1={t1:.1f} °C, график={target['t1_target']:.1f} °C, "
                                f"наружная={outside:.1f} °C ({source})."
                            ),
                            value=delta,
                        )
                    )

    for day in daily:
        if not day.get("complete_day"):
            continue
        day_rows = rows_for_date(readings, day["date"])
        runtime = numeric(day.get("runtime_hours"))
        if runtime is not None and runtime < MIN_DAILY_RUNTIME_HOURS:
            events.append(
                make_error(
                    timestamp=f"{day['date']}T23:59:00",
                    date_value=day["date"],
                    priority=1,
                    severity="critical",
                    kind="runtime_day",
                    title="Наработка < 23 ч/сутки",
                    detail=f"За сутки {runtime:.1f} ч при норме не менее 23 ч.",
                    value=runtime,
                )
            )
        m1_m2_hours = sum(
            1
            for row in day_rows
            if (imbalance := row_imbalance_pct(row)) is not None
            and imbalance > M1_M2_WARNING_PCT
        )
        if m1_m2_hours >= M1_M2_CRITICAL_HOURS_PER_DAY:
            events.append(
                make_error(
                    timestamp=f"{day['date']}T23:59:00",
                    date_value=day["date"],
                    priority=2,
                    severity="critical",
                    kind="m1_m2_day",
                    title="M1/M2 > 3% два часа и более",
                    detail=f"За сутки часов с M1/M2 > 3%: {m1_m2_hours}.",
                    value=float(m1_m2_hours),
                )
            )

    events.extend(build_no_data_gap_events(readings))

    events.sort(key=lambda item: (item["date"], item["priority"], item.get("timestamp") or ""))
    return events


def make_error(
    timestamp: str | None,
    date_value: str,
    priority: int,
    severity: str,
    kind: str,
    title: str,
    detail: str,
    value: float | None = None,
) -> dict[str, Any]:
    error = {
        "timestamp": timestamp,
        "date": date_value,
        "priority": priority,
        "severity": severity,
        "kind": kind,
        "title": title,
        "detail": detail,
    }
    if value is not None:
        error["value"] = round(value, 3)
    return error


def row_imbalance_pct(row: dict[str, Any]) -> float | None:
    m1 = numeric(row.get("M1"))
    m2 = numeric(row.get("M2"))
    if m1 is None or m2 is None or max(abs(m1), abs(m2)) < MIN_IMBALANCE_FLOW:
        return None
    return abs(m1 - m2) / max(abs(m1), abs(m2)) * 100


def rows_for_date(readings: list[dict[str, Any]], day: str) -> list[dict[str, Any]]:
    return [
        row
        for row in readings
        if row.get("timestamp") and row["timestamp"].startswith(day)
    ]


def build_no_data_gap_events(readings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    missing_start: datetime | None = None
    missing_end: datetime | None = None
    previous_timestamp: datetime | None = None
    latest_data_timestamp: datetime | None = None

    for row in readings:
        timestamp = parse_iso(row.get("timestamp"))
        if not timestamp:
            continue

        if previous_timestamp:
            missing_hours = int((timestamp - previous_timestamp).total_seconds() // 3600) - 1
            if missing_hours > NO_DATA_CRITICAL_HOURS:
                append_no_data_gap_event(
                    events,
                    previous_timestamp + timedelta(hours=1),
                    timestamp - timedelta(hours=1),
                )
        previous_timestamp = timestamp

        if row.get("is_missing"):
            if missing_start is None:
                missing_start = timestamp
            missing_end = timestamp
            continue

        latest_data_timestamp = timestamp
        if missing_start and missing_end:
            append_no_data_gap_event(events, missing_start, missing_end)
        missing_start = None
        missing_end = None

    if missing_start and missing_end:
        append_no_data_gap_event(events, missing_start, missing_end)
    if latest_data_timestamp:
        monitor_time = datetime.now().replace(minute=0, second=0, microsecond=0)
        if monitor_time > latest_data_timestamp:
            append_no_data_gap_event(
                events,
                latest_data_timestamp + timedelta(hours=1),
                monitor_time,
            )
    return events


def append_no_data_gap_event(
    events: list[dict[str, Any]],
    start: datetime,
    end: datetime,
) -> None:
    hours = int((end - start).total_seconds() // 3600) + 1
    if hours <= NO_DATA_CRITICAL_HOURS:
        return
    events.append(
        make_error(
            timestamp=end.isoformat(),
            date_value=end.date().isoformat(),
            priority=3,
            severity="critical",
            kind="no_data_gap",
            title="Нет данных более 20 ч",
            detail=f"Нет часовых данных с {start:%d.%m %H:%M} по {end:%d.%m %H:%M}: {hours} ч.",
            value=float(hours),
        )
    )


def graph_target_for_row(
    row: dict[str, Any],
    graph: "TemperatureGraph",
    graph_profile: str,
) -> dict[str, float] | None:
    outside = numeric(row.get("outside_temp_used"))
    if outside is None or outside < -45 or outside > 30:
        return None
    return graph.interpolate(outside, profile=graph_profile)


def latest_error_or_reading_date(
    errors: list[dict[str, Any]],
    readings: list[dict[str, Any]],
) -> date | None:
    dates = [parse_date(error["date"]) for error in errors if error.get("date")]
    for row in readings:
        timestamp = parse_iso(row.get("timestamp"))
        if timestamp:
            dates.append(timestamp.date())
    return max(dates, default=None)


def summarize_errors(errors: list[dict[str, Any]]) -> list[dict[str, str]]:
    grouped: dict[str, dict[str, Any]] = {}
    for error in errors:
        key = error["kind"]
        item = grouped.setdefault(
            key,
            {
                "severity": error["severity"],
                "title": error["title"],
                "priority": error["priority"],
                "count": 0,
                "latest_detail": error["detail"],
            },
        )
        item["count"] += 1
        item["latest_detail"] = error["detail"]
        if severity_rank(error["severity"]) < severity_rank(item["severity"]):
            item["severity"] = error["severity"]
            item["title"] = error["title"]

    result = []
    for item in sorted(grouped.values(), key=lambda value: value["priority"]):
        result.append(
            {
                "severity": item["severity"],
                "title": item["title"],
                "detail": f"{item['count']} событий за выбранные последние дни. {item['latest_detail']}",
            }
        )
    return result


def severity_rank(severity: str) -> int:
    return {"critical": 0, "warning": 1, "info": 2}.get(severity, 3)


def group_errors_by_day(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_day: dict[str, list[dict[str, Any]]] = {}
    for error in errors:
        by_day.setdefault(error["date"], []).append(error)

    days = []
    for day, items in sorted(by_day.items(), reverse=True):
        items.sort(key=lambda item: (item["priority"], item.get("timestamp") or ""))
        days.append(
            {
                "date": day,
                "critical": sum(1 for item in items if item["severity"] == "critical"),
                "warning": sum(1 for item in items if item["severity"] == "warning"),
                "info": sum(1 for item in items if item["severity"] == "info"),
                "errors": items,
            }
        )
    return days


def summarize_temperature_errors(errors: list[dict[str, Any]]) -> dict[str, Any]:
    t2_errors = [error for error in errors if error["kind"] == "t2_high"]
    t1_errors = [error for error in errors if error["kind"] == "t1_graph"]
    return {
        "checked_count": len(t2_errors) + len(t1_errors),
        "return_high_count": len(t2_errors),
        "supply_out_count": len(t1_errors),
    }


def find_latest_completed_day(daily: list[dict[str, Any]]) -> dict[str, Any] | None:
    completed = [day for day in daily if day.get("complete_day")]
    return completed[-1] if completed else None


def check_temperature_graph(
    rows: list[dict[str, Any]],
    latest_report: dict[str, Any],
    graph: "TemperatureGraph",
    graph_profile: str,
) -> dict[str, Any]:
    result = {
        "checked_count": 0,
        "return_high_count": 0,
        "supply_out_count": 0,
        "max_return_over": 0.0,
    }

    if not graph.points or latest_report.get("resource") != "ТС":
        return result

    for row in rows:
        outside = numeric(row.get("ta"))
        t1 = numeric(row.get("t1"))
        t2 = numeric(row.get("t2"))
        if outside is None or t1 is None or t2 is None:
            continue
        if outside < -45 or outside > 15:
            continue

        target = graph.interpolate(outside, profile=graph_profile)
        if not target:
            continue

        result["checked_count"] += 1
        return_limit = target["t2_max"] + RETURN_TEMP_TOLERANCE
        if t2 > return_limit:
            result["return_high_count"] += 1
            result["max_return_over"] = max(result["max_return_over"], t2 - target["t2_max"])

        supply_target = target["t1_target"]
        if abs(t1 - supply_target) > SUPPLY_TEMP_TOLERANCE:
            result["supply_out_count"] += 1

    return result


def report_brief(report: dict[str, Any]) -> dict[str, Any]:
    checks = report.get("checks", {})
    return {
        "id": report.get("id"),
        "source_file": report.get("source_file"),
        "pages": report.get("pages", []),
        "period_start": report.get("period_start"),
        "period_end": report.get("period_end"),
        "rows": checks.get("total_rows") or len(report.get("readings", [])),
        "missing_rows": checks.get("missing_rows"),
    }


def first_value(rows: list[dict[str, Any]], key: str) -> Any:
    for row in rows:
        value = row.get(key)
        if value is not None:
            return value
    return None


def last_value(rows: list[dict[str, Any]], key: str) -> Any:
    for row in reversed(rows):
        value = row.get(key)
        if value is not None:
            return value
    return None


def sum_numeric(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [value for row in rows if (value := numeric(row.get(key))) is not None]
    return sum(values) if values else None


def numeric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


class TemperatureGraph:
    def __init__(self, profiles: dict[str, list[dict[str, float]]]) -> None:
        self.profiles = {
            profile: sorted(points, key=lambda item: item["outdoor_temp"])
            for profile, points in profiles.items()
        }
        self.points = next(iter(self.profiles.values()), [])

    @classmethod
    def load(cls, path: str | Path) -> "TemperatureGraph":
        csv_path = Path(path)
        if not csv_path.exists():
            return cls([])

        profiles: dict[str, list[dict[str, float]]] = {}
        with csv_path.open("r", encoding="utf-8") as handle:
            reader = csv.DictReader(
                row for row in handle if row.strip() and not row.lstrip().startswith("#")
            )
            for row in reader:
                profile = row.get("profile") or "default"
                profiles.setdefault(profile, []).append(
                    {
                        "outdoor_temp": float(row["outdoor_temp"]),
                        "t1_target": float(row["t1_target"]),
                        "t2_max": float(row["t2_max"]),
                    }
                )
        return cls(profiles)

    def interpolate(self, outdoor_temp: float, profile: str = "default") -> dict[str, float] | None:
        points = self.profiles.get(profile) or self.points
        if not points:
            return None
        if outdoor_temp <= points[0]["outdoor_temp"]:
            return dict(points[0])
        if outdoor_temp >= points[-1]["outdoor_temp"]:
            return dict(points[-1])

        for lower, upper in zip(points, points[1:]):
            if lower["outdoor_temp"] <= outdoor_temp <= upper["outdoor_temp"]:
                span = upper["outdoor_temp"] - lower["outdoor_temp"]
                ratio = (outdoor_temp - lower["outdoor_temp"]) / span if span else 0
                return {
                    "outdoor_temp": outdoor_temp,
                    "t1_target": interpolate_value(lower["t1_target"], upper["t1_target"], ratio),
                    "t2_max": interpolate_value(lower["t2_max"], upper["t2_max"], ratio),
                }
        return None


def interpolate_value(start: float, end: float, ratio: float) -> float:
    return start + (end - start) * ratio


class WeatherData:
    def __init__(self, values: dict[str, float], source: str = "none") -> None:
        self.values = values
        self.source = source if values else "meter_ta_fallback"

    @classmethod
    def load(cls, path: str | Path) -> "WeatherData":
        import json

        cache_path = Path(path)
        if not cache_path.exists():
            return cls({})
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        values = {
            key: float(value)
            for key, value in payload.get("hourly_temperature_2m", {}).items()
            if value is not None
        }
        return cls(values, payload.get("source", "weather_cache"))

    def nearest_temperature(
        self,
        timestamp: datetime,
        tolerance_hours: int,
    ) -> dict[str, Any] | None:
        if not self.values:
            return None

        candidates = []
        base = timestamp.replace(minute=0, second=0, microsecond=0)
        for offset in range(-tolerance_hours, tolerance_hours + 1):
            candidate_time = base + timedelta(hours=offset)
            key = candidate_time.strftime("%Y-%m-%dT%H:%M")
            if key not in self.values:
                continue
            candidates.append(
                {
                    "timestamp": key,
                    "temperature": self.values[key],
                    "delta_hours": round((candidate_time - timestamp).total_seconds() / 3600, 3),
                    "abs_delta": abs((candidate_time - timestamp).total_seconds()),
                }
            )
        if not candidates:
            return None
        best = min(candidates, key=lambda item: item["abs_delta"])
        del best["abs_delta"]
        return best
