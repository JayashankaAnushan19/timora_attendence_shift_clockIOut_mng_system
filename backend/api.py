"""Shared JSON response format and input validation.

Success: {"success": true, ...payload}
Failure: {"success": false, "code": "SOME_CODE", "message": "Readable text"}
"""
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import jsonify, request

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[0-9+()\-\s]{5,25}$")
MAX_MONEY = 1_000_000_000


class ApiError(Exception):
    def __init__(self, message, http_status=400, /, code="BAD_REQUEST", **extra):
        super().__init__(message)
        self.message = message
        self.status = http_status
        self.code = code
        self.extra = extra


def ok(http_status=200, /, **payload):
    return jsonify(success=True, **payload), http_status


def json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("Expected a JSON object in the request body.", code="INVALID_JSON")
    return data


def get_str(data, key, label=None, required=False, max_len=200, default=""):
    label = label or key
    value = data.get(key)
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ApiError(f"{label} must be text.", code="VALIDATION_ERROR", field=key)
    value = value.strip()
    if required and not value:
        raise ApiError(f"{label} is required.", code="VALIDATION_ERROR", field=key)
    if len(value) > max_len:
        raise ApiError(f"{label} is too long (maximum {max_len} characters).",
                       code="VALIDATION_ERROR", field=key)
    return value or default


def get_int(data, key, label=None, minimum=0, maximum=100000, default=None):
    label = label or key
    value = data.get(key, default)
    if value in (None, ""):
        if default is None:
            raise ApiError(f"{label} is required.", code="VALIDATION_ERROR", field=key)
        value = default
    if isinstance(value, bool):
        raise ApiError(f"{label} must be a whole number.", code="VALIDATION_ERROR", field=key)
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ApiError(f"{label} must be a whole number.", code="VALIDATION_ERROR", field=key)
    if value < minimum or value > maximum:
        raise ApiError(f"{label} must be between {minimum} and {maximum}.",
                       code="VALIDATION_ERROR", field=key)
    return value


def get_money(data, key, label, default=0):
    """A money amount as a whole number of cents, so totals never pick up rounding errors."""
    value = data.get(key)
    if value in (None, ""):
        return default
    try:
        if isinstance(value, bool):
            raise InvalidOperation
        amount = Decimal(str(value))
        if not amount.is_finite():
            raise InvalidOperation
    except InvalidOperation:
        raise ApiError(f"{label} must be a number.", code="VALIDATION_ERROR", field=key)
    if amount < 0 or amount > MAX_MONEY:
        raise ApiError(f"{label} must be between 0 and {MAX_MONEY:,}.", code="VALIDATION_ERROR", field=key)
    if amount != amount.quantize(Decimal("0.01")):
        raise ApiError(f"{label} can have at most 2 decimal places.", code="VALIDATION_ERROR", field=key)
    return int(amount * 100)


def check_date(value, label="Date"):
    if not isinstance(value, str):
        raise ApiError(f"{label} must be a date (YYYY-MM-DD).", code="VALIDATION_ERROR")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ApiError(f"{label} must be a valid date (YYYY-MM-DD).", code="VALIDATION_ERROR")


def get_date(data, key, label=None, required=False, default=None):
    value = data.get(key)
    if value in (None, ""):
        if required:
            raise ApiError(f"{label or key} is required.", code="VALIDATION_ERROR", field=key)
        return default
    return check_date(value, label or key)


def get_time(data, key, label=None):
    value = data.get(key)
    if not isinstance(value, str) or not TIME_RE.match(value):
        raise ApiError(f"{label or key} must be a time in HH:MM format.",
                       code="VALIDATION_ERROR", field=key)
    return value


def get_enum(data, key, allowed, label=None, default=None):
    value = data.get(key, default)
    if value not in allowed:
        raise ApiError(f"{label or key} must be one of: {', '.join(map(str, allowed))}.",
                       code="VALIDATION_ERROR", field=key)
    return value


def page_args(default_per_page=25, max_per_page=200):
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    per_page = request.args.get("per_page", default_per_page, type=int) or default_per_page
    return page, min(max(per_page, 1), max_per_page)


def paginate_list(items, page, per_page):
    start = (page - 1) * per_page
    return items[start:start + per_page]


def like_escape(text):
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
