"""SQLite schema, connections and transactions.

Writes use BEGIN IMMEDIATE, so SQLite serialises them across threads and
processes. Combined with the partial unique index on open sessions, two
simultaneous clock-ins can never both succeed.
"""
import json
import sqlite3
from contextlib import contextmanager

from flask import current_app, g, has_request_context, request
from werkzeug.security import generate_password_hash

from . import timeutil

SCHEMA_VERSION = 4

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin@123"

ALL_PERMISSIONS = [
    "dashboard.view",
    "employees.view",
    "employees.manage",
    "shifts.view",
    "shifts.manage",
    "shifts.delete",
    "attendance.view",
    "attendance.manage",
    "reports.view",
    "users.manage",
    "settings.manage",
    "backup.manage",
    "audit.view",
    "overtime.manage",
    "payroll.manage",
]

SUPER_ADMIN = "Super Admin"

DEFAULT_ROLES = [
    (SUPER_ADMIN, "Full system access.", ALL_PERMISSIONS),
    ("Admin", "Manage employees, shifts and attendance; generate reports.",
     ["dashboard.view", "employees.view", "employees.manage", "shifts.view", "shifts.manage", "shifts.delete",
      "attendance.view", "attendance.manage", "reports.view", "overtime.manage", "payroll.manage"]),
    ("Manager", "Add overtime for employees; view attendance and reports.",
     ["dashboard.view", "attendance.view", "reports.view", "overtime.manage"]),
    ("Attendance Manager", "View and correct attendance; generate reports.",
     ["dashboard.view", "attendance.view", "attendance.manage", "reports.view"]),
    ("Viewer", "Read-only dashboard and reports.", ["dashboard.view", "reports.view"]),
]

DEFAULT_SHIFT_CODE = "DEFAULT"

