"""Database backup, restore and data exports."""
import json
import re
import sqlite3
import uuid
from datetime import timedelta
from pathlib import Path

from flask import Blueprint, current_app, g, request, send_file

from . import timeutil
from .api import ApiError, ok
from .attendance import fetch_records
from .auth import require
from .database import (REQUIRED_TABLES, SCHEMA_VERSION, SUPER_ADMIN, audit, close_db, connect, get_db,
                       init_db, transaction)
from .employees import employee_out
from .reports import REPORTS, attachment, rows_to_csv
from .settings import business_tz, editable_settings, get_settings, set_setting

bp = Blueprint("backup", __name__)

BACKUP_NAME_RE = re.compile(r"^timora-[a-z\-]*\d{8}-\d{6}\.db$")
SQLITE_HEADER = b"SQLite format 3\x00"

EMPLOYEE_COLUMNS = [
    ("employee_id", "Employee ID", "text"), ("name", "Name", "text"), ("department", "Department", "text"),
    ("designation", "Designation", "text"), ("employee_type", "Employee Type", "text"),
    ("contact_number", "Contact Number", "text"),
    ("email", "Email", "text"), ("date_of_joining", "Date of Joining", "date"),
    ("status", "Status", "text"), ("notes", "Notes", "text"),
]


def _cfg():
    return current_app.config["TIMORA"]


def _new_backup_path(kind):
    """A filename that never overwrites an existing backup, even within one second."""
    moment = timeutil.now_utc()
    while True:
        path = _cfg()["backup_dir"] / f"timora-{kind}-{moment.strftime('%Y%m%d-%H%M%S')}.db"
        if not path.exists():
            return path
        moment += timedelta(seconds=1)


def _remove_with_sidecars(path):
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)


def create_backup_file(kind="backup"):
    """Consistent copy of the live database, safe while the app is in use."""
    target = _new_backup_path(kind)
    source = connect(_cfg()["database"])
    dest = sqlite3.connect(str(target))
    try:
        source.backup(dest)
        # A single self-contained file, with no -wal companion to lose.
        dest.execute("PRAGMA journal_mode = DELETE")
    finally:
        dest.close()
        source.close()
    return target


def _validate_backup(path):
    """Reject anything that is not a healthy Timora database before touching live data."""
    with open(path, "rb") as handle:
        if handle.read(16) != SQLITE_HEADER:
            raise ApiError("That file is not a Timora database backup.", code="INVALID_BACKUP")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ApiError("The backup file is damaged.", code="INVALID_BACKUP")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        missing = REQUIRED_TABLES - tables
        if missing:
            raise ApiError("That file is not a Timora database backup.", code="INVALID_BACKUP")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise ApiError("This backup was made by a newer version of Timora.", code="INVALID_BACKUP")
        has_admin = conn.execute(
            "SELECT 1 FROM administrators a JOIN roles r ON r.id = a.role_id "
            "WHERE r.name = ? AND a.is_active = 1 LIMIT 1", (SUPER_ADMIN,),
        ).fetchone()
        if not has_admin:
            raise ApiError("The backup has no active Super Admin, so nobody could log in to manage it.",
                           code="INVALID_BACKUP")
    except sqlite3.DatabaseError:
        raise ApiError("That file is not a Timora database backup.", code="INVALID_BACKUP")
    finally:
        conn.close()


def _restore_from(path, label):
    """Validate, snapshot the current database, then swap the contents atomically."""
    _validate_backup(path)
    safety = create_backup_file("pre-restore")
    close_db()  # release this request's connection before replacing the data
    cfg = _cfg()
    source = sqlite3.connect(str(path))
    dest = connect(cfg["database"])
    try:
        source.backup(dest)
    finally:
        source.close()
        dest.close()
    init_db(cfg["database"])

    actor = {"id": None, "username": g.admin["username"]}
    conn = get_db()
    g.pop("settings_cache", None)
    with transaction(conn):
        set_setting(conn, "last_backup_at", timeutil.to_iso(timeutil.now_utc()), actor["username"])
        audit(conn, "backup.restore", "database", "", {"source": label, "safety_copy": safety.name}, actor=actor)
    return safety


