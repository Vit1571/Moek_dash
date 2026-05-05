from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pypdf import PdfReader


UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
DATE_ROW_RE = re.compile(
    r"(?P<body>.*?)\s*\*?(?P<date>\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})\s*$"
)
PERIOD_RE = re.compile(
    r"\((?P<start>\d{2}\.\d{2}\.\d{4}\s+\d{1,2}:\d{2}:\d{2})\s*-\s*"
    r"(?P<end>\d{2}\.\d{2}\.\d{4}\s+\d{1,2}:\d{2}:\d{2})\)"
)

SOURCE_LABELS = {
    "t1",
    "t2",
    "t3",
    "dt",
    "V1",
    "V2",
    "V3",
    "M1",
    "M2",
    "M3",
    "M1-M2",
    "M2-M1",
    "dM",
    "P1",
    "P2",
    "P3",
    "Q",
    "Qo",
    "Qg",
    "Qот",
    "Qtv",
    "ta",
    "tа",
    "tsw",
    "Pcw",
    "Tр",
    "Tp",
    "Тр",
    "TWork",
    "QntHIP",
    "QntP",
    "ВНР",
    "NS",
    "%",
}

COLUMN_ALIASES = {
    "%": "imbalance_pct",
    "M1-M2": "m1_minus_m2",
    "M2-M1": "m2_minus_m1",
    "dM": "dM",
    "Qo": "Q",
    "Qg": "Qg",
    "Qот": "Q",
    "tа": "ta",
    "Tр": "runtime_hours",
    "Tp": "runtime_hours",
    "Тр": "runtime_hours",
    "TWork": "runtime_hours",
    "QntHIP": "qnt_hip",
    "QntP": "qnt_p",
    "ВНР": "runtime_hours",
}

FALLBACK_COLUMNS = {
    8: [
        "t1",
        "t2",
        "dt",
        "M1",
        "M2",
        "imbalance_pct",
        "Qtv",
        "runtime_hours",
    ],
    18: [
        "t1",
        "t2",
        "t3",
        "dt",
        "V1",
        "V2",
        "M1",
        "M2",
        "M3",
        "imbalance_pct",
        "m1_minus_m2",
        "m2_minus_m1",
        "P1",
        "P2",
        "P3",
        "Q",
        "ta",
        "runtime_hours",
    ],
}

MIN_IMBALANCE_FLOW = 0.1


def parse_folder(folder: str | Path) -> list[dict[str, Any]]:
    folder_path = Path(folder)
    reports: list[dict[str, Any]] = []
    for pdf_path in sorted(folder_path.glob("*.pdf")):
        reports.extend(parse_pdf(pdf_path))
    reports.sort(key=lambda item: (item.get("object") or "", item.get("point") or ""))
    return reports


def parse_pdf(pdf_path: str | Path) -> list[dict[str, Any]]:
    pdf_path = Path(pdf_path)
    reader = PdfReader(str(pdf_path))

    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for page_index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if "Точка измерения:" in text:
            if current:
                sections.append(current)
            current = {
                "file": pdf_path.name,
                "pages": [page_index],
                "text": text,
            }
        elif current:
            current["pages"].append(page_index)
            current["text"] += "\n" + text

    if current:
        sections.append(current)

    reports = []
    for section_index, section in enumerate(sections, start=1):
        report = parse_section(section["text"], pdf_path.name, section["pages"], section_index)
        report["checks"] = analyze_report(report)
        reports.append(report)
    return reports


def parse_section(
    text: str, source_file: str, pages: list[int], section_index: int
) -> dict[str, Any]:
    columns = extract_columns(text)
    metadata = extract_metadata(text)
    readings = extract_readings(text, columns)

    return {
        "id": f"{source_file}#{section_index}",
        "source_file": source_file,
        "pages": pages,
        "section_index": section_index,
        "columns": columns,
        "readings": readings,
        **metadata,
    }


def extract_metadata(text: str) -> dict[str, Any]:
    period_match = PERIOD_RE.search(text)
    period_start = parse_datetime(period_match.group("start")) if period_match else None
    period_end = parse_datetime(period_match.group("end")) if period_match else None

    point = None
    resource = None
    point_match = re.search(r"Точка измерения:\s*(?P<point>.*?);\s*Ресурс:\s*(?P<resource>.*)", text)
    if point_match:
        point = point_match.group("point").strip()
        resource = point_match.group("resource").strip()

    meter_model = None
    serial_number = None
    meter_match = re.search(
        r"Прибор:\s*(?P<model>.*?)\s+Заводской номер:\s*(?P<serial>[^\n\r]+)", text
    )
    if meter_match:
        meter_model = meter_match.group("model").strip()
        serial_number = meter_match.group("serial").strip()

    uuid_match = UUID_RE.search(text)

    return {
        "uuid": uuid_match.group(0) if uuid_match else None,
        "object": extract_line_value(text, "Объект"),
        "meter_model": meter_model,
        "serial_number": serial_number,
        "point": point,
        "resource": resource,
        "scheme": extract_line_value(text, "Схема измерений"),
        "period_start": period_start.isoformat() if period_start else None,
        "period_end": period_end.isoformat() if period_end else None,
    }