SCHEMA = """
CREATE TABLE IF NOT EXISTS roles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    permissions TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS administrators (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    username             TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name            TEXT NOT NULL DEFAULT '',
    password_hash        TEXT NOT NULL,
    role_id              INTEGER NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
    is_active            INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    session_epoch        INTEGER NOT NULL DEFAULT 1,
    failed_attempts      INTEGER NOT NULL DEFAULT 0,
    locked_until         TEXT,
    last_login_at        TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS employees (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id     TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    department      TEXT NOT NULL DEFAULT '',
    designation     TEXT NOT NULL DEFAULT '',
    employee_type   TEXT NOT NULL DEFAULT '',
    basic_salary_cents INTEGER NOT NULL DEFAULT 0,
    ot_rate_cents   INTEGER NOT NULL DEFAULT 0,
    contact_number  TEXT NOT NULL DEFAULT '',
    email           TEXT NOT NULL DEFAULT '',
    date_of_joining TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    deactivated_on  TEXT,
    notes           TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_employees_name ON employees(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS ix_employees_department ON employees(department);
CREATE INDEX IF NOT EXISTS ix_employees_status ON employees(status);
CREATE INDEX IF NOT EXISTS ix_employees_type ON employees(employee_type);

CREATE TABLE IF NOT EXISTS shifts (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    name                   TEXT NOT NULL,
    code                   TEXT NOT NULL UNIQUE COLLATE NOCASE,
    start_time             TEXT NOT NULL,
    end_time               TEXT NOT NULL,
    working_days           TEXT NOT NULL DEFAULT '[0,1,2,3,4]',
    grace_minutes          INTEGER NOT NULL DEFAULT 10,
    break_minutes          INTEGER NOT NULL DEFAULT 60,
    early_clock_in_minutes INTEGER NOT NULL DEFAULT 60,
    late_clock_out_minutes INTEGER NOT NULL DEFAULT 240,
    is_flexible            INTEGER NOT NULL DEFAULT 0,
    status                 TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    deactivated_on         TEXT,
    deleted_at             TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shift_assignments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_pk    INTEGER NOT NULL REFERENCES employees(id) ON DELETE RESTRICT,
    shift_id       INTEGER NOT NULL REFERENCES shifts(id) ON DELETE RESTRICT,
    effective_from TEXT NOT NULL,
    effective_to   TEXT,
    status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded')),
    notes          TEXT NOT NULL DEFAULT '',
    assigned_by    TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_assignments_employee ON shift_assignments(employee_pk, effective_from);
CREATE INDEX IF NOT EXISTS ix_assignments_shift ON shift_assignments(shift_id);

CREATE TABLE IF NOT EXISTS attendance (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_pk             INTEGER NOT NULL REFERENCES employees(id) ON DELETE RESTRICT,
    shift_id                INTEGER REFERENCES shifts(id) ON DELETE RESTRICT,
    attendance_date         TEXT NOT NULL,
    clock_in_utc            TEXT,
    clock_out_utc           TEXT,
    is_open                 INTEGER NOT NULL DEFAULT 0,
    scheduled_start_utc     TEXT,
    scheduled_end_utc       TEXT,
    grace_minutes           INTEGER NOT NULL DEFAULT 0,
    scheduled_break_minutes INTEGER NOT NULL DEFAULT 0,
    late_clock_out_minutes  INTEGER NOT NULL DEFAULT 0,
    is_flexible             INTEGER NOT NULL DEFAULT 0,
    working_seconds         INTEGER NOT NULL DEFAULT 0,
    break_seconds           INTEGER NOT NULL DEFAULT 0,
    late_seconds            INTEGER NOT NULL DEFAULT 0,
    early_arrival_seconds   INTEGER NOT NULL DEFAULT 0,
    early_departure_seconds INTEGER NOT NULL DEFAULT 0,
    overtime_seconds        INTEGER NOT NULL DEFAULT 0,
    status                  TEXT NOT NULL,
    status_locked           INTEGER NOT NULL DEFAULT 0,
    source                  TEXT NOT NULL DEFAULT 'terminal' CHECK (source IN ('terminal', 'admin')),
    is_adjusted             INTEGER NOT NULL DEFAULT 0,
    notes                   TEXT NOT NULL DEFAULT '',
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    CHECK (clock_out_utc IS NULL OR clock_in_utc IS NOT NULL),
    CHECK (is_open = 0 OR (clock_in_utc IS NOT NULL AND clock_out_utc IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_attendance_one_open_session
    ON attendance(employee_pk) WHERE is_open = 1;
CREATE INDEX IF NOT EXISTS ix_attendance_date ON attendance(attendance_date);
CREATE INDEX IF NOT EXISTS ix_attendance_employee_date ON attendance(employee_pk, attendance_date);
CREATE INDEX IF NOT EXISTS ix_attendance_status ON attendance(status);
CREATE INDEX IF NOT EXISTS ix_attendance_shift ON attendance(shift_id);

CREATE TABLE IF NOT EXISTS attendance_adjustments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    attendance_id  INTEGER NOT NULL REFERENCES attendance(id) ON DELETE RESTRICT,
    admin_id       INTEGER REFERENCES administrators(id) ON DELETE SET NULL,
    admin_username TEXT NOT NULL,
    adjusted_at    TEXT NOT NULL,
    field_name     TEXT NOT NULL,
    original_value TEXT,
    updated_value  TEXT,
    reason         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_adjustments_attendance ON attendance_adjustments(attendance_id);

-- Overtime added by hand. rate_cents keeps the pay rate in force when it was added.
CREATE TABLE IF NOT EXISTS overtime_entries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_pk   INTEGER NOT NULL REFERENCES employees(id) ON DELETE RESTRICT,
    work_date     TEXT NOT NULL,
    minutes       INTEGER NOT NULL CHECK (minutes > 0),
    rate_cents    INTEGER NOT NULL DEFAULT 0,
    reason        TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')),
    added_by      TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    cancelled_by  TEXT,
    cancelled_at  TEXT,
    cancel_reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_overtime_employee_date ON overtime_entries(employee_pk, work_date);
CREATE INDEX IF NOT EXISTS ix_overtime_date ON overtime_entries(work_date);

-- Allowances and deductions. A one-month item has start_month = end_month;
-- a repeating item has no end_month until it is removed.
CREATE TABLE IF NOT EXISTS salary_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_pk  INTEGER NOT NULL REFERENCES employees(id) ON DELETE RESTRICT,
    kind         TEXT NOT NULL CHECK (kind IN ('allowance', 'deduction')),
    name         TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    start_month  TEXT NOT NULL,
    end_month    TEXT,
    status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'removed')),
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_salary_items_employee ON salary_items(employee_pk, start_month);

-- A finalized slip, stored whole so later pay changes can't alter it.
CREATE TABLE IF NOT EXISTS salary_slips (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_pk  INTEGER NOT NULL REFERENCES employees(id) ON DELETE RESTRICT,
    month        TEXT NOT NULL,
    data         TEXT NOT NULL,
    finalized_by TEXT NOT NULL,
    finalized_at TEXT NOT NULL,
    UNIQUE (employee_pk, month)
);

CREATE TABLE IF NOT EXISTS holidays (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    type       TEXT NOT NULL DEFAULT 'holiday' CHECK (type IN ('holiday', 'non_working')),
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leave_records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_pk INTEGER NOT NULL REFERENCES employees(id) ON DELETE RESTRICT,
    start_date  TEXT NOT NULL,
    end_date    TEXT NOT NULL,
    leave_type  TEXT NOT NULL DEFAULT 'Annual',
    reason      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'approved' CHECK (status IN ('approved', 'cancelled')),
    created_by  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_leave_employee ON leave_records(employee_pk, start_date, end_date);

CREATE TABLE IF NOT EXISTS system_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id       INTEGER,
    admin_username TEXT NOT NULL DEFAULT '',
    action         TEXT NOT NULL,
    entity_type    TEXT NOT NULL DEFAULT '',
    entity_id      TEXT NOT NULL DEFAULT '',
    details        TEXT NOT NULL DEFAULT '{}',
    ip_address     TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_created ON audit_logs(created_at);

-- Lets a terminal retry a clock request safely after a network failure.
CREATE TABLE IF NOT EXISTS clock_requests (
    request_id TEXT PRIMARY KEY,
    response   TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS tr_employees_no_delete BEFORE DELETE ON employees
BEGIN SELECT RAISE(ABORT, 'Employees cannot be deleted; deactivate them instead.'); END;

CREATE TRIGGER IF NOT EXISTS tr_attendance_no_delete BEFORE DELETE ON attendance
BEGIN SELECT RAISE(ABORT, 'Attendance records cannot be deleted.'); END;

CREATE TRIGGER IF NOT EXISTS tr_overtime_no_delete BEFORE DELETE ON overtime_entries
BEGIN SELECT RAISE(ABORT, 'Overtime entries cannot be deleted; cancel them instead.'); END;

CREATE TRIGGER IF NOT EXISTS tr_adjustments_no_update BEFORE UPDATE ON attendance_adjustments
BEGIN SELECT RAISE(ABORT, 'Attendance adjustments are permanent.'); END;

CREATE TRIGGER IF NOT EXISTS tr_adjustments_no_delete BEFORE DELETE ON attendance_adjustments
BEGIN SELECT RAISE(ABORT, 'Attendance adjustments are permanent.'); END;

CREATE TRIGGER IF NOT EXISTS tr_audit_no_update BEFORE UPDATE ON audit_logs
BEGIN SELECT RAISE(ABORT, 'Audit logs are permanent.'); END;

CREATE TRIGGER IF NOT EXISTS tr_audit_no_delete BEFORE DELETE ON audit_logs
BEGIN SELECT RAISE(ABORT, 'Audit logs are permanent.'); END;
"""

