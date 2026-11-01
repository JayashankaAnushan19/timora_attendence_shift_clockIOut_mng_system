Admin.start("attendance", "Attendance Records", async (root) => {
  const { esc } = T;
  const canManage = Admin.can("attendance.manage");
  const lookups = await Admin.call("GET", "/api/admin/lookups");
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: T.display.timezone }).format(new Date());
  const STATUSES = ["Present", "Late", "Absent", "Half Day", "Early Departure", "Overtime", "Incomplete", "Leave", "Holiday", "Weekly Off"];
  const statusLabel = (s) => (s === "Incomplete" ? "Incomplete Attendance" : s);
  const FIELD_LABELS = { clock_in: "Clock-in", clock_out: "Clock-out", status: "Status", attendance_date: "Attendance date", shift_id: "Shift", notes: "Notes", record: "Record" };

  const adjustmentValue = (v) => {
    if (v == null || v === "") return "(empty)";
    return /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/.test(v) ? T.fmtDateTime(v) : v;
  };
  const shiftName = (id) => (lookups.shifts.find((s) => s.id === id) || {}).name || "";

  function renderRecords(panel) {
    panel.innerHTML = `<section class="panel">
      <div class="toolbar">
        <div class="grow"><label for="q">Employee</label><input id="q" type="search" placeholder="Name or employee ID"></div>
        <div><label for="from">From</label><input id="from" type="date"></div>
        <div><label for="to">To</label><input id="to" type="date"></div>
        <div><label for="dept">Department</label><select id="dept"><option value="">All</option>${lookups.departments.map((d) => `<option>${esc(d)}</option>`).join("")}</select></div>
        <div><label for="shift">Shift</label><select id="shift"><option value="">All</option>${lookups.shifts.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("")}</select></div>
        <div><label for="status">Status</label><select id="status"><option value="">All</option>${STATUSES.map((s) => `<option value="${esc(s)}">${esc(statusLabel(s))}</option>`).join("")}</select></div>
        ${canManage ? '<div class="btn-group"><button class="btn btn-primary" id="add" type="button">Add Record</button></div>' : ""}
      </div><div id="tbl"></div></section>`;

    const statusOptions = [["auto", "Automatic (calculated)"], ...STATUSES.map((s) => [s, statusLabel(s)])];
    const shiftOptions = [["", "Use assigned shift"], ...lookups.shifts.map((s) => [s.id, s.name])];

    function addForm(prefill = {}) {
      Admin.formModal({
        title: "Add Attendance Record", wide: true, submitLabel: "Add record",
        intro: `Times are in the business timezone (${T.display.timezone}). This addition is recorded permanently with your name.`,
        values: { attendance_date: today, status: "auto", ...prefill },
        fields: [
          { name: "employee_id", label: "Employee", type: "select", required: true, full: true,
            options: [["", "Select an employee"], ...lookups.employees.map((e) => [e.employee_id, `${e.employee_id} - ${e.name}`])] },
          { name: "attendance_date", label: "Attendance Date", type: "date", required: true, help: "The date the shift started." },
          { name: "shift_id", label: "Shift", type: "select", options: shiftOptions },
          { name: "clock_in", label: "Clock In", type: "datetime-local", step: 1 },
          { name: "clock_out", label: "Clock Out", type: "datetime-local", step: 1 },
          { name: "status", label: "Status", type: "select", options: statusOptions, help: "Absent, Leave, Holiday and Weekly Off need no clock times." },
          { name: "notes", label: "Notes" },
          { name: "reason", label: "Reason for adding", type: "textarea", required: true, full: true },
        ],
        onSubmit: async (values) => {
          const r = await Admin.call("POST", "/api/admin/attendance", values);
          Admin.toast(r.message, "success");
          await table.refresh();
        },
      });
    }

    function editForm(rec) {
      const values = {
        attendance_date: rec.attendance_date, shift_id: rec.shift_id || "",
        clock_in: T.toLocalInput(rec.clock_in), clock_out: T.toLocalInput(rec.clock_out),
        status: rec.status_locked ? rec.status : "auto", notes: rec.notes,
      };
      Admin.formModal({
        title: `Correct record #${rec.id}: ${rec.employee_name}`, wide: true, submitLabel: "Save correction",
        intro: `Times are in the business timezone (${T.display.timezone}). The original values stay in this record's history.`,
        values,
        fields: [
          { name: "attendance_date", label: "Attendance Date", type: "date", required: true },
          { name: "shift_id", label: "Shift", type: "select", options: [["", "None"], ...lookups.shifts.map((s) => [s.id, s.name]),
            // A deleted shift stays selectable here, so correcting a record doesn't clear it.
            ...(rec.shift_id && !lookups.shifts.some((s) => s.id === rec.shift_id) ? [[rec.shift_id, rec.shift_name]] : [])] },
          { name: "clock_in", label: "Clock In", type: "datetime-local", step: 1 },
          { name: "clock_out", label: "Clock Out", type: "datetime-local", step: 1 },
          { name: "status", label: "Status", type: "select", options: statusOptions },
          { name: "notes", label: "Notes" },
          { name: "reason", label: "Reason for adjustment", type: "textarea", required: true, full: true },
        ],
        onSubmit: async (v) => {
          // Only send what changed: resending a seconds-rounded time could otherwise look like an edit.
          const body = { reason: v.reason, status: v.status, notes: v.notes };
          if (v.clock_in !== values.clock_in) body.clock_in = v.clock_in;
          if (v.clock_out !== values.clock_out) body.clock_out = v.clock_out;
          if (v.attendance_date !== values.attendance_date) body.attendance_date = v.attendance_date;
          if (String(v.shift_id) !== String(values.shift_id)) body.shift_id = v.shift_id ? Number(v.shift_id) : null;
          const r = await Admin.call("PUT", `/api/admin/attendance/${rec.id}`, body);
          Admin.toast(r.message, "success");
          await table.refresh();
        },
      });
    }

    async function viewRecord(rec) {
      const d = await Admin.call("GET", `/api/admin/attendance/${rec.id}`);
      const r = d.record;
      const history = d.adjustments.map((a) => `<tr>
        <td>${esc(T.fmtDateTime(a.adjusted_at))}</td><td>${esc(a.admin_username)}</td>
        <td>${esc(FIELD_LABELS[a.field_name] || a.field_name)}</td>
        <td class="wrap">${esc(a.field_name === "shift_id" ? shiftName(Number(a.original_value)) || "(empty)" : adjustmentValue(a.original_value))}</td>
        <td class="wrap">${esc(a.field_name === "shift_id" ? shiftName(Number(a.updated_value)) || "(empty)" : adjustmentValue(a.updated_value))}</td>
        <td class="wrap">${esc(a.reason)}</td></tr>`).join("");
      Admin.modal({
        title: `Attendance Record #${r.id}`, wide: true, footer: false,
        body: `<dl class="dl">
          <dt>Employee</dt><dd>${esc(r.employee_name)} (${esc(r.employee_id)})</dd>
          <dt>Shift</dt><dd>${esc(r.shift_name) || "-"}</dd>
          <dt>Attendance date</dt><dd>${esc(T.fmtDate(r.attendance_date))}</dd>
          <dt>Clock in</dt><dd>${esc(T.fmtDateTime(r.clock_in)) || "-"}</dd>
          <dt>Clock out</dt><dd>${esc(T.fmtDateTime(r.clock_out)) || (r.is_open ? "Still clocked in" : "-")}</dd>
          <dt>Worked</dt><dd>${esc(T.fmtDuration(r.working_seconds))}</dd>
          <dt>Break</dt><dd>${esc(T.fmtDuration(r.break_seconds))}</dd>
          <dt>Late arrival</dt><dd>${esc(T.fmtDuration(r.late_seconds))}</dd>
          <dt>Early departure</dt><dd>${esc(T.fmtDuration(r.early_departure_seconds))}</dd>
          <dt>Overtime</dt><dd>${esc(T.fmtDuration(r.overtime_seconds))}</dd>
          <dt>Status</dt><dd>${Admin.statusPill(r.status)}${r.status_locked ? '<span class="tag">Set manually</span>' : ""}</dd>
          <dt>Recorded</dt><dd>${esc(T.fmtDateTime(r.created_at))} (${r.source === "admin" ? "added by administrator" : "attendance screen"})</dd>
          ${r.notes ? `<dt>Notes</dt><dd>${esc(r.notes)}</dd>` : ""}
        </dl>
        <h3 style="margin:16px 0 6px;font-size:14px">Adjustment History</h3>
        ${history ? `<div class="table-wrap"><table><thead><tr><th>When</th><th>By</th><th>Field</th><th>Original</th><th>Updated</th><th>Reason</th></tr></thead><tbody>${history}</tbody></table></div>`
          : '<p class="muted" style="margin:0">This record has not been adjusted.</p>'}`,
      });
    }

    const table = Admin.table(panel.querySelector("#tbl"), {
      sort: "attendance_date", dir: "desc",
      empty: "No attendance records match these filters.",
      columns: [
        { key: "id", label: "#", sortable: true, render: (r) => r.id ? esc(r.id) : '<span class="muted">-</span>' },
        { key: "employee_id", label: "ID", sortable: true },
        { key: "employee_name", label: "Employee", sortable: true },
        { key: "shift_name", label: "Shift", sortable: true },
        { key: "attendance_date", label: "Date", kind: "date", sortable: true },
        { key: "clock_in", label: "Clock In", kind: "datetime", sortable: true },
        { key: "clock_out", label: "Clock Out", kind: "datetime", sortable: true },
        { key: "working_seconds", label: "Worked", kind: "duration", sortable: true, num: true },
        { key: "late_seconds", label: "Late", kind: "duration", sortable: true, num: true },
        { key: "early_departure_seconds", label: "Early Dep.", kind: "duration", sortable: true, num: true },
        { key: "overtime_seconds", label: "Overtime", kind: "duration", sortable: true, num: true },
        { key: "status", label: "Status", sortable: true, render: (r) => `${Admin.statusPill(r.status)}${r.is_adjusted ? '<span class="tag">Adjusted</span>' : ""}` },
      ],
      actions: (r) => {
        if (!r.id) {
          // Worked out from the schedule, with no stored record behind it yet.
          return canManage ? [{ label: "Add Record", onClick: () => addForm({ employee_id: r.employee_id, attendance_date: r.attendance_date, status: r.status === "Pending" ? "auto" : r.status }) }] : [];
        }
        const list = [{ label: "View", onClick: () => viewRecord(r).catch((e) => Admin.toast(e.message, "error")) }];
        if (canManage) list.push({ label: "Correct", onClick: () => editForm(r) });
        return list;
      },
      load: (p) => Admin.call("GET", `/api/admin/attendance?${Admin.queryString({
        ...p,
        q: panel.querySelector("#q").value.trim(),
        from: panel.querySelector("#from").value,
        to: panel.querySelector("#to").value,
        department: panel.querySelector("#dept").value,
        shift: panel.querySelector("#shift").value,
        status: panel.querySelector("#status").value,
      })}`),
    });

    panel.querySelector("#q").addEventListener("input", Admin.debounce(() => table.reload()));
    ["from", "to", "dept", "shift", "status"].forEach((id) => panel.querySelector(`#${id}`).addEventListener("change", () => table.reload()));
    if (canManage) panel.querySelector("#add").addEventListener("click", () => addForm());
    table.refresh();
  }

  function renderLeave(panel) {
    panel.innerHTML = `<section class="panel">
      <div class="panel-head"><h2>Leave Records</h2>${canManage ? '<button class="btn btn-primary" id="addLeave" type="button">Record Leave</button>' : ""}</div>
      <div id="leaveTbl"></div></section>`;
    const table = Admin.table(panel.querySelector("#leaveTbl"), {
      empty: "No leave has been recorded.",
      columns: [
        { key: "employee_id", label: "ID" }, { key: "employee_name", label: "Employee" },
        { key: "start_date", label: "From", kind: "date" }, { key: "end_date", label: "To", kind: "date" },
        { key: "leave_type", label: "Type" }, { key: "reason", label: "Reason", wrap: true },
        { key: "created_by", label: "Recorded By" },
        { key: "status", label: "Status", render: (l) => Admin.statusPill(l.status) },
      ],
      actions: canManage ? (l) => l.status === "approved" ? [{
        label: "Cancel", kind: "danger",
        onClick: async () => {
          if (!await Admin.confirm({ title: "Cancel leave", message: `Cancel this leave for ${l.employee_name}?`, confirmLabel: "Cancel leave", danger: true })) return;
          try {
            await Admin.call("POST", `/api/admin/leave/${l.id}/cancel`, {});
            Admin.toast("Leave cancelled.", "success");
            await table.refresh();
          } catch (err) { Admin.toast(err.message, "error"); }
        },
      }] : [] : null,
      load: Admin.localLoad(async () => (await Admin.call("GET", "/api/admin/leave")).items),
    });
    if (canManage) {
      panel.querySelector("#addLeave").addEventListener("click", () => Admin.formModal({
        title: "Record Leave",
        values: { start_date: today, end_date: today, leave_type: "Annual" },
        fields: [
          { name: "employee_id", label: "Employee", type: "select", required: true, full: true,
            options: [["", "Select an employee"], ...lookups.employees.filter((e) => e.status === "active").map((e) => [e.employee_id, `${e.employee_id} - ${e.name}`])] },
          { name: "start_date", label: "From", type: "date", required: true },
          { name: "end_date", label: "To", type: "date", required: true },
          { name: "leave_type", label: "Leave Type", type: "select", options: ["Annual", "Sick", "Casual", "Unpaid", "Other"], full: true },
          { name: "reason", label: "Reason", type: "textarea", full: true },
        ],
        onSubmit: async (values) => {
          const r = await Admin.call("POST", "/api/admin/leave", values);
          Admin.toast(r.message, "success");
          await table.refresh();
        },
      }));
    }
    table.refresh();
  }

  Admin.tabs(root, [
    { key: "records", label: "Attendance Records", render: renderRecords },
    { key: "leave", label: "Leave", render: renderLeave },
  ]);
});
