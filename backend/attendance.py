"""Clock-in/clock-out transactions, attendance records, corrections and leave."""
import json
import re
import sqlite3
from datetime import date, timedelta

from flask import Blueprint, g, request

from . import schedule, timeutil
from .api import (ApiError, check_date, get_date, get_enum, get_int, get_str, json_body,
                  like_escape, ok, page_args, paginate_list)
from .auth import require
from .database import audit, get_db, transaction
from .settings import business_tz, get_settings

bp = Blueprint("attendance", __name__)

STATUS_TYPES = ["Present", "Late", "Absent", "Half Day", "Early Departure", "Overtime",
                "Incomplete", "Leave", "Holiday", "Weekly Off"]
NO_CLOCK_STATUSES = {"Absent", "Leave", "Holiday", "Weekly Off"}
# Worked out from the schedule rather than stored, unless an admin records them.
DERIVED_STATUSES = {"Absent", "Leave", "Holiday", "Weekly Off", "Not Scheduled", "Pending"}
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9\-]{8,64}$")
EMPLOYEE_ID_RE = re.compile(r"^[A-Za-z]{1,5}\d{3,4}$")
MAX_ROSTER_DAYS = 366


def _employee_by_code(conn, code):
    return conn.execute("SELECT * FROM employees WHERE employee_id = ? COLLATE NOCASE",
                        (code,)).fetchone()


# --- Metrics ---------------------------------------------------------------

def compute_metrics(rec, settings):
    """Durations and automatic status from the record's times and shift snapshot."""
    clock_in = timeutil.parse_iso(rec["clock_in_utc"])
    clock_out = timeutil.parse_iso(rec["clock_out_utc"])
    start = timeutil.parse_iso(rec["scheduled_start_utc"])
    end = timeutil.parse_iso(rec["scheduled_end_utc"])
    flexible = bool(rec["is_flexible"])
    m = dict(working_seconds=0, break_seconds=0, late_seconds=0, early_arrival_seconds=0,
             early_departure_seconds=0, overtime_seconds=0)

    if clock_in and start and not flexible:
        delta = int((clock_in - start).total_seconds())
        if delta > rec["grace_minutes"] * 60:
            m["late_seconds"] = delta
        elif delta < 0:
            m["early_arrival_seconds"] = -delta

    half_day = False
    if clock_in and clock_out:
        gross = int((clock_out - clock_in).total_seconds())
        scheduled_gross = int((end - start).total_seconds()) if start else 0
        break_allowance = rec["scheduled_break_minutes"] * 60 if start else 0
        # A short visit didn't include the shift's break, so don't deduct it.
        m["break_seconds"] = break_allowance if gross * 2 >= scheduled_gross else 0
        m["break_seconds"] = min(m["break_seconds"], gross)
        m["working_seconds"] = gross - m["break_seconds"]

        threshold = settings["overtime_threshold_minutes"] * 60
        if start:
            scheduled_net = max(scheduled_gross - break_allowance, 0)
            if flexible:
                excess = m["working_seconds"] - scheduled_net
            else:
                excess = int((clock_out - end).total_seconds())
                if excess < 0:
                    m["early_departure_seconds"] = -excess
            if excess > 0 and excess >= threshold:
                m["overtime_seconds"] = excess
            # A 24-hour shift is a window to clock in, not hours expected to be worked.
            if scheduled_gross < 23 * 3600:
                half_day = m["working_seconds"] < scheduled_net * settings["half_day_percent"] / 100

    if not clock_in:
        status = rec["status"]
    elif not clock_out:
        status = ("Late" if m["late_seconds"] else "Present") if rec["is_open"] else "Incomplete"
    elif half_day:
        status = "Half Day"
    elif m["late_seconds"]:
        status = "Late"
    elif m["early_departure_seconds"]:
        status = "Early Departure"
    elif m["overtime_seconds"]:
        status = "Overtime"
    else:
        status = "Present"
    m["status"] = status
    return m


def _snapshot(occurrence):
    """Copy shift rules onto the record so later shift edits don't rewrite history."""
    if occurrence is None:
        return dict(shift_id=None, scheduled_start_utc=None, scheduled_end_utc=None, grace_minutes=0,
                    scheduled_break_minutes=0, late_clock_out_minutes=0, is_flexible=0)
    shift = occurrence.shift
    return dict(
        shift_id=shift["id"],
        scheduled_start_utc=timeutil.to_iso(occurrence.start),
        scheduled_end_utc=timeutil.to_iso(occurrence.end),
        grace_minutes=shift["grace_minutes"],
        scheduled_break_minutes=shift["break_minutes"],
        late_clock_out_minutes=shift["late_clock_out_minutes"],
        is_flexible=shift["is_flexible"],
    )


