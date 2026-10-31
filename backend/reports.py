"""Attendance reports with filters, sorting, pagination and CSV/JSON export."""
import csv
import io
import json
from collections import OrderedDict
from datetime import date, timedelta

from flask import Blueprint, Response, request

from . import timeutil
from .api import ApiError, check_date, ok, page_args, paginate_list
from .attendance import build_roster, check_range, close_stale_sessions, fetch_records
from .auth import require
from .database import get_db
from .settings import business_tz, get_settings

bp = Blueprint("reports", __name__)

ID = ("employee_id", "Employee ID", "text")
NAME = ("employee_name", "Employee", "text")
DEPT = ("department", "Department", "text")
SHIFT = ("shift_name", "Shift", "text")
DATE = ("attendance_date", "Date", "date")
IN = ("clock_in", "Clock In", "datetime")
OUT = ("clock_out", "Clock Out", "datetime")
WORKED = ("working_seconds", "Worked", "duration")
LATE = ("late_seconds", "Late", "duration")
EARLY = ("early_departure_seconds", "Early Departure", "duration")
OT = ("overtime_seconds", "Overtime", "duration")
STATUS = ("status", "Status", "status")

COUNTS = [
    ("scheduled_days", "Scheduled", "int"),
    ("days_present", "Present", "int"),
    ("late_count", "Late", "int"),
    ("early_count", "Early Dep.", "int"),
    ("absent_days", "Absent", "int"),
    ("leave_days", "Leave", "int"),
    ("incomplete_count", "Incomplete", "int"),
]
TOTALS = [
    ("working_seconds", "Worked", "duration"),
    ("late_seconds", "Total Late", "duration"),
    ("overtime_seconds", "Overtime", "duration"),
]


def _empty_summary(row):
    return {
        "employee_id": row["employee_id"], "employee_name": row["employee_name"],
        "department": row["department"], "scheduled_days": 0, "days_present": 0, "late_count": 0,
        "early_count": 0, "absent_days": 0, "leave_days": 0, "incomplete_count": 0,
        "working_seconds": 0, "break_seconds": 0, "late_seconds": 0, "early_departure_seconds": 0,
        "overtime_seconds": 0,
    }


def _accumulate(total, row):
    status = row["status"]
    if status == "Absent":
        total["absent_days"] += 1
    elif status == "Leave":
        total["leave_days"] += 1
    if row["clock_in"] or status in ("Absent", "Leave"):
        total["scheduled_days"] += 1
    if row["clock_in"]:
        total["days_present"] += 1
    if status == "Incomplete":
        total["incomplete_count"] += 1
    if row["late_seconds"]:
        total["late_count"] += 1
    if row["early_departure_seconds"]:
        total["early_count"] += 1
    for key in ("working_seconds", "break_seconds", "late_seconds", "early_departure_seconds",
                "overtime_seconds"):
        total[key] += row[key]


def _summary(roster):
    totals = OrderedDict()
    for row in roster:
        _accumulate(totals.setdefault(row["employee_id"], _empty_summary(row)), row)
    return list(totals.values())


def _monthly(roster):
    totals = OrderedDict()
    for row in roster:
        month = row["attendance_date"][:7]
        key = (month, row["employee_id"])
        if key not in totals:
            totals[key] = {"month": month, **_empty_summary(row)}
        _accumulate(totals[key], row)
    return list(totals.values())


def _hours(roster):
    rows = _summary(roster)
    for row in rows:
        days = row["days_present"]
        row["average_seconds"] = row["working_seconds"] // days if days else 0
    return rows


def _daily(roster):
    return roster


def _late(roster):
    return [r for r in roster if r["late_seconds"] > 0]


def _early(roster):
    return [r for r in roster if r["early_departure_seconds"] > 0]


def _overtime(roster):
    return [r for r in roster if r["overtime_seconds"] > 0]


def _absent(roster):
    return [r for r in roster if r["status"] == "Absent"]


REPORTS = {
    "summary": ("Employee Attendance Summary", [ID, NAME, DEPT] + COUNTS + TOTALS, _summary),
    "daily": ("Daily Attendance Report", [DATE, ID, NAME, DEPT, SHIFT, IN, OUT, WORKED, LATE, EARLY, OT, STATUS], _daily),
    "monthly": ("Monthly Attendance Report", [("month", "Month", "text"), ID, NAME, DEPT] + COUNTS + TOTALS, _monthly),
    "late": ("Late Arrival Report", [DATE, ID, NAME, DEPT, SHIFT, IN, LATE, STATUS], _late),
    "early": ("Early Departure Report", [DATE, ID, NAME, DEPT, SHIFT, OUT, EARLY, STATUS], _early),
    "overtime": ("Overtime Report", [DATE, ID, NAME, DEPT, SHIFT, OUT, OT, WORKED], _overtime),
    "missing_clockout": ("Missing Clock-Out Report", [DATE, ID, NAME, DEPT, SHIFT, IN, STATUS], None),
    "absent": ("Absent Employee Report", [DATE, ID, NAME, DEPT, SHIFT], _absent),
    "hours": ("Total Working Hours", [
        ID, NAME, DEPT, ("days_present", "Days Worked", "int"), WORKED,
        ("break_seconds", "Breaks", "duration"), ("average_seconds", "Average / Day", "duration"), OT,
    ], _hours),
}


