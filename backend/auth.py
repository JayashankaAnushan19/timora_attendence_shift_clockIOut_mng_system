"""Administrator login, sessions, role-based access control and user management."""
import json
from datetime import timedelta
from functools import wraps

from flask import Blueprint, g, session
from werkzeug.security import check_password_hash, generate_password_hash

from . import timeutil
from .api import ApiError, get_int, get_str, json_body, ok, page_args
from .database import ALL_PERMISSIONS, SUPER_ADMIN, audit, get_db, transaction

bp = Blueprint("auth", __name__)

MIN_PASSWORD_LENGTH = 8
# Compared against when the username is unknown, so response time doesn't reveal it.
_DUMMY_HASH = generate_password_hash("timora-placeholder")


def _settings(conn):
    from .settings import get_settings
    return get_settings(conn)


def _load_admin(conn, admin_id):
    row = conn.execute(
        "SELECT a.*, r.name AS role_name, r.permissions AS role_permissions "
        "FROM administrators a JOIN roles r ON r.id = a.role_id WHERE a.id = ?",
        (admin_id,),
    ).fetchone()
    if row is None:
        return None
    admin = dict(row)
    admin["permissions"] = set(json.loads(admin.pop("role_permissions")))
    return admin


def current_admin():
    """The logged-in administrator, or None. Applies the idle session timeout."""
    if "admin" in g:
        return g.admin
    g.admin = None
    admin_id = session.get("admin_id")
    if not admin_id:
        return None
    conn = get_db()
    admin = _load_admin(conn, admin_id)
    if (admin is None or not admin["is_active"]
            or admin["session_epoch"] != session.get("epoch")):
        session.clear()
        return None
    now = timeutil.now_utc()
    timeout = timedelta(minutes=_settings(conn)["session_timeout_minutes"])
    last_seen = timeutil.parse_iso(session.get("last_seen"))
    if last_seen is None or now - last_seen > timeout:
        session.clear()
        return None
    # Rewriting the cookie once a minute is enough for an idle timeout.
    if now - last_seen > timedelta(seconds=60):
        session["last_seen"] = timeutil.to_iso(now)
    g.admin = admin
    return admin


