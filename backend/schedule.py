"""Who is expected to work when, and what an employee may do right now.

A shift "occurrence" belongs to the business date it starts on, so a 22:00-07:00
shift worked overnight is one attendance day. All comparisons use absolute UTC
datetimes, which keeps overnight shifts and DST changes correct.
"""
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import NamedTuple

from . import timeutil

SCHEDULED = "scheduled"
NO_SHIFT = "no_shift"
HOLIDAY = "holiday"
WEEKLY_OFF = "weekly_off"
LEAVE = "leave"

# Terminal statuses
NOT_CLOCKED_IN = "not_clocked_in"
CLOCKED_IN = "clocked_in"
CLOCKED_OUT = "clocked_out"
NOT_SCHEDULED = "not_scheduled"
SHIFT_NOT_STARTED = "shift_not_started"
SHIFT_COMPLETED = "shift_completed"

STATUS_LABELS = {
    NOT_CLOCKED_IN: "NOT CLOCKED IN",
    CLOCKED_IN: "CLOCKED IN",
    CLOCKED_OUT: "CLOCKED OUT",
    NOT_SCHEDULED: "NOT SCHEDULED",
    SHIFT_NOT_STARTED: "SHIFT NOT STARTED",
    SHIFT_COMPLETED: "SHIFT COMPLETED",
}

STATUS_INDICATORS = {
    NOT_CLOCKED_IN: "blue",
    CLOCKED_IN: "green",
    CLOCKED_OUT: "red",
    NOT_SCHEDULED: "grey",
    SHIFT_NOT_STARTED: "orange",
    SHIFT_COMPLETED: "orange",
}


class Plan(NamedTuple):
    kind: str
    shift: dict = None
    label: str = ""


class Occurrence(NamedTuple):
    date: str
    shift: dict
    start: object
    end: object
    clock_in_opens: object
    clock_out_closes: object


def shift_row(row):
    shift = dict(row)
    shift["working_days"] = json.loads(shift["working_days"])
    return shift


def occurrence_for(shift, day, tz):
    start_day = date.fromisoformat(day)
    end_day = start_day + timedelta(days=1) if shift["end_time"] <= shift["start_time"] else start_day
    start = timeutil.combine_local(start_day, shift["start_time"], tz)
    end = timeutil.combine_local(end_day, shift["end_time"], tz)
    return Occurrence(
        day, shift, start, end,
        start - timedelta(minutes=shift["early_clock_in_minutes"]),
        end + timedelta(minutes=shift["late_clock_out_minutes"]),
    )


class ScheduleIndex:
    """Loads a date range once so whole-company reports don't query per employee/day."""

    def __init__(self, conn, start, end, employee_pk=None):
        self.shifts = {r["id"]: shift_row(r) for r in conn.execute("SELECT * FROM shifts")}
        self.holidays = {
            r["date"]: r for r in conn.execute(
                "SELECT date, name, type FROM holidays WHERE date BETWEEN ? AND ?", (start, end))
        }
        sql_filter, params = "", [end, start]
        if employee_pk is not None:
            sql_filter, params = " AND employee_pk = ?", params + [employee_pk]

        self.assignments = defaultdict(list)
        for r in conn.execute(
            "SELECT employee_pk, shift_id, effective_from, effective_to FROM shift_assignments "
            "WHERE status = 'active' AND effective_from <= ? "
            "AND (effective_to IS NULL OR effective_to >= ?)" + sql_filter, params,
        ):
            self.assignments[r["employee_pk"]].append(r)

        self.leaves = defaultdict(list)
        for r in conn.execute(
            "SELECT employee_pk, start_date, end_date, leave_type FROM leave_records "
            "WHERE status = 'approved' AND start_date <= ? AND end_date >= ?" + sql_filter, params,
        ):
            self.leaves[r["employee_pk"]].append(r)

    def shift_on(self, employee_pk, day):
        for a in self.assignments.get(employee_pk, ()):
            if a["effective_from"] <= day and (a["effective_to"] is None or a["effective_to"] >= day):
                return self.shifts.get(a["shift_id"])
        return None

    def plan(self, employee_pk, day):
        shift = self.shift_on(employee_pk, day)
        holiday = self.holidays.get(day)
        if holiday:
            kind = HOLIDAY if holiday["type"] == "holiday" else WEEKLY_OFF
            return Plan(kind, shift, holiday["name"])
        for leave in self.leaves.get(employee_pk, ()):
            if leave["start_date"] <= day <= leave["end_date"]:
                return Plan(LEAVE, shift, f"{leave['leave_type']} leave")
        if shift is None:
            return Plan(NO_SHIFT)
        # A deactivated shift still counts for dates before it was deactivated.
        if shift["status"] != "active" and (
                not shift["deactivated_on"] or day >= shift["deactivated_on"]):
            return Plan(NO_SHIFT, None, "Assigned shift is inactive")
        if date.fromisoformat(day).weekday() not in shift["working_days"]:
            return Plan(WEEKLY_OFF, shift, "Weekly off")
        return Plan(SCHEDULED, shift)


