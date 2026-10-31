Admin.start("reports", "Attendance Reports", async (root) => {
  const { esc } = T;
  const [lookups, list] = await Promise.all([Admin.call("GET", "/api/admin/lookups"), Admin.call("GET", "/api/admin/reports")]);
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: T.display.timezone }).format(new Date());

  root.innerHTML = `<section class="panel">
    <div class="toolbar">
      <div><label for="report">Report</label><select id="report">${list.items.map((r) => `<option value="${esc(r.key)}">${esc(r.title)}</option>`).join("")}</select></div>
      <div><label for="preset">Date range</label><select id="preset">
        <option value="today">Today</option><option value="yesterday">Yesterday</option><option value="this_week">This Week</option>
        <option value="this_month">This Month</option><option value="custom">Custom Date Range</option></select></div>
      <div class="custom" hidden><label for="from">From</label><input id="from" type="date" value="${today}"></div>
      <div class="custom" hidden><label for="to">To</label><input id="to" type="date" value="${today}"></div>
      <div><label for="emp">Employee</label><select id="emp"><option value="">All</option>
        ${lookups.employees.map((e) => `<option value="${esc(e.employee_id)}">${esc(e.employee_id)} - ${esc(e.name)}</option>`).join("")}</select></div>
      <div><label for="dept">Department</label><select id="dept"><option value="">All</option>${lookups.departments.map((d) => `<option>${esc(d)}</option>`).join("")}</select></div>
      <div><label for="shift">Shift</label><select id="shift"><option value="">All</option>${lookups.shifts.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("")}</select></div>
      <div class="btn-group">
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
      department: $("dept").value,
      shift: $("shift").value,
    };
  }

  function toColumns(columns) {
    return columns.map((c) => ({
      key: c.key, label: c.label, kind: c.kind === "text" || c.kind === "int" ? undefined : c.kind,
      sortable: true, num: c.kind === "duration" || c.kind === "int",
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
      const cell = (row, c) => {
        const v = row[c.key];
        if (c.kind === "date") return esc(T.fmtDate(v));
        if (c.kind === "datetime") return esc(T.fmtDateTime(v));
        if (c.kind === "duration") return esc(T.fmtDuration(v));
        return esc(v);
      };
      $("printTitle").textContent = d.title;
      $("printRange").textContent = `${T.fmtDate(d.start)} to ${T.fmtDate(d.end)} · ${T.display.timezone} · ${Admin.state.display.company_name}`;
      $("printTbl").innerHTML = `<table><thead><tr>${cols.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("")}</tr></thead>
        <tbody>${d.items.map((r) => `<tr>${cols.map((c) => `<td class="${c.num ? "num" : ""}">${cell(r, c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
      $("tbl").classList.add("no-print");
      window.print();
    } catch (err) {
      Admin.toast(err.message, "error");
    }
  }

  ["report", "emp", "dept", "shift", "from", "to"].forEach((id) => $(id).addEventListener("change", () => {
    if (id === "report") table.state.sort = null;
    table.reload();
  }));
  $("preset").addEventListener("change", () => {
    root.querySelectorAll(".custom").forEach((el) => { el.hidden = $("preset").value !== "custom"; });
    table.reload();
  });
  $("csv").addEventListener("click", () => Admin.download(exportUrl("csv")));
  $("json").addEventListener("click", () => Admin.download(exportUrl("json")));
  $("print").addEventListener("click", printReport);
  await table.refresh();
});
