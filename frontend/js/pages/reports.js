Admin.start("reports", "Attendance Reports", async (root) => {
  const { esc } = T;
  const [lookups, list] = await Promise.all([Admin.call("GET", "/api/admin/lookups"), Admin.call("GET", "/api/admin/reports")]);
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: T.display.timezone }).format(new Date());
  const PRESETS = [
    ["today", "Today"], ["yesterday", "Yesterday"], ["this_week", "This Week"], ["last_week", "Last Week"],
    ["this_month", "This Month"], ["last_month", "Last Month"], ["custom", "Custom Date Range"],
  ];

  root.innerHTML = `<section class="panel">
    <div class="toolbar">
      <div><label for="report">Report</label><select id="report">${list.items.map((r) => `<option value="${esc(r.key)}">${esc(r.title)}</option>`).join("")}</select></div>
      <div><label for="preset">Date range</label><select id="preset">
        ${PRESETS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select></div>
      <div class="custom" hidden><label for="from">From</label><input id="from" type="date" value="${today}"></div>
      <div class="custom" hidden><label for="to">To</label><input id="to" type="date" value="${today}"></div>
      <div><label for="emp">Employee</label><select id="emp"><option value="">All</option>
        ${lookups.employees.map((e) => `<option value="${esc(e.employee_id)}">${esc(e.employee_id)} - ${esc(e.name)}</option>`).join("")}</select></div>
      <div><label for="type">Type</label><select id="type"><option value="">All</option>${lookups.employee_types.map((t) => `<option>${esc(t)}</option>`).join("")}</select></div>
      <div><label for="dept">Department</label><select id="dept"><option value="">All</option>${lookups.departments.map((d) => `<option>${esc(d)}</option>`).join("")}</select></div>
      <div><label for="shift">Shift</label><select id="shift"><option value="">All</option>${lookups.shifts.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("")}</select></div>
      <div class="btn-group">
        <button class="btn btn-primary" id="bulk" type="button">Export All Reports</button>
        <button class="btn" id="xlsx" type="button">Excel</button>
        <button class="btn" id="csv" type="button">CSV</button>
        <button class="btn" id="json" type="button">JSON</button>
        <button class="btn" id="print" type="button">Print / PDF</button>
      </div>
    </div>
    <div class="print-only"><h2 id="printTitle"></h2><div id="printRange"></div></div>
    <div id="tbl"></div>
    <div id="printTbl" class="print-only"></div>
  </section>`;

  const $ = (id) => root.querySelector(`#${id}`);

  function filters() {
    const preset = $("preset").value;
    return {
      preset,
      from: preset === "custom" ? $("from").value : "",
      to: preset === "custom" ? $("to").value : "",
      employee: $("emp").value,
      employee_type: $("type").value,
      department: $("dept").value,
      shift: $("shift").value,
    };
  }

  function toColumns(columns) {
    return columns.map((c) => ({
      key: c.key, label: c.label, kind: c.kind === "text" || c.kind === "int" ? undefined : c.kind,
      sortable: true, num: ["duration", "int", "money"].includes(c.kind),
    }));
  }

  const table = Admin.table($("tbl"), {
    columns: [],
    empty: "No data for this report and date range.",
    load: async (p) => {
      const data = await Admin.call("GET", `/api/admin/reports/${$("report").value}?${Admin.queryString({ ...filters(), ...p })}`);
      return { items: data.items, total: data.total, columns: toColumns(data.columns) };
    },
  });

  const exportUrl = (format) => {
    const s = table.state;
    return `/api/admin/reports/${$("report").value}?${Admin.queryString({ ...filters(), sort: s.sort, dir: s.dir, format })}`;
  };

  /* Print the whole report rather than just the current page of it. */
  async function printReport() {
    try {
      const s = table.state;
      const d = await Admin.call("GET", `/api/admin/reports/${$("report").value}?${Admin.queryString({ ...filters(), sort: s.sort, dir: s.dir, all: 1 })}`);
      const cols = toColumns(d.columns);
      $("printTitle").textContent = d.title;
      $("printRange").textContent = `${T.fmtDate(d.start)} to ${T.fmtDate(d.end)} · ${T.display.timezone} · ${Admin.state.display.company_name}`;
      $("printTbl").innerHTML = `<table><thead><tr>${cols.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("")}</tr></thead>
        <tbody>${d.items.map((r) => `<tr>${cols.map((c) => `<td class="${c.num ? "num" : ""}">${Admin.renderCell(r, c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
      $("tbl").classList.add("no-print");
      window.print();
    } catch (err) {
      Admin.toast(err.message, "error");
    }
  }

  ["report", "emp", "type", "dept", "shift", "from", "to"].forEach((id) => $(id).addEventListener("change", () => {
    if (id === "report") table.state.sort = null;
    table.reload();
  }));
  $("preset").addEventListener("change", () => {
    root.querySelectorAll(".custom").forEach((el) => { el.hidden = $("preset").value !== "custom"; });
    table.reload();
  });
  function exportCurrent(format) {
    Admin.fetchDownload(exportUrl(format)).catch((err) => Admin.toast(err.message, "error"));
  }

  /* One step for every report: same dates and people, all in a single download. */
  function bulkExport() {
    const m = Admin.formModal({
      title: "Export All Reports", wide: true, submitLabel: "Build & Download",
      intro: "Each report becomes its own sheet in one Excel workbook.",
      values: { preset: "this_month", from: today, to: today, format: "xlsx", reports: list.items.map((r) => r.key) },
      fields: [
        { name: "preset", label: "Date range", type: "select", options: PRESETS },
        { name: "format", label: "File type", type: "select", options: [["xlsx", "Excel (.xlsx)"], ["csv", "CSV files (.zip)"], ["json", "JSON"]] },
        { name: "from", label: "From", type: "date" },
        { name: "to", label: "To", type: "date" },
        { name: "employee_type", label: "Employee type", type: "select", options: [["", "All types"], ...lookups.employee_types.map((t) => [t, t])] },
        { name: "department", label: "Department", type: "select", options: [["", "All departments"], ...lookups.departments.map((d) => [d, d])] },
        { name: "shift", label: "Shift", type: "select", options: [["", "All shifts"], ...lookups.shifts.map((s) => [s.id, s.name])] },
        { name: "employees", label: "Selected people (leave all unticked for everyone)", type: "checklist", full: true,
          options: lookups.employees.map((e) => [e.employee_id, `${e.employee_id} - ${e.name}`]) },
        { name: "reports", label: "Reports to include", type: "checklist", full: true, options: list.items.map((r) => [r.key, r.title]) },
      ],
      onSubmit: async (v) => {
        if (!v.reports.length) throw new Error("Choose at least one report.");
        if (v.preset === "custom" && !(v.from && v.to)) throw new Error("Choose both the From and To dates.");
        await Admin.fetchDownload(`/api/admin/reports-export?${Admin.queryString({
          preset: v.preset,
          from: v.preset === "custom" ? v.from : "",
          to: v.preset === "custom" ? v.to : "",
          employee_type: v.employee_type,
          department: v.department,
          shift: v.shift,
          employees: v.employees.join(","),
          reports: v.reports.join(","),
          format: v.format,
        })}`);
        Admin.toast("Reports downloaded.", "success");
      },
    });
    const form = m.body.querySelector("form");
    const preset = form.querySelector('[name="preset"]');
    const customDates = ["from", "to"].map((n) => form.querySelector(`[name="${n}"]`).closest(".field"));
    const toggleDates = () => customDates.forEach((f) => { f.hidden = preset.value !== "custom"; });
    preset.addEventListener("change", toggleDates);
    toggleDates();
  }

  $("bulk").addEventListener("click", bulkExport);
  $("xlsx").addEventListener("click", () => exportCurrent("xlsx"));
  $("csv").addEventListener("click", () => exportCurrent("csv"));
  $("json").addEventListener("click", () => exportCurrent("json"));
  $("print").addEventListener("click", printReport);
  await table.refresh();
});