def close_stale_sessions(conn, settings, now, employee_pk=None):
    """Sessions never clocked out become Incomplete once the clock-out window passes."""
    sql = "SELECT * FROM attendance WHERE is_open = 1"
    params = ()
    if employee_pk is not None:
        sql += " AND employee_pk = ?"
        params = (employee_pk,)
    stale = []
    for rec in conn.execute(sql, params).fetchall():
        end = timeutil.parse_iso(rec["scheduled_end_utc"])
        if end:
            deadline = end + timedelta(minutes=rec["late_clock_out_minutes"])
        else:
            deadline = timeutil.parse_iso(rec["clock_in_utc"]) + timedelta(
                hours=settings["max_session_hours"])
        if now > deadline:
            stale.append(rec)
    if not stale:
        return
    stamp = timeutil.to_iso(now)
    with transaction(conn):
        for rec in stale:
            status = rec["status"] if rec["status_locked"] else "Incomplete"
            cursor = conn.execute(
                "UPDATE attendance SET is_open = 0, status = ?, updated_at = ? "
                "WHERE id = ? AND is_open = 1", (status, stamp, rec["id"]))
            if cursor.rowcount:
                conn.execute(
                    "INSERT INTO attendance_adjustments(attendance_id, admin_username, adjusted_at, "
                    "field_name, original_value, updated_value, reason) "
                    "VALUES (?, 'system', ?, 'status', ?, ?, ?)",
                    (rec["id"], stamp, rec["status"], status,
                     "No clock-out before the clock-out window closed."),
                )


# --- Terminal --------------------------------------------------------------

def status_payload(employee, state, settings):
    tz = business_tz(settings)
    record = state["record"]
    return {
        "employee_id": employee["employee_id"],
        "employee_name": employee["name"],
        "status": state["status"],
        "status_label": state["status_label"],
        "indicator": state["indicator"],
        "action": state["action"],
        "message": state["message"],
        "shift": schedule.shift_summary(state["shift"], settings),
        "clock_in": record["clock_in_utc"] if record else None,
        "clock_out": record["clock_out_utc"] if record else None,
        "timezone": str(tz.key),
    }


@bp.get("/api/public/employees/search")
def search_employees():
    """Active employees for the terminal dropdown; an empty search lists everyone."""
    conn = get_db()
    settings = get_settings(conn)
    query = request.args.get("q", "").strip()[:80]
    # Starts-with on the ID is always useful; the setting governs name matching.
    prefix = like_escape(query) + "%"
    name_pattern = prefix if settings["search_mode"] == "starts_with" else "%" + like_escape(query) + "%"
    rows = conn.execute(
        "SELECT employee_id, name FROM employees WHERE status = 'active' AND "
        "(employee_id LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\') "
        "ORDER BY name COLLATE NOCASE",
        (prefix, name_pattern),
    ).fetchall()
    return ok(items=[{"employee_id": r["employee_id"], "name": r["name"]} for r in rows])


@bp.get("/api/public/employees/<code>/status")
def employee_status(code):
    conn = get_db()
    settings = get_settings(conn)
    employee = _employee_by_code(conn, code)
    if employee is None or employee["status"] != "active":
        raise ApiError("Employee not found.", 404, "EMPLOYEE_NOT_FOUND")
    now = timeutil.now_utc()
    close_stale_sessions(conn, settings, now, employee["id"])
    state = schedule.evaluate(conn, employee, settings, now)
    return ok(**status_payload(employee, state, settings))


def perform_clock(conn, code, action, request_id, settings, now):
    """One atomic clock transaction. Returns the JSON payload for the terminal."""
    tz = business_tz(settings)
    tf = settings["time_format"]
    with transaction(conn):
        if request_id:
            prior = conn.execute("SELECT response FROM clock_requests WHERE request_id = ?",
                                 (request_id,)).fetchone()
            if prior:
                return {**json.loads(prior["response"]), "replayed": True}

        employee = _employee_by_code(conn, code)
        if employee is None or employee["status"] != "active":
            raise ApiError("Employee not found.", 404, "EMPLOYEE_NOT_FOUND")

        close_stale_sessions(conn, settings, now, employee["id"])
        state = schedule.evaluate(conn, employee, settings, now)
        stamp = timeutil.to_iso(now)

        if action == "clock_in":
            if state["status"] == schedule.CLOCKED_IN:
                raise ApiError(f"{employee['name']} is already clocked in.", 409, "DUPLICATE_CLOCK_IN")
            if state["action"] != "clock_in":
                raise ApiError(state["message"] or "Clock-in is not available right now.", 409,
                               "CLOCK_IN_NOT_ALLOWED", status=state["status"])
            result = _clock_in(conn, employee, state, tz, settings, now, stamp)
        else:
            if state["status"] != schedule.CLOCKED_IN:
                already = state["status"] in (schedule.CLOCKED_OUT, schedule.SHIFT_COMPLETED)
                code_name = "DUPLICATE_CLOCK_OUT" if already else "NOT_CLOCKED_IN"
                message = (f"{employee['name']} has already clocked out." if already
                           else f"{employee['name']} is not clocked in.")
                raise ApiError(message, 409, code_name, status=state["status"])
            result = _clock_out(conn, employee, state["record"], settings, now, stamp)

        payload = {
            "employee_id": employee["employee_id"],
            "employee_name": employee["name"],
            "action": action,
            "timestamp": stamp,
            "local_time": timeutil.format_clock(now, tz, tf),
            **result,
        }
        if request_id:
            conn.execute("INSERT INTO clock_requests(request_id, response, created_at) VALUES (?, ?, ?)",
                         (request_id, json.dumps(payload), stamp))
            conn.execute("DELETE FROM clock_requests WHERE created_at < ?",
                         (timeutil.to_iso(now - timedelta(days=7)),))
    return payload