def extract_line_value(text: str, label: str) -> str | None:
    match = re.search(rf"{re.escape(label)}:\s*(.+)", text)
    return match.group(1).strip() if match else None


def extract_columns(text: str) -> list[str]:
    uuid_match = UUID_RE.search(text)
    header = text[: uuid_match.start()] if uuid_match else text.split("Дата", 1)[0]

    labels: list[str] = []
    for raw_line in header.splitlines():
        for token in raw_line.strip().split():
            if token in SOURCE_LABELS:
                labels.append(token)

    return unique_columns(labels)


def unique_columns(labels: list[str]) -> list[str]:
    columns: list[str] = []
    seen: dict[str, int] = {}
    for label in labels:
        normalized = COLUMN_ALIASES.get(label, label)
        count = seen.get(normalized, 0)
        seen[normalized] = count + 1
        columns.append(normalized if count == 0 else f"{normalized}_{count + 1}")
    return columns


def extract_readings(text: str, columns: list[str]) -> list[dict[str, Any]]:
    hourly_text = text.split("Дата", 1)[-1]
    stop_positions = [
        pos
        for marker in ("Средние:", "\nИтого:")
        for pos in [hourly_text.find(marker)]
        if pos >= 0
    ]
    if stop_positions:
        hourly_text = hourly_text[: min(stop_positions)]

    readings: list[dict[str, Any]] = []
    active_columns = columns[:]

    for raw_line in hourly_text.splitlines():
        line = raw_line.strip()
        row_match = DATE_ROW_RE.search(line)
        if not row_match:
            continue

        values = parse_value_tokens(row_match.group("body"))
        if not values:
            continue

        if active_columns and len(active_columns) == len(values) + 1 and active_columns[-1] == "NS":
            active_columns = active_columns[:-1]

        if not active_columns:
            active_columns = FALLBACK_COLUMNS.get(
                len(values), [f"value_{index + 1}" for index in range(len(values))]
            )

        if len(values) > len(active_columns):
            extra_count = len(values) - len(active_columns)
            active_columns.extend(
                [f"value_{len(active_columns) + index + 1}" for index in range(extra_count)]
            )

        padded_values = values + [None] * max(0, len(active_columns) - len(values))
        row = {
            column: padded_values[index] for index, column in enumerate(active_columns)
        }
        row["timestamp"] = parse_datetime(row_match.group("date")).isoformat()
        row["raw"] = line
        row["is_missing"] = all(value is None for value in values)
        readings.append(row)

    return readings


def parse_value_tokens(text: str) -> list[float | None]:
    tokens = re.findall(r"---|-?\d+(?:\.\d+)?", text.replace(",", "."))
    values: list[float | None] = []
    for token in tokens:
        values.append(None if token == "---" else float(token))
    return values


def parse_datetime(value: str) -> datetime:
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    raise ValueError(f"Unsupported datetime value: {value}")


