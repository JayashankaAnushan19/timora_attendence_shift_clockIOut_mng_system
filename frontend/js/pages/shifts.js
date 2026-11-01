Admin.start("shifts", "Shifts", async (root) => {
  const { esc } = T;
  const canManage = Admin.can("shifts.manage");
  const display = Admin.state.display;

  function hoursLabel(s) {
    const endDay = s.end_time <= s.start_time ? 2 : 1;
    return `Day 1 ${T.fmtShiftTime(s.start_time)} to Day ${endDay} ${T.fmtShiftTime(s.end_time)}`;
  }

  function daysLabel(days) {
    return Admin.WEEKDAYS.filter((_, i) => days.includes(i)).join(", ");
  }

  function renderShifts(panel) {
    panel.innerHTML = `<section class="panel">
      <div class="panel-head"><h2>All Shifts</h2>${canManage ? '<button class="btn btn-primary" id="addShift" type="button">Create Shift</button>' : ""}</div>
      <div id="shiftTbl"></div></section>`;

    function form(shift) {
      Admin.formModal({
        title: shift ? `Edit ${shift.name}` : "Create Shift",
        wide: true,
        values: shift ? { ...shift, start_day: "1", end_day: shift.end_time <= shift.start_time ? "2" : "1" } : {
          start_day: "1",
          end_day: "1",
          working_days: display.default_working_days,
          grace_minutes: display.default_grace_minutes,
          break_minutes: 60,
          early_clock_in_minutes: display.default_early_clock_in_minutes,
          late_clock_out_minutes: display.default_late_clock_out_minutes,
        },
        fields: [
          { name: "name", label: "Shift Name", required: true },
          { name: "code", label: "Shift Code", required: true, help: "Letters, numbers, - and _ only." },
          { name: "start_day", label: "Start Day", type: "select", options: [["1", "Day 1"]], disabled: true },
          { name: "start_time", label: "Start Time", type: "time", required: true },
          { name: "end_day", label: "End Day", type: "select", options: [["1", "Day 1 (same day)"], ["2", "Day 2 (next day)"]] },
          { name: "end_time", label: "End Time", type: "time", required: true },
          { name: "working_days", label: "Applicable Working Days", type: "weekdays", full: true },
          { name: "grace_minutes", label: "Grace Period (minutes)", type: "number", min: 0, max: 240 },
          { name: "break_minutes", label: "Break Duration (minutes)", type: "number", min: 0, max: 480 },
          { name: "early_clock_in_minutes", label: "Early Clock-In Allowance (minutes)", type: "number", min: 0, max: 720, help: "How long before the start clock-in opens." },
          { name: "late_clock_out_minutes", label: "Late Clock-Out Allowance (minutes)", type: "number", min: 0, max: 720, help: "How long after the end clock-out stays open." },
          { name: "is_flexible", label: "Flexible shift (no late or early flags; overtime is based on hours worked)", type: "checkbox", full: true },
        ],
        onSubmit: async (values) => {
          const r = shift
            ? await Admin.call("PUT", `/api/admin/shifts/${shift.id}`, values)
            : await Admin.call("POST", "/api/admin/shifts", values);
          Admin.toast(r.message, "success");
          await table.refresh();
        },
      });
    }

    async function view(shift) {
      const d = await Admin.call("GET", `/api/admin/shifts/${shift.id}`);
      const s = d.shift;
      const m = Admin.modal({
        title: s.name, wide: true, footer: false,
        body: `<dl class="dl">
            <dt>Code</dt><dd>${esc(s.code)}</dd>
            <dt>Hours</dt><dd>${esc(hoursLabel(s))}${s.is_overnight ? '<span class="tag">Overnight</span>' : ""}${s.is_24h ? '<span class="tag">24 hours</span>' : ""}${s.is_flexible ? '<span class="tag">Flexible</span>' : ""}</dd>
            <dt>Working days</dt><dd>${esc(daysLabel(s.working_days))}</dd>
            <dt>Grace period</dt><dd>${esc(s.grace_minutes)} minutes</dd>
            <dt>Break</dt><dd>${esc(s.break_minutes)} minutes</dd>
            <dt>Early clock-in</dt><dd>${esc(s.early_clock_in_minutes)} minutes before start</dd>
            <dt>Late clock-out</dt><dd>${esc(s.late_clock_out_minutes)} minutes after end</dd>
            <dt>Status</dt><dd>${Admin.statusPill(s.status)}</dd>
          </dl><h3 style="margin:16px 0 6px;font-size:14px">Employees assigned today (${d.employees.length})</h3><div id="emps"></div>`,
      });
      Admin.table(m.body.querySelector("#emps"), {
        perPage: 25, empty: "Nobody is assigned to this shift today.",
        columns: [
          { key: "employee_id", label: "ID" }, { key: "name", label: "Name" }, { key: "department", label: "Department" },
          { key: "effective_from", label: "From", kind: "date" },
        ],
        load: Admin.localLoad(() => d.employees),
      }).refresh();
    }

    async function toggle(shift) {
      const activate = shift.status !== "active";
      const ok = await Admin.confirm({
        title: activate ? "Activate shift" : "Deactivate shift",
        message: activate ? `Activate ${shift.name}?`
          : `Deactivate ${shift.name}? ${shift.assigned_count} employee(s) on it will show as Not Scheduled until reassigned. Past records are unaffected.`,
        confirmLabel: activate ? "Activate" : "Deactivate",
        danger: !activate,
      });
      if (!ok) return;
      try {
        const r = await Admin.call("POST", `/api/admin/shifts/${shift.id}/status`, { status: activate ? "active" : "inactive" });
        Admin.toast(r.message, "success");
        await table.refresh();
      } catch (err) { Admin.toast(err.message, "error"); }
    }

    async function remove(shift) {
      const ok = await Admin.confirm({
        title: "Delete shift",
        message: `Delete ${shift.name}? It will no longer be available. Past attendance records keep this shift.`,
        confirmLabel: "Delete", danger: true,
      });
      if (!ok) return;
      try {
        const r = await Admin.call("DELETE", `/api/admin/shifts/${shift.id}`);
        Admin.toast(r.message, "success");
        await table.refresh();
      } catch (err) { Admin.toast(err.message, "error"); }
    }

    const table = Admin.table(panel.querySelector("#shiftTbl"), {
      empty: "No shifts yet.",
      columns: [
        { key: "name", label: "Name", render: (s) => `${esc(s.name)}${s.is_overnight ? '<span class="tag">Overnight</span>' : ""}${s.is_24h ? '<span class="tag">24 hours</span>' : ""}${s.is_flexible ? '<span class="tag">Flexible</span>' : ""}` },
        { key: "code", label: "Code" },
        { key: "hours", label: "Hours", render: (s) => esc(hoursLabel(s)) },
        { key: "days", label: "Working Days", render: (s) => esc(daysLabel(s.working_days)) },
        { key: "grace_minutes", label: "Grace", render: (s) => `${esc(s.grace_minutes)}m`, num: true },
        { key: "assigned_count", label: "Employees", num: true },
        { key: "status", label: "Status", render: (s) => Admin.statusPill(s.status) },
      ],
      actions: (s) => {
        const list = [{ label: "View", onClick: () => view(s).catch((e) => Admin.toast(e.message, "error")) }];
        if (canManage) {
          list.push({ label: "Edit", onClick: () => form(s) });
          list.push(s.status === "active"
            ? { label: "Deactivate", kind: "danger", onClick: () => toggle(s) }
            : { label: "Activate", onClick: () => toggle(s) });
        }
        if (Admin.can("shifts.delete")) list.push({ label: "Delete", kind: "danger", onClick: () => remove(s) });
        return list;
      },
      load: async () => {
        const d = await Admin.call("GET", "/api/admin/shifts");
        return { items: d.items, total: d.items.length };
      },
    });
    table.state.perPage = 100;
    if (canManage) panel.querySelector("#addShift").addEventListener("click", () => form(null));
    table.refresh();
  }

  function renderHolidays(panel) {
    panel.innerHTML = `<section class="panel">
      <div class="panel-head"><h2>Holidays &amp; Non-Working Days</h2>${canManage ? '<button class="btn btn-primary" id="addDay" type="button">Add Date</button>' : ""}</div>
      <div id="dayTbl"></div></section>`;
    const table = Admin.table(panel.querySelector("#dayTbl"), {
      empty: "No holidays or non-working days added.",
      columns: [
        { key: "date", label: "Date", kind: "date" },
        { key: "name", label: "Name" },
        { key: "type", label: "Type", render: (h) => h.type === "holiday" ? "Holiday" : "Non-working day" },
      ],
      actions: canManage ? (h) => [{
        label: "Remove", kind: "danger",
        onClick: async () => {
          if (!await Admin.confirm({ title: "Remove date", message: `Remove ${h.name}? Employees will be scheduled normally on this date.`, confirmLabel: "Remove", danger: true })) return;
          try {
            await Admin.call("DELETE", `/api/admin/holidays/${h.id}`);
            Admin.toast("Date removed.", "success");
            await table.refresh();
          } catch (err) { Admin.toast(err.message, "error"); }
        },
      }] : null,
      load: async () => {
        const d = await Admin.call("GET", "/api/admin/holidays");
        return { items: d.items, total: d.items.length };
      },
    });
    table.state.perPage = 100;
    if (canManage) {
      panel.querySelector("#addDay").addEventListener("click", () => Admin.formModal({
        title: "Add Holiday or Non-Working Day",
        fields: [
          { name: "date", label: "Date", type: "date", required: true },
          { name: "type", label: "Type", type: "select", options: [["holiday", "Holiday"], ["non_working", "Non-working day"]] },
          { name: "name", label: "Name", required: true, full: true },
        ],
        onSubmit: async (values) => {
          await Admin.call("POST", "/api/admin/holidays", values);
          Admin.toast("Date added.", "success");
          await table.refresh();
        },
      }));
    }
    table.refresh();
  }

  Admin.tabs(root, [
    { key: "shifts", label: "Shifts", render: renderShifts },
    { key: "holidays", label: "Holidays & Non-Working Days", render: renderHolidays },
  ]);
});
