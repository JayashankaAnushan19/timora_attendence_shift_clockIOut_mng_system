"""Dashboard summary and lookups shared by admin filter controls."""
from flask import Blueprint, g

from . import timeutil
from .api import ok
from .attendance import RECORD_SELECT, build_roster, close_stale_sessions, record_row
from .auth import current_admin, require
from .database import get_db
from .settings import business_tz, get_settings

bp = Blueprint("dashboard", __name__)

NOT_EXPECTED = {"Not Scheduled", "Holiday", "Weekly Off", "Leave"}


@bp.get("/api/admin/dashboard")
@require("dashboard.view")
def dashboard():
    conn = get_db()
    settings = get_settings(conn)
    now = timeutil.now_utc()
    close_stale_sessions(conn, settings, now)
    today = timeutil.local_date(now, business_tz(settings)).isoformat()

    total_employees = conn.execute("SELECT COUNT(*) FROM employees WHERE status = 'active'").fetchone()[0]
    clocked_in = conn.execute("SELECT COUNT(*) FROM attendance WHERE is_open = 1").fetchone()[0]
    clocked_out = conn.execute(
        "SELECT COUNT(DISTINCT employee_pk) FROM attendance a WHERE a.attendance_date = ? "
        "AND a.clock_out_utc IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM attendance o WHERE o.employee_pk = a.employee_pk AND o.is_open = 1)", (today,),
    ).fetchone()[0]
    late_today = conn.execute(
        "SELECT COUNT(DISTINCT employee_pk) FROM attendance WHERE attendance_date = ? AND late_seconds > 0",
        (today,),
    ).fetchone()[0]
    records_today = conn.execute("SELECT COUNT(*) FROM attendance WHERE attendance_date = ?",
                                 (today,)).fetchone()[0]

    roster = build_roster(conn, today, today, settings, now)
    active_ids = {r["employee_id"] for r in conn.execute(
        "SELECT employee_id FROM employees WHERE status = 'active'")}
    roster = [r for r in roster if r["employee_id"] in active_ids]
    not_scheduled = sum(1 for r in roster if r["status"] in NOT_EXPECTED)

    recent = conn.execute(
        RECORD_SELECT + " WHERE a.clock_in_utc IS NOT NULL "
        "ORDER BY COALESCE(a.clock_out_utc, a.clock_in_utc) DESC, a.id DESC LIMIT 10"
    ).fetchall()

    return ok(
        today=today,
        server_time=timeutil.to_iso(now),
        cards={
            "total_employees": total_employees,
            "clocked_in": clocked_in,
            "clocked_out": clocked_out,
            "late_today": late_today,
            "not_scheduled": not_scheduled,
            "records_today": records_today,
        },
        recent=[record_row(r) for r in recent],
        overview=roster,
    )


@bp.get("/api/admin/lookups")
@require()
def lookups():
    """Names for filter dropdowns. Any permitted role can see these labels."""
    conn = get_db()
    permissions = current_admin()["permissions"]
    if not permissions & {"employees.view", "shifts.view", "attendance.view", "reports.view", "overtime.manage"}:
        return ok(departments=[], shifts=[], employees=[], employee_types=[])
    departments = [r[0] for r in conn.execute(
        "SELECT DISTINCT department FROM employees WHERE department <> '' ORDER BY department")]
    shifts = [dict(r) for r in conn.execute(
        "SELECT id, name, code, status FROM shifts WHERE deleted_at IS NULL ORDER BY name")]
    employees = [dict(r) for r in conn.execute(
        "SELECT employee_id, name, status FROM employees ORDER BY name COLLATE NOCASE")]
    return ok(departments=departments, shifts=shifts, employees=employees,
              employee_types=get_settings(conn)["employee_types"])
