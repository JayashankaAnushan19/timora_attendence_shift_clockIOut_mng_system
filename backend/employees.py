"""Employee records. IDs are sequential, permanent and never reused."""
import json

from flask import Blueprint, g, request

from . import schedule, timeutil
from .api import (EMAIL_RE, PHONE_RE, ApiError, get_date, get_enum, get_int, get_str, json_body,
                  like_escape, ok, page_args)
from .attendance import close_stale_sessions, record_row, RECORD_SELECT
from .auth import require
from .database import audit, get_db, transaction
from .settings import MAX_EMPLOYEES, business_tz, get_settings, set_setting
from .shifts import assign_shift, require_active_shift

bp = Blueprint("employees", __name__)

CURRENT_SHIFT_SQL = """
SELECT s.id, s.name FROM shift_assignments sa JOIN shifts s ON s.id = sa.shift_id
WHERE sa.employee_pk = ? AND sa.status = 'active' AND sa.effective_from <= ?
  AND (sa.effective_to IS NULL OR sa.effective_to >= ?)
ORDER BY sa.effective_from DESC, sa.id DESC LIMIT 1
"""


def _next_number(conn, settings):
    # Read fresh rather than from cached settings: inside the write transaction
    # this value is guaranteed current, even with simultaneous registrations.
    row = conn.execute("SELECT value FROM system_settings WHERE key = 'next_employee_number'").fetchone()
    return max(json.loads(row["value"]) if row else 1, settings["employee_id_start"])


def capacity(conn, settings):
    """Remaining room, limited by both the count setting and the ID number range."""
    used = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
    next_number = _next_number(conn, settings)
    remaining = min(settings["max_employees"] - used, MAX_EMPLOYEES - next_number + 1)
    remaining = max(remaining, 0)
    limit = used + remaining
    return {
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "warning": remaining == 0 or remaining <= max(limit // 10, 1),
    }


def next_employee_id(conn, settings):
    """Must be called inside a write transaction. The counter only ever moves up."""
    cap = capacity(conn, settings)
    if cap["remaining"] <= 0:
        raise ApiError("The employee limit has been reached. No more employee IDs are available.",
                       409, "EMPLOYEE_LIMIT_REACHED")
    number = _next_number(conn, settings)
    code = f"{settings['employee_id_prefix']}{number:03d}"
    actor = g.admin["username"] if g.get("admin") else "system"
    set_setting(conn, "next_employee_number", number + 1, actor)
    return code


def _read_fields(data, today, existing=None):
    existing = dict(existing) if existing else {}
    email = get_str(data, "email", "Email", max_len=120)
    if email and not EMAIL_RE.match(email):
        raise ApiError("Email address is not valid.", code="VALIDATION_ERROR", field="email")
    phone = get_str(data, "contact_number", "Contact number", max_len=25)
    if phone and not PHONE_RE.match(phone):
        raise ApiError("Contact number is not valid.", code="VALIDATION_ERROR", field="contact_number")
    return {
        "name": get_str(data, "name", "Employee name", required=True, max_len=120),
        "department": get_str(data, "department", "Department", required=True, max_len=80),
        "designation": get_str(data, "designation", "Designation", required=True, max_len=80),
        "contact_number": phone,
        "email": email,
        "date_of_joining": get_date(data, "date_of_joining", "Date of joining",
                                    default=existing.get("date_of_joining") or today),
        "notes": get_str(data, "notes", "Notes", max_len=1000),
    }


def create_employee(conn, data, settings, actor_name):
    """Shared by the API and the demo-data command."""
    today = timeutil.local_date(timeutil.now_utc(), business_tz(settings)).isoformat()
    fields = _read_fields(data, today)
    status = get_enum(data, "status", ("active", "inactive"), "Status", default="active")
    shift_id = get_int(data, "shift_id", "Shift", 1, 10**9) if data.get("shift_id") else None
    with transaction(conn):
        if shift_id:
            require_active_shift(conn, shift_id)
        code = next_employee_id(conn, settings)
        stamp = timeutil.to_iso(timeutil.now_utc())
        cursor = conn.execute(
            "INSERT INTO employees(employee_id, name, department, designation, contact_number, email, "
            "date_of_joining, status, deactivated_on, notes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (code, fields["name"], fields["department"], fields["designation"], fields["contact_number"],
             fields["email"], fields["date_of_joining"], status,
             today if status == "inactive" else None, fields["notes"], stamp, stamp),
        )
        if shift_id:
            assign_shift(conn, cursor.lastrowid, shift_id, fields["date_of_joining"], None, "", actor_name)
        audit(conn, "employee.create", "employee", code, {"name": fields["name"]},
              actor=g.get("admin") or {"id": None, "username": actor_name})
    return code


