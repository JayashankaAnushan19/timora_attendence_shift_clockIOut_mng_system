"""Shifts, dated shift assignments and holidays."""
import json
import re
from datetime import date, timedelta

from flask import Blueprint, g, request

from . import timeutil
from .api import ApiError, get_date, get_enum, get_int, get_str, get_time, json_body, ok, page_args
from .auth import require
from .database import audit, get_db, transaction
from .settings import business_tz, get_settings

bp = Blueprint("shifts", __name__)

CODE_RE = re.compile(r"^[A-Z0-9_\-]{1,20}$")


def _check_end_day(end_day, start, end):
    """The form says which day a shift ends on; make sure the times agree with it."""
    if end_day in (None, ""):
        return
    if str(end_day) not in ("1", "2"):
        raise ApiError("End day must be Day 1 or Day 2.", code="VALIDATION_ERROR", field="end_day")
    if str(end_day) == "1" and end <= start:
        raise ApiError("A shift ending on Day 1 must end after its start time. "
                       "If it finishes the next day, choose Day 2 as the end day.",
                       code="VALIDATION_ERROR", field="end_time")
    if str(end_day) == "2" and end > start:
        raise ApiError("A shift cannot be longer than 24 hours. On Day 2, the end time must be "
                       "the same as or earlier than the start time. If it finishes the same day, "
                       "choose Day 1 as the end day.",
                       code="VALIDATION_ERROR", field="end_time")


def _shift_fields(data, settings):
    code = get_str(data, "code", "Shift code", required=True, max_len=20).upper()
    if not CODE_RE.match(code):
        raise ApiError("Shift code may contain only letters, numbers, - and _.",
                       code="VALIDATION_ERROR", field="code")
    start = get_time(data, "start_time", "Start time")
    end = get_time(data, "end_time", "End time")
    _check_end_day(data.get("end_day"), start, end)
    days = data.get("working_days", settings["default_working_days"])
    if (not isinstance(days, list) or not days
            or not all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in days)):
        raise ApiError("Choose at least one working day.", code="VALIDATION_ERROR", field="working_days")
    return {
        "name": get_str(data, "name", "Shift name", required=True, max_len=80),
        "code": code,
        "start_time": start,
        "end_time": end,
        "working_days": json.dumps(sorted(set(days))),
        "grace_minutes": get_int(data, "grace_minutes", "Grace period", 0, 240,
                                 default=settings["default_grace_minutes"]),
        "break_minutes": get_int(data, "break_minutes", "Break duration", 0, 480, default=60),
        "early_clock_in_minutes": get_int(data, "early_clock_in_minutes", "Early clock-in allowance", 0, 720,
                                          default=settings["default_early_clock_in_minutes"]),
        "late_clock_out_minutes": get_int(data, "late_clock_out_minutes", "Late clock-out allowance", 0, 720,
                                          default=settings["default_late_clock_out_minutes"]),
        "is_flexible": 1 if data.get("is_flexible") else 0,
    }


def _shift_out(row, assigned=None):
    shift = dict(row)
    shift["working_days"] = json.loads(shift["working_days"])
    shift["is_flexible"] = bool(shift["is_flexible"])
    shift["is_overnight"] = shift["end_time"] < shift["start_time"]
    shift["is_24h"] = shift["end_time"] == shift["start_time"]
    if assigned is not None:
        shift["assigned_count"] = assigned
    return shift


def _today(conn):
    return timeutil.local_date(timeutil.now_utc(), business_tz(get_settings(conn))).isoformat()


def require_active_shift(conn, shift_id):
    row = conn.execute("SELECT * FROM shifts WHERE id = ? AND deleted_at IS NULL", (shift_id,)).fetchone()
    if row is None:
        raise ApiError("That shift does not exist.", code="VALIDATION_ERROR", field="shift_id")
    if row["status"] != "active":
        raise ApiError(f"{row['name']} is inactive. Reactivate it before assigning employees.",
                       409, "SHIFT_INACTIVE")
    return row


@bp.get("/api/admin/shifts")
@require("shifts.view")
def list_shifts():
    conn = get_db()
    today = _today(conn)
    rows = conn.execute(
        "SELECT s.*, (SELECT COUNT(DISTINCT sa.employee_pk) FROM shift_assignments sa "
        "JOIN employees e ON e.id = sa.employee_pk "
        "WHERE sa.shift_id = s.id AND sa.status = 'active' AND e.status = 'active' "
        "AND sa.effective_from <= ? AND (sa.effective_to IS NULL OR sa.effective_to >= ?)) AS assigned "
        "FROM shifts s WHERE s.deleted_at IS NULL ORDER BY s.status, s.start_time",
        (today, today),
    ).fetchall()
    return ok(items=[_shift_out(r, r["assigned"]) for r in rows])


