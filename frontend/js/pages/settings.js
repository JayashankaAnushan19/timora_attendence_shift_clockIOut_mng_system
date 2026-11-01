Admin.start("settings", "System Settings", async (root) => {
  const { esc } = T;
  const [{ settings, logo_rev: initialLogo }, { timezones }] = await Promise.all([
    Admin.call("GET", "/api/admin/settings"),
    Admin.call("GET", "/api/admin/settings/timezones"),
  ]);
  let logoRev = initialLogo;

  const SECTIONS = [
    ["General Settings", [
      ["company_name", "Company Name", "text"],
      ["app_name", "Application Name", "text"],
      ["timezone", "Business Timezone", "timezone"],
      ["date_format", "Date Format", "select", [["long", "Saturday, 31 October 2026"], ["DD/MM/YYYY", "DD/MM/YYYY"], ["MM/DD/YYYY", "MM/DD/YYYY"], ["YYYY-MM-DD", "YYYY-MM-DD"]]],
      ["time_format", "Time Format", "select", [["12", "12-hour"], ["24", "24-hour"]]],
      ["default_working_days", "Default Working Days", "weekdays"],
    ]],
    ["Company Profile", [
      ["company_tagline", "Tagline", "text", "A short line under the company name."],
      ["company_phone", "Phone", "text"],
      ["company_email", "Email", "text"],
      ["company_website", "Website", "text"],
      ["company_registration", "Registration Number", "text"],
      ["company_address", "Address", "text"],
      ["company_about", "About the Company", "textarea", "Shown on the company card. Click the company name on the attendance screen to see it."],
    ]],
    ["Attendance Settings", [
      ["default_grace_minutes", "Default Grace Period (minutes)", "number", "Used as the starting value for new shifts."],
      ["default_early_clock_in_minutes", "Early Clock-In Allowance (minutes)", "number", "Starting value for new shifts."],
      ["default_late_clock_out_minutes", "Clock-Out Rule: Late Allowance (minutes)", "number", "Starting value for new shifts. After this, a missing clock-out becomes Incomplete Attendance."],
      ["max_session_hours", "Clock-Out Rule: Longest Unscheduled Session (hours)", "number"],
      ["duplicate_window_seconds", "Duplicate Attendance Prevention (seconds)", "number", "Blocks a clock-out this soon after clocking in."],
      ["allow_multiple_sessions", "Allow more than one session per shift", "checkbox"],
      ["allow_unscheduled_clock_in", "Allow clock-in when not scheduled", "checkbox"],
      ["half_day_percent", "Half-Day Threshold (% of scheduled hours)", "number"],
      ["overtime_threshold_minutes", "Minimum Overtime (minutes)", "number", "Shorter stays past shift end are not counted."],
      ["attendance_confirmation_seconds", "Attendance Confirmation Duration (seconds)", "number", "How long the result stays before the screen resets."],
    ]],
    ["Employee Settings", [
      ["max_employees", "Maximum Employee Count", "number", "Cannot exceed 999."],
      ["employee_id_prefix", "Employee ID Prefix", "text", "Applies to new employees only."],
      ["employee_id_start", "Employee ID Starting Number", "number", "IDs already issued are never reused."],
      ["employee_types", "Employee Types", "list", "Separate with commas, such as Technician, Manager, Cleaner."],
      ["currency", "Currency", "text", "Shown beside pay figures, such as LKR. Optional."],
    ]],
    ["Display Settings", [
      ["clock_enabled", "Show the large clock on the attendance screen", "checkbox"],
      ["theme", "Screen Theme", "select", [["light", "Light"], ["dark", "Dark"]]],
      ["success_notification_seconds", "Success Notification Duration (seconds)", "number"],
      ["search_mode", "Employee Search Behavior", "select", [["contains", "Name contains the text"], ["starts_with", "Name starts with the text"]]],
      ["terminal_idle_reset_seconds", "Clear Selection When Idle (seconds)", "number"],
    ]],
    ["Admin Settings", [
      ["session_timeout_minutes", "Session Timeout (minutes)", "number"],
      ["login_max_attempts", "Login Attempts Before Lockout", "number"],
      ["login_lockout_minutes", "Login Lockout Duration (minutes)", "number"],
    ]],
  ];

  function fieldHtml([key, label, type, extra]) {
    const v = settings[key];
    const help = typeof extra === "string" ? `<span class="help">${esc(extra)}</span>` : "";
    const id = `s_${key}`;
    if (type === "checkbox") {
      return `<div class="field field-check"><input id="${id}" data-key="${key}" data-type="checkbox" type="checkbox" ${v ? "checked" : ""}><label for="${id}">${esc(label)}</label></div>`;
    }
    if (type === "textarea") {
      return `<div class="field"><label for="${id}">${esc(label)}</label><textarea id="${id}" data-key="${key}" data-type="text" rows="2">${esc(v)}</textarea>${help}</div>`;
    }
    if (type === "weekdays") {
      const boxes = Admin.WEEKDAYS.map((d, i) => `<label><input type="checkbox" data-day="${i}" ${v.includes(i) ? "checked" : ""}>${d}</label>`).join("");
      return `<div class="field"><label>${esc(label)}</label><div class="weekdays" data-key="${key}" data-type="weekdays">${boxes}</div></div>`;
    }
    if (type === "select" || type === "timezone") {
      const options = type === "timezone" ? timezones.map((z) => [z, z]) : extra;
      const list = options.map(([val, text]) => `<option value="${esc(val)}" ${val === v ? "selected" : ""}>${esc(text)}</option>`).join("");
      return `<div class="field"><label for="${id}">${esc(label)}</label><select id="${id}" data-key="${key}" data-type="text">${list}</select>${help}</div>`;
    }
    return `<div class="field"><label for="${id}">${esc(label)}</label><input id="${id}" data-key="${key}" data-type="${type}" type="${type === "list" ? "text" : type}" value="${esc(type === "list" ? v.join(", ") : v)}">${help}</div>`;
  }

  root.innerHTML = `
    <section class="panel"><div class="panel-head"><h2>Company Logo</h2></div>
      <div class="panel-body" style="display:flex;gap:16px;align-items:center;flex-wrap:wrap">
        <img id="logoPreview" alt="Current logo" style="width:64px;height:64px;object-fit:contain;border:1px solid var(--line);border-radius:10px">
        <input id="logoFile" type="file" accept="image/png,image/jpeg,image/webp" style="max-width:320px">
        <button class="btn" id="logoUpload" type="button">Upload Logo</button>
        <button class="btn btn-danger" id="logoRemove" type="button">Remove</button>
        <span class="muted">PNG, JPEG or WebP, up to 512 KB.</span>
      </div></section>
    <form id="form" style="display:contents" novalidate>
      ${SECTIONS.map(([title, fields]) => `<section class="panel"><div class="panel-head"><h2>${esc(title)}</h2></div>
        <div class="panel-body settings-section">${fields.map(fieldHtml).join("")}</div></section>`).join("")}
      <div id="formError" class="form-error" hidden></div>
      <div><button class="btn btn-primary" type="submit">Save Settings</button></div>
    </form>
    <p class="muted" style="margin:0">Role permissions are managed under <a href="/admin/users">User Access Management</a>.</p>`;

  const form = root.querySelector("#form");

  function renderLogo() {
    root.querySelector("#logoPreview").src = logoRev ? `/api/public/logo?v=${encodeURIComponent(logoRev)}` : "/assets/img/logo.svg";
    root.querySelector("#logoRemove").disabled = !logoRev;
  }
  renderLogo();

  root.querySelector("#logoUpload").addEventListener("click", async () => {
    const file = root.querySelector("#logoFile").files[0];
    if (!file) { Admin.toast("Choose an image file first.", "error"); return; }
    const data = new FormData();
    data.append("logo", file);
    try {
      const r = await Admin.call("POST", "/api/admin/settings/logo", data);
      logoRev = r.logo_rev;
      renderLogo();
      Admin.toast(r.message, "success");
    } catch (err) { Admin.toast(err.message, "error"); }
  });
  root.querySelector("#logoRemove").addEventListener("click", async () => {
    try {
      const r = await Admin.call("DELETE", "/api/admin/settings/logo");
      logoRev = null;
      renderLogo();
      Admin.toast(r.message, "success");
    } catch (err) { Admin.toast(err.message, "error"); }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const box = root.querySelector("#formError");
    box.hidden = true;
    const body = {};
    form.querySelectorAll("[data-key]").forEach((el) => {
      const { key, type } = el.dataset;
      if (type === "checkbox") body[key] = el.checked;
      else if (type === "weekdays") body[key] = [...el.querySelectorAll("input:checked")].map((i) => +i.dataset.day);
      else if (type === "list") body[key] = el.value.split(",").map((t) => t.trim()).filter(Boolean);
      else if (type === "number") body[key] = el.value === "" ? null : Number(el.value);
      else body[key] = el.value;
    });
    try {
      await Admin.call("PUT", "/api/admin/settings", body);
      Admin.toast("Settings saved.", "success");
      setTimeout(() => location.reload(), 700);
    } catch (err) {
      box.textContent = err.message;
      box.hidden = false;
      box.scrollIntoView({ block: "center" });
    }
  });
});
