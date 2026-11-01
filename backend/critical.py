"""Super Admin only: purge data by table or selection, clean up everything, or reset to defaults.

History tables normally refuse deletion through database triggers. Only this module
lifts them, inside one transaction, after a password check and a safety backup.
"""
from contextlib import contextmanager
from functools import wraps

from flask import Blueprint, current_app, g, request, session
from werkzeug.security import check_password_hash

from . import timeutil
from .api import ApiError, check_date, json_body, ok, page_args
from .auth import require
from .backup import create_backup_file
from .database import SUPER_ADMIN, audit, get_db, seed_defaults, transaction
from .starter import load_starter_data

bp = Blueprint("critical", __name__)

PURGE_PHRASE = "PURGE"
RESET_PHRASE = "RESET"
MAX_SELECTED = 10000
IDS = "(SELECT id FROM temp.purge_ids)"

EMPLOYEE_JOIN = " JOIN employees e ON e.id = t.employee_pk"
# Every value here is fixed in code, so it is safe to build SQL from. User input is always bound.
TABLES = {
    "attendance": {
        "label": "Attendance Records", "source": "attendance t" + EMPLOYEE_JOIN, "date": "t.attendance_date",
        "select": "t.id, t.attendance_date, e.employee_id, e.name AS employee_name, t.clock_in_utc AS clock_in, t.status",
        "columns": [("attendance_date", "Date", "date"), ("employee_id", "ID", None), ("employee_name", "Employee", None),
                    ("clock_in", "Clock In", "datetime"), ("status", "Status", None)],
    },
    "overtime": {
        "label": "Overtime Entries", "source": "overtime_entries t" + EMPLOYEE_JOIN, "date": "t.work_date",
        "select": "t.id, t.work_date, e.employee_id, e.name AS employee_name, t.minutes * 60 AS seconds, t.reason, t.status",
        "columns": [("work_date", "Date", "date"), ("employee_id", "ID", None), ("employee_name", "Employee", None),
                    ("seconds", "Hours", "duration"), ("reason", "Reason", None), ("status", "Status", None)],
    },
    "salary_items": {
        "label": "Allowances & Deductions", "source": "salary_items t" + EMPLOYEE_JOIN, "month": "t.start_month",
        "select": "t.id, t.start_month, t.end_month, e.employee_id, e.name AS employee_name, t.kind, t.name, "
                  "t.amount_cents / 100.0 AS amount",
        "columns": [("start_month", "From", None), ("end_month", "To", None), ("employee_id", "ID", None),
                    ("employee_name", "Employee", None), ("kind", "Type", None), ("name", "Name", None),
                    ("amount", "Amount", "money")],
    },
    "salary_slips": {
        "label": "Finalized Salary Slips", "source": "salary_slips t" + EMPLOYEE_JOIN, "month": "t.month",
        "select": "t.id, t.month, e.employee_id, e.name AS employee_name, t.finalized_by, t.finalized_at",
        "columns": [("month", "Month", None), ("employee_id", "ID", None), ("employee_name", "Employee", None),
                    ("finalized_by", "Finalized By", None), ("finalized_at", "Finalized On", "datetime")],
    },
    "leave": {
        "label": "Leave Records", "source": "leave_records t" + EMPLOYEE_JOIN, "date": "t.start_date",
        "select": "t.id, t.start_date, t.end_date, e.employee_id, e.name AS employee_name, t.leave_type, t.status",
        "columns": [("start_date", "From", "date"), ("end_date", "To", "date"), ("employee_id", "ID", None),
                    ("employee_name", "Employee", None), ("leave_type", "Type", None), ("status", "Status", None)],
    },
    "assignments": {
        "label": "Shift Assignments",
        "source": "shift_assignments t" + EMPLOYEE_JOIN + " JOIN shifts s ON s.id = t.shift_id",
        "date": "t.effective_from",
        "select": "t.id, t.effective_from, t.effective_to, e.employee_id, e.name AS employee_name, s.name AS shift_name, t.status",
        "columns": [("effective_from", "From", "date"), ("effective_to", "To", "date"), ("employee_id", "ID", None),
                    ("employee_name", "Employee", None), ("shift_name", "Shift", None), ("status", "State", None)],
    },
    "holidays": {
        "label": "Holidays", "source": "holidays t", "date": "t.date", "no_employee": True,
        "select": "t.id, t.date, t.name, t.type",
        "columns": [("date", "Date", "date"), ("name", "Name", None), ("type", "Type", None)],
    },
    "audit_logs": {
        "label": "Audit Log", "source": "audit_logs t", "date": "substr(t.created_at, 1, 10)", "no_employee": True,
        "select": "t.id, t.created_at, t.admin_username, t.action, t.entity_id",
        "columns": [("created_at", "When", "datetime"), ("admin_username", "Administrator", None),
                    ("action", "Action", None), ("entity_id", "Target", None)],
    },
    "shifts": {
        "label": "Shifts", "source": "shifts t", "no_employee": True,
        "select": "t.id, t.name, t.code, t.start_time, t.end_time, "
                  "CASE WHEN t.deleted_at IS NULL THEN t.status ELSE 'deleted' END AS status",
        "columns": [("name", "Name", None), ("code", "Code", None), ("start_time", "Start", None),
                    ("end_time", "End", None), ("status", "Status", None)],
    },
    "employees": {
        "label": "Employees (and all their records)", "source": "employees t JOIN employees e ON e.id = t.id",
        "date": "t.date_of_joining",
        "select": "t.id, t.employee_id, t.name AS employee_name, t.employee_type, t.department, t.status",
        "columns": [("employee_id", "ID", None), ("employee_name", "Name", None), ("employee_type", "Type", None),
                    ("department", "Department", None), ("status", "Status", None)],
    },
}