@bp.get("/api/admin/employees")
@require("employees.view")
def list_employees():
    conn = get_db()
    settings = get_settings(conn)
    today = timeutil.local_date(timeutil.now_utc(), business_tz(settings)).isoformat()
    page, per_page = page_args()
    where, params = [], []
    query = request.args.get("q", "").strip()
    if query:
        pattern = "%" + like_escape(query) + "%"
        where.append("(e.employee_id LIKE ? ESCAPE '\\' OR e.name LIKE ? ESCAPE '\\')")
        params += [pattern, pattern]
    if request.args.get("department"):
        where.append("e.department = ?")
        params.append(request.args["department"])
    if request.args.get("status"):
        where.append("e.status = ?")
        params.append(request.args["status"])
    clause = " WHERE " + " AND ".join(where) if where else ""

    sorts = {"employee_id": "e.employee_id", "name": "e.name COLLATE NOCASE", "department": "e.department",
             "designation": "e.designation", "status": "e.status", "date_of_joining": "e.date_of_joining"}
    order = sorts.get(request.args.get("sort"), "e.employee_id")
    direction = "DESC" if request.args.get("dir") == "desc" else "ASC"

    total = conn.execute(f"SELECT COUNT(*) FROM employees e{clause}", params).fetchone()[0]
    rows = conn.execute(
        "SELECT e.*, (SELECT s.name FROM shift_assignments sa JOIN shifts s ON s.id = sa.shift_id "
        "  WHERE sa.employee_pk = e.id AND sa.status = 'active' AND sa.effective_from <= ? "
        "  AND (sa.effective_to IS NULL OR sa.effective_to >= ?) "
        "  ORDER BY sa.effective_from DESC, sa.id DESC LIMIT 1) AS shift_name, "
        "(SELECT sa.shift_id FROM shift_assignments sa "
        "  WHERE sa.employee_pk = e.id AND sa.status = 'active' AND sa.effective_from <= ? "
        "  AND (sa.effective_to IS NULL OR sa.effective_to >= ?) "
        "  ORDER BY sa.effective_from DESC, sa.id DESC LIMIT 1) AS shift_id "
        f"FROM employees e{clause} ORDER BY {order} {direction} LIMIT ? OFFSET ?",
        [today, today, today, today] + params + [per_page, (page - 1) * per_page],
    ).fetchall()
    return ok(items=[dict(r) for r in rows], total=total, page=page, per_page=per_page,
              capacity=capacity(conn, settings))


@bp.post("/api/admin/employees")
@require("employees.manage")
def add_employee():
    conn = get_db()
    settings = get_settings(conn)
    code = create_employee(conn, json_body(), settings, g.admin["username"])
    settings = get_settings(conn)
    return ok(201, employee_id=code, capacity=capacity(conn, settings),
              message=f"Employee {code} created.")


def _find(conn, code):
    row = conn.execute("SELECT * FROM employees WHERE employee_id = ? COLLATE NOCASE", (code,)).fetchone()
    if row is None:
        raise ApiError("Employee not found.", 404, "EMPLOYEE_NOT_FOUND")
    return row


