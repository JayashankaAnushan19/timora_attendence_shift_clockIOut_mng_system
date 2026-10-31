Admin.start("dashboard", "Dashboard", async (root) => {
  const { esc } = T;
  const CARDS = [
    ["total_employees", "Total Employees"],
    ["clocked_in", "Currently Clocked In"],
    ["clocked_out", "Currently Clocked Out"],
    ["late_today", "Late Today"],
    ["not_scheduled", "Not Scheduled Today"],
    ["records_today", "Attendance Records Today"],
  ];

  root.innerHTML = `
    <div id="tzHint"></div>
    <div class="stat-row" id="cards"></div>
    <div class="two-col">
      <section class="panel"><div class="panel-head"><h2>Employee Attendance Overview</h2>
        <span class="muted" id="todayLabel"></span></div><div id="overview"></div></section>
      <section class="panel"><div class="panel-head"><h2>Recent Activity</h2></div><ul class="feed" id="feed"></ul></section>
    </div>`;

  let data = null;
  const overview = Admin.table(root.querySelector("#overview"), {
    sort: "employee_name",
    perPage: 25,
    empty: "No employees yet. Add employees to see today's attendance here.",
    columns: [
      { key: "employee_id", label: "ID", sortable: true },
      { key: "employee_name", label: "Employee", sortable: true },
      { key: "shift_name", label: "Shift", sortable: true },
      { key: "clock_in", label: "In", kind: "datetime", sortable: true },
      { key: "clock_out", label: "Out", kind: "datetime", sortable: true },
      { key: "status", label: "Status", kind: "status", sortable: true },
    ],
    load: Admin.localLoad(() => data.overview),
  });

  function renderFeed(items) {
    const feed = root.querySelector("#feed");
    if (!items.length) {
      feed.innerHTML = '<li class="empty">No clock activity yet.</li>';
      return;
    }
    feed.innerHTML = items.map((r) => {
      const out = Boolean(r.clock_out);
      return `<li><div><b>${esc(r.employee_name)}</b> <span class="muted">${esc(r.employee_id)}</span>
        <small>${out ? "Clocked out" : "Clocked in"} · ${esc(T.fmtDateTime(out ? r.clock_out : r.clock_in))}</small></div>
        ${Admin.statusPill(r.status)}</li>`;
    }).join("");
  }

  async function refresh() {
    data = await Admin.call("GET", "/api/admin/dashboard");
    root.querySelector("#cards").innerHTML = CARDS.map(([key, label]) =>
      `<div class="stat"><div class="label">${esc(label)}</div><div class="value">${esc(data.cards[key])}</div></div>`).join("");
    root.querySelector("#todayLabel").textContent = T.fmtDate(data.today);
    renderFeed(data.recent);
    await overview.refresh();
  }

  const browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (T.display.timezone === "UTC" && browserTz && browserTz !== "UTC" && browserTz !== "Etc/UTC") {
    const canEdit = Admin.can("settings.manage");
    root.querySelector("#tzHint").innerHTML = `<div class="banner">The business timezone is still set to UTC, but this computer is on ${esc(browserTz)}.
      Attendance rules follow the business timezone. ${canEdit ? 'Set it in <a href="/admin/settings">System Settings</a>.' : "Ask a Super Admin to set it."}</div>`;
  }

  await refresh();
  setInterval(() => refresh().catch(() => {}), 30000);
});