# (label, table, where). Children come first, so foreign keys are satisfied.
STEPS = {
    "attendance": [
        ("Attendance corrections", "attendance_adjustments", f"attendance_id IN {IDS}"),
        ("Attendance Records", "attendance", f"id IN {IDS}"),
    ],
    "overtime": [("Overtime Entries", "overtime_entries", f"id IN {IDS}")],
    "salary_items": [("Allowances & Deductions", "salary_items", f"id IN {IDS}")],
    "salary_slips": [("Finalized Salary Slips", "salary_slips", f"id IN {IDS}")],
    "leave": [("Leave Records", "leave_records", f"id IN {IDS}")],
    "assignments": [("Shift Assignments", "shift_assignments", f"id IN {IDS}")],
    "holidays": [("Holidays", "holidays", f"id IN {IDS}")],
    "audit_logs": [("Audit Log entries", "audit_logs", f"id IN {IDS}")],
    "shifts": [
        ("Shift Assignments", "shift_assignments", f"shift_id IN {IDS}"),
        ("Shifts", "shifts", f"id IN {IDS}"),
    ],
    "employees": [
        ("Attendance corrections", "attendance_adjustments",
         f"attendance_id IN (SELECT id FROM attendance WHERE employee_pk IN {IDS})"),
        ("Attendance Records", "attendance", f"employee_pk IN {IDS}"),
        ("Overtime Entries", "overtime_entries", f"employee_pk IN {IDS}"),
        ("Finalized Salary Slips", "salary_slips", f"employee_pk IN {IDS}"),
        ("Allowances & Deductions", "salary_items", f"employee_pk IN {IDS}"),
        ("Leave Records", "leave_records", f"employee_pk IN {IDS}"),
        ("Shift Assignments", "shift_assignments", f"employee_pk IN {IDS}"),
        ("Employees", "employees", f"id IN {IDS}"),
    ],
}

CLEANUP_STEPS = [
    ("Attendance corrections", "attendance_adjustments", "1 = 1"),
    ("Attendance Records", "attendance", "1 = 1"),
    ("Overtime Entries", "overtime_entries", "1 = 1"),
    ("Finalized Salary Slips", "salary_slips", "1 = 1"),
    ("Allowances & Deductions", "salary_items", "1 = 1"),
    ("Leave Records", "leave_records", "1 = 1"),
    ("Shift Assignments", "shift_assignments", "1 = 1"),
    ("Employees", "employees", "1 = 1"),
    ("Holidays", "holidays", "1 = 1"),
    ("Audit Log entries", "audit_logs", "1 = 1"),
]
RESET_STEPS = CLEANUP_STEPS + [
    ("Shifts", "shifts", "1 = 1"),
    ("System settings", "system_settings", "1 = 1"),
    ("Administrator accounts", "administrators", "1 = 1"),
    ("Roles", "roles", "1 = 1"),
]


