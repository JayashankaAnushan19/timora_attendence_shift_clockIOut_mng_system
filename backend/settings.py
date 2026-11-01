"""System settings: schema, validation, admin API and the public display config."""
import base64
import json
import re
from zoneinfo import available_timezones

from flask import Blueprint, Response, g, has_request_context, request

from . import timeutil
from .api import ApiError, json_body, ok
from .auth import require
from .database import audit, get_db, transaction

bp = Blueprint("settings", __name__)

DATE_FORMATS = ["long", "DD/MM/YYYY", "MM/DD/YYYY", "YYYY-MM-DD"]
MAX_EMPLOYEES = 999
LOGO_TYPES = {
    "image/png": b"\x89PNG",
    "image/jpeg": b"\xff\xd8\xff",
    "image/webp": b"RIFF",
}
MAX_LOGO_BYTES = 512 * 1024


def _timezone(value):
    if not isinstance(value, str) or value not in available_timezones():
        raise ValueError("is not a known timezone")
    return value


def _days(value):
    if (not isinstance(value, list) or not value
            or not all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in value)):
        raise ValueError("must list at least one weekday")
    return sorted(set(value))


def _text(max_len):
    def check(value):
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_len:
            raise ValueError(f"must be 1-{max_len} characters")
        return value.strip()
    return check


def _choice(*options):
    def check(value):
        if value not in options:
            raise ValueError(f"must be one of: {', '.join(options)}")
        return value
    return check


def _number(low, high):
    def check(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
            raise ValueError("must be a whole number")
        if not low <= value <= high:
            raise ValueError(f"must be between {low} and {high}")
        return int(value)
    return check


def _flag(value):
    if not isinstance(value, bool):
        raise ValueError("must be on or off")
    return value


def _type_list(value):
    if not isinstance(value, list) or not value or not all(isinstance(t, str) and t.strip() for t in value):
        raise ValueError("must list at least one type")
    cleaned = [t.strip() for t in value]
    if any(len(t) > 40 for t in cleaned) or len({t.lower() for t in cleaned}) != len(cleaned):
        raise ValueError("must be unique and at most 40 characters each")
    return cleaned


def _currency(value):
    if not isinstance(value, str) or len(value.strip()) > 10:
        raise ValueError("must be at most 10 characters")
    return value.strip()


def _optional_text(max_len):
    def check(value):
        if not isinstance(value, str) or len(value.strip()) > max_len:
            raise ValueError(f"must be at most {max_len} characters")
        return value.strip()
    return check


def _optional_email(value):
    if not isinstance(value, str) or (value.strip() and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value.strip())):
        raise ValueError("is not a valid email address")
    return value.strip()


def _optional_website(value):
    """Only web addresses, because this becomes a clickable link on a public page."""
    if not isinstance(value, str) or len(value.strip()) > 200 or re.search(r"\s", value.strip()):
        raise ValueError("is not a valid web address")
    value = value.strip()
    if not value:
        return value
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:", value) and not re.match(r"^https?://", value, re.I):
        raise ValueError("must start with http:// or https://")
    return value if re.match(r"^https?://", value, re.I) else f"https://{value}"