@bp.post("/api/admin/shifts")
@require("shifts.manage")
def create_shift():
    conn = get_db()
    fields = _shift_fields(json_body(), get_settings(conn))
    stamp = timeutil.to_iso(timeutil.now_utc())
    with transaction(conn):
        if conn.execute("SELECT 1 FROM shifts WHERE code = ?", (fields["code"],)).fetchone():
            raise ApiError("That shift code is already used.", 409, "CODE_TAKEN", field="code")
        columns = ", ".join(fields)
        marks = ", ".join("?" * len(fields))
        cursor = conn.execute(
            f"INSERT INTO shifts({columns}, created_at, updated_at) VALUES ({marks}, ?, ?)",
            list(fields.values()) + [stamp, stamp],
        )
        audit(conn, "shift.create", "shift", cursor.lastrowid, fields)
    return ok(201, id=cursor.lastrowid, message="Shift created.")


@bp.get("/api/admin/shifts/<int:shift_id>")
@require("shifts.view")
def shift_detail(shift_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM shifts WHERE id = ? AND deleted_at IS NULL", (shift_id,)).fetchone()
    if row is None:
        raise ApiError("Shift not found.", 404, "NOT_FOUND")
    today = _today(conn)
    employees = conn.execute(
        "SELECT e.employee_id, e.name, e.department, sa.effective_from, sa.effective_to "
        "FROM shift_assignments sa JOIN employees e ON e.id = sa.employee_pk "
        "WHERE sa.shift_id = ? AND sa.status = 'active' AND e.status = 'active' "
        "AND sa.effective_from <= ? AND (sa.effective_to IS NULL OR sa.effective_to >= ?) "
        "ORDER BY e.name COLLATE NOCASE",
        (shift_id, today, today),
    ).fetchall()
    return ok(shift=_shift_out(row), employees=[dict(e) for e in employees])


@bp.put("/api/admin/shifts/<int:shift_id>")
@require("shifts.manage")
def update_shift(shift_id):
    conn = get_db()
    fields = _shift_fields(json_body(), get_settings(conn))
    with transaction(conn):
        before = conn.execute("SELECT * FROM shifts WHERE id = ? AND deleted_at IS NULL", (shift_id,)).fetchone()
        if before is None:
            raise ApiError("Shift not found.", 404, "NOT_FOUND")
        if conn.execute("SELECT 1 FROM shifts WHERE code = ? AND id <> ?",
                        (fields["code"], shift_id)).fetchone():
            raise ApiError("That shift code is already used.", 409, "CODE_TAKEN", field="code")
        assignments = ", ".join(f"{column} = ?" for column in fields)
        conn.execute(f"UPDATE shifts SET {assignments}, updated_at = ? WHERE id = ?",
                     list(fields.values()) + [timeutil.to_iso(timeutil.now_utc()), shift_id])
        audit(conn, "shift.update", "shift", shift_id,
              {k: {"from": before[k], "to": v} for k, v in fields.items() if before[k] != v})
    return ok(message="Shift updated. Existing attendance records keep their original times.")


@bp.post("/api/admin/shifts/<int:shift_id>/status")
@require("shifts.manage")
def set_shift_status(shift_id):
    status = get_enum(json_body(), "status", ("active", "inactive"), "Status")
    conn = get_db()
    with transaction(conn):
        if conn.execute("SELECT 1 FROM shifts WHERE id = ? AND deleted_at IS NULL", (shift_id,)).fetchone() is None:
            raise ApiError("Shift not found.", 404, "NOT_FOUND")
        conn.execute("UPDATE shifts SET status = ?, deactivated_on = ?, updated_at = ? WHERE id = ?",
                     (status, _today(conn) if status == "inactive" else None,
                      timeutil.to_iso(timeutil.now_utc()), shift_id))
        audit(conn, f"shift.{status}", "shift", shift_id)
    return ok(message="Shift activated." if status == "active" else "Shift deactivated.")


@bp.delete("/api/admin/shifts/<int:shift_id>")
@require("shifts.delete")
def delete_shift(shift_id):
    """A shift with history is hidden rather than erased, so past records keep their shift."""
    conn = get_db()
    today = _today(conn)
    stamp = timeutil.to_iso(timeutil.now_utc())
    with transaction(conn):
        shift = conn.execute("SELECT * FROM shifts WHERE id = ? AND deleted_at IS NULL", (shift_id,)).fetchone()
        if shift is None:
            raise ApiError("Shift not found.", 404, "NOT_FOUND")
        if conn.execute("SELECT COUNT(*) FROM shifts WHERE deleted_at IS NULL").fetchone()[0] <= 1:
            raise ApiError("At least one shift must remain. Create another shift before deleting this one.",
                           409, "LAST_SHIFT")
        assigned = conn.execute(
            "SELECT COUNT(DISTINCT sa.employee_pk) FROM shift_assignments sa JOIN employees e ON e.id = sa.employee_pk "
            "WHERE sa.shift_id = ? AND sa.status = 'active' AND e.status = 'active' "
            "AND (sa.effective_to IS NULL OR sa.effective_to >= ?)", (shift_id, today),
        ).fetchone()[0]
        if assigned:
            raise ApiError(f"{assigned} employee(s) are still assigned to {shift['name']}. "
                           "Move them to another shift under Shift Assignments, then delete it.",
                           409, "SHIFT_IN_USE", assigned=assigned)
        has_history = (
            conn.execute("SELECT 1 FROM attendance WHERE shift_id = ? LIMIT 1", (shift_id,)).fetchone()
            or conn.execute("SELECT 1 FROM shift_assignments WHERE shift_id = ? AND status = 'active' LIMIT 1",
                            (shift_id,)).fetchone()
        )
        if has_history:
            # Frees the code for reuse. Inactive means it stops applying from today.
            conn.execute(
                "UPDATE shifts SET code = ?, status = 'inactive', deactivated_on = COALESCE(deactivated_on, ?), "
                "deleted_at = ?, updated_at = ? WHERE id = ?",
                (f"{shift['code']}~{shift_id}", today, stamp, stamp, shift_id),
            )
        else:
            conn.execute("DELETE FROM shift_assignments WHERE shift_id = ?", (shift_id,))
            conn.execute("DELETE FROM shifts WHERE id = ?", (shift_id,))
        audit(conn, "shift.delete", "shift", shift_id, {"name": shift["name"], "code": shift["code"]})
    return ok(message=f"{shift['name']} deleted. Past attendance records are unchanged.")


# --- Assignments -----------------------------------------------------------

def assign_shift(conn, employee_pk, shift_id, start, end, notes, actor):
    """Give an employee a shift for a date range, trimming whatever it overlaps.

    Earlier periods stay as they were, so history still shows the shift actually
    assigned on any past date. Runs inside the caller's transaction.
    """
    stamp = timeutil.to_iso(timeutil.now_utc())
    overlaps = conn.execute(
        "SELECT * FROM shift_assignments WHERE employee_pk = ? AND status = 'active' "
        "AND (effective_to IS NULL OR effective_to >= ?) "
        + ("AND effective_from <= ?" if end else ""),
        (employee_pk, start, end) if end else (employee_pk, start),
    ).fetchall()
    day_before = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
    day_after = (date.fromisoformat(end) + timedelta(days=1)).isoformat() if end else None

    for old in overlaps:
        runs_past_end = end is not None and (old["effective_to"] is None or old["effective_to"] > end)
        if old["effective_from"] < start:
            conn.execute("UPDATE shift_assignments SET effective_to = ? WHERE id = ?",
                         (day_before, old["id"]))
            if runs_past_end:
                conn.execute(
                    "INSERT INTO shift_assignments(employee_pk, shift_id, effective_from, effective_to, "
                    "notes, assigned_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (employee_pk, old["shift_id"], day_after, old["effective_to"],
                     old["notes"], old["assigned_by"], stamp),
                )
        elif runs_past_end:
            conn.execute("UPDATE shift_assignments SET effective_from = ? WHERE id = ?",
                         (day_after, old["id"]))
        else:
            conn.execute("UPDATE shift_assignments SET status = 'superseded' WHERE id = ?", (old["id"],))

    cursor = conn.execute(
        "INSERT INTO shift_assignments(employee_pk, shift_id, effective_from, effective_to, notes, "
        "assigned_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (employee_pk, shift_id, start, end, notes, actor, stamp),
    )
    return cursor.lastrowid


