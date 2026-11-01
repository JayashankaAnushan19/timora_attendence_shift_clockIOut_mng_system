Admin.start("users", "User Access Management", async (root) => {
  const { esc } = T;
  const rolesData = await Admin.call("GET", "/api/admin/roles");
  const PERMISSION_LABELS = {
    "dashboard.view": "View dashboard",
    "employees.view": "View employees",
    "employees.manage": "Manage employees",
    "shifts.view": "View shifts",
    "shifts.manage": "Manage shifts and assignments",
    "attendance.view": "View attendance",
    "attendance.manage": "Correct attendance and leave",
    "reports.view": "View and export reports",
    "users.manage": "Manage administrators and roles",
    "settings.manage": "Manage system settings",
    "backup.manage": "Backup and restore",
    "audit.view": "View audit log",
    "overtime.manage": "Add and cancel overtime",
    "payroll.manage": "View and edit salary and OT rates",
  };

  function renderUsers(panel) {
    panel.innerHTML = `<section class="panel">
      <div class="panel-head"><h2>Administrators</h2><button class="btn btn-primary" id="addUser" type="button">Create User</button></div>
      <div id="tbl"></div></section>`;
    const roleOptions = rolesData.items.map((r) => [r.id, r.name]);

    function createForm() {
      Admin.formModal({
        title: "Create User", submitLabel: "Create user",
        intro: "The user must choose a new password at first login.",
        fields: [
          { name: "username", label: "Username", required: true },
          { name: "full_name", label: "Full Name" },
          { name: "role_id", label: "Role", type: "select", options: roleOptions, required: true },
          { name: "password", label: "Temporary Password", type: "password", required: true, help: "At least 8 characters." },
        ],
        onSubmit: async (values) => {
          const r = await Admin.call("POST", "/api/admin/users", values);
          Admin.toast(r.message, "success");
          await table.refresh();
        },
      });
    }

    function editForm(user) {
      Admin.formModal({
        title: `Edit ${user.username}`, values: user,
        fields: [
          { name: "full_name", label: "Full Name" },
          { name: "role_id", label: "Role", type: "select", options: roleOptions, required: true },
        ],
        onSubmit: async (values) => {
          const r = await Admin.call("PUT", `/api/admin/users/${user.id}`, values);
          Admin.toast(r.message, "success");
          await table.refresh();
        },
      });
    }

    function resetForm(user) {
      Admin.formModal({
        title: `Reset password: ${user.username}`, submitLabel: "Reset password",
        intro: "The user is logged out and must choose a new password at next login.",
        fields: [{ name: "password", label: "Temporary Password", type: "password", required: true, full: true, help: "At least 8 characters." }],
        onSubmit: async (values) => {
          const r = await Admin.call("POST", `/api/admin/users/${user.id}/reset-password`, values);
          Admin.toast(r.message, "success");
          await table.refresh();
        },
      });
    }

    async function toggle(user) {
      const activate = !user.is_active;
      if (!await Admin.confirm({
        title: activate ? "Activate user" : "Deactivate user",
        message: `${activate ? "Activate" : "Deactivate"} ${user.username}?`,
        confirmLabel: activate ? "Activate" : "Deactivate", danger: !activate,
      })) return;
      try {
        const r = await Admin.call("POST", `/api/admin/users/${user.id}/status`, { is_active: activate });
        Admin.toast(r.message, "success");
        await table.refresh();
      } catch (err) { Admin.toast(err.message, "error"); }
    }

    const table = Admin.table(panel.querySelector("#tbl"), {
      sort: "username", empty: "No administrators.",
      columns: [
        { key: "username", label: "Username", sortable: true },
        { key: "full_name", label: "Name", sortable: true },
        { key: "role", label: "Role", sortable: true },
        { key: "last_login_at", label: "Last Login", kind: "datetime", sortable: true },
        { key: "is_active", label: "Status", render: (u) => Admin.statusPill(u.is_active ? "active" : "inactive") },
      ],
      actions: (u) => [
        { label: "Edit", onClick: () => editForm(u) },
        { label: "Reset Password", onClick: () => resetForm(u) },
        u.is_active ? { label: "Deactivate", kind: "danger", onClick: () => toggle(u) } : { label: "Activate", onClick: () => toggle(u) },
      ],
      load: Admin.localLoad(async () => (await Admin.call("GET", "/api/admin/users")).items),
    });
    panel.querySelector("#addUser").addEventListener("click", createForm);
    table.refresh();
  }

  function renderRoles(panel) {
    const roles = rolesData.items;
    const head = roles.map((r) => `<th>${esc(r.name)}</th>`).join("");
    const rows = rolesData.all_permissions.map((p) => `<tr><td>${esc(PERMISSION_LABELS[p] || p)}</td>${roles.map((r) => `
      <td><input type="checkbox" aria-label="${esc(r.name)}: ${esc(PERMISSION_LABELS[p] || p)}" data-role="${r.id}" data-perm="${esc(p)}"
        ${r.permissions.includes(p) ? "checked" : ""} ${r.locked ? "disabled" : ""}></td>`).join("")}</tr>`).join("");
    panel.innerHTML = `<section class="panel">
      <div class="panel-head"><h2>Role Permissions</h2><button class="btn btn-primary" id="saveRoles" type="button">Save Permissions</button></div>
      <div class="table-wrap"><table class="perm-table"><thead><tr><th>Permission</th>${head}</tr></thead><tbody>${rows}</tbody></table></div>
      <div class="panel-body muted">Permissions are enforced by the server on every request. Super Admin always has full access.</div></section>`;
    panel.querySelector("#saveRoles").addEventListener("click", async () => {
      try {
        for (const role of roles.filter((r) => !r.locked)) {
          const permissions = [...panel.querySelectorAll(`input[data-role="${role.id}"]:checked`)].map((i) => i.dataset.perm);
          await Admin.call("PUT", `/api/admin/roles/${role.id}`, { permissions });
          role.permissions = permissions;
        }
        Admin.toast("Role permissions updated.", "success");
      } catch (err) { Admin.toast(err.message, "error"); }
    });
  }

  function renderAudit(panel) {
    panel.innerHTML = '<section class="panel"><div class="panel-head"><h2>Audit Log</h2></div><div id="auditTbl"></div></section>';
    Admin.table(panel.querySelector("#auditTbl"), {
      perPage: 50, empty: "No activity recorded.",
      columns: [
        { key: "created_at", label: "When", kind: "datetime" },
        { key: "admin_username", label: "Administrator" },
        { key: "action", label: "Action" },
        { key: "entity_id", label: "Target" },
        { key: "ip_address", label: "IP Address" },
        { key: "details", label: "Details", wrap: true, render: (a) => `<span class="muted" style="font-size:12px;overflow-wrap:anywhere">${esc(a.details === "{}" ? "" : a.details)}</span>` },
      ],
      load: (p) => Admin.call("GET", `/api/admin/audit?page=${p.page}&per_page=${p.per_page}`),
    }).refresh();
  }

  const tabs = [
    { key: "users", label: "Administrators", render: renderUsers },
    { key: "roles", label: "Roles & Permissions", render: renderRoles },
  ];
  if (Admin.can("audit.view")) tabs.push({ key: "audit", label: "Audit Log", render: renderAudit });
  Admin.tabs(root, tabs);
});
