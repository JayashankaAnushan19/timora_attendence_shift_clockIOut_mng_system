Admin.start("backup", "Backup & Restore", async (root) => {
  const { esc } = T;
  root.innerHTML = `
    <section class="panel"><div class="panel-head"><h2>Database Backup</h2><span class="muted" id="last"></span></div>
      <div class="panel-body" style="display:flex;gap:10px;flex-wrap:wrap">
        <button class="btn btn-primary" id="create" type="button">Create &amp; Download Backup</button>
      </div></section>
    <section class="panel"><div class="panel-head"><h2>Export Data</h2></div>
      <div class="panel-body" style="display:flex;gap:10px;flex-wrap:wrap">
        <a class="btn" href="/api/admin/export/employees?format=csv" download>Employees (CSV)</a>
        <a class="btn" href="/api/admin/export/employees?format=json" download>Employees (JSON)</a>
        <a class="btn" href="/api/admin/export/attendance?format=csv" download>Attendance (CSV)</a>
        <a class="btn" href="/api/admin/export/attendance?format=json" download>Attendance (JSON)</a>
        <a class="btn" href="/api/admin/export/config" download>System Configuration (JSON)</a>
      </div></section>
    <section class="panel"><div class="panel-head"><h2>Restore From File</h2></div>
      <div class="panel-body" style="display:flex;flex-direction:column;gap:12px">
        <div class="banner">Restoring replaces all current data. Timora saves a copy of the current database first, which appears in the list below.</div>
        <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
          <input id="file" type="file" accept=".db" style="max-width:360px">
          <button class="btn btn-danger" id="restore" type="button">Restore Database</button>
        </div>
      </div></section>
    <section class="panel"><div class="panel-head"><h2>Saved Backups</h2></div><div id="tbl"></div></section>`;

  const fmtSize = (bytes) => bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`;

  async function afterRestore(result) {
    Admin.toast(result.message, "success");
    setTimeout(() => location.reload(), 1500);
  }

  const table = Admin.table(root.querySelector("#tbl"), {
    empty: "No backups yet.",
    columns: [
      { key: "name", label: "File" },
      { key: "created", label: "Created", kind: "datetime" },
      { key: "size", label: "Size", num: true, render: (f) => esc(fmtSize(f.size)) },
    ],
    actions: (f) => [
      { label: "Download", onClick: () => Admin.download(`/api/admin/backup/files/${encodeURIComponent(f.name)}`) },
      {
        label: "Restore", kind: "danger",
        onClick: async () => {
          if (!await Admin.confirm({ title: "Restore database", message: `Replace all current data with ${f.name}? A copy of the current data is saved first.`, confirmLabel: "Restore", danger: true })) return;
          try {
            await afterRestore(await Admin.call("POST", `/api/admin/backup/restore/${encodeURIComponent(f.name)}`, {}));
          } catch (err) { Admin.toast(err.message, "error"); }
        },
      },
    ],
    load: async (p) => {
      const data = await Admin.call("GET", "/api/admin/backup");
      root.querySelector("#last").textContent = data.last_backup_at
        ? `Last backup: ${T.fmtDateTime(data.last_backup_at)}` : "No backup has been made yet";
      return { items: data.files.slice((p.page - 1) * p.per_page, p.page * p.per_page), total: data.files.length };
    },
  });

  root.querySelector("#create").addEventListener("click", async (event) => {
    const button = event.target;
    button.disabled = true;
    try {
      const r = await Admin.call("POST", "/api/admin/backup/create", {});
      Admin.download(`/api/admin/backup/files/${encodeURIComponent(r.name)}`);
      Admin.toast(r.message, "success");
      await table.refresh();
    } catch (err) {
      Admin.toast(err.message, "error");
    } finally {
      button.disabled = false;
    }
  });

  root.querySelector("#restore").addEventListener("click", async () => {
    const file = root.querySelector("#file").files[0];
    if (!file) { Admin.toast("Choose a backup file first.", "error"); return; }
    if (!await Admin.confirm({ title: "Restore database", message: `Replace all current data with ${file.name}? A copy of the current data is saved first.`, confirmLabel: "Restore", danger: true })) return;
    const data = new FormData();
    data.append("backup", file);
    try {
      await afterRestore(await Admin.call("POST", "/api/admin/backup/restore", data));
    } catch (err) { Admin.toast(err.message, "error"); }
  });

  await table.refresh();
});
