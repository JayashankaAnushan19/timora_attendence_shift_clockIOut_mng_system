"""Monthly salary: basic pay, allowances, deductions, overtime and printable slips."""
import calendar
import json
import re
from datetime import date

from flask import Blueprint, g, request

from . import timeutil
from .api import ApiError, get_enum, get_money, get_str, json_body, like_escape, ok
from .attendance import build_roster, close_stale_sessions
from .auth import require
from .database import audit, get_db, transaction
from .settings import business_tz, get_settings

bp = Blueprint("payroll", __name__)

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def parse_month(value):
    if not isinstance(value, str) or not MONTH_RE.match(value):
        raise ApiError("Choose a valid month.", code="VALIDATION_ERROR", field="month")
    return value


def _month_range(month):
    year, number = map(int, month.split("-"))
    return date(year, number, 1).isoformat(), date(year, number, calendar.monthrange(year, number)[1]).isoformat()


def _previous_month(month):
    year, number = map(int, month.split("-"))
    return f"{year - 1}-12" if number == 1 else f"{year}-{number - 1:02d}"


def _money(cents):
    return cents / 100


def _find_employee(conn, code):
    row = conn.execute("SELECT * FROM employees WHERE employee_id = ? COLLATE NOCASE", (code,)).fetchone()
    if row is None:
        raise ApiError("Employee not found.", 404, "EMPLOYEE_NOT_FOUND")
    return row


def _items_for_month(conn, employee_pk, month):
    return conn.execute(
        "SELECT * FROM salary_items WHERE employee_pk = ? AND status = 'active' AND start_month <= ? "
        "AND (end_month IS NULL OR end_month >= ?) ORDER BY id",
        (employee_pk, month, month),
    ).fetchall()


def _overtime_lines(conn, employee_pk, month):
    """One line per pay rate, so the slip shows exactly how the amount was reached."""
    start, end = _month_range(month)
    groups = {}
    for entry in conn.execute(
        "SELECT minutes, rate_cents FROM overtime_entries WHERE employee_pk = ? AND status = 'active' "
        "AND work_date BETWEEN ? AND ?", (employee_pk, start, end),
    ):
        group = groups.setdefault(entry["rate_cents"], {"minutes": 0, "cents": 0})
        group["minutes"] += entry["minutes"]
        group["cents"] += round(entry["minutes"] * entry["rate_cents"] / 60)
    return [{"rate_cents": rate, **group} for rate, group in sorted(groups.items())]


def _attendance_summary(conn, employee, month, settings, now):
    start, end = _month_range(month)
    today = timeutil.local_date(now, business_tz(settings)).isoformat()
    summary = {"present": 0, "absent": 0, "leave": 0, "late": 0, "worked_seconds": 0}
    if start > today:
        return summary
    roster = build_roster(conn, start, min(end, today), settings, now, {"employee": employee["employee_id"]})
    for row in roster:
        summary["present"] += 1 if row["clock_in"] else 0
        summary["absent"] += 1 if row["status"] == "Absent" else 0
        summary["leave"] += 1 if row["status"] == "Leave" else 0
        summary["late"] += 1 if row["late_seconds"] else 0
        summary["worked_seconds"] += row["working_seconds"]
    return summary


def compute_slip(conn, employee, month, settings, now, with_attendance=True):
    basic = employee["basic_salary_cents"]
    items = _items_for_month(conn, employee["id"], month)
    overtime = _overtime_lines(conn, employee["id"], month)
    allowances = [i for i in items if i["kind"] == "allowance"]
    deductions = [i for i in items if i["kind"] == "deduction"]

    allowance_total = sum(i["amount_cents"] for i in allowances)
    overtime_total = sum(o["cents"] for o in overtime)
    deduction_total = sum(i["amount_cents"] for i in deductions)
    gross = basic + allowance_total + overtime_total

    earnings = [{"label": "Basic Salary", "amount": _money(basic)}]
    earnings += [{"label": i["name"], "amount": _money(i["amount_cents"])} for i in allowances]
    earnings += [{"label": "Overtime", "minutes": o["minutes"], "rate": _money(o["rate_cents"]),
                  "amount": _money(o["cents"])} for o in overtime]
    return {
        "month": month,
        "employee": {key: employee[key] for key in (
            "employee_id", "name", "employee_type", "designation", "department", "date_of_joining")},
        "earnings": earnings,
        "deductions": [{"label": i["name"], "amount": _money(i["amount_cents"])} for i in deductions],
        "totals": {
            "basic": _money(basic),
            "allowances": _money(allowance_total),
            "overtime": _money(overtime_total),
            "gross": _money(gross),
            "deductions": _money(deduction_total),
            "net": _money(gross - deduction_total),
        },
        "overtime_seconds": sum(o["minutes"] for o in overtime) * 60,
        "attendance": _attendance_summary(conn, employee, month, settings, now) if with_attendance else None,
    }