def super_admin_only(view):
    """Holds even if a restored backup somehow gave another role this permission."""
    @wraps(view)
    @require("system.critical")
    def wrapper(*args, **kwargs):
        if g.admin["role_name"] != SUPER_ADMIN:
            raise ApiError("Only a Super Admin can do that.", 403, "FORBIDDEN")
        return view(*args, **kwargs)
    return wrapper


@contextmanager
def _history_unlocked(conn):
    """Lift the no-delete triggers. Must run inside a transaction, so a failure restores them."""
    triggers = conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'").fetchall()
    for trigger in triggers:
        conn.execute(f'DROP TRIGGER "{trigger["name"]}"')
    yield
    for trigger in triggers:
        conn.execute(trigger["sql"])


def _filter_clause(spec, values):
    where, params = [], []
    start, end = values.get("from"), values.get("to")
    if start:
        check_date(start, "From date")
    if end:
        check_date(end, "To date")
    if start and end and start > end:
        raise ApiError("The start date must not be after the end date.", code="VALIDATION_ERROR")
    if spec.get("date"):
        if start:
            where.append(f"{spec['date']} >= ?")
            params.append(start)
        if end:
            where.append(f"{spec['date']} <= ?")
            params.append(end)
    elif spec.get("month"):
        if start:
            where.append(f"{spec['month']} >= ?")
            params.append(start[:7])
        if end:
            where.append(f"{spec['month']} <= ?")
            params.append(end[:7])
    employee = values.get("employee")
    if employee and not spec.get("no_employee"):
        where.append("e.employee_id = ? COLLATE NOCASE")
        params.append(employee)
    return (" WHERE " + " AND ".join(where) if where else ""), params


def _read_table(value):
    if value not in TABLES:
        raise ApiError("Choose a table.", code="VALIDATION_ERROR", field="table")
    return value


def _load_ids(conn, table, body):
    """Put the rows chosen into a temp table. Returns nothing; steps read from it."""
    spec = TABLES[table]
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS purge_ids (id INTEGER PRIMARY KEY)")
    conn.execute("DELETE FROM temp.purge_ids")
    mode = body.get("mode")
    if mode == "selected":
        ids = body.get("ids")
        if (not isinstance(ids, list) or not ids or len(ids) > MAX_SELECTED
                or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids)):
            raise ApiError("Select at least one row.", code="VALIDATION_ERROR", field="ids")
        conn.executemany("INSERT OR IGNORE INTO temp.purge_ids(id) VALUES (?)", [(i,) for i in ids])
        conn.execute(f"DELETE FROM temp.purge_ids WHERE id NOT IN (SELECT t.id FROM {spec['source']})")
    elif mode == "filtered":
        clause, params = _filter_clause(spec, body)
        conn.execute(f"INSERT INTO temp.purge_ids(id) SELECT t.id FROM {spec['source']}{clause}", params)
    else:
        raise ApiError("Choose what to purge.", code="VALIDATION_ERROR", field="mode")


def _plan(conn, body):
    scope = body.get("scope")
    if scope == "table":
        table = _read_table(body.get("table"))
        _load_ids(conn, table, body)
        return STEPS[table]
    if scope == "cleanup":
        return CLEANUP_STEPS
    if scope == "reset":
        return RESET_STEPS
    raise ApiError("Unknown operation.", code="VALIDATION_ERROR", field="scope")


def _impact(conn, steps):
    rows = [{"label": label, "count": conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]}
            for label, table, where in steps]
    return [r for r in rows if r["count"]]