@bp.get("/api/admin/assignments")
@require("shifts.view")
def list_assignments():
    conn = get_db()
    page, per_page = page_args()
    where, params = ["1 = 1"], []
    if request.args.get("employee"):
        where.append("e.employee_id = ? COLLATE NOCASE")
        params.append(request.args["employee"])
    if request.args.get("shift"):
        where.append("sa.shift_id = ?")
        params.append(request.args["shift"])
    if request.args.get("view") == "current":
        today = _today(conn)
        where.append("sa.status = 'active' AND sa.effective_from <= ? "
                     "AND (sa.effective_to IS NULL OR sa.effective_to >= ?)")
        params += [today, today]
    clause = " AND ".join(where)
    base = ("FROM shift_assignments sa JOIN employees e ON e.id = sa.employee_pk "
            "JOIN shifts s ON s.id = sa.shift_id WHERE " + clause)
    total = conn.execute("SELECT COUNT(*) " + base, params).fetchone()[0]
    rows = conn.execute(
        "SELECT sa.*, e.employee_id, e.name AS employee_name, s.name AS shift_name " + base +
        " ORDER BY sa.effective_from DESC, sa.id DESC LIMIT ? OFFSET ?",
        params + [per_page, (page - 1) * per_page],
    ).fetchall()
    return ok(items=[dict(r) for r in rows], total=total, page=page, per_page=per_page)


