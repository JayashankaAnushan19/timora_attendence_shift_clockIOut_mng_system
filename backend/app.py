"""Flask application factory: pages, public time endpoint, errors and headers."""
import sqlite3

from flask import Flask, abort, jsonify, redirect, request, send_from_directory
from werkzeug.exceptions import HTTPException

from . import attendance, auth, backup, dashboard, employees, overtime, reports, settings, shifts, timeutil
from .api import ApiError, ok
from .config import FRONTEND_DIR, load_config, load_secret_key
from .database import close_db, get_db, init_db

ADMIN_PAGES = {
    "dashboard": "dashboard.view",
    "employees": "employees.view",
    "employee": "employees.view",
    "shifts": "shifts.view",
    "assignments": "shifts.view",
    "attendance": "attendance.view",
    "overtime": "overtime.manage",
    "reports": "reports.view",
    "users": "users.manage",
    "settings": "settings.manage",
    "backup": "backup.manage",
}
ASSET_DIRS = {"css", "js", "img"}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
}


def create_app(overrides=None):
    cfg = load_config(overrides)
    app = Flask(__name__, static_folder=None)
    app.config["TIMORA"] = cfg
    app.config["SECRET_KEY"] = load_secret_key(cfg["database"])
    app.config["SESSION_COOKIE_NAME"] = "timora_session"
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["MAX_CONTENT_LENGTH"] = cfg["max_upload_mb"] * 1024 * 1024
    app.json.sort_keys = False

    init_db(cfg["database"])
    app.teardown_appcontext(close_db)

    for module in (auth, settings, attendance, employees, shifts, overtime, dashboard, reports, backup):
        app.register_blueprint(module.bp)

    _register_guards(app)
    _register_errors(app)
    _register_pages(app)
    return app


def _register_guards(app):
    @app.before_request
    def require_ajax_header_for_changes():
        # Browsers won't let another site add a custom header without CORS
        # approval, which Timora never grants. This blocks cross-site form posts.
        if request.method in ("POST", "PUT", "DELETE", "PATCH") and (
                request.path.startswith("/api/admin/") or request.path.startswith("/api/auth/")):
            if request.headers.get("X-Timora-Request") != "1":
                raise ApiError("This request was blocked.", 403, "CSRF_BLOCKED")

    @app.after_request
    def security_headers(response):
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.path.startswith("/api/") and "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        return response


def _register_errors(app):
    @app.errorhandler(ApiError)
    def handle_api_error(error):
        return jsonify(success=False, code=error.code, message=error.message, **error.extra), error.status

    @app.errorhandler(sqlite3.OperationalError)
    def handle_db_busy(error):
        app.logger.exception("Database error: %s", error)
        return jsonify(success=False, code="DATABASE_BUSY",
                       message="The system is busy. Nothing was saved. Please try again."), 503

    @app.errorhandler(HTTPException)
    def handle_http(error):
        if request.path.startswith("/api/"):
            messages = {404: "Not found.", 405: "Method not allowed.",
                        413: "That file is too large."}
            return jsonify(success=False, code=error.name.upper().replace(" ", "_"),
                           message=messages.get(error.code, error.description)), error.code
        return error

    @app.errorhandler(Exception)
    def handle_unexpected(error):
        app.logger.exception("Unhandled error: %s", error)
        return jsonify(success=False, code="SERVER_ERROR",
                       message="Something went wrong on the server. Nothing was confirmed; please try again."), 500


def _register_pages(app):
    def page(name):
        return send_from_directory(FRONTEND_DIR, name, max_age=0)

    @app.get("/")
    def terminal():
        return page("index.html")

    @app.get("/assets/<kind>/<path:filename>")
    def assets(kind, filename):
        if kind not in ASSET_DIRS:
            abort(404)
        return send_from_directory(FRONTEND_DIR / kind, filename, max_age=0)

    @app.get("/favicon.ico")
    def favicon():
        return send_from_directory(FRONTEND_DIR / "img", "favicon.svg", mimetype="image/svg+xml")

    @app.get("/api/public/time")
    def server_time():
        now = timeutil.now_utc()
        conn = get_db()
        return ok(utc=timeutil.to_iso(now), timezone=settings.get_settings(conn)["timezone"])

    @app.get("/admin/login")
    def login_page():
        admin = auth.current_admin()
        if admin and not admin["must_change_password"]:
            return redirect("/admin")
        return page("admin-login.html")

    @app.get("/admin")
    @app.get("/admin/")
    def admin_home():
        admin = auth.current_admin()
        if admin is None or admin["must_change_password"]:
            return redirect("/admin/login")
        for name, permission in ADMIN_PAGES.items():
            if permission in admin["permissions"]:
                return redirect(f"/admin/{name}")
        return page("no-access.html")

    @app.get("/admin/<name>")
    def admin_page(name):
        permission = ADMIN_PAGES.get(name)
        if permission is None:
            abort(404)
        admin = auth.current_admin()
        if admin is None or admin["must_change_password"]:
            return redirect("/admin/login")
        if permission not in admin["permissions"]:
            return redirect("/admin")
        return page(f"{name}.html")