@bp.get("/api/admin/critical/tables")
@super_admin_only
def list_tables():
    conn = get_db()
    return ok(items=[{
        "key": key,
        "label": spec["label"],
        "count": conn.execute(f"SELECT COUNT(*) FROM {spec['source']}").fetchone()[0],
        "has_dates": bool(spec.get("date") or spec.get("month")),
        "has_employee": not spec.get("no_employee"),
    } for key, spec in TABLES.items()])


@bp.get("/api/admin/critical/rows")
@super_admin_only
def list_rows():
    table = _read_table(request.args.get("table"))
    spec = TABLES[table]
    conn = get_db()
    page, per_page = page_args()
    clause, params = _filter_clause(spec, request.args)
    total = conn.execute(f"SELECT COUNT(*) FROM {spec['source']}{clause}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT {spec['select']} FROM {spec['source']}{clause} ORDER BY t.id DESC LIMIT ? OFFSET ?",
        params + [per_page, (page - 1) * per_page],
    ).fetchall()
    return ok(items=[dict(r) for r in rows], total=total, page=page, per_page=per_page,
              columns=[{"key": c[0], "label": c[1], "kind": c[2]} for c in spec["columns"]])


@bp.post("/api/admin/critical/preview")
@super_admin_only
def preview():
    """What an operation would remove. Changes nothing."""
    body = json_body()
    conn = get_db()
    impact = _impact(conn, _plan(conn, body))
    return ok(impact=impact, total=sum(r["count"] for r in impact), phrase=_phrase(body))


def _phrase(body):
    return RESET_PHRASE if body.get("scope") == "reset" else PURGE_PHRASE


def _verify(body):
    password = body.get("password")
    if not isinstance(password, str) or not check_password_hash(g.admin["password_hash"], password):
        raise ApiError("Your password is incorrect. Nothing was changed.", 403, "WRONG_PASSWORD")
    phrase = _phrase(body)
    if body.get("phrase") != phrase:
        raise ApiError(f"Type {phrase} exactly to confirm. Nothing was changed.", code="CONFIRMATION_REQUIRED")


@bp.post("/api/admin/critical/execute")
@super_admin_only
def execute():
    body = json_body()
    _verify(body)
    conn = get_db()
    scope = body.get("scope")
    if not _impact(conn, _plan(conn, body)) and scope != "reset":
        raise ApiError("Nothing matches, so there is nothing to purge.", code="NOTHING_TO_PURGE")

    safety = create_backup_file("pre-purge" if scope != "reset" else "pre-reset")
    actor = {"id": None, "username": g.admin["username"]}
    with transaction(conn):
        steps = _plan(conn, body)
        impact = _impact(conn, steps)
        with _history_unlocked(conn):
            if body.get("table") == "shifts" and scope == "table":
                conn.execute(f"UPDATE attendance SET shift_id = NULL WHERE shift_id IN {IDS}")
            if scope in ("cleanup", "reset"):
                conn.execute("DELETE FROM clock_requests")
            elif body.get("table") in ("attendance", "employees"):
                conn.execute("DELETE FROM clock_requests")
            for _label, table, where in steps:
                conn.execute(f"DELETE FROM {table} WHERE {where}")
            if scope == "reset":
                # The session id must not match a new account, so administrators keep their counter.
                conn.execute("DELETE FROM sqlite_sequence WHERE name <> 'administrators'")
            # Always leaves a usable system: roles, an admin, and at least one shift.
            seed_defaults(conn)
        audit(conn, f"critical.{scope}", "database", body.get("table", ""),
              {"mode": body.get("mode", "all"), "removed": {r["label"]: r["count"] for r in impact},
               "safety_copy": safety.name}, actor=actor)
    g.pop("settings_cache", None)

    removed = sum(r["count"] for r in impact)
    if scope == "reset":
        load_starter_data(current_app._get_current_object())
        session.clear()
        return ok(impact=impact, safety_copy=safety.name, logged_out=True,
                  message="Timora was reset to its default data. Log in with the default administrator account.")
    return ok(impact=impact, safety_copy=safety.name,
              message=f"Purged {removed:,} record(s). A copy of the previous data was saved first.")