REQUIRED_TABLES = {
    "employees", "administrators", "roles", "shifts", "shift_assignments", "attendance",
    "attendance_adjustments", "holidays", "leave_records", "system_settings", "audit_logs",
}


def connect(path):
    conn = sqlite3.connect(str(path), timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    # FULL: a confirmed clock event must survive a power cut.
    conn.execute("PRAGMA synchronous = FULL")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["TIMORA"]["database"])
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


@contextmanager
def transaction(conn):
    """Atomic write block. Nested use joins the outer transaction."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def init_db(path):
    """Create or upgrade the database. Safe to run on every start."""
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        previous_version = conn.execute("PRAGMA user_version").fetchone()[0]
        _add_missing_columns(conn)
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        _seed(conn)
        if previous_version == 1:
            _grant_admin_permissions(conn, "overtime.manage", "payroll.manage")
        if 1 <= previous_version < 4:
            _grant_admin_permissions(conn, "shifts.delete")
    finally:
        conn.close()


def _add_missing_columns(conn):
    """Databases and backups from earlier versions gain the newer employee columns."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(employees)")}
    if not existing:
        return
    for column, definition in (
        ("employee_type", "TEXT NOT NULL DEFAULT ''"),
        ("basic_salary_cents", "INTEGER NOT NULL DEFAULT 0"),
        ("ot_rate_cents", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if column not in existing:
            conn.execute(f"ALTER TABLE employees ADD COLUMN {column} {definition}")
    if "deleted_at" not in {row["name"] for row in conn.execute("PRAGMA table_info(shifts)")}:
        conn.execute("ALTER TABLE shifts ADD COLUMN deleted_at TEXT")


def _grant_admin_permissions(conn, *added):
    row = conn.execute("SELECT permissions FROM roles WHERE name = 'Admin'").fetchone()
    if row:
        permissions = set(json.loads(row["permissions"])) | set(added)
        conn.execute("UPDATE roles SET permissions = ? WHERE name = 'Admin'",
                     (json.dumps(sorted(permissions)),))


def _seed(conn):
    now = timeutil.to_iso(timeutil.now_utc())
    with transaction(conn):
        for name, description, permissions in DEFAULT_ROLES:
            conn.execute(
                "INSERT OR IGNORE INTO roles(name, description, permissions, created_at) "
                "VALUES (?, ?, ?, ?)",
                (name, description, json.dumps(permissions), now),
            )
        # Super Admin always keeps every permission, including ones added later.
        conn.execute("UPDATE roles SET permissions = ? WHERE name = ?",
                     (json.dumps(ALL_PERMISSIONS), SUPER_ADMIN))

        if conn.execute("SELECT COUNT(*) FROM administrators").fetchone()[0] == 0:
            role_id = conn.execute("SELECT id FROM roles WHERE name = ?",
                                   (SUPER_ADMIN,)).fetchone()[0]
            conn.execute(
                "INSERT INTO administrators(username, full_name, password_hash, role_id, "
                "must_change_password, created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                (DEFAULT_ADMIN_USERNAME, "System Administrator",
                 generate_password_hash(DEFAULT_ADMIN_PASSWORD), role_id, now, now),
            )

        if conn.execute("SELECT COUNT(*) FROM shifts").fetchone()[0] == 0:
            # A 24-hour window: clock in any time, every day; each day starts at 8 AM.
            conn.execute(
                "INSERT INTO shifts(name, code, start_time, end_time, working_days, break_minutes, "
                "early_clock_in_minutes, is_flexible, created_at, updated_at) "
                "VALUES ('Default Shift', ?, '08:00', '08:00', '[0,1,2,3,4,5,6]', 0, 0, 1, ?, ?)",
                (DEFAULT_SHIFT_CODE, now, now),
            )
            conn.execute(
                "INSERT INTO shifts(name, code, start_time, end_time, created_at, updated_at) "
                "VALUES ('Manager Shift', 'MANAGER', '08:00', '17:00', ?, ?)",
                (now, now),
            )
            conn.execute(
                "INSERT INTO shifts(name, code, start_time, end_time, working_days, break_minutes, "
                "early_clock_in_minutes, is_flexible, created_at, updated_at) "
                "VALUES ('Midnight Shift', 'MIDNIGHT', '00:00', '00:00', '[0,1,2,3,4,5,6]', 0, 0, 1, ?, ?)",
                (now, now),
            )


def audit(conn, action, entity_type="", entity_id="", details=None, actor=None):
    """Append to the audit trail. actor defaults to the logged-in administrator."""
    if actor is None and has_request_context():
        actor = g.get("admin")
    ip = request.remote_addr if has_request_context() else ""
    conn.execute(
        "INSERT INTO audit_logs(admin_id, admin_username, action, entity_type, entity_id, "
        "details, ip_address, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            actor["id"] if actor else None,
            actor["username"] if actor else "system",
            action,
            entity_type,
            str(entity_id),
            json.dumps(details or {}, default=str),
            ip or "",
            timeutil.to_iso(timeutil.now_utc()),
        ),
    )
