/* Admin shell (sidebar, header) and reusable table, modal and form helpers. */
const Admin = (() => {
  const { esc } = T;
  const ICONS = {
    dashboard: "M4 13h6V4H4zM14 20h6V4h-6zM4 20h6v-3H4z",
    employees: "M16 19v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1M9.5 10a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM21 19v-1a4 4 0 0 0-3-3.8M15.5 3.2a3.5 3.5 0 0 1 0 6.6",
    shifts: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2",
    assignments: "M8 7h12M8 12h12M8 17h12M4 7h.01M4 12h.01M4 17h.01",
    attendance: "M9 11l2.5 2.5L16 9M5 4h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z",
    reports: "M5 20V10M12 20V4M19 20v-7",
    overtime: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 8v8M8 12h8",
    users: "M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.500-7-10V6z",
    settings: "M4 7h10M18 7h2M4 17h2M10 17h10M16 9.500a2.500 2.500 0 1 0 0-5 2.500 2.500 0 0 0 0 5zM8 19.500a2.500 2.500 0 1 0 0-5 2.500 2.500 0 0 0 0 5z",
    backup: "M12 3v12M7 10l5 5 5-5M4 20h16",
    logout: "M15 17l5-5-5-5M20 12H9M11 21H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h6",
    home: "M3 11l9-8 9 8M5 9.500V20a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V9.500",
  };
  const NAV = [
    ["dashboard", "Dashboard", "dashboard.view"],
    ["employees", "Employees", "employees.view"],
    ["shifts", "Shifts", "shifts.view"],
    ["assignments", "Shift Assignments", "shifts.view"],
    ["attendance", "Attendance Records", "attendance.view"],
    ["overtime", "Overtime", "overtime.manage"],
    ["reports", "Attendance Reports", "reports.view"],
    ["users", "User Access Management", "users.manage"],
    ["settings", "System Settings", "settings.manage"],
    ["backup", "Backup & Restore", "backup.manage"],
  ];
  const icon = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${ICONS[name]}"/></svg>`;

  const state = { admin: null, display: null };

  function can(permission) {
    return state.admin && state.admin.permissions.includes(permission);
  }

  /* Calls the API; a lost session sends the user to the login page. */
  async function call(method, url, body) {
    try {
      return await T.api(method, url, body);
    } catch (err) {
      if (err.status === 401 || err.code === "PASSWORD_CHANGE_REQUIRED") {
        location.href = "/admin/login";
        return new Promise(() => {});
      }
      throw err;
    }
  }

  function toast(message, kind = "") {
    let area = document.querySelector(".toast-area");
    if (!area) {
      area = document.createElement("div");
      area.className = "toast-area";
      area.setAttribute("role", "status");
      document.body.append(area);
    }
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = message;
    area.append(el);
    setTimeout(() => el.remove(), kind === "error" ? 7000 : 3500);
  }

  function renderShell(page, title) {
    const nav = NAV.filter(([, , perm]) => can(perm)).map(([key, label]) => {
      const active = key === page || (page === "employee" && key === "employees");
      return `<a href="/admin/${key}" class="${active ? "active" : ""}">${icon(key)}<span>${esc(label)}</span></a>`;
    }).join("");
    const app = document.getElementById("app");
    app.innerHTML = `
      <div class="shell">
        <aside class="sidebar no-print">
          <a class="side-brand" href="/" title="Go to home page"><img src="/assets/img/logo.svg" alt=""><strong>${esc(state.display.app_name)}</strong></a>
          <nav class="side-nav">${nav}</nav>
          <div class="side-nav side-foot"><a href="/">${icon("home")}<span>Home</span></a>
            <button id="logout" type="button">${icon("logout")}<span>Logout</span></button></div>
        </aside>
        <div class="main">
          <header class="topbar">
            <h1>${esc(title)}</h1>
            <div class="topbar-right">
              <div class="topbar-time"><b id="hTime"></b><span id="hDate"></span></div>
              <div class="topbar-user"><b>${esc(state.admin.full_name || state.admin.username)}</b>
                <span class="muted">${esc(state.admin.role)}</span> ·
                <button class="link-btn" id="changePw" type="button">Change password</button></div>
            </div>
          </header>
          <main class="content" id="content"></main>
        </div>
      </div>`;
    document.getElementById("logout").addEventListener("click", async () => {
      try { await T.api("POST", "/api/auth/logout", {}); } finally { location.href = "/admin/login"; }
    });
    document.getElementById("changePw").addEventListener("click", changePasswordDialog);
    const tick = () => {
      const now = new Date();
      document.getElementById("hTime").textContent = T.fmtClock(now);
      document.getElementById("hDate").textContent = T.fmtToday(now);
    };
    tick();
    setInterval(tick, 1000);
  }

  function changePasswordDialog() {
    formModal({
      title: "Change password",
      submitLabel: "Save password",
      fields: [
        { name: "current_password", label: "Current password", type: "password", required: true, full: true },
        { name: "new_password", label: "New password (at least 8 characters)", type: "password", required: true, full: true },
      ],
      onSubmit: async (values) => {
        await call("POST", "/api/auth/change-password", values);
        toast("Password changed.", "success");
      },
    });
  }

  async function start(page, title, render) {
    let me;
    try {
      me = await T.api("GET", "/api/admin/me");
    } catch (err) {
      if (err.status === 401) { location.href = "/admin/login"; return; }
      document.getElementById("app").innerHTML = `<div class="content"><div class="banner danger">${esc(err.message)}</div></div>`;
      return;
    }
    state.admin = me.admin;
    state.display = me.display;
    T.setDisplay(me.display);
    document.title = `${title} · ${me.display.app_name}`;
    renderShell(page, title);
    try {
      await render(document.getElementById("content"));
    } catch (err) {
      document.getElementById("content").innerHTML = `<div class="banner danger">${esc(err.message || "This page could not be loaded.")}</div>`;
    }
  }

  /* ---------- Table ---------- */
  function statusPill(status) {
    const map = {
      Present: "pill-green", Overtime: "pill-blue", Late: "pill-orange", "Half Day": "pill-orange",
      "Early Departure": "pill-orange", Absent: "pill-red", Incomplete: "pill-red",
      active: "pill-green", approved: "pill-green",
    };
    const label = status === "Incomplete" ? "Incomplete Attendance" : status;
    return `<span class="pill ${map[status] || ""}">${esc(label)}</span>`;
  }

  function fmtMoney(value) {
    return Number(value || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function renderCell(row, col) {
    if (col.render) return col.render(row);
    const v = row[col.key];
    switch (col.kind) {
      case "date": return esc(T.fmtDate(v));
      case "datetime": return esc(T.fmtDateTime(v)) || '<span class="muted">-</span>';
      case "duration": return esc(T.fmtDuration(v));
      case "clock_date": return v ? esc(T.fmtDate(T.toLocalInput(v).slice(0, 10))) : '<span class="muted">-</span>';
      case "clock_time": return v ? esc(T.fmtClock(new Date(v))) : '<span class="muted">-</span>';
      case "money": return esc(fmtMoney(v));
      case "status": return statusPill(v);
      default: return esc(v);
    }
  }

  /**
   * load({page, per_page, sort, dir}) -> {items, total}
   * columns: {key, label, kind, sortable, num, wrap, render(row) -> safe HTML}
   * actions(row) -> [{label, onClick, kind}]
   */
  function table(container, { columns, load, actions, sort = null, dir = "asc", perPage = 25, empty = "Nothing to show." }) {
    const st = { page: 1, perPage, sort, dir, total: 0, cols: columns };
    container.innerHTML = `<div class="table-wrap"><table><thead></thead><tbody></tbody></table></div>
      <div class="pager"><span class="pager-info"></span><div class="pager-btns">
        <label>Rows <select class="pp"><option>25</option><option>50</option><option>100</option></select></label>
        <button class="btn btn-sm prev" type="button">Previous</button>
        <span class="pg"></span>
        <button class="btn btn-sm next" type="button">Next</button></div></div>`;
    const [thead, tbody] = [container.querySelector("thead"), container.querySelector("tbody")];
    const pp = container.querySelector(".pp");
    pp.value = String(perPage);

    function renderHead() {
      const cells = st.cols.map((c) => {
        const arrow = st.sort === c.key ? `<span class="arrow">${st.dir === "asc" ? "▲" : "▼"}</span>` : "";
        return `<th class="${c.sortable ? "sortable" : ""} ${c.num ? "num" : ""}" data-key="${esc(c.key)}">${esc(c.label)}${arrow}</th>`;
      }).join("");
      thead.innerHTML = `<tr>${cells}${actions ? "<th></th>" : ""}</tr>`;
      thead.querySelectorAll("th.sortable").forEach((th) => th.addEventListener("click", () => {
        const key = th.dataset.key;
        if (st.sort === key) st.dir = st.dir === "asc" ? "desc" : "asc";
        else { st.sort = key; st.dir = "asc"; }
        st.page = 1;
        refresh();
      }));
    }

    async function refresh() {
      let data;
      try {
        data = await load({ page: st.page, per_page: st.perPage, sort: st.sort, dir: st.dir });
      } catch (err) {
        tbody.innerHTML = `<tr><td class="empty-row" colspan="${st.cols.length + 1}">${esc(err.message)}</td></tr>`;
        return;
      }
      if (data.columns) st.cols = data.columns;
      st.total = data.total;
      renderHead();
      tbody.replaceChildren();
      if (!data.items.length) {
        tbody.innerHTML = `<tr><td class="empty-row" colspan="${st.cols.length + 1}">${esc(empty)}</td></tr>`;
      }
      for (const row of data.items) {
        const tr = document.createElement("tr");
        tr.innerHTML = st.cols.map((c) => `<td class="${c.num ? "num" : ""} ${c.wrap ? "wrap" : ""}">${renderCell(row, c)}</td>`).join("");
        if (actions) {
          const td = document.createElement("td");
          td.className = "actions";
          for (const a of actions(row)) {
            const b = document.createElement("button");
            b.type = "button";
            b.className = `btn btn-sm ${a.kind === "danger" ? "btn-danger" : ""}`;
            b.textContent = a.label;
            b.addEventListener("click", a.onClick);
            td.append(b);
          }
          tr.append(td);
        }
        tbody.append(tr);
      }
      const pages = Math.max(Math.ceil(st.total / st.perPage), 1);
      if (st.page > pages) { st.page = pages; return refresh(); }
      const first = st.total ? (st.page - 1) * st.perPage + 1 : 0;
      container.querySelector(".pager-info").textContent = `${first}-${Math.min(st.page * st.perPage, st.total)} of ${st.total}`;
      container.querySelector(".pg").textContent = `Page ${st.page} of ${pages}`;
      container.querySelector(".prev").disabled = st.page <= 1;
      container.querySelector(".next").disabled = st.page >= pages;
    }

    container.querySelector(".prev").addEventListener("click", () => { st.page--; refresh(); });
    container.querySelector(".next").addEventListener("click", () => { st.page++; refresh(); });
    pp.addEventListener("change", () => { st.perPage = +pp.value; st.page = 1; refresh(); });

    return { refresh, reload() { st.page = 1; return refresh(); }, state: st };
  }

  /* Pagination and sorting for rows already in the browser */
  function localLoad(getRows) {
    return async ({ page, per_page, sort, dir }) => {
      let rows = [...await getRows()];
      if (sort) {
        rows.sort((a, b) => {
          const [x, y] = [a[sort] ?? "", b[sort] ?? ""];
          const r = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
          return dir === "asc" ? r : -r;
        });
      }
      return { items: rows.slice((page - 1) * per_page, page * per_page), total: rows.length };
    };
  }

  function queryString(params) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") q.set(k, v);
    return q.toString();
  }

  /* ---------- Modal ---------- */
  function modal({ title, body = "", wide = false, footer = true }) {
    const back = document.createElement("div");
    back.className = "modal-back";
    back.innerHTML = `<div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true" aria-label="${esc(title)}">
      <div class="modal-head"><h2>${esc(title)}</h2><button class="modal-close" type="button" aria-label="Close">&times;</button></div>
      <div class="modal-body">${body}</div>${footer ? '<div class="modal-foot"></div>' : ""}</div>`;
    const close = () => { back.remove(); document.removeEventListener("keydown", onKey); };
    const onKey = (event) => { if (event.key === "Escape") close(); };
    document.addEventListener("keydown", onKey);
    back.querySelector(".modal-close").addEventListener("click", close);
    document.body.append(back);
    return { el: back, body: back.querySelector(".modal-body"), foot: back.querySelector(".modal-foot"), close };
  }

  function confirm({ title, message, confirmLabel = "Confirm", danger = false }) {
    return new Promise((resolve) => {
      const m = modal({ title, body: `<p style="margin:0">${esc(message)}</p>` });
      const cancel = document.createElement("button");
      cancel.className = "btn";
      cancel.type = "button";
      cancel.textContent = "Cancel";
      cancel.addEventListener("click", () => { m.close(); resolve(false); });
      const go = document.createElement("button");
      go.className = `btn ${danger ? "btn-danger" : "btn-primary"}`;
      go.type = "button";
      go.textContent = confirmLabel;
      go.addEventListener("click", () => { m.close(); resolve(true); });
      m.foot.append(cancel, go);
      go.focus();
    });
  }

  const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  function fieldHtml(f, value) {
    const id = `f_${f.name}`;
    const req = f.required ? "required" : "";
    const dis = f.disabled ? "disabled" : "";
    const wrap = (inner, cls = "") => `<div class="field ${f.full ? "full" : ""} ${cls}">${inner}${f.help ? `<span class="help">${esc(f.help)}</span>` : ""}</div>`;
    const label = `<label for="${id}">${esc(f.label)}${f.required ? " *" : ""}</label>`;
    switch (f.type) {
      case "textarea":
        return wrap(`${label}<textarea id="${id}" name="${esc(f.name)}" rows="${f.rows || 2}" ${req} ${dis}>${esc(value)}</textarea>`);
      case "select": {
        const opts = f.options.map((o) => {
          const [v, l] = Array.isArray(o) ? o : [o, o];
          return `<option value="${esc(v)}" ${String(v) === String(value ?? "") ? "selected" : ""}>${esc(l)}</option>`;
        }).join("");
        return wrap(`${label}<select id="${id}" name="${esc(f.name)}" ${req} ${dis}>${opts}</select>`);
      }
      case "checkbox":
        return wrap(`<input id="${id}" name="${esc(f.name)}" type="checkbox" ${value ? "checked" : ""} ${dis}><label for="${id}">${esc(f.label)}</label>`, "field-check");
      case "weekdays": {
        const chosen = value || [];
        const boxes = WEEKDAYS.map((d, i) => `<label><input type="checkbox" data-day="${i}" ${chosen.includes(i) ? "checked" : ""}>${d}</label>`).join("");
        return wrap(`<label>${esc(f.label)}</label><div class="weekdays" data-weekdays="${esc(f.name)}">${boxes}</div>`);
      }
      case "checklist": {
        const chosen = value || [];
        const boxes = f.options.map(([v, l]) => `<label data-text="${esc(String(l).toLowerCase())}"><input type="checkbox" value="${esc(v)}" ${chosen.includes(v) ? "checked" : ""}>${esc(l)}</label>`).join("");
        return wrap(`<label>${esc(f.label)}${f.required ? " *" : ""}</label>
          <input type="search" placeholder="Filter by name or ID" data-checkfilter="${esc(f.name)}">
          <div class="checklist" data-checklist="${esc(f.name)}">${boxes}</div>`);
      }
      default: {
        const extras = [f.min !== undefined ? `min="${f.min}"` : "", f.max !== undefined ? `max="${f.max}"` : "", f.step ? `step="${f.step}"` : "", f.placeholder ? `placeholder="${esc(f.placeholder)}"` : ""].join(" ");
        return wrap(`${label}<input id="${id}" name="${esc(f.name)}" type="${f.type || "text"}" value="${esc(value)}" ${extras} ${req} ${dis} autocomplete="off">`);
      }
    }
  }

  function readFields(root, fields) {
    const out = {};
    for (const f of fields) {
      if (f.type === "weekdays") {
        out[f.name] = [...root.querySelectorAll(`[data-weekdays="${f.name}"] input:checked`)].map((i) => +i.dataset.day);
      } else if (f.type === "checklist") {
        out[f.name] = [...root.querySelectorAll(`[data-checklist="${f.name}"] input:checked`)].map((i) => i.value);
      } else if (f.type === "checkbox") {
        out[f.name] = root.querySelector(`[name="${f.name}"]`).checked;
      } else {
        const el = root.querySelector(`[name="${f.name}"]`);
        out[f.name] = f.type === "number" && el.value !== "" ? Number(el.value) : el.value;
      }
    }
    return out;
  }

  function bindFieldBehaviour(root) {
    root.querySelectorAll("[data-checkfilter]").forEach((input) => {
      const list = root.querySelector(`[data-checklist="${input.dataset.checkfilter}"]`);
      input.addEventListener("input", () => {
        const q = input.value.toLowerCase();
        list.querySelectorAll("label").forEach((l) => { l.hidden = !l.dataset.text.includes(q); });
      });
    });
  }

  function formModal({ title, fields, values = {}, submitLabel = "Save", onSubmit, wide = false, intro = "" }) {
    const grid = fields.map((f) => fieldHtml(f, values[f.name] ?? f.value ?? "")).join("");
    const m = modal({
      title, wide, footer: false,
      body: `<form novalidate><div class="form-error" hidden></div>${intro ? `<p class="muted" style="margin-top:0">${esc(intro)}</p>` : ""}
        <div class="form-grid">${grid}</div>
        <div class="modal-foot" style="margin:16px -18px -16px"><button type="button" class="btn cancel">Cancel</button>
        <button type="submit" class="btn btn-primary">${esc(submitLabel)}</button></div></form>`,
    });
    const form = m.body.querySelector("form");
    const errorBox = form.querySelector(".form-error");
    bindFieldBehaviour(form);
    form.querySelector(".cancel").addEventListener("click", m.close);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      errorBox.hidden = true;
      const missing = fields.find((f) => f.required && f.type !== "checklist" && !form.querySelector(`[name="${f.name}"]`).value.trim());
      if (missing) {
        errorBox.textContent = `${missing.label} is required.`;
        errorBox.hidden = false;
        return;
      }
      const submit = form.querySelector('[type="submit"]');
      submit.disabled = true;
      try {
        await onSubmit(readFields(form, fields));
        m.close();
      } catch (err) {
        errorBox.textContent = err.message;
        errorBox.hidden = false;
        m.el.scrollTop = 0;
      } finally {
        submit.disabled = false;
      }
    });
    const first = form.querySelector("input:not([type=checkbox]):not([disabled]), select, textarea");
    if (first) first.focus();
    return m;
  }

  /* Tabs: {key, label, render(container)} */
  function tabs(root, items) {
    const bar = document.createElement("div");
    bar.className = "tabs no-print";
    const area = document.createElement("div");
    area.style.display = "contents";
    root.append(bar, area);
    const panels = {};
    const show = (key) => {
      bar.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.key === key));
      for (const [k, el] of Object.entries(panels)) el.hidden = k !== key;
      const item = items.find((i) => i.key === key);
      if (!panels[key]) {
        const el = document.createElement("div");
        el.style.cssText = "display:flex;flex-direction:column;gap:16px";
        panels[key] = el;
        area.append(el);
        item.render(el);
      }
    };
    items.forEach((item) => {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.key = item.key;
      b.textContent = item.label;
      b.addEventListener("click", () => show(item.key));
      bar.append(b);
    });
    show(items[0].key);
  }

  function download(url) {
    const a = document.createElement("a");
    a.href = url;
    a.download = "";
    document.body.append(a);
    a.click();
    a.remove();
  }

  /* Download through fetch, so a failure shows its message instead of saving an error as a file. */
  async function fetchDownload(url) {
    let response;
    try {
      response = await fetch(url, { credentials: "same-origin" });
    } catch (err) {
      throw new Error("Could not reach the server. Please check the connection and try again.");
    }
    if (!response.ok) {
      if (response.status === 401) { location.href = "/admin/login"; return new Promise(() => {}); }
      const data = await response.json().catch(() => ({}));
      throw new Error(data.message || "The export could not be created.");
    }
    const match = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "");
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await response.blob());
    link.download = match ? match[1] : "timora-export";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 60000);
  }

  function debounce(fn, ms = 250) {
    let timer;
    return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
  }

  return { start, call, can, toast, table, localLoad, queryString, modal, confirm, formModal, tabs, statusPill, renderCell, fmtMoney, download, fetchDownload, debounce, state, WEEKDAYS };
})();