def _clock_in(conn, employee, state, tz, settings, now, stamp):
    occurrence = state["occurrence"]
    day = occurrence.date if occurrence else timeutil.local_date(now, tz).isoformat()
    rec = {**_snapshot(occurrence), "clock_in_utc": stamp, "clock_out_utc": None, "is_open": 1,
           "status": "Present"}
    metrics = compute_metrics(rec, settings)
    try:
        conn.execute(
            "INSERT INTO attendance(employee_pk, shift_id, attendance_date, clock_in_utc, is_open, "
            "scheduled_start_utc, scheduled_end_utc, grace_minutes, scheduled_break_minutes, "
            "late_clock_out_minutes, is_flexible, late_seconds, early_arrival_seconds, status, "
            "source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'terminal', ?, ?)",
            (employee["id"], rec["shift_id"], day, stamp, rec["scheduled_start_utc"],
             rec["scheduled_end_utc"], rec["grace_minutes"], rec["scheduled_break_minutes"],
             rec["late_clock_out_minutes"], rec["is_flexible"], metrics["late_seconds"],
             metrics["early_arrival_seconds"], metrics["status"], stamp, stamp),
        )
    except sqlite3.IntegrityError:
        raise ApiError(f"{employee['name']} is already clocked in.", 409, "DUPLICATE_CLOCK_IN")

    if metrics["late_seconds"]:
        note = f"Late by {timeutil.human_duration(metrics['late_seconds'])}"
    elif metrics["early_arrival_seconds"] >= 60:
        note = f"Early by {timeutil.human_duration(metrics['early_arrival_seconds'])}"
    else:
        note = "On time" if occurrence else "Unscheduled"
    return {"status": schedule.CLOCKED_IN, "attendance_status": metrics["status"], "note": note,
            "message": "Clock-in successful"}


def _clock_out(conn, employee, rec, settings, now, stamp):
    window = settings["duplicate_window_seconds"]
    if window and (now - timeutil.parse_iso(rec["clock_in_utc"])).total_seconds() < window:
        raise ApiError(f"{employee['name']} clocked in moments ago. Please wait a minute before "
                       "clocking out.", 409, "DUPLICATE_WINDOW")
    updated = {**dict(rec), "clock_out_utc": stamp, "is_open": 0}
    metrics = compute_metrics(updated, settings)
    status = rec["status"] if rec["status_locked"] else metrics["status"]
    cursor = conn.execute(
        "UPDATE attendance SET clock_out_utc = ?, is_open = 0, working_seconds = ?, break_seconds = ?, "
        "late_seconds = ?, early_arrival_seconds = ?, early_departure_seconds = ?, overtime_seconds = ?, "
        "status = ?, updated_at = ? WHERE id = ? AND is_open = 1",
        (stamp, metrics["working_seconds"], metrics["break_seconds"], metrics["late_seconds"],
         metrics["early_arrival_seconds"], metrics["early_departure_seconds"],
         metrics["overtime_seconds"], status, stamp, rec["id"]),
    )
    if cursor.rowcount != 1:
        raise ApiError(f"{employee['name']} has already clocked out.", 409, "DUPLICATE_CLOCK_OUT")

    notes = [f"Worked {timeutil.human_duration(metrics['working_seconds'])}"]
    if metrics["early_departure_seconds"]:
        notes.append(f"left {timeutil.human_duration(metrics['early_departure_seconds'])} early")
    if metrics["overtime_seconds"]:
        notes.append(f"overtime {timeutil.human_duration(metrics['overtime_seconds'])}")
    return {"status": schedule.CLOCKED_OUT, "attendance_status": status, "note": ", ".join(notes),
            "message": "Clock-out successful"}


