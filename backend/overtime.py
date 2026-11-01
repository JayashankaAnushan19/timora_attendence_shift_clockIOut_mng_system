"""Overtime added by hand. Pay is calculated only from these approved entries."""
from decimal import Decimal, InvalidOperation

from flask import Blueprint, g, request

from . import timeutil
from .api import ApiError, check_date, get_date, get_str, json_body, ok, page_args
from .auth import current_admin, require
from .database import audit, get_db, transaction
from .settings import business_tz, get_settings

bp = Blueprint("overtime", __name__)

MINUTES_PER_DAY = 24 * 60

ENTRY_SELECT = """
SELECT o.*, e.employee_id, e.name AS employee_name, e.employee_type, e.department,
       e.basic_salary_cents, e.ot_rate_cents
FROM overtime_entries o JOIN employees e ON e.id = o.employee_pk
"""


def entry_row(row):
    return {
        "id": row["id"],
        "employee_id": row["employee_id"],
        "employee_name": row["employee_name"],
        "employee_type": row["employee_type"],
        "department": row["department"],
        "work_date": row["work_date"],
        "added_seconds": row["minutes"] * 60,
        "reason": row["reason"],
        "status": row["status"],
        "added_by": row["added_by"],
        "created_at": row["created_at"],
        "cancelled_by": row["cancelled_by"],
        "cancelled_at": row["cancelled_at"],
        "cancel_reason": row["cancel_reason"],
        "rate": row["rate_cents"] / 100,
        "ot_pay": round(row["minutes"] * row["rate_cents"] / 60) / 100,
        "basic_salary": row["basic_salary_cents"] / 100,
        "current_ot_rate": row["ot_rate_cents"] / 100,
    }


def fetch_entries(conn, start, end, filters):
    """Active overtime in a date range, for reports."""
    where, params = ["o.status = 'active'", "o.work_date BETWEEN ? AND ?"], [start, end]
    if filters.get("employee"):
        where.append("e.employee_id = ? COLLATE NOCASE")
        params.append(filters["employee"])
    if filters.get("employees"):
        where.append(f"e.employee_id IN ({', '.join('?' * len(filters['employees']))})")
        params += filters["employees"]
    if filters.get("department"):
        where.append("e.department = ?")
        params.append(filters["department"])
    if filters.get("employee_type"):
        where.append("e.employee_type = ?")
        params.append(filters["employee_type"])
    rows = conn.execute(
        ENTRY_SELECT + " WHERE " + " AND ".join(where) + " ORDER BY o.work_date, e.name COLLATE NOCASE, o.id",
        params,
    ).fetchall()
    return [entry_row(r) for r in rows]


def _read_minutes(data):
    try:
        hours = Decimal(str(data.get("hours", "")))
        if not hours.is_finite():
            raise InvalidOperation
    except InvalidOperation:
        raise ApiError("Hours must be a number, such as 2 or 1.5.", code="VALIDATION_ERROR", field="hours")
    minutes = int((hours * 60).to_integral_value())
    if minutes <= 0 or minutes > MINUTES_PER_DAY:
        raise ApiError("Hours must be more than 0 and no more than 24.", code="VALIDATION_ERROR", field="hours")
    return minutes


def _public_entry(row, can_see_pay):
    entry = entry_row(row)
    for key in ("basic_salary", "current_ot_rate"):
        entry.pop(key)
    if not can_see_pay:
        entry.pop("rate")
        entry.pop("ot_pay")
    return entry


@bp.get("/api/admin/overtime")
@require("overtime.manage")
def list_overtime():
    conn = get_db()
    page, per_page = page_args()
    where, params = ["1 = 1"], []
    args = request.args
    if args.get("from"):
        where.append("o.work_date >= ?")
        params.append(check_date(args["from"], "From date"))
    if args.get("to"):
        where.append("o.work_date <= ?")
        params.append(check_date(args["to"], "To date"))
    if args.get("employee"):
        where.append("e.employee_id = ? COLLATE NOCASE")
        params.append(args["employee"])
    if args.get("status") in ("active", "cancelled"):
        where.append("o.status = ?")
        params.append(args["status"])
    clause = " WHERE " + " AND ".join(where)
    total = conn.execute(
        "SELECT COUNT(*) FROM overtime_entries o JOIN employees e ON e.id = o.employee_pk" + clause, params,
    ).fetchone()[0]
    rows = conn.execute(
        ENTRY_SELECT + clause + " ORDER BY o.work_date DESC, o.id DESC LIMIT ? OFFSET ?",
        params + [per_page, (page - 1) * per_page],
    ).fetchall()
    can_see_pay = "payroll.manage" in current_admin()["permissions"]
    return ok(items=[_public_entry(r, can_see_pay) for r in rows], total=total, page=page, per_page=per_page)


