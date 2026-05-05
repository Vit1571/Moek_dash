from __future__ import annotations

import argparse
import json
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any

from meter_analytics import build_meters
from moek_parser import parse_folder
from weather_fetcher import update_moscow_weather_cache


def main() -> None:
    parser = argparse.ArgumentParser(description="Build MOEK heat meter MVP dashboard.")
    parser.add_argument("--pdf-dir", default=".", help="Folder with source PDF printouts.")
    parser.add_argument("--output", default="dashboard.html", help="Output HTML file.")
    parser.add_argument("--json-output", default="parsed_reports.json", help="Output parsed JSON file.")
    parser.add_argument(
        "--graph",
        default="moek_temperature_graph.csv",
        help="CSV with MOEK temperature graph values.",
    )
    parser.add_argument(
        "--graph-profile",
        default="rts_kts_150_70",
        help="Temperature graph profile from moek_temperature_graph.csv.",
    )
    parser.add_argument(
        "--meter-profiles",
        default="meter_graph_profiles.csv",
        help="Optional CSV mapping meter serial/point/resource to graph profiles.",
    )
    parser.add_argument(
        "--history-dir",
        help="Optional folder for timestamped dashboard/json snapshots.",
    )
    parser.add_argument("--weather-cache", default="weather_moscow_hourly.json")
    parser.add_argument("--no-weather-fetch", action="store_true")
    parser.add_argument("--clock-tolerance-hours", type=int, default=2)
    args = parser.parse_args()

    pdf_dir = Path(args.pdf_dir)
    reports = parse_folder(pdf_dir)
    weather_status = update_weather_if_needed(
        reports,
        args.weather_cache,
        skip_fetch=args.no_weather_fetch,
    )
    meters = build_meters(
        reports,
        graph_path=args.graph,
        default_graph_profile=args.graph_profile,
        profile_map_path=args.meter_profiles,
        weather_path=args.weather_cache,
        clock_tolerance_hours=args.clock_tolerance_hours,
    )
    generated_at = datetime.now().isoformat(timespec="seconds")
    payload = {
        "generated_at": generated_at,
        "pdf_dir": str(pdf_dir.resolve()),
        "report_count": len(reports),
        "graph_profile": args.graph_profile,
        "weather": weather_status,
        "summary": build_summary(meters),
        "meters": meters,
    }

    json_path = Path(args.json_output)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    html = build_html(payload)
    output_path = Path(args.output)
    output_path.write_text(html, encoding="utf-8")

    if args.history_dir:
        history_dir = Path(args.history_dir)
        history_dir.mkdir(parents=True, exist_ok=True)
        stamp = generated_at.replace(":", "").replace("-", "").replace("T", "_")
        shutil.copyfile(output_path, history_dir / f"dashboard_{stamp}.html")
        shutil.copyfile(json_path, history_dir / f"parsed_reports_{stamp}.json")

    print(f"Parsed reports: {len(reports)}")
    print(f"Grouped meters: {len(meters)}")
    print(f"Weather: {weather_status['status']}")
    print(f"JSON: {json_path.resolve()}")
    print(f"Dashboard: {output_path.resolve()}")