def _final_slip(conn, employee_pk, month):
    return conn.execute("SELECT * FROM salary_slips WHERE employee_pk = ? AND month = ?",
                        (employee_pk, month)).fetchone()


def _slip_for(conn, employee, month, settings, now):
    """A finalized slip stays exactly as it was; otherwise it reflects current figures."""
    final = _final_slip(conn, employee["id"], month)
    if final:
        slip = json.loads(final["data"])
        slip.update(status="final", finalized_by=final["finalized_by"], finalized_at=final["finalized_at"])
        return slip
    close_stale_sessions(conn, settings, now, employee["id"])
    return {**compute_slip(conn, employee, month, settings, now), "status": "draft"}


def _guard_not_final(conn, employee, month):
    if _final_slip(conn, employee["id"], month):
        raise ApiError(f"The {month} slip for {employee['name']} is finalized. Reopen it to make changes.",
                       409, "SLIP_FINALIZED")


@bp.get("/api/admin/payroll")
@require("payroll.manage")
def monthly_salaries():
    conn = get_db()
    settings = get_settings(conn)
    now = timeutil.now_utc()
    month = parse_month(request.args.get("month"))
    start, end = _month_range(month)
    where = ["date_of_joining <= ?", "(status = 'active' OR deactivated_on >= ?)"]
    params = [end, start]
    query = request.args.get("q", "").strip()
    if query:
        pattern = "%" + like_escape(query) + "%"
        where.append("(employee_id LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\')")
        params += [pattern, pattern]
    employees = conn.execute(
        "SELECT * FROM employees WHERE " + " AND ".join(where) + " ORDER BY name COLLATE NOCASE", params,
    ).fetchall()
    finals = {r["employee_pk"]: json.loads(r["data"]) for r in conn.execute(
        "SELECT employee_pk, data FROM salary_slips WHERE month = ?", (month,))}

    items = []
    totals = dict.fromkeys(("basic", "allowances", "overtime", "gross", "deductions", "net"), 0)
    for employee in employees:
        final = finals.get(employee["id"])
        slip = final or compute_slip(conn, employee, month, settings, now, with_attendance=False)
        for key in totals:
            totals[key] = round(totals[key] + slip["totals"][key], 2)
        items.append({
            "employee_id": employee["employee_id"], "name": employee["name"],
            "employee_type": employee["employee_type"], "status": "final" if final else "draft",
            **slip["totals"],
        })
    return ok(month=month, items=items, total=len(items), totals=totals)


def _detail(conn, employee, month, settings):
    return {
        "slip": _slip_for(conn, employee, month, settings, timeutil.now_utc()),
        "basic_salary": _money(employee["basic_salary_cents"]),
        "ot_rate": _money(employee["ot_rate_cents"]),
        "items": [{
            "id": i["id"], "kind": i["kind"], "name": i["name"], "amount": _money(i["amount_cents"]),
            "repeats": i["end_month"] is None, "start_month": i["start_month"],
        } for i in _items_for_month(conn, employee["id"], month)],
    }


@bp.get("/api/admin/payroll/<code>")
@require("payroll.manage")
def salary_detail(code):
    conn = get_db()
    settings = get_settings(conn)
    employee = _find_employee(conn, code)
    month = parse_month(request.args.get("month"))
    company = {key: settings[f"company_{key}"] for key in (
        "name", "tagline", "address", "phone", "email", "website", "registration")}
    return ok(company=company, logo_rev=settings.get("logo_rev"), currency=settings["currency"],
              **_detail(conn, employee, month, settings))


