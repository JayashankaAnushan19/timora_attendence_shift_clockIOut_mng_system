Admin.start("overtime", "Overtime", async (root) => {
  const { esc } = T;
  const canSeePay = Admin.can("payroll.manage");
  const lookups = await Admin.call("GET", "/api/admin/lookups");
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: T.display.timezone }).format(new Date());
  const active = lookups.employees.filter((e) => e.status === "active");

  root.innerHTML = `<section class="panel">
    <div class="toolbar">
      <div class="grow"><label for="emp">Employee</label><select id="emp"><option value="">All employees</option>
        ${lookups.employees.map((e) => `<option value="${esc(e.employee_id)}">${esc(e.employee_id)} - ${esc(e.name)}</option>`).join("")}</select></div>
      <div><label for="from">From</label><input id="from" type="date"></div>
      <div><label for="to">To</label><input id="to" type="date"></div>
      <div><label for="status">Status</label><select id="status"><option value="active">Active</option><option value="cancelled">Cancelled</option><option value="">All</option></select></div>
      <div class="btn-group"><button class="btn btn-primary" id="add" type="button">Add Overtime</button></div>
    </div><div id="tbl"></div></section>`;

  function addForm() {
    const m = Admin.formModal({
      title: "Add Overtime", wide: true, submitLabel: "Add overtime",
      intro: "Overtime pay is calculated only from hours added here, using each employee's OT rate at the time. Your name is recorded with each entry.",
      values: { work_date: today, apply_to: "selected" },
      fields: [
        { name: "work_date", label: "Date", type: "date", required: true },
        { name: "hours", label: "Overtime Hours", type: "number", min: 0, max: 24, step: "0.25", required: true, help: "For example, 2 or 1.5." },
        { name: "apply_to", label: "Add for", type: "select", full: true, options: [["selected", "Selected employees"], ["all", `All active employees (${active.length})`]] },
        { name: "employee_ids", label: "Employees", type: "checklist", full: true, options: active.map((e) => [e.employee_id, `${e.employee_id} - ${e.name}`]) },
        { name: "reason", label: "Reason", type: "textarea", required: true, full: true },
      ],
      onSubmit: async (v) => {
        const everyone = v.apply_to === "all";
        if (!everyone && !v.employee_ids.length) throw new Error("Select at least one employee.");
        const r = await Admin.call("POST", "/api/admin/overtime", {
          work_date: v.work_date, hours: v.hours, reason: v.reason,
          ...(everyone ? { all: true } : { employee_ids: v.employee_ids }),
        });
        Admin.toast(r.message, "success");
        await table.refresh();
      },
    });
    const form = m.body.querySelector("form");
    const applyTo = form.querySelector('[name="apply_to"]');
    const list = form.querySelector('[data-checklist="employee_ids"]').closest(".field");
    const toggle = () => { list.hidden = applyTo.value === "all"; };
    applyTo.addEventListener("change", toggle);
    toggle();
  }

  function cancelForm(entry) {
    Admin.formModal({
      title: `Cancel overtime: ${entry.employee_name}`, submitLabel: "Cancel overtime",
      intro: `${T.fmtDuration(entry.added_seconds)} on ${T.fmtDate(entry.work_date)}. The entry stays on record as cancelled and no longer counts towards pay.`,
      fields: [{ name: "reason", label: "Reason for cancelling", type: "textarea", required: true, full: true }],
      onSubmit: async (v) => {
        const r = await Admin.call("POST", `/api/admin/overtime/${entry.id}/cancel`, v);
        Admin.toast(r.message, "success");
        await table.refresh();
      },
    });
  }

  const table = Admin.table(root.querySelector("#tbl"), {
    empty: "No overtime matches these filters.",
    columns: [
      { key: "work_date", label: "Date", kind: "date" },
      { key: "employee_id", label: "ID" },
      { key: "employee_name", label: "Employee" },
      { key: "employee_type", label: "Type" },
      { key: "added_seconds", label: "Hours", kind: "duration", num: true },
      ...(canSeePay ? [
        { key: "rate", label: "Rate / Hour", kind: "money", num: true },
        { key: "ot_pay", label: "OT Pay", kind: "money", num: true },
      ] : []),
      { key: "reason", label: "Reason", wrap: true },
      { key: "added_by", label: "Added By" },
      { key: "created_at", label: "Added On", kind: "datetime" },
      { key: "status", label: "Status", render: (e) => e.status === "active"
        ? Admin.statusPill("active")
        : `<span class="pill">cancelled</span><div class="muted">${esc(e.cancelled_by)}: ${esc(e.cancel_reason)}</div>` },
    ],
    actions: (e) => e.status === "active" ? [{ label: "Cancel", kind: "danger", onClick: () => cancelForm(e) }] : [],
    load: (p) => Admin.call("GET", `/api/admin/overtime?${Admin.queryString({
      page: p.page, per_page: p.per_page,
      employee: root.querySelector("#emp").value,
      from: root.querySelector("#from").value,
      to: root.querySelector("#to").value,
      status: root.querySelector("#status").value,
    })}`),
  });

  ["emp", "from", "to", "status"].forEach((id) => root.querySelector(`#${id}`).addEventListener("change", () => table.reload()));
  root.querySelector("#add").addEventListener("click", addForm);
  await table.refresh();
});