def resolve_range(args, today):
    preset = args.get("preset", "today")
    if preset == "today":
        return today, today
    if preset == "yesterday":
        day = today - timedelta(days=1)
        return day, day
    if preset == "this_week":
        return today - timedelta(days=today.weekday()), today
    if preset == "this_month":
        return today.replace(day=1), today
    if preset == "custom":
        start = check_date(args.get("from", ""), "From date")
        end = check_date(args.get("to", ""), "To date")
        return date.fromisoformat(start), date.fromisoformat(end)
    raise ApiError("Unknown date range.", code="VALIDATION_ERROR")


def _build(conn, report, args, settings, now):
    tz = business_tz(settings)
    start, end = resolve_range(args, timeutil.local_date(now, tz))
    start, end = start.isoformat(), end.isoformat()
    check_range(start, end)
    filters = {
        "employee": args.get("employee", "").strip(),
        "department": args.get("department", "").strip(),
        "shift": args.get("shift", "").strip(),
    }
    if filters["shift"] and not filters["shift"].isdigit():
        raise ApiError("Invalid shift filter.", code="VALIDATION_ERROR")
    close_stale_sessions(conn, settings, now)

    title, columns, builder = REPORTS[report]
    if report == "missing_clockout":
        rows = [r for r in fetch_records(conn, {**filters, "from": start, "to": end})
                if r["clock_in"] and not r["clock_out"] and not r["is_open"]]
    else:
        rows = builder(build_roster(conn, start, end, settings, now, filters))
    return title, columns, rows, start, end


def _sort(rows, columns, args):
    keys = {c[0] for c in columns}
    sort = args.get("sort")
    if sort in keys:
        reverse = args.get("dir") == "desc"
        rows = sorted(rows, key=lambda r: (r.get(sort) is None, str(r.get(sort) or "").lower()
                      if isinstance(r.get(sort), str) else r.get(sort) or 0), reverse=reverse)
    return rows


def _plain_value(value, kind, tz):
    if value is None:
        return ""
    if kind == "datetime":
        return timeutil.format_local_datetime(value, tz)
    if kind == "duration":
        minutes = int(value) // 60
        return f"{minutes // 60}:{minutes % 60:02d}"
    return value


def _safe_csv(value):
    # Stops spreadsheet apps treating employee-entered text as a formula.
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def rows_to_csv(columns, rows, tz):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([c[1] for c in columns])
    for row in rows:
        writer.writerow([_safe_csv(_plain_value(row.get(c[0]), c[2], tz)) for c in columns])
    return "﻿" + buffer.getvalue()


def attachment(content, filename, mimetype):
    response = Response(content, mimetype=mimetype)
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@bp.get("/api/admin/reports/<report>")
@require("reports.view")
def run_report(report):
    if report not in REPORTS:
        raise ApiError("Unknown report.", 404, "NOT_FOUND")
    conn = get_db()
    settings = get_settings(conn)
    args = request.args
    title, columns, rows, start, end = _build(conn, report, args, settings, timeutil.now_utc())
    rows = _sort(rows, columns, args)
    tz = business_tz(settings)
    export = args.get("format")
    name = f"timora-{report}-{start}-to-{end}"

    if export == "csv":
        return attachment(rows_to_csv(columns, rows, tz), f"{name}.csv", "text/csv; charset=utf-8")
    if export == "json":
        plain = [{c[1]: _plain_value(r.get(c[0]), c[2], tz) for c in columns} for r in rows]
        body = json.dumps({"report": title, "from": start, "to": end, "timezone": settings["timezone"],
                           "rows": plain}, indent=2)
        return attachment(body, f"{name}.json", "application/json")

    column_info = [{"key": c[0], "label": c[1], "kind": c[2]} for c in columns]
    keep = {c[0] for c in columns} | {"id"}
    trim = lambda items: [{k: v for k, v in r.items() if k in keep} for r in items]
    if args.get("all") == "1":  # printable view
        return ok(title=title, columns=column_info, items=trim(rows), total=len(rows), start=start, end=end)
    page, per_page = page_args()
    return ok(title=title, columns=column_info, items=trim(paginate_list(rows, page, per_page)),
              total=len(rows), page=page, per_page=per_page, start=start, end=end)


@bp.get("/api/admin/reports")
@require("reports.view")
def report_list():
    return ok(items=[{"key": key, "title": spec[0]} for key, spec in REPORTS.items()])