@bp.put("/api/admin/payroll/<code>/pay")
@require("payroll.manage")
def set_pay(code):
    data = json_body()
    conn = get_db()
    with transaction(conn):
        employee = _find_employee(conn, code)
        basic = get_money(data, "basic_salary", "Basic salary", default=employee["basic_salary_cents"])
        rate = get_money(data, "ot_rate", "OT pay rate", default=employee["ot_rate_cents"])
        conn.execute("UPDATE employees SET basic_salary_cents = ?, ot_rate_cents = ?, updated_at = ? WHERE id = ?",
                     (basic, rate, timeutil.to_iso(timeutil.now_utc()), employee["id"]))
        # The audit log notes that pay changed, without keeping the amounts in it.
        audit(conn, "payroll.pay_update", "employee", employee["employee_id"])
    return ok(message="Pay details saved. Finalized slips keep their original amounts.")


@bp.post("/api/admin/payroll/<code>/items")
@require("payroll.manage")
def add_item(code):
    data = json_body()
    kind = get_enum(data, "kind", ("allowance", "deduction"), "Type")
    name = get_str(data, "name", "Name", required=True, max_len=80)
    amount = get_money(data, "amount", "Amount", default=None)
    if not amount:
        raise ApiError("Amount must be more than 0.", code="VALIDATION_ERROR", field="amount")
    month = parse_month(data.get("month"))
    repeats = data.get("repeats") is True
    conn = get_db()
    with transaction(conn):
        employee = _find_employee(conn, code)
        _guard_not_final(conn, employee, month)
        cursor = conn.execute(
            "INSERT INTO salary_items(employee_pk, kind, name, amount_cents, start_month, end_month, created_by, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (employee["id"], kind, name, amount, month, None if repeats else month, g.admin["username"],
             timeutil.to_iso(timeutil.now_utc())),
        )
        audit(conn, f"payroll.{kind}_add", "employee", employee["employee_id"],
              {"name": name, "month": month, "repeats": repeats})
    label = "Allowance" if kind == "allowance" else "Deduction"
    return ok(201, id=cursor.lastrowid, message=f"{label} added.")


@bp.post("/api/admin/payroll/items/<int:item_id>/remove")
@require("payroll.manage")
def remove_item(item_id):
    """Remove from the chosen month onwards. Earlier months keep a repeating item."""
    month = parse_month(json_body().get("month"))
    conn = get_db()
    with transaction(conn):
        item = conn.execute("SELECT * FROM salary_items WHERE id = ? AND status = 'active'", (item_id,)).fetchone()
        if item is None:
            raise ApiError("Item not found.", 404, "NOT_FOUND")
        employee = conn.execute("SELECT * FROM employees WHERE id = ?", (item["employee_pk"],)).fetchone()
        _guard_not_final(conn, employee, month)
        if item["start_month"] >= month:
            conn.execute("UPDATE salary_items SET status = 'removed' WHERE id = ?", (item_id,))
        else:
            conn.execute("UPDATE salary_items SET end_month = ? WHERE id = ?", (_previous_month(month), item_id))
        audit(conn, "payroll.item_remove", "employee", employee["employee_id"],
              {"name": item["name"], "from_month": month})
    return ok(message="Item removed.")


@bp.post("/api/admin/payroll/<code>/finalize")
@require("payroll.manage")
def finalize(code):
    month = parse_month(json_body().get("month"))
    conn = get_db()
    settings = get_settings(conn)
    now = timeutil.now_utc()
    with transaction(conn):
        employee = _find_employee(conn, code)
        _guard_not_final(conn, employee, month)
        close_stale_sessions(conn, settings, now, employee["id"])
        slip = compute_slip(conn, employee, month, settings, now)
        conn.execute(
            "INSERT INTO salary_slips(employee_pk, month, data, finalized_by, finalized_at) VALUES (?, ?, ?, ?, ?)",
            (employee["id"], month, json.dumps(slip), g.admin["username"], timeutil.to_iso(now)),
        )
        audit(conn, "payroll.finalize", "employee", employee["employee_id"], {"month": month})
    return ok(message="Salary slip finalized. Its amounts will not change.")


@bp.post("/api/admin/payroll/<code>/reopen")
@require("payroll.manage")
def reopen(code):
    month = parse_month(json_body().get("month"))
    conn = get_db()
    with transaction(conn):
        employee = _find_employee(conn, code)
        cursor = conn.execute("DELETE FROM salary_slips WHERE employee_pk = ? AND month = ?", (employee["id"], month))
        if cursor.rowcount == 0:
            raise ApiError("That slip is not finalized.", 409, "SLIP_NOT_FINAL")
        audit(conn, "payroll.reopen", "employee", employee["employee_id"], {"month": month})
    return ok(message="Salary slip reopened. It now shows current figures.")