@bp.post("/api/attendance/clock")
def clock():
    data = json_body()
    code = get_str(data, "employee_id", "Employee", required=True, max_len=12)
    if not EMPLOYEE_ID_RE.match(code):
        raise ApiError("Please select a valid employee.", code="VALIDATION_ERROR")
    action = get_enum(data, "action", ("clock_in", "clock_out"), "Action")
    request_id = data.get("request_id")
    if request_id is not None and (not isinstance(request_id, str)
                                   or not REQUEST_ID_RE.match(request_id)):
        raise ApiError("Invalid request id.", code="VALIDATION_ERROR")
    conn = get_db()
    settings = get_settings(conn)
    return ok(**perform_clock(conn, code, action, request_id, settings, timeutil.now_utc()))


# --- Records ---------------------------------------------------------------

RECORD_SELECT = """
SELECT a.*, e.employee_id AS employee_code, e.name AS employee_name, e.department, e.designation,
       e.employee_type,
       s.name AS shift_name, s.code AS shift_code
FROM attendance a
JOIN employees e ON e.id = a.employee_pk
LEFT JOIN shifts s ON s.id = a.shift_id
"""

SORTABLE = {
    "id": "a.id", "employee_id": "e.employee_id", "employee_name": "e.name COLLATE NOCASE",
    "attendance_date": "a.attendance_date", "clock_in": "a.clock_in_utc", "clock_out": "a.clock_out_utc",
    "working_seconds": "a.working_seconds", "late_seconds": "a.late_seconds",
    "early_departure_seconds": "a.early_departure_seconds",
    "overtime_seconds": "a.overtime_seconds", "status": "a.status", "shift_name": "s.name",
    "department": "e.department",
}


def record_row(r):
    return {
        "id": r["id"],
        "employee_id": r["employee_code"],
        "employee_name": r["employee_name"],
        "department": r["department"],
        "designation": r["designation"],
        "employee_type": r["employee_type"],
        "shift_id": r["shift_id"],
        "shift_name": r["shift_name"] or "",
        "attendance_date": r["attendance_date"],
        "clock_in": r["clock_in_utc"],
        "clock_out": r["clock_out_utc"],
        "is_open": bool(r["is_open"]),
        "scheduled_start": r["scheduled_start_utc"],
        "scheduled_end": r["scheduled_end_utc"],
        "working_seconds": r["working_seconds"],
        "break_seconds": r["break_seconds"],
        "late_seconds": r["late_seconds"],
        "early_arrival_seconds": r["early_arrival_seconds"],
        "early_departure_seconds": r["early_departure_seconds"],
        "overtime_seconds": r["overtime_seconds"],
        "status": r["status"],
        "status_locked": bool(r["status_locked"]),
        "source": r["source"],
        "is_adjusted": bool(r["is_adjusted"]),
        "notes": r["notes"],
        "created_at": r["created_at"],
    }


def record_filters(args):
    """WHERE clause from filter values. Every value is a bound parameter."""
    where, params = [], []
    if args.get("from"):
        where.append("a.attendance_date >= ?")
        params.append(check_date(args["from"], "From date"))
    if args.get("to"):
        where.append("a.attendance_date <= ?")
        params.append(check_date(args["to"], "To date"))
    if args.get("employee"):
        where.append("e.employee_id = ? COLLATE NOCASE")
        params.append(args["employee"])
    if args.get("employees"):
        where.append(f"e.employee_id IN ({', '.join('?' * len(args['employees']))})")
        params += args["employees"]
    if args.get("q"):
        pattern = "%" + like_escape(args["q"]) + "%"
        where.append("(e.employee_id LIKE ? ESCAPE '\\' OR e.name LIKE ? ESCAPE '\\')")
        params += [pattern, pattern]
    if args.get("department"):
        where.append("e.department = ?")
        params.append(args["department"])
    if args.get("employee_type"):
        where.append("e.employee_type = ?")
        params.append(args["employee_type"])
    if args.get("shift"):
        where.append("a.shift_id = ?")
        params.append(args["shift"])
    if args.get("status"):
        where.append("a.status = ?")
        params.append(args["status"])
    return (" WHERE " + " AND ".join(where) if where else ""), params


def fetch_records(conn, filters):
    where, params = record_filters(filters)
    rows = conn.execute(RECORD_SELECT + where + " ORDER BY a.attendance_date, e.name COLLATE NOCASE, a.id",
                        params).fetchall()
    return [record_row(r) for r in rows]


