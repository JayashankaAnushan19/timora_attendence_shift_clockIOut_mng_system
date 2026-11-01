/* Attendance terminal: select employee, verify status, press the available button. */
(() => {
  const $ = (id) => document.getElementById(id);
  const HEARTBEAT_MS = 30000;
  const OFFLINE_RETRY_MS = 4000;
  const CONFIG_REFRESH_MS = 300000;
  const REQUEST_TIMEOUT_MS = 10000;
  const PILL = { green: "pill-green", red: "pill-red", orange: "pill-orange", grey: "", blue: "pill-blue" };

  let config = { success_notification_seconds: 5, attendance_confirmation_seconds: 8, terminal_idle_reset_seconds: 30 };
  let serverOffset = 0;   // server clock minus browser clock, in ms
  let online = true;
  let selected = null;    // the employee's status from the server
  let busy = false;
  let pickerOpen = false;
  let results = [];
  let shownQuery = null;
  let activeIndex = -1;
  let searchSeq = 0;
  let searchTimer = null;
  let idleTimer = null;
  let resetTimer = null;
  let overlayTimer = null;
  let heartbeatTimer = null;
  let locked = false;     // showing a confirmation; buttons stay off until reset
  // Reused on retry so a request that actually reached the server isn't applied twice.
  let pending = null;

  /* ---------- Clock ---------- */
  function renderClock() {
    const now = new Date(Date.now() + serverOffset);
    const c = T.clockParts(now);
    $("clock").innerHTML = T.esc(c.time) + (c.ampm ? `<span class="ampm">${T.esc(c.ampm)}</span>` : "");
    $("topTime").textContent = T.fmtClock(now);
    const day = T.fmtToday(now);
    $("date").textContent = day;
    $("topDate").textContent = day;
  }

  function startClock() {
    renderClock();
    // Align to the next whole second so the display doesn't drift or skip.
    setTimeout(() => { renderClock(); setInterval(renderClock, 1000); }, 1000 - ((Date.now() + serverOffset) % 1000));
  }

  /* ---------- Connection ---------- */
  function setOnline(value) {
    if (online === value) return;
    online = value;
    $("offline").hidden = online;
    if (online && selected && !locked) refreshSelected();
    renderButtons();
  }

  async function heartbeat() {
    clearTimeout(heartbeatTimer);
    try {
      const started = Date.now();
      const data = await T.api("GET", "/api/public/time", undefined, { timeout: REQUEST_TIMEOUT_MS });
      const ended = Date.now();
      serverOffset = new Date(data.utc).getTime() + 500 - (started + ended) / 2;
      setOnline(true);
    } catch (err) {
      setOnline(false);
    }
    heartbeatTimer = setTimeout(heartbeat, online ? HEARTBEAT_MS : OFFLINE_RETRY_MS);
  }

  async function loadConfig() {
    try {
      const data = await T.api("GET", "/api/public/config", undefined, { timeout: REQUEST_TIMEOUT_MS });
      applyConfig(data.config);
    } catch (err) {
      setOnline(false);
    }
  }

  function applyConfig(next) {
    config = next;
    T.setDisplay({ timezone: next.timezone, time_format: next.time_format, date_format: next.date_format });
    document.documentElement.dataset.theme = next.theme;
    document.title = next.app_name;
    $("brandName").textContent = next.app_name.toUpperCase();
    $("company").textContent = next.company_name;
    $("tz").textContent = `Time zone: ${next.timezone}`;
    $("clockWrap").hidden = !next.clock_enabled;
    const logo = next.logo_rev ? `/api/public/logo?v=${encodeURIComponent(next.logo_rev)}` : "/assets/img/logo.svg";
    $("brandLogo").src = logo;
    $("bizLogo").src = logo;
    renderClock();
  }

  /* ---------- Employee dropdown ---------- */
  function openPicker() {
    if (pickerOpen) return;
    // The next person shouldn't have to wait out the previous confirmation.
    if (locked) clearSelection();
    pickerOpen = true;
    $("pickerPanel").hidden = false;
    $("pickerBtn").setAttribute("aria-expanded", "true");
    $("search").value = "";
    $("search").focus();
    touchIdle();
    runSearch("");
  }

  function closePicker({ refocus = false } = {}) {
    if (!pickerOpen) return;
    pickerOpen = false;
    searchSeq++;
    clearTimeout(searchTimer);
    $("pickerPanel").hidden = true;
    $("pickerBtn").setAttribute("aria-expanded", "false");
    results = [];
    activeIndex = -1;
    if (refocus) $("pickerBtn").focus();
  }

  function renderPickerLabel() {
    $("pickerLabel").textContent = selected ? `${selected.employee_name} (${selected.employee_id})` : "Select employee";
    $("pickerBtn").classList.toggle("is-empty", !selected);
    $("clear").hidden = !selected;
  }

  function renderResults(emptyMessage) {
    const list = $("results");
    list.replaceChildren();
    if (!results.length) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = emptyMessage;
      list.append(li);
    }
    results.forEach((emp, i) => {
      const li = document.createElement("li");
      li.setAttribute("role", "option");
      li.setAttribute("aria-selected", String(i === activeIndex));
      if (i === activeIndex) li.className = "active";
      const id = document.createElement("b");
      id.textContent = emp.employee_id;
      const name = document.createElement("span");
      name.textContent = emp.name;
      li.append(id, name);
      li.addEventListener("click", () => selectEmployee(emp.employee_id));
      list.append(li);
    });
    const active = list.querySelector(".active");
    if (active) active.scrollIntoView({ block: "nearest" });
  }

  async function runSearch(query) {
    const seq = ++searchSeq;
    try {
      const data = await T.api("GET", `/api/public/employees/search?q=${encodeURIComponent(query)}`, undefined, { timeout: REQUEST_TIMEOUT_MS });
      if (seq !== searchSeq) return;
      results = data.items;
      shownQuery = query;
      // With a search typed, Enter picks the top match straight away.
      activeIndex = query && results.length ? 0 : -1;
      renderResults(query ? "No matching employee found." : "No employees have been added yet.");
    } catch (err) {
      if (seq !== searchSeq) return;
      results = [];
      renderResults("Could not load employees. Please check the connection.");
    }
  }

  function onSearchInput() {
    touchIdle();
    clearTimeout(searchTimer);
    const query = $("search").value.trim();
    searchTimer = setTimeout(() => runSearch(query), 120);
  }

  /* Fast typing can reach Enter before the search has run, so finish it first. */
  async function pickFromSearch() {
    const query = $("search").value.trim();
    if (query !== shownQuery) {
      clearTimeout(searchTimer);
      await runSearch(query);
    }
    if (pickerOpen && results[activeIndex]) selectEmployee(results[activeIndex].employee_id);
  }

  function onSearchKey(event) {
    touchIdle();
    if (event.key === "ArrowDown" && results.length) {
      activeIndex = (activeIndex + 1) % results.length;
      renderResults();
      event.preventDefault();
    } else if (event.key === "ArrowUp" && results.length) {
      activeIndex = activeIndex <= 0 ? results.length - 1 : activeIndex - 1;
      renderResults();
      event.preventDefault();
    } else if (event.key === "Enter") {
      event.preventDefault();
      pickFromSearch();
    } else if (event.key === "Escape") {
      closePicker({ refocus: true });
    } else if (event.key === "Tab") {
      closePicker();
    }
  }

  /* Typing on the closed dropdown opens it and goes straight into the search box. */
  function onPickerButtonKey(event) {
    const typing = event.key.length === 1 && event.key !== " " && !event.ctrlKey && !event.metaKey && !event.altKey;
    if (event.key === "ArrowDown" || typing) {
      if (event.key === "ArrowDown") event.preventDefault();
      openPicker();
    }
  }

  /* ---------- Selected employee ---------- */
  async function selectEmployee(code) {
    closePicker();
    hideError();
    touchIdle();
    const seq = ++searchSeq;
    try {
      const status = await T.api("GET", `/api/public/employees/${encodeURIComponent(code)}/status`, undefined, { timeout: REQUEST_TIMEOUT_MS });
      if (seq !== searchSeq) return;
      setOnline(true);
      showEmployee(status);
      const button = status.action === "clock_in" ? $("btnIn") : status.action === "clock_out" ? $("btnOut") : $("pickerBtn");
      button.focus();
    } catch (err) {
      if (err.code === "NETWORK_ERROR") setOnline(false);
      showError(err.message);
    }
  }

  async function refreshSelected() {
    const code = selected && selected.employee_id;
    if (!code) return;
    try {
      const status = await T.api("GET", `/api/public/employees/${encodeURIComponent(code)}/status`, undefined, { timeout: REQUEST_TIMEOUT_MS });
      if (selected && selected.employee_id === code && !locked) showEmployee(status);
    } catch (err) {
      if (err.code === "NETWORK_ERROR") setOnline(false);
      else clearSelection();
    }
  }

  function showEmployee(status) {
    selected = status;
    renderPickerLabel();
    renderCard();
    renderButtons();
  }

  function renderCard() {
    const card = $("card");
    if (!selected) {
      card.className = "t-card placeholder";
      card.textContent = "Select your name to begin.";
      return;
    }
    const s = selected;
    const shift = s.shift ? `${T.esc(s.shift.name)}: ${T.esc(s.shift.start)} – ${T.esc(s.shift.end)}. ` : "";
    card.className = "t-card";
    card.innerHTML = `
      <div class="t-card-head">
        <div>
          <div class="t-card-name">${T.esc(s.employee_name)}</div>
          <div class="t-card-id">Employee ID: ${T.esc(s.employee_id)}</div>
        </div>
        <span class="pill t-status ${PILL[s.indicator] || ""}">${T.esc(s.status_label)}</span>
      </div>
      <div class="t-card-info">${shift}${T.esc(s.message)}</div>`;
  }

  function renderButtons() {
    const action = selected && online && !locked ? selected.action : null;
    const inBtn = $("btnIn");
    const outBtn = $("btnOut");
    inBtn.disabled = busy || action !== "clock_in";
    outBtn.disabled = busy || action !== "clock_out";
    inBtn.className = `t-btn in${action === "clock_in" ? " available" : ""}${busy && action === "clock_in" ? " busy" : ""}`;
    outBtn.className = `t-btn out${action === "clock_out" ? " available" : ""}${busy && action === "clock_out" ? " busy" : ""}`;
    inBtn.textContent = busy && action === "clock_in" ? "Processing…" : "CLOCK IN";
    outBtn.textContent = busy && action === "clock_out" ? "Processing…" : "CLOCK OUT";
  }

  function clearSelection() {
    selected = null;
    pending = null;
    locked = false;
    clearTimeout(resetTimer);
    hideError();
    closePicker();
    renderPickerLabel();
    renderCard();
    renderButtons();
  }

  function showError(message) {
    $("error").textContent = message;
    $("error").hidden = false;
  }

  function hideError() {
    $("error").hidden = true;
  }

  /* ---------- Clock in / out ---------- */
  async function submit(action) {
    if (busy || !selected || selected.action !== action || !online) return;
    const code = selected.employee_id;
    const key = `${code}:${action}`;
    if (!pending || pending.key !== key) pending = { key, id: T.requestId() };

    busy = true;
    hideError();
    renderButtons();
    touchIdle();
    try {
      const result = await T.api("POST", "/api/attendance/clock",
        { employee_id: code, action, request_id: pending.id }, { timeout: REQUEST_TIMEOUT_MS });
      pending = null;
      busy = false;
      setOnline(true);
      onSuccess(result);
    } catch (err) {
      busy = false;
      if (err.code === "NETWORK_ERROR" || err.status >= 500) {
        // Unknown outcome: keep the request id, so a retry can't record it twice.
        if (err.code === "NETWORK_ERROR") setOnline(false);
        showError("Your attendance has NOT been confirmed. Please try again.");
        heartbeat();
        renderButtons();
      } else {
        pending = null;
        showError(err.message);
        await refreshSelected();
        renderButtons();
      }
    }
  }

  function onSuccess(result) {
    const isIn = result.action === "clock_in";
    selected = {
      ...selected,
      status: result.status,
      status_label: isIn ? "CLOCKED IN" : "CLOCKED OUT",
      indicator: isIn ? "green" : "red",
      action: null,
      message: result.note || "",
    };
    locked = true;
    renderCard();
    renderButtons();

    $("successBox").className = `t-success${isIn ? "" : " out"}`;
    $("sName").textContent = result.employee_name;
    $("sId").textContent = result.employee_id;
    $("sAction").textContent = isIn ? "Clocked In Successfully" : "Clocked Out Successfully";
    $("sTime").textContent = `Time: ${T.fmtClock(new Date(result.timestamp))}`;
    $("sNote").textContent = result.note || "";
    $("overlay").hidden = false;

    clearTimeout(overlayTimer);
    overlayTimer = setTimeout(() => { $("overlay").hidden = true; }, config.success_notification_seconds * 1000);
    clearTimeout(resetTimer);
    const resetAfter = Math.max(config.attendance_confirmation_seconds, config.success_notification_seconds);
    resetTimer = setTimeout(() => { clearSelection(); $("pickerBtn").focus(); }, resetAfter * 1000);
  }

  /* A shared terminal must not leave one person's profile selected for the next. */
  function touchIdle() {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      if (!locked && !busy && (selected || pickerOpen)) {
        clearSelection();
        $("pickerBtn").focus();
      }
    }, config.terminal_idle_reset_seconds * 1000);
  }

  /* ---------- Company card ---------- */
  const CARD_ICONS = {
    phone: "M5 4h4l2 5-2.500 1.500a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A15 15 0 0 1 3 6a2 2 0 0 1 2-2z",
    email: "M3 6h18v12H3zM3 7l9 7 9-7",
    website: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18",
    address: "M12 21s7-6.500 7-12a7 7 0 1 0-14 0c0 5.500 7 12 7 12zM12 11.500a2.500 2.500 0 1 0 0-5 2.500 2.500 0 0 0 0 5z",
  };

  function cardRow(kind, text, href) {
    const li = document.createElement("li");
    const icon = document.createElement("span");
    icon.className = "biz-icon";
    icon.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${CARD_ICONS[kind]}"/></svg>`;
    const value = document.createElement(href ? "a" : "span");
    value.textContent = text;
    if (href) {
      value.href = href;
      if (kind === "website") {
        value.target = "_blank";
        value.rel = "noopener noreferrer";
      }
    }
    li.append(icon, value);
    return li;
  }

  function openCompanyCard() {
    const profile = config.company_profile || {};
    const show = (id, text) => { $(id).textContent = text || ""; $(id).hidden = !text; };
    $("bizName").textContent = config.company_name;
    show("bizTagline", profile.tagline);
    show("bizAbout", profile.about);
    show("bizFoot", profile.registration ? `Reg. No: ${profile.registration}` : "");

    const rows = $("bizRows");
    rows.replaceChildren();
    if (profile.phone) rows.append(cardRow("phone", profile.phone, `tel:${profile.phone.replace(/[^0-9+]/g, "")}`));
    if (profile.email) rows.append(cardRow("email", profile.email, `mailto:${profile.email}`));
    if (profile.website) rows.append(cardRow("website", profile.website.replace(/^https?:\/\//i, ""), /^https?:\/\//i.test(profile.website) ? profile.website : null));
    if (profile.address) rows.append(cardRow("address", profile.address));
    $("bizEmpty").hidden = Boolean(rows.children.length || profile.about);

    $("bizBack").hidden = false;
    $("bizClose").focus();
  }

  function closeCompanyCard() {
    if ($("bizBack").hidden) return;
    $("bizBack").hidden = true;
    $("company").focus();
  }

  /* ---------- Start ---------- */
  $("company").addEventListener("click", openCompanyCard);
  $("bizClose").addEventListener("click", closeCompanyCard);
  $("bizBack").addEventListener("click", (event) => { if (event.target === $("bizBack")) closeCompanyCard(); });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeCompanyCard(); });
  $("search").addEventListener("input", onSearchInput);
  $("search").addEventListener("keydown", onSearchKey);
  $("pickerBtn").addEventListener("click", () => { if (pickerOpen) closePicker(); else openPicker(); });
  $("pickerBtn").addEventListener("keydown", onPickerButtonKey);
  document.addEventListener("pointerdown", (event) => { if (!$("picker").contains(event.target)) closePicker(); });
  $("clear").addEventListener("click", () => { clearSelection(); $("pickerBtn").focus(); });
  $("btnIn").addEventListener("click", () => submit("clock_in"));
  $("btnOut").addEventListener("click", () => submit("clock_out"));
  $("overlay").addEventListener("click", () => { $("overlay").hidden = true; });
  window.addEventListener("offline", () => setOnline(false));
  window.addEventListener("online", heartbeat);

  (async () => {
    await loadConfig();
    await heartbeat();
    startClock();
    renderButtons();
    setInterval(loadConfig, CONFIG_REFRESH_MS);
  })();
})();