def require(*permissions, allow_password_change_pending=False):
    """Protect an API route: login required, plus every listed permission."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            admin = current_admin()
            if admin is None:
                raise ApiError("Please log in.", 401, "AUTH_REQUIRED")
            if admin["must_change_password"] and not allow_password_change_pending:
                raise ApiError("You must change your password first.", 403,
                               "PASSWORD_CHANGE_REQUIRED")
            missing = [p for p in permissions if p not in admin["permissions"]]
            if missing:
                raise ApiError("You do not have permission to do that.", 403, "FORBIDDEN")
            return view(*args, **kwargs)
        return wrapper
    return decorator


def admin_public(admin):
    return {
        "id": admin["id"],
        "username": admin["username"],
        "full_name": admin["full_name"],
        "role": admin["role_name"],
        "permissions": sorted(admin["permissions"]),
        "must_change_password": bool(admin["must_change_password"]),
    }


def check_password_strength(password):
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise ApiError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
                       code="WEAK_PASSWORD")
    if len(password) > 200:
        raise ApiError("Password is too long.", code="WEAK_PASSWORD")
    return password


@bp.post("/api/auth/login")
def login():
    data = json_body()
    username = get_str(data, "username", "Username", required=True, max_len=80)
    password = data.get("password")
    if not isinstance(password, str) or not password:
        raise ApiError("Password is required.", code="VALIDATION_ERROR")

    conn = get_db()
    settings = _settings(conn)
    invalid = ApiError("Incorrect username or password.", 401, "INVALID_CREDENTIALS")
    row = conn.execute(
        "SELECT id, password_hash, is_active, failed_attempts, locked_until, username "
        "FROM administrators WHERE username = ?", (username,),
    ).fetchone()
    if row is None:
        check_password_hash(_DUMMY_HASH, password)
        raise invalid

    now = timeutil.now_utc()
    locked_until = timeutil.parse_iso(row["locked_until"])
    if locked_until and locked_until > now:
        raise ApiError("Too many failed attempts. Please try again later.", 429, "ACCOUNT_LOCKED")

    if not check_password_hash(row["password_hash"], password) or not row["is_active"]:
        with transaction(conn):
            attempts = row["failed_attempts"] + 1
            lock = None
            if attempts >= settings["login_max_attempts"]:
                lock = timeutil.to_iso(now + timedelta(minutes=settings["login_lockout_minutes"]))
                attempts = 0
            conn.execute("UPDATE administrators SET failed_attempts = ?, locked_until = ? "
                         "WHERE id = ?", (attempts, lock, row["id"]))
            audit(conn, "auth.login_failed", "administrator", row["id"],
                  {"locked": bool(lock)}, actor={"id": row["id"], "username": row["username"]})
        raise invalid

    with transaction(conn):
        conn.execute("UPDATE administrators SET failed_attempts = 0, locked_until = NULL, "
                     "last_login_at = ? WHERE id = ?", (timeutil.to_iso(now), row["id"]))
        audit(conn, "auth.login", "administrator", row["id"],
              actor={"id": row["id"], "username": row["username"]})

    admin = _load_admin(conn, row["id"])
    session.clear()
    session["admin_id"] = admin["id"]
    session["epoch"] = admin["session_epoch"]
    session["last_seen"] = timeutil.to_iso(now)
    g.admin = admin
    return ok(admin=admin_public(admin), message="Logged in.")


@bp.post("/api/auth/logout")
def logout():
    admin = current_admin()
    if admin:
        conn = get_db()
        with transaction(conn):
            audit(conn, "auth.logout", "administrator", admin["id"])
    session.clear()
    return ok(message="Logged out.")


@bp.post("/api/auth/change-password")
@require(allow_password_change_pending=True)
def change_password():
    data = json_body()
    current = data.get("current_password")
    new = check_password_strength(data.get("new_password"))
    if not isinstance(current, str) or not check_password_hash(g.admin["password_hash"], current):
        raise ApiError("Your current password is incorrect.", 400, "INVALID_CREDENTIALS")
    if current == new:
        raise ApiError("Choose a different password from your current one.", code="WEAK_PASSWORD")
    conn = get_db()
    with transaction(conn):
        conn.execute(
            "UPDATE administrators SET password_hash = ?, must_change_password = 0, "
            "session_epoch = session_epoch + 1, updated_at = ? WHERE id = ?",
            (generate_password_hash(new), timeutil.to_iso(timeutil.now_utc()), g.admin["id"]),
        )
        audit(conn, "auth.change_password", "administrator", g.admin["id"])
    session["epoch"] = g.admin["session_epoch"] + 1
    return ok(message="Password changed.")


@bp.get("/api/admin/me")
@require(allow_password_change_pending=True)
def me():
    from .settings import get_settings
    settings = get_settings(get_db())
    return ok(
        admin=admin_public(g.admin),
        display={
            "app_name": settings["app_name"],
            "company_name": settings["company_name"],
            "timezone": settings["timezone"],
            "date_format": settings["date_format"],
            "time_format": settings["time_format"],
            "default_working_days": settings["default_working_days"],
            "default_grace_minutes": settings["default_grace_minutes"],
            "default_early_clock_in_minutes": settings["default_early_clock_in_minutes"],
            "default_late_clock_out_minutes": settings["default_late_clock_out_minutes"],
        },
    )


# --- Administrator accounts ------------------------------------------------

def _role(conn, role_id):
    row = conn.execute("SELECT * FROM roles WHERE id = ?", (role_id,)).fetchone()
    if row is None:
        raise ApiError("That role does not exist.", code="VALIDATION_ERROR", field="role_id")
    return row


def _guard_role_grant(role):
    """An administrator cannot hand out more access than they hold themselves."""
    if not set(json.loads(role["permissions"])) <= g.admin["permissions"]:
        raise ApiError("You cannot assign a role with more access than your own.", 403, "FORBIDDEN")


def _guard_target(conn, user_id):
    target = _load_admin(conn, user_id)
    if target is None:
        raise ApiError("Administrator not found.", 404, "NOT_FOUND")
    if not target["permissions"] <= g.admin["permissions"]:
        raise ApiError("You cannot change an account with more access than your own.", 403,
                       "FORBIDDEN")
    return target


def _ensure_other_super_admin(conn, target):
    if target["role_name"] != SUPER_ADMIN:
        return
    remaining = conn.execute(
        "SELECT COUNT(*) FROM administrators a JOIN roles r ON r.id = a.role_id "
        "WHERE r.name = ? AND a.is_active = 1 AND a.id <> ?", (SUPER_ADMIN, target["id"]),
    ).fetchone()[0]
    if remaining == 0:
        raise ApiError("At least one active Super Admin must remain.", 409, "LAST_SUPER_ADMIN")


def _user_row(row):
    return {
        "id": row["id"],
        "username": row["username"],
        "full_name": row["full_name"],
        "role_id": row["role_id"],
        "role": row["role_name"],
        "is_active": bool(row["is_active"]),
        "must_change_password": bool(row["must_change_password"]),
        "last_login_at": row["last_login_at"],
        "created_at": row["created_at"],
    }


@bp.get("/api/admin/users")
@require("users.manage")
def list_users():
    rows = get_db().execute(
        "SELECT a.*, r.name AS role_name FROM administrators a "
        "JOIN roles r ON r.id = a.role_id ORDER BY a.username"
    ).fetchall()
    return ok(items=[_user_row(r) for r in rows])


@bp.post("/api/admin/users")
@require("users.manage")
def create_user():
    data = json_body()
    username = get_str(data, "username", "Username", required=True, max_len=60)
    full_name = get_str(data, "full_name", "Full name", max_len=120)
    password = check_password_strength(data.get("password"))
    conn = get_db()
    with transaction(conn):
        role = _role(conn, get_int(data, "role_id", "Role", 1, 10**9))
        _guard_role_grant(role)
        if conn.execute("SELECT 1 FROM administrators WHERE username = ?", (username,)).fetchone():
            raise ApiError("That username is already taken.", 409, "USERNAME_TAKEN", field="username")
        now = timeutil.to_iso(timeutil.now_utc())
        cursor = conn.execute(
            "INSERT INTO administrators(username, full_name, password_hash, role_id, "
            "must_change_password, created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
            (username, full_name, generate_password_hash(password), role["id"], now, now),
        )
        audit(conn, "user.create", "administrator", cursor.lastrowid,
              {"username": username, "role": role["name"]})
    return ok(201, id=cursor.lastrowid, message="Administrator created.")


@bp.put("/api/admin/users/<int:user_id>")
@require("users.manage")
def update_user(user_id):
    data = json_body()
    conn = get_db()
    with transaction(conn):
        target = _guard_target(conn, user_id)
        full_name = get_str(data, "full_name", "Full name", max_len=120, default=target["full_name"])
        role = _role(conn, get_int(data, "role_id", "Role", 1, 10**9, default=target["role_id"]))
        _guard_role_grant(role)
        if role["id"] != target["role_id"]:
            _ensure_other_super_admin(conn, target)
        conn.execute("UPDATE administrators SET full_name = ?, role_id = ?, updated_at = ? "
                     "WHERE id = ?",
                     (full_name, role["id"], timeutil.to_iso(timeutil.now_utc()), user_id))
        audit(conn, "user.update", "administrator", user_id, {
            "full_name": {"from": target["full_name"], "to": full_name},
            "role": {"from": target["role_name"], "to": role["name"]},
        })
    return ok(message="Administrator updated.")


@bp.post("/api/admin/users/<int:user_id>/status")
@require("users.manage")
def set_user_status(user_id):
    data = json_body()
    active = data.get("is_active")
    if not isinstance(active, bool):
        raise ApiError("is_active must be true or false.", code="VALIDATION_ERROR")
    conn = get_db()
    with transaction(conn):
        target = _guard_target(conn, user_id)
        if not active:
            if user_id == g.admin["id"]:
                raise ApiError("You cannot deactivate your own account.", 409, "SELF_DEACTIVATE")
            _ensure_other_super_admin(conn, target)
        conn.execute("UPDATE administrators SET is_active = ?, failed_attempts = 0, "
                     "locked_until = NULL, updated_at = ? WHERE id = ?",
                     (int(active), timeutil.to_iso(timeutil.now_utc()), user_id))
        audit(conn, "user.activate" if active else "user.deactivate", "administrator", user_id,
              {"username": target["username"]})
    return ok(message="Administrator activated." if active else "Administrator deactivated.")


@bp.post("/api/admin/users/<int:user_id>/reset-password")
@require("users.manage")
def reset_user_password(user_id):
    password = check_password_strength(json_body().get("password"))
    conn = get_db()
    with transaction(conn):
        target = _guard_target(conn, user_id)
        conn.execute(
            "UPDATE administrators SET password_hash = ?, must_change_password = 1, "
            "session_epoch = session_epoch + 1, failed_attempts = 0, locked_until = NULL, "
            "updated_at = ? WHERE id = ?",
            (generate_password_hash(password), timeutil.to_iso(timeutil.now_utc()), user_id),
        )
        audit(conn, "user.reset_password", "administrator", user_id,
              {"username": target["username"]})
    if user_id == g.admin["id"]:
        session.clear()
    return ok(message="Password reset. The user must choose a new one at next login.")


# --- Roles and permissions -------------------------------------------------

@bp.get("/api/admin/roles")
@require("users.manage")
def list_roles():
    rows = get_db().execute("SELECT * FROM roles ORDER BY id").fetchall()
    return ok(
        all_permissions=ALL_PERMISSIONS,
        items=[{
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "permissions": json.loads(r["permissions"]),
            "locked": r["name"] == SUPER_ADMIN,
        } for r in rows],
    )


@bp.put("/api/admin/roles/<int:role_id>")
@require("users.manage")
def update_role(role_id):
    permissions = json_body().get("permissions")
    if not isinstance(permissions, list) or not all(p in ALL_PERMISSIONS for p in permissions):
        raise ApiError("Permissions must come from the known list.", code="VALIDATION_ERROR")
    conn = get_db()
    with transaction(conn):
        role = _role(conn, role_id)
        if role["name"] == SUPER_ADMIN:
            raise ApiError("The Super Admin role always has full access.", 409, "ROLE_LOCKED")
        if not set(permissions) <= g.admin["permissions"]:
            raise ApiError("You cannot grant access you do not hold.", 403, "FORBIDDEN")
        conn.execute("UPDATE roles SET permissions = ? WHERE id = ?",
                     (json.dumps(sorted(set(permissions))), role_id))
        audit(conn, "role.update", "role", role_id, {
            "role": role["name"],
            "from": json.loads(role["permissions"]),
            "to": sorted(set(permissions)),
        })
    return ok(message="Role permissions updated.")


@bp.get("/api/admin/audit")
@require("audit.view")
def list_audit():
    page, per_page = page_args(default_per_page=50)
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
    rows = conn.execute(
        "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ? OFFSET ?",
        (per_page, (page - 1) * per_page),
    ).fetchall()
    return ok(items=[dict(r) for r in rows], total=total, page=page, per_page=per_page)