def build_roster(conn, start, end, settings, now, filters=None):
    """One row per employee per date: real records, or what the schedule implies."""
    filters = filters or {}
    tz = business_tz(settings)
    index = schedule.ScheduleIndex(conn, start, end)
    by_key = {}
    record_args = {k: filters.get(k) for k in ("employee", "employees", "department", "employee_type", "q")}
    for rec in fetch_records(conn, {**record_args, "from": start, "to": end}):
        by_key.setdefault((rec["employee_id"], rec["attendance_date"]), []).append(rec)

    where, params = ["1 = 1"], []
    if filters.get("employee"):
        where.append("employee_id = ? COLLATE NOCASE")
        params.append(filters["employee"])
    if filters.get("employees"):
        where.append(f"employee_id IN ({', '.join('?' * len(filters['employees']))})")
        params += filters["employees"]
    if filters.get("department"):
        where.append("department = ?")
        params.append(filters["department"])
    if filters.get("employee_type"):
        where.append("employee_type = ?")
        params.append(filters["employee_type"])
    if filters.get("q"):
        pattern = "%" + like_escape(filters["q"]) + "%"
        where.append("(employee_id LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\')")
        params += [pattern, pattern]
    employees = conn.execute(
        "SELECT * FROM employees WHERE " + " AND ".join(where) + " ORDER BY name COLLATE NOCASE",
        params).fetchall()

    shift_filter = int(filters["shift"]) if filters.get("shift") else None
    rows = []
    for day in timeutil.date_range(start, end):
        for emp in employees:
            records = by_key.get((emp["employee_id"], day))
            if records:
                rows += [r for r in records if shift_filter in (None, r["shift_id"])]
                continue
            if day < emp["date_of_joining"]:
                continue
            if emp["status"] == "inactive" and (not emp["deactivated_on"] or day >= emp["deactivated_on"]):
                continue
            plan = index.plan(emp["id"], day)
            if plan.kind == schedule.SCHEDULED:
                occ = schedule.occurrence_for(plan.shift, day, tz)
                status = "Absent" if now >= occ.end else "Pending"
            else:
                status = {schedule.HOLIDAY: "Holiday", schedule.WEEKLY_OFF: "Weekly Off",
                          schedule.LEAVE: "Leave", schedule.NO_SHIFT: "Not Scheduled"}[plan.kind]
            shift = plan.shift
            if shift_filter is not None and (not shift or shift["id"] != shift_filter):
                continue
            rows.append({
                "id": None,
                "employee_id": emp["employee_id"],
                "employee_name": emp["name"],
                "department": emp["department"],
                "designation": emp["designation"],
                "employee_type": emp["employee_type"],
                "shift_id": shift["id"] if shift else None,
                "shift_name": shift["name"] if shift else "",
                "attendance_date": day,
                "clock_in": None, "clock_out": None, "is_open": False,
                "working_seconds": 0, "break_seconds": 0, "late_seconds": 0,
                "early_arrival_seconds": 0, "early_departure_seconds": 0, "overtime_seconds": 0,
                "status": status, "status_locked": False, "source": "schedule",
                "is_adjusted": False, "notes": plan.label,
            })
    return rows


def check_range(start, end):
    if start > end:
        raise ApiError("The start date must not be after the end date.", code="VALIDATION_ERROR")
    if (date.fromisoformat(end) - date.fromisoformat(start)).days >= MAX_ROSTER_DAYS:
        raise ApiError("Please choose a date range of one year or less.", code="VALIDATION_ERROR")


@bp.get("/api/admin/attendance")
@require("attendance.view")
def list_records():
    conn = get_db()
    settings = get_settings(conn)
    now = timeutil.now_utc()
    close_stale_sessions(conn, settings, now)
    page, per_page = page_args()
    args = request.args
    filters = {k: args.get(k, "").strip() for k in
               ("from", "to", "employee", "q", "department", "shift", "status")}
    sort = args.get("sort", "attendance_date")
    direction = "ASC" if args.get("dir") == "asc" else "DESC"

    if filters["status"] in DERIVED_STATUSES:
        today = timeutil.local_date(now, business_tz(settings))
        end = check_date(filters["to"], "To date") if filters["to"] else today.isoformat()
        start = check_date(filters["from"], "From date") if filters["from"] else (
            date.fromisoformat(end) - timedelta(days=30)).isoformat()
        check_range(start, end)
        if filters["shift"] and not filters["shift"].isdigit():
            raise ApiError("Invalid shift filter.", code="VALIDATION_ERROR")
        rows = [r for r in build_roster(conn, start, end, settings, now, filters)
                if r["status"] == filters["status"]]
        if rows:
            key = sort if sort in rows[0] else "attendance_date"
            rows.sort(key=lambda r: (r[key] is None, r[key]), reverse=direction == "DESC")
        return ok(items=paginate_list(rows, page, per_page), total=len(rows), page=page,
                  per_page=per_page)

    where, params = record_filters(filters)
    total = conn.execute(
        "SELECT COUNT(*) FROM attendance a JOIN employees e ON e.id = a.employee_pk" + where, params,
    ).fetchone()[0]
    order = SORTABLE.get(sort, "a.attendance_date")
    rows = conn.execute(
        f"{RECORD_SELECT}{where} ORDER BY {order} {direction}, a.id DESC LIMIT ? OFFSET ?",
        params + [per_page, (page - 1) * per_page],
    ).fetchall()
    return ok(items=[record_row(r) for r in rows], total=total, page=page, per_page=per_page)


