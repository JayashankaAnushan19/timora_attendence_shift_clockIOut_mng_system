Admin.start("critical", "Critical Operations", async (root) => {
  const { esc } = T;
  const [lookups, tables] = await Promise.all([
    Admin.call("GET", "/api/admin/lookups"),
    Admin.call("GET", "/api/admin/critical/tables"),
  ]);
  const selected = new Set();

  root.innerHTML = `
    <div class="banner danger"><b>These actions delete data and cannot be undone here.</b>
      Each one shows what will be removed, asks for your password, and saves a backup first. Backups are listed under
      <a href="/admin/backup">Backup &amp; Restore</a>.</div>

    <section class="panel">
      <div class="panel-head"><h2>Purge by Table</h2></div>
      <div class="toolbar">
        <div class="grow"><label for="table">Table</label><select id="table">
          ${tables.items.map((t) => `<option value="${esc(t.key)}">${esc(t.label)} (${esc(t.count)})</option>`).join("")}</select></div>
        <div data-filter="dates"><label for="from">From</label><input id="from" type="date"></div>
        <div data-filter="dates"><label for="to">To</label><input id="to" type="date"></div>
        <div data-filter="employee"><label for="emp">Employee</label><select id="emp"><option value="">All employees</option>
          ${lookups.employees.map((e) => `<option value="${esc(e.employee_id)}">${esc(e.employee_id)} - ${esc(e.name)}</option>`).join("")}</select></div>
        <div class="btn-group">
          <button class="btn" id="selectPage" type="button">Select Page</button>
          <button class="btn btn-danger" id="purgeSelected" type="button" disabled>Purge Selected</button>
          <button class="btn btn-danger" id="purgeAll" type="button">Purge All Shown</button>
        </div>
      </div>
      <div id="tbl"></div>
    </section>

    <div class="critical-grid">
      <section class="panel"><div class="panel-head"><h2>Full Cleanup</h2></div>
        <div class="panel-body">
          <p>Deletes all employees, attendance, overtime, salary data, leave, holidays and the audit log.</p>
          <p class="muted">Keeps shifts, settings and administrator accounts. Employee IDs continue from the last number and are not reused.</p>
          <button class="btn btn-danger" id="cleanup" type="button">Clean Up All Data</button>
        </div></section>
      <section class="panel"><div class="panel-head"><h2>Reset to Default</h2></div>
        <div class="panel-body">
          <p>Returns Timora to a new installation: default shifts, default settings and the starter employee.</p>
          <p class="muted">Deletes everything else, including all administrator accounts. You are logged out, and the login returns to <code>admin</code> / <code>admin@123</code>.</p>
          <button class="btn btn-danger" id="reset" type="button">Reset Everything</button>
        </div></section>
    </div>`;

  const $ = (id) => root.querySelector(`#${id}`);
  const filters = () => ({ table: $("table").value, from: $("from").value, to: $("to").value, employee: $("emp").value });

  function renderSelection() {
    const button = $("purgeSelected");
    button.disabled = selected.size === 0;
    button.textContent = selected.size ? `Purge Selected (${selected.size})` : "Purge Selected";
  }

  function applyTable() {
    const info = tables.items.find((t) => t.key === $("table").value);
    root.querySelectorAll('[data-filter="dates"]').forEach((el) => { el.hidden = !info.has_dates; });
    root.querySelectorAll('[data-filter="employee"]').forEach((el) => { el.hidden = !info.has_employee; });
    if (!info.has_dates) { $("from").value = ""; $("to").value = ""; }
    if (!info.has_employee) $("emp").value = "";
  }

  const pickColumn = {
    key: "_pick", label: "",
    render: (r) => `<input type="checkbox" class="purge-pick" data-id="${esc(r.id)}" aria-label="Select row ${esc(r.id)}" ${selected.has(r.id) ? "checked" : ""}>`,
  };

  const table = Admin.table($("tbl"), {
    columns: [pickColumn],
    empty: "No records match.",
    load: async (p) => {
      const data = await Admin.call("GET", `/api/admin/critical/rows?${Admin.queryString({ ...filters(), page: p.page, per_page: p.per_page })}`);
      return { items: data.items, total: data.total, columns: [pickColumn, ...data.columns] };
    },
  });

  $("tbl").addEventListener("change", (event) => {
    if (!event.target.classList.contains("purge-pick")) return;
    const id = Number(event.target.dataset.id);
    if (event.target.checked) selected.add(id); else selected.delete(id);
    renderSelection();
  });

  async function runOperation(body, title, confirmLabel) {
    let preview;
    try {
      preview = await Admin.call("POST", "/api/admin/critical/preview", body);
    } catch (err) {
      Admin.toast(err.message, "error");
      return;
    }
    if (!preview.total && body.scope !== "reset") {
      Admin.toast("Nothing matches, so there is nothing to purge.", "error");
      return;
    }
    const summary = preview.impact.map((r) => `${r.label}: ${r.count.toLocaleString("en-US")}`).join(" · ");
    const defaults = body.scope === "reset" ? " Afterwards you are logged out and the login returns to admin / admin@123." : "";
    Admin.formModal({
      title, submitLabel: confirmLabel,
      intro: `This will permanently delete: ${summary || "no records"}.${defaults} A backup is saved first.`,
      fields: [
        { name: "phrase", label: `Type ${preview.phrase} to confirm`, required: true, full: true },
        { name: "password", label: "Your password", type: "password", required: true, full: true },
      ],
      onSubmit: async (v) => {
        const result = await Admin.call("POST", "/api/admin/critical/execute", { ...body, ...v });
        if (result.logged_out) {
          location.href = "/admin/login";
          return new Promise(() => {});
        }
        Admin.toast(`${result.message} Backup: ${result.safety_copy}`, "success");
        selected.clear();
        renderSelection();
        const fresh = await Admin.call("GET", "/api/admin/critical/tables");
        tables.items = fresh.items;
        const current = $("table").value;
        $("table").innerHTML = tables.items.map((t) => `<option value="${esc(t.key)}">${esc(t.label)} (${esc(t.count)})</option>`).join("");
        $("table").value = current;
        await table.reload();
        return undefined;
      },
    });
    const button = document.querySelector(".modal-back:last-of-type .modal button[type=submit]");
    if (button) { button.classList.remove("btn-primary"); button.classList.add("btn-danger"); }
  }

  ["from", "to", "emp"].forEach((id) => $(id).addEventListener("change", () => table.reload()));
  $("table").addEventListener("change", () => {
    selected.clear();
    renderSelection();
    applyTable();
    table.reload();
  });
  $("selectPage").addEventListener("click", () => {
    $("tbl").querySelectorAll(".purge-pick").forEach((box) => { box.checked = true; selected.add(Number(box.dataset.id)); });
    renderSelection();
  });
  $("purgeSelected").addEventListener("click", () => runOperation(
    { scope: "table", table: $("table").value, mode: "selected", ids: [...selected] }, "Purge Selected Records", "Purge Selected"));
  $("purgeAll").addEventListener("click", () => runOperation(
    { scope: "table", mode: "filtered", ...filters() }, "Purge All Shown Records", "Purge All Shown"));
  $("cleanup").addEventListener("click", () => runOperation({ scope: "cleanup" }, "Clean Up All Data", "Clean Up All Data"));
  $("reset").addEventListener("click", () => runOperation({ scope: "reset" }, "Reset Everything to Default", "Reset Everything"));

  applyTable();
  await table.refresh();
});