@bp.get("/api/admin/backup")
@require("backup.manage")
def backup_info():
    settings = get_settings(get_db())
    files = []
    for path in sorted(_cfg()["backup_dir"].glob("timora-*.db"), reverse=True):
        stat = path.stat()
        files.append({"name": path.name, "size": stat.st_size,
                      "created": timeutil.to_iso(timeutil.datetime.fromtimestamp(stat.st_mtime, timeutil.UTC))})
    return ok(last_backup_at=settings.get("last_backup_at"), files=files)


@bp.post("/api/admin/backup/create")
@require("backup.manage")
def create_backup():
    path = create_backup_file()
    conn = get_db()
    with transaction(conn):
        set_setting(conn, "last_backup_at", timeutil.to_iso(timeutil.now_utc()), g.admin["username"])
        audit(conn, "backup.create", "database", path.name)
    return ok(201, name=path.name, message="Backup created.")


@bp.get("/api/admin/backup/files/<name>")
@require("backup.manage")
def download_backup(name):
    if not BACKUP_NAME_RE.match(name):
        raise ApiError("Backup not found.", 404, "NOT_FOUND")
    path = _cfg()["backup_dir"] / name
    if not path.is_file():
        raise ApiError("Backup not found.", 404, "NOT_FOUND")
    conn = get_db()
    with transaction(conn):
        audit(conn, "backup.download", "database", name)
    return send_file(path, as_attachment=True, download_name=name, mimetype="application/octet-stream")


@bp.post("/api/admin/backup/restore")
@require("backup.manage")
def restore_upload():
    upload = request.files.get("backup")
    if upload is None or not upload.filename:
        raise ApiError("Choose a backup file to restore.", code="VALIDATION_ERROR")
    temp = _cfg()["backup_dir"] / f".upload-{uuid.uuid4().hex}.tmp"
    upload.save(temp)
    try:
        safety = _restore_from(temp, upload.filename[:200])
    finally:
        _remove_with_sidecars(temp)
    return ok(safety_copy=safety.name,
              message="Database restored. A copy of the previous data was saved first.")


@bp.post("/api/admin/backup/restore/<name>")
@require("backup.manage")
def restore_existing(name):
    if not BACKUP_NAME_RE.match(name):
        raise ApiError("Backup not found.", 404, "NOT_FOUND")
    source = _cfg()["backup_dir"] / name
    if not source.is_file():
        raise ApiError("Backup not found.", 404, "NOT_FOUND")
    safety = _restore_from(source, name)
    return ok(safety_copy=safety.name,
              message="Database restored. A copy of the previous data was saved first.")


@bp.get("/api/admin/export/employees")
@require("backup.manage")
def export_employees():
    conn = get_db()
    settings = get_settings(conn)
    rows = [employee_out(r) for r in conn.execute("SELECT * FROM employees ORDER BY employee_id")]
    if request.args.get("format") == "json":
        return attachment(json.dumps({"employees": rows}, indent=2), "timora-employees.json", "application/json")
    return attachment(rows_to_csv(EMPLOYEE_COLUMNS, rows, business_tz(settings)),
                      "timora-employees.csv", "text/csv; charset=utf-8")


@bp.get("/api/admin/export/attendance")
@require("backup.manage")
def export_attendance():
    conn = get_db()
    settings = get_settings(conn)
    rows = fetch_records(conn, {})
    if request.args.get("format") == "json":
        return attachment(json.dumps({"timezone": settings["timezone"], "attendance": rows}, indent=2),
                          "timora-attendance.json", "application/json")
    return attachment(rows_to_csv(REPORTS["daily"][1], rows, business_tz(settings)),
                      "timora-attendance.csv", "text/csv; charset=utf-8")


@bp.get("/api/admin/export/config")
@require("backup.manage")
def export_config():
    conn = get_db()
    settings = get_settings(conn)
    shifts = [dict(r) for r in conn.execute(
        "SELECT name, code, start_time, end_time, working_days, grace_minutes, break_minutes, "
        "early_clock_in_minutes, late_clock_out_minutes, is_flexible, status FROM shifts")]
    roles = [{"name": r["name"], "permissions": json.loads(r["permissions"])}
             for r in conn.execute("SELECT name, permissions FROM roles")]
    holidays = [dict(r) for r in conn.execute("SELECT date, name, type FROM holidays ORDER BY date")]
    return attachment(
        json.dumps({"settings": editable_settings(settings), "shifts": shifts, "roles": roles,
                    "holidays": holidays}, indent=2),
        "timora-config.json", "application/json")