@bp.post("/api/admin/assignments")
@require("shifts.manage")
def create_assignments():
    """Assign one shift to one or many employees."""
    data = json_body()
    codes = data.get("employee_ids")
    if not isinstance(codes, list) or not codes or not all(isinstance(c, str) for c in codes):
        raise ApiError("Select at least one employee.", code="VALIDATION_ERROR", field="employee_ids")
    shift_id = get_int(data, "shift_id", "Shift", 1, 10**9)
    conn = get_db()
    start = get_date(data, "effective_from", "Effective from", default=_today(conn))
    end = get_date(data, "effective_to", "Effective to")
    if end and end < start:
        raise ApiError("Effective-to must not be before effective-from.", code="VALIDATION_ERROR")
    notes = get_str(data, "notes", "Notes", max_len=300)
    with transaction(conn):
        require_active_shift(conn, shift_id)
        for code in codes:
            row = conn.execute("SELECT id FROM employees WHERE employee_id = ? COLLATE NOCASE",
                               (code,)).fetchone()
            if row is None:
                raise ApiError(f"Employee {code} was not found.", 404, "EMPLOYEE_NOT_FOUND")
            assign_shift(conn, row["id"], shift_id, start, end, notes, g.admin["username"])
        audit(conn, "assignment.create", "shift", shift_id,
              {"employees": codes, "from": start, "to": end})
    return ok(201, count=len(codes), message=f"Shift assigned to {len(codes)} employee(s).")


@bp.post("/api/admin/assignments/<int:assignment_id>/end")
@require("shifts.manage")
def end_assignment(assignment_id):
    conn = get_db()
    end = get_date(json_body(), "effective_to", "End date", required=True)
    with transaction(conn):
        row = conn.execute("SELECT * FROM shift_assignments WHERE id = ? AND status = 'active'",
                           (assignment_id,)).fetchone()
        if row is None:
            raise ApiError("Assignment not found.", 404, "NOT_FOUND")
        if end < row["effective_from"]:
            raise ApiError("End date must not be before the assignment starts.", code="VALIDATION_ERROR")
        conn.execute("UPDATE shift_assignments SET effective_to = ? WHERE id = ?", (end, assignment_id))
        audit(conn, "assignment.end", "assignment", assignment_id,
              {"from": row["effective_to"], "to": end})
    return ok(message="Assignment end date saved.")


# --- Holidays --------------------------------------------------------------

@bp.get("/api/admin/holidays")
@require("shifts.view")
def list_holidays():
    rows = get_db().execute("SELECT * FROM holidays ORDER BY date DESC LIMIT 500").fetchall()
    return ok(items=[dict(r) for r in rows])


@bp.post("/api/admin/holidays")
@require("shifts.manage")
def create_holiday():
    data = json_body()
    day = get_date(data, "date", "Date", required=True)
    name = get_str(data, "name", "Name", required=True, max_len=100)
    kind = get_enum(data, "type", ("holiday", "non_working"), "Type", default="holiday")
    conn = get_db()
    with transaction(conn):
        if conn.execute("SELECT 1 FROM holidays WHERE date = ?", (day,)).fetchone():
            raise ApiError("That date is already in the list.", 409, "HOLIDAY_EXISTS")
        cursor = conn.execute(
            "INSERT INTO holidays(date, name, type, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (day, name, kind, g.admin["username"], timeutil.to_iso(timeutil.now_utc())),
        )
        audit(conn, "holiday.create", "holiday", cursor.lastrowid, {"date": day, "name": name, "type": kind})
    return ok(201, id=cursor.lastrowid, message="Date added.")


@bp.delete("/api/admin/holidays/<int:holiday_id>")
@require("shifts.manage")
def delete_holiday(holiday_id):
    conn = get_db()
    with transaction(conn):
        row = conn.execute("SELECT * FROM holidays WHERE id = ?", (holiday_id,)).fetchone()
        if row is None:
            raise ApiError("Date not found.", 404, "NOT_FOUND")
        conn.execute("DELETE FROM holidays WHERE id = ?", (holiday_id,))
        audit(conn, "holiday.delete", "holiday", holiday_id, {"date": row["date"], "name": row["name"]})
    return ok(message="Date removed.")
