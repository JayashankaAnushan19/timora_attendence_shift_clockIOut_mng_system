Admin.start("employees", "Employees", async (root) => {
  const { esc } = T;
  const canManage = Admin.can("employees.manage");
  const lookups = await Admin.call("GET", "/api/admin/lookups");

  root.innerHTML = `
    <div id="capacity"></div>
    <section class="panel">
      <div class="toolbar">
        <div class="grow"><label for="q">Search</label><input id="q" type="search" placeholder="Name or employee ID"></div>
        <div><label for="dept">Department</label><select id="dept"><option value="">All departments</option>
          ${lookups.departments.map((d) => `<option>${esc(d)}</option>`).join("")}</select></div>
        <div><label for="status">Status</label><select id="status"><option value="">All</option>
          <option value="active">Active</option><option value="inactive">Inactive</option></select></div>
        <div class="spacer"></div>
        ${canManage ? '<div class="btn-group"><button class="btn btn-primary" id="add" type="button">Add Employee</button></div>' : ""}
      </div>
      <div id="tbl"></div>
    </section>`;

  const shiftOptions = () => [["", "No shift"], ...lookups.shifts.filter((s) => s.status === "active").map((s) => [s.id, s.name])];

  function employeeForm(employee) {
    const editing = Boolean(employee);
    const today = new Intl.DateTimeFormat("en-CA", { timeZone: T.display.timezone }).format(new Date());
    Admin.formModal({
      title: editing ? `Edit ${employee.employee_id}` : "Add Employee",
      submitLabel: editing ? "Save changes" : "Create employee",
      values: editing ? employee : { date_of_joining: today },
      intro: editing ? "" : "The employee ID is generated automatically.",
      fields: [
        { name: "name", label: "Employee Name", required: true, full: true },
        { name: "department", label: "Department", required: true },
        { name: "designation", label: "Designation", required: true },
        { name: "contact_number", label: "Contact Number" },
        { name: "email", label: "Email", type: "email" },
        { name: "date_of_joining", label: "Date of Joining", type: "date", required: true },
        { name: "shift_id", label: "Assigned Shift", type: "select", options: shiftOptions(),
          help: editing ? "A new shift takes effect today. Use Shift Assignments for other dates." : "" },
        { name: "notes", label: "Notes", type: "textarea", full: true },
      ],
      onSubmit: async (values) => {
        if (editing) {
          await Admin.call("PUT", `/api/admin/employees/${encodeURIComponent(employee.employee_id)}`, values);
          Admin.toast("Employee updated.", "success");
        } else {
          const created = await Admin.call("POST", "/api/admin/employees", values);
          Admin.toast(created.message, "success");
        }
        await table.refresh();
      },
    });
  }

  async function setStatus(row, activate) {
    const ok = await Admin.confirm({
      title: activate ? "Reactivate employee" : "Deactivate employee",
      message: activate
        ? `Reactivate ${row.name} (${row.employee_id})? They will be able to clock in again.`
        : `Deactivate ${row.name} (${row.employee_id})? They will no longer be able to clock in. Their attendance history is kept.`,
      confirmLabel: activate ? "Reactivate" : "Deactivate",
      danger: !activate,
    });
    if (!ok) return;
    try {
      const r = await Admin.call("POST", `/api/admin/employees/${encodeURIComponent(row.employee_id)}/${activate ? "reactivate" : "deactivate"}`, {});
      Admin.toast(r.message, "success");
      await table.refresh();
    } catch (err) {
      Admin.toast(err.message, "error");
    }
  }

  function renderCapacity(cap) {
    const box = root.querySelector("#capacity");
    if (!cap || !cap.warning) { box.innerHTML = ""; return; }
    box.innerHTML = cap.remaining === 0
      ? `<div class="banner danger">The employee limit has been reached (${cap.used} of ${cap.limit}). Employee IDs are never reused, so no more employees can be added.</div>`
      : `<div class="banner">Approaching the employee limit: ${cap.used} of ${cap.limit} used, ${cap.remaining} remaining.</div>`;
  }

  const table = Admin.table(root.querySelector("#tbl"), {
    sort: "employee_id",
    empty: "No employees match. Use Add Employee to create one.",
    columns: [
      { key: "employee_id", label: "ID", sortable: true },
      { key: "name", label: "Name", sortable: true, render: (r) => `<a href="/admin/employee?id=${encodeURIComponent(r.employee_id)}">${esc(r.name)}</a>` },
      { key: "department", label: "Department", sortable: true },
      { key: "designation", label: "Designation", sortable: true },
      { key: "shift_name", label: "Shift", render: (r) => esc(r.shift_name) || '<span class="muted">None</span>' },
      { key: "date_of_joining", label: "Joined", kind: "date", sortable: true },
      { key: "status", label: "Status", sortable: true, render: (r) => Admin.statusPill(r.status) },
    ],
    actions: (r) => {
      const list = [{ label: "View", onClick: () => { location.href = `/admin/employee?id=${encodeURIComponent(r.employee_id)}`; } }];
      if (canManage) {
        list.push({ label: "Edit", onClick: () => employeeForm(r) });
        list.push(r.status === "active"
          ? { label: "Deactivate", kind: "danger", onClick: () => setStatus(r, false) }
          : { label: "Reactivate", onClick: () => setStatus(r, true) });
      }
      return list;
    },
    load: async (p) => {
      const data = await Admin.call("GET", `/api/admin/employees?${Admin.queryString({
        ...p,
        q: root.querySelector("#q").value.trim(),
        department: root.querySelector("#dept").value,
        status: root.querySelector("#status").value,
      })}`);
      renderCapacity(data.capacity);
      return data;
    },
  });

  root.querySelector("#q").addEventListener("input", Admin.debounce(() => table.reload()));
  root.querySelector("#dept").addEventListener("change", () => table.reload());
  root.querySelector("#status").addEventListener("change", () => table.reload());
  if (canManage) root.querySelector("#add").addEventListener("click", () => employeeForm(null));
  await table.refresh();
});
