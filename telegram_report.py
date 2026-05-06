from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont


DEFAULT_DATA_PATH = Path("parsed_reports.json")
DEFAULT_OUTPUT_DIR = Path("telegram_reports")
REPORT_HOURS = 36
MOSCOW_TZ = ZoneInfo("Europe/Moscow")

COLUMNS = [
    ("time", "Дата время"),
    ("M1", "M1"),
    ("M2", "M2"),
    ("diff", "M разн. %"),
    ("t1", "T1"),
    ("t2", "T2"),
    ("dt", "dT"),
    ("Q", "Q"),
    ("runtime", "Нараб."),
    ("outside", "T нар."),
]

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Verdana.ttf",
]

BOLD_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


class ReportError(RuntimeError):
    pass


def load_dashboard_data(path: str | Path = DEFAULT_DATA_PATH) -> dict[str, Any]:
    data_path = Path(path)
    if not data_path.exists():
        raise ReportError(f"Файл данных не найден: {data_path}")
    return json.loads(data_path.read_text(encoding="utf-8"))


def load_meters(path: str | Path = DEFAULT_DATA_PATH) -> list[dict[str, Any]]:
    data = load_dashboard_data(path)
    meters = data.get("meters") or []
    if not meters:
        raise ReportError("В parsed_reports.json нет теплосчетчиков. Сначала соберите дашборд.")
    return sorted(meters, key=meter_sort_key)


def meter_sort_key(meter: dict[str, Any]) -> tuple[str, str, str]:
    return (
        resource_label(meter.get("resource")),
        str(meter.get("object") or ""),
        str(meter.get("point") or ""),
    )


def available_resources(meters: list[dict[str, Any]]) -> list[str]:
    resources = sorted({str(meter.get("resource") or "Не указана") for meter in meters})
    return sorted(resources, key=lambda value: (resource_label(value), value))


def resource_label(resource: Any) -> str:
    value = str(resource or "Не указана")
    if value == "ТС":
        return "ТЭ/ТС"
    return value


def short_meter_title(meter: dict[str, Any]) -> str:
    serial = meter.get("serial_number") or "без номера"
    point = meter.get("point") or "без системы"
    resource = resource_label(meter.get("resource"))
    return f"{serial} | {resource} | {point}"


def full_meter_title(meter: dict[str, Any]) -> str:
    title = short_meter_title(meter)
    obj = str(meter.get("object") or "").strip()
    if obj:
        return f"{title}\n{obj}"
    return title


def filter_last_hours(meter: dict[str, Any], hours: int = REPORT_HOURS) -> list[dict[str, Any]]:
    rows = [
        row
        for row in meter.get("readings", [])
        if parse_iso(row.get("timestamp")) is not None
    ]
    rows.sort(key=lambda row: row.get("timestamp") or "")
    if not rows:
        return []

    end_time = parse_iso(rows[-1].get("timestamp"))
    if end_time is None:
        return rows[-hours:]
    start_time = end_time - timedelta(hours=hours - 1)
    return [
        row
        for row in rows
        if (timestamp := parse_iso(row.get("timestamp"))) is not None
        and start_time <= timestamp <= end_time
    ]