@bp.post("/api/admin/overtime")
@require("overtime.manage")
def add_overtime():
    """Add overtime for selected employees, or for all active employees."""
    data = json_body()
    conn = get_db()
    settings = get_settings(conn)
    now = timeutil.now_utc()
    today = timeutil.local_date(now, business_tz(settings)).isoformat()
    work_date = get_date(data, "work_date", "Date", required=True)
    if work_date > today:
        raise ApiError("Overtime cannot be added for a future date.", code="VALIDATION_ERROR", field="work_date")
    minutes = _read_minutes(data)
    reason = get_str(data, "reason", "Reason", required=True, max_len=500)
    apply_to_all = data.get("all") is True
    codes = data.get("employee_ids")
    if not apply_to_all and (not isinstance(codes, list) or not codes
                             or not all(isinstance(c, str) for c in codes)):
        raise ApiError("Select at least one employee.", code="VALIDATION_ERROR", field="employee_ids")

    stamp = timeutil.to_iso(now)
    with transaction(conn):
        if apply_to_all:
            employees = conn.execute("SELECT * FROM employees WHERE status = 'active'").fetchall()
            if not employees:
                raise ApiError("There are no active employees.", code="VALIDATION_ERROR")
        else:
            employees = []
            for code in dict.fromkeys(codes):
                row = conn.execute("SELECT * FROM employees WHERE employee_id = ? COLLATE NOCASE", (code,)).fetchone()
                if row is None or row["status"] != "active":
                    raise ApiError(f"Employee {code} was not found or is inactive.", 404, "EMPLOYEE_NOT_FOUND")
                employees.append(row)
        for employee in employees:
            existing = conn.execute(
                "SELECT COALESCE(SUM(minutes), 0) FROM overtime_entries "
                "WHERE employee_pk = ? AND work_date = ? AND status = 'active'",
                (employee["id"], work_date),
            ).fetchone()[0]
            if existing + minutes > MINUTES_PER_DAY:
                raise ApiError(f"{employee['name']} would have more than 24 hours of overtime on that date.",
                               409, "OVERTIME_LIMIT")
            conn.execute(
                "INSERT INTO overtime_entries(employee_pk, work_date, minutes, rate_cents, reason, added_by, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (employee["id"], work_date, minutes, employee["ot_rate_cents"], reason, g.admin["username"], stamp),
            )
        audit(conn, "overtime.add", "employee", "all" if apply_to_all else ",".join(e["employee_id"] for e in employees),
              {"date": work_date, "minutes": minutes, "reason": reason, "count": len(employees)})
    return ok(201, count=len(employees), message=f"Overtime added for {len(employees)} employee(s).")


@bp.post("/api/admin/overtime/<int:entry_id>/cancel")
@require("overtime.manage")
def cancel_overtime(entry_id):
    """Entries are cancelled, never deleted, so the record of what was added remains."""
    reason = get_str(json_body(), "reason", "Reason", required=True, max_len=500)
    conn = get_db()
    with transaction(conn):
        cursor = conn.execute(
            "UPDATE overtime_entries SET status = 'cancelled', cancelled_by = ?, cancelled_at = ?, cancel_reason = ? "
            "WHERE id = ? AND status = 'active'",
            (g.admin["username"], timeutil.to_iso(timeutil.now_utc()), reason, entry_id),
        )
        if cursor.rowcount == 0:
            raise ApiError("Overtime entry not found or already cancelled.", 404, "NOT_FOUND")
        audit(conn, "overtime.cancel", "overtime", entry_id, {"reason": reason})
    return ok(message="Overtime cancelled.")