def _result(status, action=None, message="", occurrence=None, record=None, shift=None):
    return {
        "status": status,
        "status_label": STATUS_LABELS[status],
        "indicator": STATUS_INDICATORS[status],
        "action": action,
        "message": message,
        "occurrence": occurrence,
        "record": record,
        "shift": shift or (occurrence.shift if occurrence else None),
    }


def evaluate(conn, employee, settings, now):
    """Decide the employee's current status and the single action available."""
    tz = timeutil.zone(settings["timezone"])
    tf = settings["time_format"]
    employee_pk = employee["id"]

    open_rec = conn.execute(
        "SELECT * FROM attendance WHERE employee_pk = ? AND is_open = 1", (employee_pk,),
    ).fetchone()
    if open_rec:
        shift = None
        if open_rec["shift_id"]:
            shift = shift_row(conn.execute("SELECT * FROM shifts WHERE id = ?",
                                           (open_rec["shift_id"],)).fetchone())
        since = timeutil.format_hhmm(timeutil.parse_iso(open_rec["clock_in_utc"]), tz, tf)
        return _result(CLOCKED_IN, "clock_out", f"Clocked in since {since}.",
                       record=open_rec, shift=shift)

    today = timeutil.local_date(now, tz)
    yesterday, tomorrow = today - timedelta(days=1), today + timedelta(days=1)
    index = ScheduleIndex(conn, yesterday.isoformat(), tomorrow.isoformat(), employee_pk)

    records = defaultdict(list)
    for r in conn.execute(
        "SELECT * FROM attendance WHERE employee_pk = ? AND attendance_date BETWEEN ? AND ? "
        "ORDER BY id", (employee_pk, yesterday.isoformat(), tomorrow.isoformat()),
    ):
        records[r["attendance_date"]].append(r)

    today_str = today.isoformat()
    occurrences = []
    today_plan = None
    for day in (yesterday.isoformat(), today_str, tomorrow.isoformat()):
        plan = index.plan(employee_pk, day)
        if day == today_str:
            today_plan = plan
        if plan.kind != SCHEDULED or day < employee["date_of_joining"]:
            continue
        # An administrator marked this date (Leave, Absent, ...) without any clock times.
        marked = next((r for r in records[day] if not r["clock_in_utc"]), None)
        if marked:
            if day == today_str:
                today_plan = Plan(NO_SHIFT, plan.shift, f"Marked as {marked['status']} by an administrator")
            continue
        occurrences.append(occurrence_for(plan.shift, day, tz))

    multiple = settings["allow_multiple_sessions"]
    clocked_out = None
    for occ in occurrences:
        if occ.clock_in_opens <= now < occ.end:
            done = records[occ.date][-1] if records[occ.date] else None
            if not done or multiple:
                return _result(NOT_CLOCKED_IN, "clock_in", "You can clock in now.", occ)
            clocked_out = (occ, done)
    if clocked_out:
        occ, done = clocked_out
        return _result(CLOCKED_OUT, None, "Your attendance for this shift is complete.", occ, done)

    for occ in occurrences:
        if now < occ.clock_in_opens and (
                occ.date == today_str or timeutil.local_date(occ.clock_in_opens, tz) == today):
            opens = timeutil.format_hhmm(occ.clock_in_opens, tz, tf)
            return _result(SHIFT_NOT_STARTED, None, f"Clock-in opens at {opens}.", occ)

    for occ in reversed(occurrences):
        if now >= occ.end and (occ.date == today_str or timeutil.local_date(occ.end, tz) == today):
            done = records[occ.date][-1] if records[occ.date] else None
            message = ("Your shift has ended." if done else
                       "Your shift has ended with no clock-in. Please contact an administrator.")
            return _result(SHIFT_COMPLETED, None, message, occ, done)

    reason = {
        NO_SHIFT: today_plan.label or "No shift is assigned to you for today.",
        HOLIDAY: f"Today is a holiday: {today_plan.label}.",
        WEEKLY_OFF: f"Today is not a working day: {today_plan.label}.",
        LEAVE: f"You are on {today_plan.label} today.",
    }.get(today_plan.kind, "You are not scheduled to work today.")
    if today_str < employee["date_of_joining"]:
        reason = "Your employment has not started yet."

    if settings["allow_unscheduled_clock_in"] and today_str >= employee["date_of_joining"]:
        if multiple or not records[today_str]:
            return _result(NOT_SCHEDULED, "clock_in",
                           f"{reason} Unscheduled clock-in is allowed.", shift=today_plan.shift)
        return _result(CLOCKED_OUT, None, "Your attendance for today is complete.",
                       record=records[today_str][-1])
    return _result(NOT_SCHEDULED, None, reason, shift=today_plan.shift)


def shift_summary(shift, settings):
    if not shift:
        return None

    def fmt(hhmm):
        parsed = datetime.strptime(hhmm, "%H:%M")
        return parsed.strftime("%H:%M" if settings["time_format"] == "24" else "%I:%M %p")

    next_day = " (next day)" if shift["end_time"] <= shift["start_time"] else ""
    return {"name": shift["name"], "code": shift["code"],
            "start": fmt(shift["start_time"]), "end": fmt(shift["end_time"]) + next_day}