def _adjustments(conn, record_id):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM attendance_adjustments WHERE attendance_id = ? ORDER BY id DESC", (record_id,))]


@bp.get("/api/admin/attendance/<int:record_id>")
@require("attendance.view")
def get_record(record_id):
    conn = get_db()
    row = conn.execute(RECORD_SELECT + " WHERE a.id = ?", (record_id,)).fetchone()
    if row is None:
        raise ApiError("Attendance record not found.", 404, "NOT_FOUND")
    return ok(record=record_row(row), adjustments=_adjustments(conn, record_id))


def _parse_local(data, key, label, tz, now):
    value = data.get(key)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ApiError(f"{label} is not a valid date and time.", code="VALIDATION_ERROR", field=key)
    try:
        parsed = timeutil.parse_local_datetime(value, tz)
    except ValueError:
        raise ApiError(f"{label} is not a valid date and time.", code="VALIDATION_ERROR", field=key)
    if parsed > now:
        raise ApiError(f"{label} cannot be in the future.", code="VALIDATION_ERROR", field=key)
    return timeutil.to_iso(parsed)


def _resolve_snapshot(conn, employee_pk, day, shift_id, tz):
    """Shift rules for a corrected or manual record."""
    if shift_id is not None:
        row = conn.execute("SELECT * FROM shifts WHERE id = ?", (shift_id,)).fetchone()
        if row is None:
            raise ApiError("That shift does not exist.", code="VALIDATION_ERROR", field="shift_id")
        shift = schedule.shift_row(row)
    else:
        shift = schedule.ScheduleIndex(conn, day, day, employee_pk).shift_on(employee_pk, day)
    return _snapshot(schedule.occurrence_for(shift, day, tz) if shift else None)


def _check_overlap(conn, employee_pk, record_id, clock_in, clock_out, is_open):
    """Sessions for one person can't overlap, or hours would be counted twice."""
    if not clock_in:
        return
    new_end = clock_out or (None if is_open else clock_in)
    for other in conn.execute(
        "SELECT id, clock_in_utc, clock_out_utc, is_open FROM attendance "
        "WHERE employee_pk = ? AND clock_in_utc IS NOT NULL AND id <> ?",
        (employee_pk, record_id or 0),
    ):
        other_end = other["clock_out_utc"] or (None if other["is_open"] else other["clock_in_utc"])
        starts_before_other_ends = other_end is None or clock_in <= other_end
        ends_after_other_starts = new_end is None or new_end >= other["clock_in_utc"]
        if starts_before_other_ends and ends_after_other_starts:
            raise ApiError(f"These times overlap attendance record #{other['id']}.", 409, "OVERLAP")


def _check_times(clock_in, clock_out):
    if clock_out and not clock_in:
        raise ApiError("A clock-out needs a clock-in time as well.", code="VALIDATION_ERROR")
    if clock_in and clock_out and clock_out <= clock_in:
        raise ApiError("Clock-out must be after clock-in.", code="VALIDATION_ERROR", field="clock_out")


def _record_adjustments(conn, record_id, changes, reason, stamp):
    for field, (old, new) in changes.items():
        conn.execute(
            "INSERT INTO attendance_adjustments(attendance_id, admin_id, admin_username, adjusted_at, "
            "field_name, original_value, updated_value, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (record_id, g.admin["id"], g.admin["username"], stamp, field,
             None if old is None else str(old), None if new is None else str(new), reason),
        )


def _requested_status(data):
    status = data.get("status", "auto") or "auto"
    if status != "auto" and status not in STATUS_TYPES:
        raise ApiError("Unknown attendance status.", code="VALIDATION_ERROR", field="status")
    return status