@bp.get("/api/admin/employees/<code>")
@require("employees.view")
def employee_detail(code):
    conn = get_db()
    settings = get_settings(conn)
    now = timeutil.now_utc()
    employee = _find(conn, code)
    close_stale_sessions(conn, settings, now, employee["id"])
    today = timeutil.local_date(now, business_tz(settings)).isoformat()
    current_shift = conn.execute(CURRENT_SHIFT_SQL, (employee["id"], today, today)).fetchone()
    state = schedule.evaluate(conn, employee, settings, now)
    summary = conn.execute(
        "SELECT COUNT(clock_in_utc) AS days_attended, "
        "COALESCE(SUM(working_seconds), 0) AS working_seconds, "
        "COALESCE(SUM(overtime_seconds), 0) AS overtime_seconds, "
        "COALESCE(SUM(late_seconds), 0) AS late_seconds, "
        "COALESCE(SUM(early_departure_seconds), 0) AS early_departure_seconds, "
        "COALESCE(SUM(late_seconds > 0), 0) AS late_count, "
        "COALESCE(SUM(early_departure_seconds > 0), 0) AS early_departure_count, "
        "COALESCE(SUM(overtime_seconds > 0), 0) AS overtime_count "
        "FROM attendance WHERE employee_pk = ?", (employee["id"],),
    ).fetchone()
    history = conn.execute(
        RECORD_SELECT + " WHERE a.employee_pk = ? ORDER BY a.attendance_date DESC, a.id DESC LIMIT 100",
        (employee["id"],),
    ).fetchall()
    assignments = conn.execute(
        "SELECT sa.*, s.name AS shift_name, s.code AS shift_code FROM shift_assignments sa "
        "JOIN shifts s ON s.id = sa.shift_id WHERE sa.employee_pk = ? "
        "ORDER BY sa.effective_from DESC, sa.id DESC", (employee["id"],),
    ).fetchall()
    return ok(
        employee=dict(employee),
        current_shift=dict(current_shift) if current_shift else None,
        current_status={"status": state["status"], "label": state["status_label"],
                        "indicator": state["indicator"], "message": state["message"]},
        summary=dict(summary),
        attendance=[record_row(r) for r in history],
        assignments=[dict(a) for a in assignments],
    )


@bp.put("/api/admin/employees/<code>")
@require("employees.manage")
def update_employee(code):
    data = json_body()
    conn = get_db()
    settings = get_settings(conn)
    today = timeutil.local_date(timeutil.now_utc(), business_tz(settings)).isoformat()
    with transaction(conn):
        before = _find(conn, code)
        fields = _read_fields(data, today, before)
        conn.execute(
            "UPDATE employees SET name = ?, department = ?, designation = ?, contact_number = ?, "
            "email = ?, date_of_joining = ?, notes = ?, updated_at = ? WHERE id = ?",
            (fields["name"], fields["department"], fields["designation"], fields["contact_number"],
             fields["email"], fields["date_of_joining"], fields["notes"],
             timeutil.to_iso(timeutil.now_utc()), before["id"]),
        )
        changes = {k: {"from": before[k], "to": v} for k, v in fields.items() if before[k] != v}

        # Choosing a different shift here takes effect today; history is untouched.
        if "shift_id" in data:
            shift_id = get_int(data, "shift_id", "Shift", 1, 10**9) if data["shift_id"] else None
            current = conn.execute(CURRENT_SHIFT_SQL, (before["id"], today, today)).fetchone()
            if shift_id and (current is None or current["id"] != shift_id):
                require_active_shift(conn, shift_id)
                assign_shift(conn, before["id"], shift_id, today, None, "Changed on employee form",
                             g.admin["username"])
                changes["shift_id"] = {"from": current["id"] if current else None, "to": shift_id}
        if changes:
            audit(conn, "employee.update", "employee", before["employee_id"], changes)
    return ok(message="Employee updated.")


def _set_status(code, status):
    conn = get_db()
    settings = get_settings(conn)
    today = timeutil.local_date(timeutil.now_utc(), business_tz(settings)).isoformat()
    with transaction(conn):
        employee = _find(conn, code)
        if employee["status"] == status:
            return employee
        if status == "inactive" and conn.execute(
            "SELECT 1 FROM attendance WHERE employee_pk = ? AND is_open = 1", (employee["id"],),
        ).fetchone():
            raise ApiError("This employee is currently clocked in. Clock them out first.", 409,
                           "EMPLOYEE_CLOCKED_IN")
        conn.execute("UPDATE employees SET status = ?, deactivated_on = ?, updated_at = ? WHERE id = ?",
                     (status, today if status == "inactive" else None,
                      timeutil.to_iso(timeutil.now_utc()), employee["id"]))
        audit(conn, f"employee.{'deactivate' if status == 'inactive' else 'reactivate'}",
              "employee", employee["employee_id"])
    return employee


@bp.post("/api/admin/employees/<code>/deactivate")
@require("employees.manage")
def deactivate_employee(code):
    _set_status(code, "inactive")
    return ok(message="Employee deactivated. Attendance history is kept.")


@bp.post("/api/admin/employees/<code>/reactivate")
@require("employees.manage")
def reactivate_employee(code):
    _set_status(code, "active")
    return ok(message="Employee reactivated.")