def _prefix(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{1,5}", value):
        raise ValueError("must be 1-5 capital letters")
    return value


# key -> (default, validator, label)
SCHEMA = {
    # General
    "company_name": ("My Company", _text(100), "Company name"),
    "app_name": ("Timora", _text(40), "Application name"),
    "company_tagline": ("", _optional_text(120), "Company tagline"),
    "company_about": ("", _optional_text(400), "About the company"),
    "company_address": ("", _optional_text(200), "Company address"),
    "company_phone": ("", _optional_text(40), "Company phone"),
    "company_email": ("", _optional_email, "Company email"),
    "company_website": ("", _optional_website, "Company website"),
    "company_registration": ("", _optional_text(60), "Company registration number"),
    "timezone": ("UTC", _timezone, "Business timezone"),
    "date_format": ("long", _choice(*DATE_FORMATS), "Date format"),
    "time_format": ("12", _choice("12", "24"), "Time format"),
    "default_working_days": ([0, 1, 2, 3, 4], _days, "Default working days"),
    # Attendance
    "default_grace_minutes": (10, _number(0, 240), "Default grace period"),
    "default_early_clock_in_minutes": (60, _number(0, 720), "Early clock-in allowance"),
    "default_late_clock_out_minutes": (240, _number(0, 720), "Late clock-out allowance"),
    "allow_unscheduled_clock_in": (False, _flag, "Allow unscheduled clock-in"),
    "allow_multiple_sessions": (False, _flag, "Allow multiple sessions per shift"),
    "duplicate_window_seconds": (60, _number(0, 3600), "Duplicate prevention window"),
    "max_session_hours": (16, _number(1, 48), "Maximum unscheduled session"),
    "half_day_percent": (50, _number(0, 100), "Half-day threshold"),
    "overtime_threshold_minutes": (15, _number(0, 240), "Minimum overtime"),
    "attendance_confirmation_seconds": (8, _number(2, 60), "Attendance confirmation duration"),
    # Employees
    "max_employees": (MAX_EMPLOYEES, _number(1, MAX_EMPLOYEES), "Maximum employee count"),
    "employee_id_prefix": ("EM", _prefix, "Employee ID prefix"),
    "employee_id_start": (1, _number(1, MAX_EMPLOYEES), "Employee ID starting number"),
    "employee_types": (["Technician", "Manager", "Cleaner"], _type_list, "Employee types"),
    "currency": ("", _currency, "Currency"),
    # Display
    "clock_enabled": (True, _flag, "Clock display"),
    "theme": ("light", _choice("light", "dark"), "Screen theme"),
    "success_notification_seconds": (5, _number(1, 30), "Success notification duration"),
    "search_mode": ("contains", _choice("contains", "starts_with"), "Employee search behaviour"),
    "terminal_idle_reset_seconds": (30, _number(5, 600), "Terminal idle reset"),
    # Admin
    "session_timeout_minutes": (30, _number(5, 1440), "Session timeout"),
    "login_max_attempts": (5, _number(1, 50), "Login attempts before lockout"),
    "login_lockout_minutes": (15, _number(1, 1440), "Login lockout duration"),
}

# Not editable through the settings form.
INTERNAL_KEYS = {"next_employee_number", "last_backup_at", "logo_rev", "logo_type", "starter_data_loaded"}


def get_settings(conn):
    """All settings with defaults applied (the logo image itself is excluded)."""
    if has_request_context() and "settings_cache" in g:
        return g.settings_cache
    values = {key: spec[0] for key, spec in SCHEMA.items()}
    for row in conn.execute("SELECT key, value FROM system_settings WHERE key <> 'company_logo'"):
        values[row["key"]] = json.loads(row["value"])
    if has_request_context():
        g.settings_cache = values
    return values


def business_tz(settings):
    return timeutil.zone(settings["timezone"])


def set_setting(conn, key, value, actor_name="system"):
    conn.execute(
        "INSERT INTO system_settings(key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
        "updated_at = excluded.updated_at, updated_by = excluded.updated_by",
        (key, json.dumps(value), timeutil.to_iso(timeutil.now_utc()), actor_name),
    )
    if has_request_context():
        g.pop("settings_cache", None)


def editable_settings(settings):
    return {key: settings[key] for key in SCHEMA}


def public_config(settings):
    """Only what the attendance terminal needs. Safe for unauthenticated use."""
    return {
        "app_name": settings["app_name"],
        "company_name": settings["company_name"],
        "company_profile": {key: settings[f"company_{key}"] for key in (
            "tagline", "about", "address", "phone", "email", "website", "registration")},
        "timezone": settings["timezone"],
        "date_format": settings["date_format"],
        "time_format": settings["time_format"],
        "clock_enabled": settings["clock_enabled"],
        "theme": settings["theme"],
        "success_notification_seconds": settings["success_notification_seconds"],
        "attendance_confirmation_seconds": settings["attendance_confirmation_seconds"],
        "terminal_idle_reset_seconds": settings["terminal_idle_reset_seconds"],
        "logo_rev": settings.get("logo_rev"),
    }


@bp.get("/api/public/config")
def config_endpoint():
    return ok(config=public_config(get_settings(get_db())))


@bp.get("/api/public/logo")
def logo():
    conn = get_db()
    row = conn.execute("SELECT value FROM system_settings WHERE key = 'company_logo'").fetchone()
    if not row:
        return Response(status=404)
    settings = get_settings(conn)
    response = Response(base64.b64decode(json.loads(row["value"])),
                        mimetype=settings.get("logo_type", "image/png"))
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@bp.get("/api/admin/settings")
@require("settings.manage")
def read_settings():
    settings = get_settings(get_db())
    return ok(settings=editable_settings(settings), logo_rev=settings.get("logo_rev"))


@bp.put("/api/admin/settings")
@require("settings.manage")
def update_settings():
    data = json_body()
    conn = get_db()
    unknown = sorted(set(data) - set(SCHEMA))
    if unknown:
        raise ApiError(f"Unknown setting: {unknown[0]}", code="VALIDATION_ERROR")
    cleaned = {}
    for key, value in data.items():
        _default, validator, label = SCHEMA[key]
        try:
            cleaned[key] = validator(value)
        except ValueError as exc:
            raise ApiError(f"{label} {exc}.", code="VALIDATION_ERROR", field=key)

    with transaction(conn):
        current = get_settings(conn)
        changes = {key: {"from": current[key], "to": value}
                   for key, value in cleaned.items() if current[key] != value}
        for key in changes:
            set_setting(conn, key, cleaned[key], g.admin["username"])
        if changes:
            audit(conn, "settings.update", "settings", "", changes)
    return ok(settings=editable_settings(get_settings(conn)), changed=sorted(changes),
              message="Settings saved.")


@bp.get("/api/admin/settings/timezones")
@require("settings.manage")
def timezones():
    return ok(timezones=sorted(available_timezones()))


@bp.post("/api/admin/settings/logo")
@require("settings.manage")
def upload_logo():
    upload = request.files.get("logo")
    if upload is None:
        raise ApiError("Choose an image file to upload.", code="VALIDATION_ERROR")
    content = upload.read(MAX_LOGO_BYTES + 1)
    if len(content) > MAX_LOGO_BYTES:
        raise ApiError("The logo must be 512 KB or smaller.", code="VALIDATION_ERROR")
    # Decide the type from the bytes, not the browser's claim. SVG is refused
    # because it can carry script.
    mimetype = next((m for m, magic in LOGO_TYPES.items() if content.startswith(magic)), None)
    if mimetype is None:
        raise ApiError("The logo must be a PNG, JPEG or WebP image.", code="VALIDATION_ERROR")
    conn = get_db()
    rev = timeutil.to_iso(timeutil.now_utc())
    with transaction(conn):
        set_setting(conn, "company_logo", base64.b64encode(content).decode("ascii"),
                    g.admin["username"])
        set_setting(conn, "logo_type", mimetype, g.admin["username"])
        set_setting(conn, "logo_rev", rev, g.admin["username"])
        audit(conn, "settings.logo_upload", "settings", "company_logo", {"bytes": len(content)})
    return ok(logo_rev=rev, message="Logo updated.")


@bp.delete("/api/admin/settings/logo")
@require("settings.manage")
def delete_logo():
    conn = get_db()
    with transaction(conn):
        conn.execute("DELETE FROM system_settings WHERE key IN "
                     "('company_logo', 'logo_type', 'logo_rev')")
        audit(conn, "settings.logo_remove", "settings", "company_logo")
    g.pop("settings_cache", None)
    return ok(message="Logo removed.")
