Admin.start("employee", "Employee Details", async (root) => {
  const { esc } = T;
  const code = new URLSearchParams(location.search).get("id") || "";
  const d = await Admin.call("GET", `/api/admin/employees/${encodeURIComponent(code)}`);
  const e = d.employee;
  const s = d.summary;
  const pill = { green: "pill-green", red: "pill-red", orange: "pill-orange", blue: "pill-blue" }[d.current_status.indicator] || "";

  const row = (label, value) => `<dt>${esc(label)}</dt><dd>${value === "" || value == null ? '<span class="muted">-</span>' : esc(value)}</dd>`;

  root.innerHTML = `
    <div><a href="/admin/employees">&larr; All employees</a></div>
    <div class="stat-row">
      <div class="stat"><div class="label">Total Working Hours</div><div class="value">${esc(T.fmtDuration(s.working_seconds))}</div></div>
      <div class="stat"><div class="label">Days Attended</div><div class="value">${esc(s.days_attended)}</div></div>
      <div class="stat"><div class="label">Late Arrivals</div><div class="value">${esc(s.late_count)}</div></div>
      <div class="stat"><div class="label">Early Departures</div><div class="value">${esc(s.early_departure_count)}</div></div>
      <div class="stat"><div class="label">Overtime Days</div><div class="value">${esc(s.overtime_count)}</div></div>
      <div class="stat"><div class="label">Overtime Total</div><div class="value">${esc(T.fmtDuration(s.overtime_seconds))}</div></div>
    </div>
    <div class="two-col" style="grid-template-columns:1fr 1fr">
      <section class="panel"><div class="panel-head"><h2>${esc(e.name)}</h2>${Admin.statusPill(e.status)}</div>
        <div class="panel-body"><dl class="dl">
          ${row("Employee ID", e.employee_id)}
          ${row("Department", e.department)}
          ${row("Designation", e.designation)}
          ${row("Contact Number", e.contact_number)}
          ${row("Email", e.email)}
          ${row("Date of Joining", T.fmtDate(e.date_of_joining))}
          ${row("Assigned Shift", d.current_shift ? d.current_shift.name : "")}
          <dt>Current Status</dt><dd><span class="pill ${pill}">${esc(d.current_status.label)}</span>
            <div class="muted">${esc(d.current_status.message)}</div></dd>
          ${row("Notes", e.notes)}
        </dl></div></section>
      <section class="panel"><div class="panel-head"><h2>Shift Assignment History</h2></div><div id="assign"></div></section>
    </div>
    <section class="panel"><div class="panel-head"><h2>Attendance History</h2><span class="muted">Latest 100 records</span></div><div id="history"></div></section>`;

  Admin.table(root.querySelector("#assign"), {
    perPage: 25,
    empty: "No shift has been assigned.",
    columns: [
      { key: "shift_name", label: "Shift" },
      { key: "effective_from", label: "From", kind: "date" },
      { key: "effective_to", label: "To", render: (r) => r.effective_to ? esc(T.fmtDate(r.effective_to)) : '<span class="muted">Ongoing</span>' },
      { key: "status", label: "State", render: (r) => r.status === "superseded" ? '<span class="muted">Replaced</span>' : "Active" },
    ],
    load: Admin.localLoad(() => d.assignments),
  }).refresh();

  Admin.table(root.querySelector("#history"), {
    sort: "attendance_date",
    dir: "desc",
    empty: "No attendance recorded yet.",
    columns: [
      { key: "attendance_date", label: "Date", kind: "date", sortable: true },
      { key: "shift_name", label: "Shift", sortable: true },
      { key: "clock_in", label: "Clock In", kind: "datetime", sortable: true },
      { key: "clock_out", label: "Clock Out", kind: "datetime", sortable: true },
      { key: "working_seconds", label: "Worked", kind: "duration", sortable: true, num: true },
      { key: "late_seconds", label: "Late", kind: "duration", sortable: true, num: true },
      { key: "early_departure_seconds", label: "Early Dep.", kind: "duration", sortable: true, num: true },
      { key: "overtime_seconds", label: "Overtime", kind: "duration", sortable: true, num: true },
      { key: "status", label: "Status", kind: "status", sortable: true },
    ],
    load: Admin.localLoad(() => d.attendance),
  }).refresh();
});