def analyze_report(report: dict[str, Any]) -> dict[str, Any]:
    readings = report.get("readings", [])
    issues: list[dict[str, str]] = []

    total_rows = len(readings)
    missing_rows = sum(1 for row in readings if row.get("is_missing"))
    non_missing = [row for row in readings if not row.get("is_missing")]

    period_start = parse_iso(report.get("period_start"))
    period_end = parse_iso(report.get("period_end"))
    expected_rows = None
    if period_start and period_end:
        expected_rows = int((period_end - period_start).total_seconds() // 3600) + 1

    latest_data_time = max(
        (parse_iso(row.get("timestamp")) for row in non_missing if row.get("timestamp")),
        default=None,
    )

    stale_hours = None
    if period_end and latest_data_time:
        stale_hours = max(0.0, (period_end - latest_data_time).total_seconds() / 3600)

    def add_issue(severity: str, title: str, detail: str) -> None:
        issues.append({"severity": severity, "title": title, "detail": detail})

    if total_rows == 0:
        add_issue("critical", "Нет часовых строк", "В распечатке не найдены часовые данные.")

    if expected_rows and total_rows < expected_rows:
        add_issue(
            "warning",
            "Неполный период",
            f"Найдено {total_rows} строк из ожидаемых {expected_rows}.",
        )

    if missing_rows:
        severity = "critical" if missing_rows >= 4 else "warning"
        add_issue(
            severity,
            "Пропуски данных",
            f"Строк с прочерками: {missing_rows}.",
        )

    if stale_hours is not None and stale_hours > 2:
        add_issue(
            "critical",
            "Данные остановились",
            f"Последнее непустое значение на {stale_hours:.0f} ч раньше конца периода.",
        )

    imbalance_values = calculate_imbalance_values(non_missing)
    max_imbalance = max(imbalance_values, default=None)
    avg_imbalance = average(imbalance_values)
    if max_imbalance is not None:
        if max_imbalance > 10:
            add_issue(
                "critical",
                "Большой небаланс M1/M2",
                f"Максимальное расхождение {max_imbalance:.1f}%.",
            )
        elif max_imbalance > 5:
            add_issue(
                "warning",
                "Небаланс M1/M2",
                f"Максимальное расхождение {max_imbalance:.1f}%.",
            )

    reversed_temperature = [
        row
        for row in non_missing
        if numeric(row.get("t1")) is not None
        and numeric(row.get("t2")) is not None
        and numeric(row.get("t1")) <= numeric(row.get("t2"))
    ]
    if reversed_temperature and report.get("resource") == "ТС":
        add_issue(
            "critical",
            "Подача не выше обратки",
            f"Часов с t1 <= t2: {len(reversed_temperature)}.",
        )

    dt_values = collect_numeric(non_missing, "dt")
    min_dt = min(dt_values, default=None)
    avg_dt = average(dt_values)
    if min_dt is not None and min_dt <= 1 and has_positive_flow_or_energy(non_missing):
        add_issue(
            "warning",
            "Малая дельта температур",
            f"Минимальная dt: {min_dt:.2f} °C.",
        )

    runtime_values = collect_numeric(non_missing, "runtime_hours")
    runtime_total = sum(runtime_values) if runtime_values else None
    if runtime_total is not None and non_missing and runtime_total < len(non_missing) * 0.95:
        add_issue(
            "warning",
            "Малая наработка",
            f"Наработка {runtime_total:.1f} ч при {len(non_missing)} непустых строках.",
        )

    pressure_issues = find_pressure_issues(non_missing)
    if pressure_issues:
        add_issue(
            "warning",
            "Давление вне диапазона",
            f"Найдено подозрительных значений: {pressure_issues}.",
        )

    negative_energy = [
        row
        for row in non_missing
        for key in ("Q", "Qtv")
        if numeric(row.get(key)) is not None and numeric(row.get(key)) < 0
    ]
    if negative_energy:
        add_issue(
            "critical",
            "Отрицательная энергия",
            f"Строк с отрицательным Q/Qtv: {len(negative_energy)}.",
        )

    status = "OK"
    if any(issue["severity"] == "critical" for issue in issues):
        status = "Critical"
    elif issues:
        status = "Warning"
    elif total_rows == 0:
        status = "No Data"

    return {
        "status": status,
        "issues": issues,
        "total_rows": total_rows,
        "missing_rows": missing_rows,
        "expected_rows": expected_rows,
        "latest_data_time": latest_data_time.isoformat() if latest_data_time else None,
        "stale_hours": round(stale_hours, 2) if stale_hours is not None else None,
        "max_imbalance_pct": round(max_imbalance, 3) if max_imbalance is not None else None,
        "avg_imbalance_pct": round(avg_imbalance, 3) if avg_imbalance is not None else None,
        "min_dt": round(min_dt, 3) if min_dt is not None else None,
        "avg_dt": round(avg_dt, 3) if avg_dt is not None else None,
        "runtime_total": round(runtime_total, 3) if runtime_total is not None else None,
    }


def parse_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def numeric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def collect_numeric(rows: list[dict[str, Any]], key: str) -> list[float]:
    return [value for row in rows if (value := numeric(row.get(key))) is not None]


def average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def calculate_imbalance_values(rows: list[dict[str, Any]]) -> list[float]:
    values: list[float] = []
    for row in rows:
        m1 = numeric(row.get("M1"))
        m2 = numeric(row.get("M2"))
        if m1 is None or m2 is None or max(abs(m1), abs(m2)) < MIN_IMBALANCE_FLOW:
            continue
        values.append(abs(m1 - m2) / max(abs(m1), abs(m2)) * 100)
    return values


def has_positive_flow_or_energy(rows: list[dict[str, Any]]) -> bool:
    for row in rows:
        for key in ("M1", "M2", "V1", "V2", "Q", "Qtv"):
            value = numeric(row.get(key))
            if value is not None and value > 0:
                return True
    return False


def find_pressure_issues(rows: list[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        for key in ("P1", "P2", "P3"):
            value = numeric(row.get(key))
            if value is None or value == 0:
                continue
            if value < 0 or value > 20:
                count += 1
    return count