def render_hourly_report(
    meter: dict[str, Any],
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    hours: int = REPORT_HOURS,
) -> Path:
    rows = filter_last_hours(meter, hours)
    if not rows:
        raise ReportError("У выбранного теплосчетчика нет часовых данных.")

    output_path = report_path(meter, output_dir, rows)
    table_rows = [format_row(row) for row in rows]
    image = draw_report(meter, table_rows, rows[0].get("timestamp"), rows[-1].get("timestamp"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return output_path


def report_path(meter: dict[str, Any], output_dir: str | Path, rows: list[dict[str, Any]]) -> Path:
    serial = sanitize_filename(meter.get("serial_number") or "meter")
    point = sanitize_filename(meter.get("point") or "system")
    resource = sanitize_filename(meter.get("resource") or "resource")
    stamp = moscow_now().strftime("%Y%m%d_%H%M%S")
    period = sanitize_filename((rows[-1].get("timestamp") or stamp).replace("T", "_"))
    return Path(output_dir) / f"hourly_{serial}_{resource}_{point}_{period}_{stamp}.png"


def draw_report(
    meter: dict[str, Any],
    rows: list[list[str]],
    start_timestamp: str | None,
    end_timestamp: str | None,
) -> Image.Image:
    header_font = load_font(30)
    meta_font = load_font(20)
    table_font = load_font(19)
    table_bold = load_font(19, bold=True)

    column_widths = [152, 106, 106, 118, 82, 82, 82, 96, 96, 104]
    left = 38
    right = 38
    row_h = 38
    header_h = 40
    footer_h = 42
    table_width = sum(column_widths)
    width = left + table_width + right
    meta_lines = [short_meter_title(meter)]
    object_text = str(meter.get("object") or "").strip()
    if object_text:
        meta_lines.extend(wrap_text(object_text, meta_font, table_width, max_lines=3))
    title_h = 126 + len(meta_lines) * 26
    height = title_h + header_h + row_h * len(rows) + footer_h

    img = Image.new("RGB", (width, height), "#f5f7fb")
    draw = ImageDraw.Draw(img)

    draw.rectangle((0, 0, width, 16), fill="#2563eb")
    draw.text((left, 30), "Часовая распечатка за последние 36 часов", fill="#111827", font=header_font)
    period = f"{format_period_time(start_timestamp)} - {format_period_time(end_timestamp)}"
    period_y = 66
    draw.text((left, period_y), period, fill="#64748b", font=meta_font)

    meta_y = 98
    for line in meta_lines:
        draw.text((left, meta_y), line, fill="#475569", font=meta_font)
        meta_y += 26

    y = title_h
    x = left
    draw.rounded_rectangle(
        (left - 1, y - 1, left + table_width + 1, y + header_h + row_h * len(rows) + 1),
        radius=10,
        fill="#ffffff",
        outline="#d8dee9",
        width=1,
    )

    for index, (_, label) in enumerate(COLUMNS):
        cell_w = column_widths[index]
        draw.rectangle((x, y, x + cell_w, y + header_h), fill="#e8eef8")
        draw.text((x + 10, y + 10), label, fill="#334155", font=table_bold)
        x += cell_w

    y += header_h
    for row_index, row in enumerate(rows):
        x = left
        fill = "#ffffff" if row_index % 2 == 0 else "#f8fafc"
        if row_is_missing(row):
            fill = "#f1f5f9"
        draw.rectangle((left, y, left + table_width, y + row_h), fill=fill)
        for col_index, value in enumerate(row):
            cell_w = column_widths[col_index]
            color = "#111827"
            if value == "---":
                color = "#94a3b8"
            if col_index == 3 and numeric_value(value) is not None and numeric_value(value) > 3:
                color = "#c2410c"
            if col_index == 8 and numeric_value(value) is not None and numeric_value(value) < 1:
                color = "#be123c"
            draw.text((x + 10, y + 9), value, fill=color, font=table_font)
            draw.line((x, y, x, y + row_h), fill="#e2e8f0")
            x += cell_w
        draw.line((left, y + row_h, left + table_width, y + row_h), fill="#e2e8f0")
        y += row_h

    generated = "Сформировано: " + moscow_now().strftime("%d.%m.%Y %H:%M")
    draw.text((left, height - 30), generated, fill="#64748b", font=meta_font)
    return img


def moscow_now() -> datetime:
    return datetime.now(MOSCOW_TZ)


def format_row(row: dict[str, Any]) -> list[str]:
    return [
        format_time(row.get("timestamp")),
        format_number(row.get("M1"), 2),
        format_number(row.get("M2"), 2),
        format_number(imbalance_pct(row), 1),
        format_number(row.get("t1"), 1),
        format_number(row.get("t2"), 1),
        format_number(delta_t(row), 1),
        format_number(first_present(row, "Q", "Qtv"), 2),
        format_number(row.get("runtime_hours"), 2),
        format_number(outside_temperature(row), 1),
    ]


def imbalance_pct(row: dict[str, Any]) -> float | None:
    m1 = numeric(row.get("M1"))
    m2 = numeric(row.get("M2"))
    if m1 is None or m2 is None:
        return numeric(row.get("imbalance_pct"))
    base = max(abs(m1), abs(m2))
    if base < 0.1:
        return None
    return abs(m1 - m2) / base * 100


def delta_t(row: dict[str, Any]) -> float | None:
    existing = numeric(row.get("dt"))
    if existing is not None:
        return existing
    t1 = numeric(row.get("t1"))
    t2 = numeric(row.get("t2"))
    if t1 is None or t2 is None:
        return None
    return t1 - t2


def first_present(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


def outside_temperature(row: dict[str, Any]) -> float | None:
    weather_temp = numeric(row.get("weather_temp"))
    if weather_temp is not None and -55 < weather_temp < 55:
        return weather_temp

    if row.get("outside_temp_source") == "weather":
        outside_temp = numeric(row.get("outside_temp_used"))
        if outside_temp is not None and -55 < outside_temp < 55:
            return outside_temp
    return None


def format_number(value: Any, digits: int) -> str:
    number = numeric(value)
    if number is None:
        return "---"
    return f"{number:.{digits}f}"


def format_time(value: Any) -> str:
    timestamp = parse_iso(value)
    if not timestamp:
        return "---"
    return timestamp.strftime("%d.%m %H:%M")


def format_period_time(value: Any) -> str:
    timestamp = parse_iso(value)
    if not timestamp:
        return "---"
    return timestamp.strftime("%d.%m.%Y %H:%M")


def parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def numeric(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    text = str(value).replace(",", ".").strip()
    try:
        return float(text)
    except ValueError:
        return None


def numeric_value(value: str) -> float | None:
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None


def row_is_missing(row: list[str]) -> bool:
    return all(value == "---" for value in row[1:])


def sanitize_filename(value: str) -> str:
    text = re.sub(r"[^\wа-яА-ЯёЁ.-]+", "_", str(value), flags=re.UNICODE)
    return text.strip("._")[:80] or "report"


def text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def wrap_text(
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    max_lines: int,
) -> list[str]:
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if text_width(probe, candidate, font) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len(" ".join(lines)) < len(text):
        lines[-1] = lines[-1].rstrip(".,;: ") + "..."
    return lines


def load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = FONT_CANDIDATES
    if bold:
        candidates = [*BOLD_FONT_CANDIDATES, *FONT_CANDIDATES]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create Telegram PNG reports for MOEK meters.")
    parser.add_argument("--data", default=str(DEFAULT_DATA_PATH), help="Path to parsed_reports.json.")
    parser.add_argument("--list", action="store_true", help="List available meters.")
    parser.add_argument("--sample", type=int, help="Render a report for meter index from --list.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()

    meters = load_meters(args.data)
    if args.list:
        for index, meter in enumerate(meters):
            print(f"{index}: {short_meter_title(meter)}")
        return
    if args.sample is not None:
        if args.sample < 0 or args.sample >= len(meters):
            raise SystemExit(f"Meter index out of range: {args.sample}")
        path = render_hourly_report(meters[args.sample], output_dir=args.output_dir)
        print(path.resolve())
        return
    parser.print_help()


if __name__ == "__main__":
    main()