@bp.post("/api/admin/attendance")
@require("attendance.manage")
def create_record():
    """Add a missing attendance record by hand."""
    data = json_body()
    conn = get_db()
    settings = get_settings(conn)
    tz = business_tz(settings)
    now = timeutil.now_utc()
    reason = get_str(data, "reason", "Reason", required=True, max_len=500)
    day = get_date(data, "attendance_date", "Attendance date", required=True)
    clock_in = _parse_local(data, "clock_in", "Clock-in", tz, now)
    clock_out = _parse_local(data, "clock_out", "Clock-out", tz, now)
    status = _requested_status(data)
    _check_times(clock_in, clock_out)
    if not clock_in and status not in NO_CLOCK_STATUSES:
        raise ApiError("Enter a clock-in time, or choose Absent, Leave, Holiday or Weekly Off.",
                       code="VALIDATION_ERROR", field="clock_in")
    shift_id = get_int(data, "shift_id", "Shift", 1, 10**9) if data.get("shift_id") else None

    with transaction(conn):
        employee = _employee_by_code(conn, get_str(data, "employee_id", "Employee", required=True, max_len=12))
        if employee is None:
            raise ApiError("Employee not found.", 404, "EMPLOYEE_NOT_FOUND")
        same_day = conn.execute("SELECT id FROM attendance WHERE employee_pk = ? AND attendance_date = ?",
                                (employee["id"], day)).fetchone()
        if same_day and not (settings["allow_multiple_sessions"] and clock_in):
            raise ApiError(f"Record #{same_day['id']} already exists for that date. Edit it instead.",
                           409, "RECORD_EXISTS", record_id=same_day["id"])
        _check_overlap(conn, employee["id"], None, clock_in, clock_out, False)

        rec = {**_resolve_snapshot(conn, employee["id"], day, shift_id, tz), "clock_in_utc": clock_in,
               "clock_out_utc": clock_out, "is_open": 0, "status": status}
        metrics = compute_metrics(rec, settings)
        final_status = metrics["status"] if status == "auto" else status
        stamp = timeutil.to_iso(now)
        cursor = conn.execute(
            "INSERT INTO attendance(employee_pk, shift_id, attendance_date, clock_in_utc, clock_out_utc, "
            "is_open, scheduled_start_utc, scheduled_end_utc, grace_minutes, scheduled_break_minutes, "
            "late_clock_out_minutes, is_flexible, working_seconds, break_seconds, late_seconds, "
            "early_arrival_seconds, early_departure_seconds, overtime_seconds, status, status_locked, "
            "source, is_adjusted, notes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'admin', 1, ?, ?, ?)",
            (employee["id"], rec["shift_id"], day, clock_in, clock_out, rec["scheduled_start_utc"],
             rec["scheduled_end_utc"], rec["grace_minutes"], rec["scheduled_break_minutes"],
             rec["late_clock_out_minutes"], rec["is_flexible"], metrics["working_seconds"],
             metrics["break_seconds"], metrics["late_seconds"], metrics["early_arrival_seconds"],
             metrics["early_departure_seconds"], metrics["overtime_seconds"], final_status,
             int(status != "auto"), get_str(data, "notes", "Notes", max_len=500), stamp, stamp),
        )
        record_id = cursor.lastrowid
        _record_adjustments(conn, record_id, {
            "record": (None, "created manually"),
            "clock_in": (None, clock_in),
            "clock_out": (None, clock_out),
            "status": (None, final_status),
        }, reason, stamp)
        audit(conn, "attendance.create", "attendance", record_id,
              {"employee": employee["employee_id"], "date": day, "reason": reason})
    return ok(201, id=record_id, message="Attendance record added.")


@bp.put("/api/admin/attendance/<int:record_id>")
@require("attendance.manage")
def update_record(record_id):
    """Correct a record. The originals stay in attendance_adjustments permanently."""
    data = json_body()
    conn = get_db()
    settings = get_settings(conn)
    tz = business_tz(settings)
    now = timeutil.now_utc()
    reason = get_str(data, "reason", "Reason", required=True, max_len=500)
    status = _requested_status(data)

    with transaction(conn):
        row = conn.execute("SELECT * FROM attendance WHERE id = ?", (record_id,)).fetchone()
        if row is None:
            raise ApiError("Attendance record not found.", 404, "NOT_FOUND")
        rec = dict(row)
        clock_in = _parse_local(data, "clock_in", "Clock-in", tz, now) if "clock_in" in data else rec["clock_in_utc"]
        clock_out = _parse_local(data, "clock_out", "Clock-out", tz, now) if "clock_out" in data else rec["clock_out_utc"]
        _check_times(clock_in, clock_out)
        if not clock_in and status not in NO_CLOCK_STATUSES and not (
                status == "auto" and rec["status"] in NO_CLOCK_STATUSES):
            raise ApiError("This status needs a clock-in time.", code="VALIDATION_ERROR", field="status")

        day = get_date(data, "attendance_date", "Attendance date", default=rec["attendance_date"])
        shift_id = rec["shift_id"]
        if "shift_id" in data:
            shift_id = get_int(data, "shift_id", "Shift", 1, 10**9) if data["shift_id"] else None
        if day != rec["attendance_date"] or shift_id != rec["shift_id"]:
            if shift_id is None and "shift_id" in data:
                rec.update(_snapshot(None))
            else:
                rec.update(_resolve_snapshot(conn, rec["employee_pk"], day, shift_id, tz))

        # A manual edit never re-opens a closed session.
        is_open = 1 if row["is_open"] and clock_in and not clock_out else 0
        rec.update(attendance_date=day, clock_in_utc=clock_in, clock_out_utc=clock_out, is_open=is_open)
        _check_overlap(conn, rec["employee_pk"], record_id, clock_in, clock_out, is_open)

        metrics = compute_metrics(rec, settings)
        locked = status != "auto"
        final_status = status if locked else metrics.pop("status")
        metrics.pop("status", None)
        notes = get_str(data, "notes", "Notes", max_len=500, default=rec["notes"])

        changes = {}
        for label, old, new in (
            ("clock_in", row["clock_in_utc"], clock_in),
            ("clock_out", row["clock_out_utc"], clock_out),
            ("attendance_date", row["attendance_date"], day),
            ("shift_id", row["shift_id"], rec["shift_id"]),
            ("status", row["status"], final_status),
            ("notes", row["notes"], notes),
        ):
            if old != new:
                changes[label] = (old, new)
        if not changes and bool(row["status_locked"]) == locked:
            raise ApiError("Nothing was changed.", code="NO_CHANGES")

        stamp = timeutil.to_iso(now)
        conn.execute(
            "UPDATE attendance SET shift_id = ?, attendance_date = ?, clock_in_utc = ?, clock_out_utc = ?, "
            "is_open = ?, scheduled_start_utc = ?, scheduled_end_utc = ?, grace_minutes = ?, "
            "scheduled_break_minutes = ?, late_clock_out_minutes = ?, is_flexible = ?, working_seconds = ?, "
            "break_seconds = ?, late_seconds = ?, early_arrival_seconds = ?, early_departure_seconds = ?, "
            "overtime_seconds = ?, status = ?, status_locked = ?, is_adjusted = 1, notes = ?, updated_at = ? "
            "WHERE id = ?",
            (rec["shift_id"], day, clock_in, clock_out, is_open, rec["scheduled_start_utc"],
             rec["scheduled_end_utc"], rec["grace_minutes"], rec["scheduled_break_minutes"],
             rec["late_clock_out_minutes"], rec["is_flexible"], metrics["working_seconds"],
             metrics["break_seconds"], metrics["late_seconds"], metrics["early_arrival_seconds"],
             metrics["early_departure_seconds"], metrics["overtime_seconds"], final_status,
             int(locked), notes, stamp, record_id),
        )
        _record_adjustments(conn, record_id, changes, reason, stamp)
        audit(conn, "attendance.update", "attendance", record_id,
              {"changes": {k: {"from": v[0], "to": v[1]} for k, v in changes.items()}, "reason": reason})
    return ok(message="Attendance record updated.")


