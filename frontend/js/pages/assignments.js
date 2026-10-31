Admin.start("assignments", "Shift Assignments", async (root) => {
  const { esc } = T;
  const canManage = Admin.can("shifts.manage");
  const lookups = await Admin.call("GET", "/api/admin/lookups");
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: T.display.timezone }).format(new Date());

  root.innerHTML = `<section class="panel">
    <div class="toolbar">
      <div><label for="view">Show</label><select id="view"><option value="current">Current assignments</option><option value="all">Full history</option></select></div>
      <div class="grow"><label for="emp">Employee</label><select id="emp"><option value="">All employees</option>
        ${lookups.employees.map((e) => `<option value="${esc(e.employee_id)}">${esc(e.employee_id)} - ${esc(e.name)}</option>`).join("")}</select></div>
      <div><label for="shift">Shift</label><select id="shift"><option value="">All shifts</option>
        ${lookups.shifts.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("")}</select></div>
      <div class="spacer"></div>
      ${canManage ? '<div class="btn-group"><button class="btn btn-primary" id="assign" type="button">Assign Shift</button></div>' : ""}
    </div><div id="tbl"></div></section>`;

  function assignForm() {
    const active = lookups.employees.filter((e) => e.status === "active");
    Admin.formModal({
      title: "Assign Shift", wide: true, submitLabel: "Assign",
      intro: "The new shift replaces any existing shift for these dates. Earlier dates keep their original shift, so past reports stay accurate.",
      values: { effective_from: today },
      fields: [
        { name: "shift_id", label: "Shift", type: "select", required: true, full: true,
          options: [["", "Select a shift"], ...lookups.shifts.filter((s) => s.status === "active").map((s) => [s.id, s.name])] },
        { name: "effective_from", label: "Effective From", type: "date", required: true },
        { name: "effective_to", label: "Effective To (optional)", type: "date", help: "Leave empty for no end date." },
        { name: "employee_ids", label: "Employees", type: "checklist", required: true, full: true, options: active.map((e) => [e.employee_id, `${e.employee_id} - ${e.name}`]) },
        { name: "notes", label: "Notes", full: true },
      ],
      onSubmit: async (values) => {
        const r = await Admin.call("POST", "/api/admin/assignments", values);
        Admin.toast(r.message, "success");
        await table.refresh();
      },
    });
  }

  function endForm(a) {
    Admin.formModal({
      title: `End assignment: ${a.employee_name}`, submitLabel: "Save",
      values: { effective_to: a.effective_to || today },
      fields: [{ name: "effective_to", label: "Last day on this shift", type: "date", required: true, full: true }],
      onSubmit: async (values) => {
        const r = await Admin.call("POST", `/api/admin/assignments/${a.id}/end`, values);
        Admin.toast(r.message, "success");
        await table.refresh();
      },
    });
  }

  const table = Admin.table(root.querySelector("#tbl"), {
    empty: "No shift assignments match.",
    columns: [
      { key: "employee_id", label: "ID" },
      { key: "employee_name", label: "Employee" },
      { key: "shift_name", label: "Shift" },
      { key: "effective_from", label: "From", kind: "date" },
      { key: "effective_to", label: "To", render: (a) => a.effective_to ? esc(T.fmtDate(a.effective_to)) : '<span class="muted">Ongoing</span>' },
      { key: "assigned_by", label: "Assigned By" },
      { key: "notes", label: "Notes", wrap: true },
      { key: "status", label: "State", render: (a) => a.status === "superseded" ? '<span class="muted">Replaced</span>' : "Active" },
    ],
    actions: canManage ? (a) => a.status === "active" ? [{ label: "Set End Date", onClick: () => endForm(a) }] : [] : null,
    load: (p) => Admin.call("GET", `/api/admin/assignments?${Admin.queryString({
      page: p.page, per_page: p.per_page,
      view: root.querySelector("#view").value,
      employee: root.querySelector("#emp").value,
      shift: root.querySelector("#shift").value,
    })}`),
  });

  ["view", "emp", "shift"].forEach((id) => root.querySelector(`#${id}`).addEventListener("change", () => table.reload()));
  if (canManage) root.querySelector("#assign").addEventListener("click", assignForm);
  await table.refresh();
});