def build_summary(meters: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    resource_counts: dict[str, int] = {}
    total_missing = 0
    critical_issues = 0
    warning_issues = 0

    for meter in meters:
        checks = meter.get("checks", {})
        status = checks.get("status", "Unknown")
        resource = meter.get("resource") or "Не указан"
        status_counts[status] = status_counts.get(status, 0) + 1
        resource_counts[resource] = resource_counts.get(resource, 0) + 1
        total_missing += checks.get("missing_rows") or 0
        critical_issues += len(checks.get("critical_errors_7_days", []))
        warning_issues += sum(
            1
            for error in checks.get("recent_errors", [])
            if error.get("severity") == "warning"
        )

    return {
        "total_reports": sum(meter.get("reports_count") or 0 for meter in meters),
        "total_meters": len(meters),
        "status_counts": status_counts,
        "resource_counts": resource_counts,
        "total_missing_rows": total_missing,
        "critical_issues": critical_issues,
        "warning_issues": warning_issues,
    }


def update_weather_if_needed(
    reports: list[dict[str, Any]],
    cache_path: str,
    skip_fetch: bool,
) -> dict[str, Any]:
    date_range = report_date_range(reports)
    if not date_range:
        return {"status": "no_dates", "cache_path": cache_path}

    start_date, end_date = date_range
    status = {
        "status": "skipped" if skip_fetch else "updated",
        "cache_path": cache_path,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "source": "Open-Meteo Historical Weather API",
    }
    if skip_fetch:
        return status

    try:
        update_moscow_weather_cache(start_date, end_date, cache_path=cache_path)
    except Exception as exc:
        status["status"] = "fallback"
        status["error"] = str(exc)
    return status


def report_date_range(reports: list[dict[str, Any]]) -> tuple[date, date] | None:
    dates: list[date] = []
    for report in reports:
        for key in ("period_start", "period_end"):
            value = report.get(key)
            if value:
                dates.append(datetime.fromisoformat(value).date())
        for row in report.get("readings", []):
            value = row.get("timestamp")
            if value:
                dates.append(datetime.fromisoformat(value).date())
    if not dates:
        return None
    return min(dates), max(dates)


def build_html(payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False)
    return DASHBOARD_TEMPLATE.replace("__MOEK_DATA__", data)


DASHBOARD_TEMPLATE = r"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>МОЭК | Контроль теплосчетчиков</title>
  <style>
    :root {
      --bg: #f6f7fb;
      --panel: #ffffff;
      --ink: #172033;
      --muted: #667085;
      --line: #d9dee8;
      --ok: #168a52;
      --ok-bg: #dcf7e8;
      --warn: #b76b00;
      --warn-bg: #fff0c2;
      --crit: #c9253d;
      --crit-bg: #ffe0e5;
      --blue: #2563eb;
      --cyan: #0284c7;
      --violet: #7c3aed;
      --shadow: 0 16px 40px rgba(23, 32, 51, .08);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      color: var(--ink);
      background: var(--bg);
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }

    .topbar {
      background: #ffffff;
      border-bottom: 1px solid var(--line);
      padding: 18px 28px;
      position: sticky;
      top: 0;
      z-index: 10;
    }

    .topbar-inner {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
      max-width: 1520px;
      margin: 0 auto;
    }

    h1 {
      font-size: 24px;
      line-height: 1.2;
      margin: 0;
      font-weight: 760;
    }

    .generated {
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
    }

    main {
      max-width: 1520px;
      margin: 0 auto;
      padding: 24px 28px 40px;
    }

    .kpis {
      display: grid;
      grid-template-columns: repeat(6, minmax(120px, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }

    .kpi {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px 16px;
      min-height: 92px;
      box-shadow: 0 10px 26px rgba(23, 32, 51, .04);
    }

    .kpi-label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
      text-transform: uppercase;
    }

    .kpi-value {
      font-size: 30px;
      font-weight: 800;
      margin-top: 8px;
    }

    .kpi.ok .kpi-value { color: var(--ok); }
    .kpi.warn .kpi-value { color: var(--warn); }
    .kpi.crit .kpi-value { color: var(--crit); }
    .kpi.blue .kpi-value { color: var(--blue); }

    .filters {
      display: grid;
      grid-template-columns: minmax(220px, 1fr) 180px 180px;
      gap: 12px;
      margin: 18px 0;
    }

    input, select {
      width: 100%;
      height: 42px;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      padding: 0 12px;
      font: inherit;
    }

    .layout {
      display: grid;
      grid-template-columns: minmax(460px, 1.08fr) minmax(420px, .92fr);
      gap: 18px;
      align-items: start;
    }

    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }

    .panel-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      padding: 15px 16px;
      border-bottom: 1px solid var(--line);
    }

    .panel-title {
      font-size: 16px;
      font-weight: 760;
    }

    .report-list {
      display: grid;
      gap: 0;
      max-height: 720px;
      overflow: auto;
    }

    .report-row {
      display: grid;
      grid-template-columns: 10px 1fr auto;
      width: 100%;
      border: 0;
      border-bottom: 1px solid var(--line);
      background: #fff;
      color: inherit;
      padding: 0;
      text-align: left;
      cursor: pointer;
      font: inherit;
      min-height: 86px;
    }

    .report-row:hover,
    .report-row.active {
      background: #f8fbff;
    }

    .stripe.ok { background: var(--ok); }
    .stripe.warning { background: var(--warn); }
    .stripe.critical { background: var(--crit); }
    .stripe.unknown { background: var(--muted); }

    .row-main {
      padding: 12px 14px;
      min-width: 0;
    }

    .object {
      font-weight: 740;
      font-size: 14px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .meta {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      margin-top: 4px;
    }

    .row-side {
      padding: 12px 14px 12px 0;
      display: flex;
      align-items: flex-start;
      gap: 8px;
      flex-direction: column;
    }

    .badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 86px;
      border-radius: 999px;
      padding: 5px 10px;
      font-size: 12px;
      font-weight: 760;
    }

    .badge.ok { color: var(--ok); background: var(--ok-bg); }
    .badge.warning { color: var(--warn); background: var(--warn-bg); }
    .badge.critical { color: var(--crit); background: var(--crit-bg); }
    .badge.unknown { color: var(--muted); background: #eef1f6; }

    .mini {
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }

    .detail {
      padding: 18px;
    }

    .detail-title {
      font-size: 19px;
      font-weight: 790;
      line-height: 1.25;
      margin-bottom: 8px;
    }

    .detail-subtitle {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }

    .stat-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 10px;
      margin: 18px 0;
    }

    .stat {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fbfcff;
      min-height: 78px;
    }

    .stat-label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }

    .stat-value {
      font-size: 20px;
      font-weight: 790;
      margin-top: 5px;
    }

    .issues {
      display: grid;
      gap: 8px;
      margin-bottom: 18px;
    }

    .issue {
      border-radius: 8px;
      padding: 10px 12px;
      border: 1px solid var(--line);
      font-size: 13px;
      line-height: 1.45;
    }

    .issue.critical {
      background: var(--crit-bg);
      border-color: #ffb6c3;
    }

    .issue.warning {
      background: var(--warn-bg);
      border-color: #ffd56d;
    }

    .issue.info {
      background: #eef5ff;
      border-color: #bfd7ff;
    }

    .issue-title {
      font-weight: 760;
      margin-bottom: 2px;
    }

    .chart-box {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 12px;
      margin-bottom: 16px;
      min-width: 0;
      overflow: hidden;
    }

    .chart-controls {
      display: grid;
      grid-template-columns: 160px 160px 1fr;
      gap: 10px;
      align-items: end;
      margin: 16px 0;
      min-width: 0;
    }

    .chart-controls > * {
      min-width: 0;
    }

    .field-label {
      color: var(--muted);
      display: block;
      font-size: 12px;
      font-weight: 700;
      margin-bottom: 5px;
    }

    .param-grid {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    .param-grid label {
      align-items: center;
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      display: inline-flex;
      font-size: 12px;
      gap: 6px;
      padding: 8px 10px;
    }

    .param-grid input {
      height: auto;
      width: auto;
    }

    .secondary-button {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      color: var(--ink);
      cursor: pointer;
      font: inherit;
      font-size: 13px;
      font-weight: 720;
      height: 42px;
      padding: 0 12px;
    }

    .secondary-button.active {
      border-color: var(--blue);
      color: var(--blue);
    }

    canvas {
      width: 100%;
      height: 280px;
      display: block;
    }

    .legend {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 12px;
      margin-top: 10px;
    }

    .legend-item {
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }

    .dot {
      width: 9px;
      height: 9px;
      border-radius: 50%;
      display: inline-block;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }

    .table-wrap {
      max-height: 520px;
      overflow: auto;
      width: 100%;
    }

    .table-wrap table {
      min-width: 860px;
    }

    th, td {
      border-bottom: 1px solid var(--line);
      padding: 7px 6px;
      text-align: right;
      white-space: nowrap;
    }

    th:first-child, td:first-child {
      text-align: left;
    }

    th {
      color: var(--muted);
      font-weight: 720;
      background: #fbfcff;
    }

    .errors-table td:last-child,
    .errors-table th:last-child {
      min-width: 360px;
      text-align: left;
      white-space: normal;
    }

    .hourly-table td:first-child,
    .hourly-table th:first-child {
      position: sticky;
      left: 0;
      background: #fff;
      z-index: 1;
    }

    .empty {
      padding: 36px 18px;
      color: var(--muted);
      text-align: center;
    }

    @media (max-width: 1100px) {
      .kpis { grid-template-columns: repeat(3, 1fr); }
      .layout { grid-template-columns: 1fr; }
      .report-list { max-height: none; }
    }

    @media (max-width: 760px) {
      .topbar { padding: 14px 16px; }
      .topbar-inner { align-items: flex-start; flex-direction: column; }
      .generated { white-space: normal; }
      main { padding: 16px; }
      .kpis { grid-template-columns: repeat(2, 1fr); }
      .filters { grid-template-columns: 1fr; }
      .stat-grid { grid-template-columns: repeat(2, 1fr); }
      .chart-controls { grid-template-columns: 1fr; }
      .report-row { grid-template-columns: 8px 1fr; }
      .row-side { grid-column: 2; padding: 0 14px 12px; flex-direction: row; }
    }
  </style>
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <h1>Контроль теплосчетчиков МОЭК</h1>
      <div class="generated" id="generatedAt"></div>
    </div>
  </header>

  <main>
    <section class="kpis" id="kpis"></section>

    <section class="filters">
      <input id="searchInput" type="search" placeholder="Поиск по объекту, прибору, номеру" />
      <select id="resourceFilter"></select>
      <select id="statusFilter">
        <option value="">Все статусы</option>
        <option value="Critical">Critical</option>
        <option value="Warning">Warning</option>
        <option value="OK">OK</option>
        <option value="No Data">No Data</option>
      </select>
    </section>

    <section class="layout">
      <div class="panel">
        <div class="panel-head">
          <div class="panel-title">Теплосчетчики</div>
          <div class="mini" id="visibleCount"></div>
        </div>
        <div class="report-list" id="reportList"></div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <div class="panel-title">Детализация</div>
          <div class="mini" id="detailPeriod"></div>
        </div>
        <div class="detail" id="detail"></div>
      </div>
    </section>
  </main>

  <script>
    const DATA = __MOEK_DATA__;
    const meters = DATA.meters || DATA.reports || [];
    let selectedId = meters[0]?.id || null;
    let showExtraErrors = false;

    const statusOrder = { Critical: 0, Warning: 1, OK: 2, "No Data": 3 };
    const statusLabels = {
      OK: "OK",
      Warning: "Warning",
      Critical: "Critical",
      "No Data": "No Data",
      Unknown: "Unknown"
    };

    function statusClass(status) {
      const value = (status || "Unknown").toLowerCase();
      if (value === "ok") return "ok";
      if (value === "warning") return "warning";
      if (value === "critical") return "critical";
      return "unknown";
    }

    function fmt(value, digits = 1) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
      return Number(value).toLocaleString("ru-RU", { maximumFractionDigits: digits });
    }

    function fmtDate(value) {
      if (!value) return "—";
      return new Date(value).toLocaleString("ru-RU", {
        day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"
      });
    }

    function renderKpis() {
      const summary = DATA.summary || {};
      const counts = summary.status_counts || {};
      const items = [
        ["Приборы", summary.total_meters || meters.length || 0, "blue"],
        ["Critical", counts.Critical || 0, "crit"],
        ["Warning", counts.Warning || 0, "warn"],
        ["OK", counts.OK || 0, "ok"],
        ["Пропуски", summary.total_missing_rows || 0, "blue"],
        ["Остальные", summary.warning_issues || 0, "warn"],
      ];
      document.getElementById("kpis").innerHTML = items.map(([label, value, klass]) => `
        <div class="kpi ${klass}">
          <div class="kpi-label">${label}</div>
          <div class="kpi-value">${value}</div>
        </div>
      `).join("");
    }

    function renderResourceFilter() {
      const resources = Array.from(new Set(meters.map(r => r.resource || "Не указан"))).sort();
      document.getElementById("resourceFilter").innerHTML = [
        '<option value="">Все ресурсы</option>',
        ...resources.map(resource => `<option value="${escapeHtml(resource)}">${escapeHtml(resource)}</option>`)
      ].join("");
    }

    function filteredReports() {
      const query = document.getElementById("searchInput").value.trim().toLowerCase();
      const resource = document.getElementById("resourceFilter").value;
      const status = document.getElementById("statusFilter").value;

      return meters
        .filter(meter => {
          const checks = meter.checks || {};
          const haystack = [
            meter.object, meter.meter_model, meter.serial_number,
            meter.point, meter.source_file, meter.scheme
          ].join(" ").toLowerCase();
          return (!query || haystack.includes(query))
            && (!resource || (meter.resource || "Не указан") === resource)
            && (!status || checks.status === status);
        })
        .sort((a, b) => {
          const sa = statusOrder[a.checks?.status] ?? 9;
          const sb = statusOrder[b.checks?.status] ?? 9;
          return sa - sb || String(a.object).localeCompare(String(b.object), "ru");
        });
    }

    function renderList() {
      const list = filteredReports();
      const container = document.getElementById("reportList");
      document.getElementById("visibleCount").textContent = `${list.length} из ${meters.length}`;

      if (!list.length) {
        container.innerHTML = '<div class="empty">Нет приборов по текущим фильтрам</div>';
        return;
      }

      if (!list.some(meter => meter.id === selectedId)) {
        selectedId = list[0].id;
      }

      container.innerHTML = list.map(meter => {
        const checks = meter.checks || {};
        const klass = statusClass(checks.status);
        const issues = checks.issues || [];
        return `
          <button class="report-row ${meter.id === selectedId ? "active" : ""}"
                  data-id="${escapeHtml(meter.id)}">
            <span class="stripe ${klass}"></span>
            <span class="row-main">
              <div class="object">${escapeHtml(meter.object || "Объект не указан")}</div>
              <div class="meta">
                ${escapeHtml(meter.resource || "—")} · ${escapeHtml(meter.point || "—")} ·
                ${escapeHtml(meter.meter_model || "—")} № ${escapeHtml(meter.serial_number || "—")}
              </div>
              <div class="meta">Отчетов: ${fmt(meter.reports_count, 0)} · последний: ${escapeHtml(meter.source_file || "")}</div>
            </span>
            <span class="row-side">
              <span class="badge ${klass}">${statusLabels[checks.status] || checks.status || "Unknown"}</span>
              <span class="mini">${issues.length} крит.</span>
            </span>
          </button>
        `;
      }).join("");

      container.querySelectorAll(".report-row").forEach(button => {
        button.addEventListener("click", () => {
          selectedId = button.dataset.id;
          showExtraErrors = false;
          renderList();
          renderDetail();
        });
      });
    }

    const PARAMS = [
      { key: "t1", label: "t1", color: "#e11d48" },
      { key: "t2", label: "t2", color: "#2563eb" },
      { key: "dt", label: "dt", color: "#16a34a" },
      { key: "M1", label: "M1", color: "#7c3aed" },
      { key: "M2", label: "M2", color: "#0891b2" },
      { key: "Q", label: "Q", color: "#f97316" },
      { key: "Qtv", label: "Qtv", color: "#f97316" },
      { key: "runtime_hours", label: "наработка", color: "#0f766e" },
      { key: "weather_temp", label: "погода", color: "#64748b" },
    ];
    const DEFAULT_PARAMS = new Set(["t1", "t2", "dt", "weather_temp"]);

    function renderDetail() {
      const report = meters.find(item => item.id === selectedId) || meters[0];
      const detail = document.getElementById("detail");
      if (!report) {
        detail.innerHTML = '<div class="empty">Нет разобранных теплосчетчиков</div>';
        return;
      }

      const checks = report.checks || {};
      const klass = statusClass(checks.status);
      const defaultRange = defaultDateRange(report);
      document.getElementById("detailPeriod").textContent =
        `${fmtDate(report.period_start)} — ${fmtDate(report.period_end)}`;

      detail.innerHTML = `
        <div class="detail-title">${escapeHtml(report.object || "Объект не указан")}</div>
        <div class="detail-subtitle">
          ${escapeHtml(report.resource || "—")} · ${escapeHtml(report.point || "—")} ·
          ${escapeHtml(report.scheme || "—")}<br>
          ${escapeHtml(report.meter_model || "—")} № ${escapeHtml(report.serial_number || "—")} · отчетов: ${fmt(report.reports_count, 0)} · график: ${escapeHtml(report.graph_profile || "—")} · погода: ${escapeHtml(report.weather_source || "—")}
        </div>

        <div class="stat-grid">
          ${stat("Статус", `<span class="badge ${klass}">${statusLabels[checks.status] || checks.status}</span>`)}
          ${stat("История", `${fmt(checks.total_rows, 0)} ч`)}
          ${stat("Пропуски", fmt(checks.missing_rows, 0))}
          ${stat("M1/M2 max", `${fmt(checks.max_imbalance_pct, 1)}%`)}
          ${stat("Крит. за 7 дней", fmt((checks.critical_errors_7_days || []).length, 0))}
          ${stat("Наработка сутки", `${fmt(checks.runtime_total, 1)} ч`)}
        </div>

        <div class="issues">
          ${(checks.issues || []).length ? checks.issues.map(issue => `
            <div class="issue ${escapeHtml(issue.severity || "warning")}">
              <div class="issue-title">${escapeHtml(issue.title || "")}</div>
              <div>${escapeHtml(issue.detail || "")}</div>
            </div>
          `).join("") : '<div class="issue"><div class="issue-title">Критических ошибок нет</div><div>За последние 5 дней критических событий нет. Остальные события доступны ниже.</div></div>'}
        </div>

        <div class="chart-box">
          <div class="chart-controls">
            <label><span class="field-label">С даты</span><input id="chartStart" type="date" value="${defaultRange.start}"></label>
            <label><span class="field-label">По дату</span><input id="chartEnd" type="date" value="${defaultRange.end}"></label>
            <div>
              <span class="field-label">Параметры графика</span>
              <div class="param-grid">
                ${PARAMS.filter(param => hasParam(report, param.key)).map(param => `
                  <label><input class="paramToggle" type="checkbox" value="${param.key}" ${DEFAULT_PARAMS.has(param.key) ? "checked" : ""}>${param.label}</label>
                `).join("")}
              </div>
            </div>
          </div>
          <canvas id="trendChart" width="900" height="360"></canvas>
          <div class="legend" id="chartLegend"></div>
        </div>

        <div class="chart-box">
          <div class="chart-controls" style="grid-template-columns: 220px 180px 1fr;">
            <label>
              <span class="field-label">Ошибки</span>
              <select id="errorScope">
                <option value="recent">Последние 5 дней</option>
                <option value="all">Все дни</option>
              </select>
            </label>
            <button class="secondary-button" id="extraErrorsBtn" type="button">Показать остальные</button>
            <div class="mini">Красный статус: критические ошибки за последние 7 дней. Пропуски справочные.</div>
          </div>
          <div id="errorsByDay"></div>
        </div>

        <div class="chart-box">
          <div class="field-label">Часовые данные за выбранный период</div>
          <div id="hourlyData"></div>
        </div>

        <div class="chart-box">
          ${renderReportsTable(report)}
        </div>
      `;

      setupDetailControls(report);
      drawTrendChart(report);
      renderErrorsByDay(report);
      renderHourlyData(report);
    }

    function stat(label, value) {
      return `<div class="stat"><div class="stat-label">${label}</div><div class="stat-value">${value}</div></div>`;
    }

    function hasParam(report, key) {
      return (report.readings || []).some(row => Number.isFinite(Number(row[key])));
    }

    function defaultDateRange(report) {
      const rows = (report.readings || []).filter(row => row.timestamp);
      if (!rows.length) return { start: "", end: "" };
      const end = dateOnly(rows[rows.length - 1].timestamp);
      const endDate = parseDateInput(end);
      endDate.setDate(endDate.getDate() - 4);
      return { start: formatDateInput(endDate), end };
    }

    function setupDetailControls(report) {
      ["chartStart", "chartEnd"].forEach(id => {
        document.getElementById(id)?.addEventListener("input", () => {
          drawTrendChart(report);
          renderHourlyData(report);
        });
      });
      document.querySelectorAll(".paramToggle").forEach(input => {
        input.addEventListener("input", () => drawTrendChart(report));
      });
      document.getElementById("errorScope")?.addEventListener("input", () => renderErrorsByDay(report));
      document.getElementById("extraErrorsBtn")?.addEventListener("click", () => {
        showExtraErrors = !showExtraErrors;
        renderErrorsByDay(report);
      });
    }

    function selectedRows(report, includeMissing = true) {
      const start = document.getElementById("chartStart")?.value;
      const end = document.getElementById("chartEnd")?.value;
      return (report.readings || [])
        .filter(row => includeMissing || !row.is_missing)
        .filter(row => !start || dateOnly(row.timestamp) >= start)
        .filter(row => !end || dateOnly(row.timestamp) <= end);
    }

    function renderHourlyData(report) {
      const container = document.getElementById("hourlyData");
      if (!container) return;
      const rows = selectedRows(report, true);
      const columns = [
        "timestamp", "weather_temp", "outside_temp_used", "t1", "t2", "dt",
        "M1", "M2", "M3", "V1", "V2", "Q", "Qtv", "runtime_hours",
        "is_missing", "source_file"
      ].filter(col => col === "timestamp" || rows.some(row => row[col] !== undefined && row[col] !== null));
      if (!rows.length) {
        container.innerHTML = '<div class="empty">За выбранный период часовых данных нет</div>';
        return;
      }
      container.innerHTML = `
        <div class="table-wrap">
        <table class="hourly-table">
          <thead><tr>${columns.map(col => `<th>${columnLabel(col)}</th>`).join("")}</tr></thead>
          <tbody>
            ${rows.map(row => `<tr>${columns.map(col => {
              return `<td>${formatCell(row, col)}</td>`;
            }).join("")}</tr>`).join("")}
          </tbody>
        </table>
        </div>
      `;
    }

    function columnLabel(col) {
      return {
        timestamp: "Время",
        weather_temp: "Погода",
        outside_temp_used: "Наруж.",
        runtime_hours: "Нараб.",
        is_missing: "Пропуск",
        source_file: "Файл"
      }[col] || col;
    }

    function formatCell(row, col) {
      if (col === "timestamp") return fmtDate(row[col]);
      if (col === "is_missing") return row[col] ? "да" : "";
      if (col === "source_file") return escapeHtml(row[col] || "");
      return fmt(row[col], 3);
    }

    function renderErrorsByDay(report) {
      const container = document.getElementById("errorsByDay");
      if (!container) return;
      const scope = document.getElementById("errorScope")?.value || "recent";
      const toggle = document.getElementById("extraErrorsBtn");
      if (toggle) {
        toggle.textContent = showExtraErrors ? "Скрыть остальные" : "Показать остальные";
        toggle.classList.toggle("active", showExtraErrors);
      }
      const checks = report.checks || {};
      const sourceErrors = scope === "all" ? (checks.errors || []) : (checks.recent_errors || []);
      const errors = showExtraErrors
        ? sourceErrors
        : sourceErrors.filter(error => error.severity === "critical");
      if (!errors.length) {
        container.innerHTML = showExtraErrors
          ? '<div class="empty">Ошибок за выбранный период нет</div>'
          : '<div class="empty">Критических ошибок за выбранный период нет</div>';
        return;
      }
      const byDay = {};
      errors.forEach(error => {
        (byDay[error.date] ||= []).push(error);
      });
      container.innerHTML = Object.keys(byDay).sort().reverse().map(day => {
        const items = byDay[day].sort((a, b) => (a.priority - b.priority) || String(a.timestamp).localeCompare(String(b.timestamp)));
        const crit = items.filter(item => item.severity === "critical").length;
        const warn = items.filter(item => item.severity === "warning").length;
        const info = items.filter(item => item.severity === "info").length;
        return `
          <div class="table-wrap" style="margin-bottom:14px;">
          <table class="errors-table">
            <thead>
              <tr><th>${day} · C:${crit} W:${warn} I:${info}</th><th>Приоритет</th><th>Тип</th><th>Деталь</th></tr>
            </thead>
            <tbody>
              ${items.map(error => `
                <tr>
                  <td>${fmtDate(error.timestamp)}</td>
                  <td>${fmt(error.priority, 0)}</td>
                  <td><span class="badge ${statusClass(error.severity === "info" ? "Unknown" : error.severity)}">${escapeHtml(error.title)}</span></td>
                  <td>${escapeHtml(error.detail)}</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
          </div>
        `;
      }).join("");
    }

    function renderReportsTable(report) {
      const rows = (report.reports || []).slice().reverse();
      if (!rows.length) return "";
      return `
        <div class="table-wrap">
        <table>
          <thead><tr><th>Последние отчеты</th><th>Период</th><th>Строк</th><th>Пропусков</th></tr></thead>
          <tbody>
            ${rows.map(row => `
              <tr>
                <td>${escapeHtml(row.source_file || "")}</td>
                <td>${fmtDate(row.period_start)} — ${fmtDate(row.period_end)}</td>
                <td>${fmt(row.rows, 0)}</td>
                <td>${fmt(row.missing_rows, 0)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
        </div>
      `;
    }

    function drawTrendChart(report) {
      const canvas = document.getElementById("trendChart");
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      const rect = canvas.getBoundingClientRect();
      const scale = window.devicePixelRatio || 1;
      canvas.width = Math.max(640, rect.width * scale);
      canvas.height = 360 * scale;
      ctx.scale(scale, scale);

      const width = canvas.width / scale;
      const height = canvas.height / scale;
      const pad = { left: 44, right: 16, top: 18, bottom: 34 };
      const start = document.getElementById("chartStart")?.value;
      const end = document.getElementById("chartEnd")?.value;
      const active = new Set(Array.from(document.querySelectorAll(".paramToggle:checked")).map(input => input.value));
      const rows = (report.readings || [])
        .filter(row => !row.is_missing)
        .filter(row => !start || dateOnly(row.timestamp) >= start)
        .filter(row => !end || dateOnly(row.timestamp) <= end);
      const series = PARAMS
        .filter(item => active.has(item.key))
        .filter(item => rows.some(row => Number.isFinite(Number(row[item.key]))));
      document.getElementById("chartLegend").innerHTML = series.map(item => `
        <span class="legend-item"><span class="dot" style="background:${item.color}"></span>${item.label}</span>
      `).join("");

      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, width, height);

      if (!rows.length || !series.length) {
        ctx.fillStyle = "#667085";
        ctx.font = "14px sans-serif";
        ctx.fillText("Нет данных для выбранного графика", pad.left, pad.top + 28);
        return;
      }

      const allValues = [];
      series.forEach(item => rows.forEach(row => {
        const value = Number(row[item.key]);
        if (Number.isFinite(value)) allValues.push(value);
      }));

      let min = Math.min(...allValues);
      let max = Math.max(...allValues);
      if (min === max) { min -= 1; max += 1; }
      const span = max - min;
      min -= span * 0.08;
      max += span * 0.08;

      const x = index => pad.left + index * ((width - pad.left - pad.right) / Math.max(1, rows.length - 1));
      const y = value => pad.top + (max - value) * ((height - pad.top - pad.bottom) / (max - min));

      ctx.strokeStyle = "#d9dee8";
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (let i = 0; i < 5; i++) {
        const yy = pad.top + i * ((height - pad.top - pad.bottom) / 4);
        ctx.moveTo(pad.left, yy);
        ctx.lineTo(width - pad.right, yy);
      }
      ctx.stroke();

      ctx.fillStyle = "#667085";
      ctx.font = "11px sans-serif";
      ctx.textAlign = "right";
      for (let i = 0; i < 5; i++) {
        const value = max - i * ((max - min) / 4);
        const yy = pad.top + i * ((height - pad.top - pad.bottom) / 4);
        ctx.fillText(fmt(value, 1), pad.left - 8, yy + 4);
      }

      ctx.textAlign = "center";
      const first = rows[0]?.timestamp;
      const last = rows[rows.length - 1]?.timestamp;
      ctx.fillText(fmtDate(first), pad.left, height - 10);
      ctx.fillText(fmtDate(last), width - pad.right - 20, height - 10);

      series.forEach(item => {
        ctx.strokeStyle = item.color;
        ctx.lineWidth = 2.4;
        ctx.beginPath();
        let started = false;
        rows.forEach((row, index) => {
          const value = Number(row[item.key]);
          if (!Number.isFinite(value)) return;
          if (!started) {
            ctx.moveTo(x(index), y(value));
            started = true;
          } else {
            ctx.lineTo(x(index), y(value));
          }
        });
        ctx.stroke();
      });
    }

    function dateOnly(value) {
      return String(value || "").slice(0, 10);
    }

    function parseDateInput(value) {
      const [year, month, day] = value.split("-").map(Number);
      return new Date(year, month - 1, day);
    }

    function formatDateInput(value) {
      const year = value.getFullYear();
      const month = String(value.getMonth() + 1).padStart(2, "0");
      const day = String(value.getDate()).padStart(2, "0");
      return `${year}-${month}-${day}`;
    }

    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    function init() {
      document.getElementById("generatedAt").textContent =
        `Собрано: ${new Date(DATA.generated_at).toLocaleString("ru-RU")}`;
      renderKpis();
      renderResourceFilter();
      ["searchInput", "resourceFilter", "statusFilter"].forEach(id => {
        document.getElementById(id).addEventListener("input", () => {
          renderList();
          renderDetail();
        });
      });
      renderList();
      renderDetail();
      window.addEventListener("resize", () => {
        const report = meters.find(item => item.id === selectedId) || meters[0];
        if (report) drawTrendChart(report);
      });
    }

    init();
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