# --- Leave -----------------------------------------------------------------

@bp.get("/api/admin/leave")
@require("attendance.view")
def list_leave():
    rows = get_db().execute(
        "SELECT l.*, e.employee_id, e.name AS employee_name FROM leave_records l "
        "JOIN employees e ON e.id = l.employee_pk ORDER BY l.start_date DESC, l.id DESC LIMIT 500"
    ).fetchall()
    return ok(items=[dict(r) for r in rows])


@bp.post("/api/admin/leave")
@require("attendance.manage")
def create_leave():
    data = json_body()
    conn = get_db()
    start = get_date(data, "start_date", "Start date", required=True)
    end = get_date(data, "end_date", "End date", default=start)
    if end < start:
        raise ApiError("End date must not be before the start date.", code="VALIDATION_ERROR")
    leave_type = get_str(data, "leave_type", "Leave type", max_len=40, default="Annual")
    reason = get_str(data, "reason", "Reason", max_len=500)
    with transaction(conn):
        employee = _employee_by_code(conn, get_str(data, "employee_id", "Employee", required=True, max_len=12))
        if employee is None:
            raise ApiError("Employee not found.", 404, "EMPLOYEE_NOT_FOUND")
        if conn.execute(
            "SELECT 1 FROM leave_records WHERE employee_pk = ? AND status = 'approved' "
            "AND start_date <= ? AND end_date >= ?", (employee["id"], end, start),
        ).fetchone():
            raise ApiError("That employee already has leave on those dates.", 409, "LEAVE_OVERLAP")
        cursor = conn.execute(
            "INSERT INTO leave_records(employee_pk, start_date, end_date, leave_type, reason, "
            "created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (employee["id"], start, end, leave_type, reason, g.admin["username"],
             timeutil.to_iso(timeutil.now_utc())),
        )
        audit(conn, "leave.create", "leave", cursor.lastrowid,
              {"employee": employee["employee_id"], "from": start, "to": end, "type": leave_type})
    return ok(201, id=cursor.lastrowid, message="Leave recorded.")


@bp.post("/api/admin/leave/<int:leave_id>/cancel")
@require("attendance.manage")
def cancel_leave(leave_id):
    conn = get_db()
    with transaction(conn):
        cursor = conn.execute("UPDATE leave_records SET status = 'cancelled' "
                              "WHERE id = ? AND status = 'approved'", (leave_id,))
        if cursor.rowcount == 0:
            raise ApiError("Leave record not found or already cancelled.", 404, "NOT_FOUND")
        audit(conn, "leave.cancel", "leave", leave_id)
    return ok(message="Leave cancelled.")
