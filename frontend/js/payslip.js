/* Printable salary slip: A5 width, growing taller when there are more lines. */
(async () => {
  const { esc } = T;
  const $ = (id) => document.getElementById(id);
  const A5_HEIGHT_MM = 210;
  const MM_PER_PX = 25.4 / 96;
  const params = new URLSearchParams(location.search);
  const code = params.get("employee") || "";
  const month = params.get("month") || "";

  const money = (value) => Number(value || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  function monthLabel(value) {
    const [year, number] = value.split("-").map(Number);
    return new Date(Date.UTC(year, number - 1, 1)).toLocaleDateString("en-GB", { timeZone: "UTC", month: "long", year: "numeric" });
  }

  function lines(rows, emptyText) {
    if (!rows.length) return `<tr><td class="slip-none" colspan="2">${esc(emptyText)}</td></tr>`;
    return rows.map((row) => {
      const detail = row.minutes
        ? `<small>${esc(T.fmtDuration(row.minutes * 60))} at ${esc(money(row.rate))} per hour</small>` : "";
      return `<tr><td>${esc(row.label)}${detail}</td><td class="slip-amount">${esc(money(row.amount))}</td></tr>`;
    }).join("");
  }

  function render(data) {
    const { slip, company, currency } = data;
    const person = slip.employee;
    const totals = slip.totals;
    const attendance = slip.attendance;
    const website = (company.website || "").replace(/^https?:\/\//i, "");
    const contact = [company.phone, company.email, website].filter(Boolean).map(esc).join(" &nbsp;|&nbsp; ");
    const logo = data.logo_rev ? `/api/public/logo?v=${encodeURIComponent(data.logo_rev)}` : "/assets/img/logo.svg";
    const details = [
      ["Employee", person.name], ["Employee ID", person.employee_id],
      ["Designation", person.designation], ["Department", person.department],
      ["Type", person.employee_type], ["Date of Joining", T.fmtDate(person.date_of_joining)],
    ].filter(([, value]) => value);

    document.title = `Salary Slip ${person.employee_id} ${month}`;
    $("slip").innerHTML = `
      <header class="slip-head">
        <img src="${esc(logo)}" alt="">
        <div>
          <h1>${esc(company.name)}</h1>
          ${company.address ? `<p>${esc(company.address)}</p>` : ""}
          ${contact ? `<p>${contact}</p>` : ""}
          ${company.registration ? `<p>Reg. No: ${esc(company.registration)}</p>` : ""}
        </div>
      </header>
      <div class="slip-title">
        <h2>SALARY SLIP</h2>
        <div>${esc(monthLabel(month))}${slip.status === "final" ? "" : '<span class="slip-draft">DRAFT</span>'}</div>
      </div>
      <dl class="slip-person">${details.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>

      <table class="slip-table">
        <thead><tr><th>Earnings</th><th class="slip-amount">Amount${currency ? ` (${esc(currency)})` : ""}</th></tr></thead>
        <tbody>${lines(slip.earnings, "None")}</tbody>
        <tfoot><tr><td>Gross Pay</td><td class="slip-amount">${esc(money(totals.gross))}</td></tr></tfoot>
      </table>

      <table class="slip-table">
        <thead><tr><th>Deductions</th><th class="slip-amount">Amount${currency ? ` (${esc(currency)})` : ""}</th></tr></thead>
        <tbody>${lines(slip.deductions, "No deductions")}</tbody>
        <tfoot><tr><td>Total Deductions</td><td class="slip-amount">${esc(money(totals.deductions))}</td></tr></tfoot>
      </table>

      <div class="slip-net"><span>Net Salary</span><strong>${currency ? `${esc(currency)} ` : ""}${esc(money(totals.net))}</strong></div>

      ${attendance ? `<div class="slip-attendance">
        <div><b>${esc(attendance.present)}</b>Days Present</div>
        <div><b>${esc(attendance.absent)}</b>Absent</div>
        <div><b>${esc(attendance.leave)}</b>Leave</div>
        <div><b>${esc(attendance.late)}</b>Late</div>
        <div><b>${esc(T.fmtDuration(attendance.worked_seconds))}</b>Worked</div>
      </div>` : ""}

      <div class="slip-signs"><div>Prepared by</div><div>Received by</div></div>
      <footer class="slip-credit">Developed by JS_Software</footer>`;
    $("slip").hidden = false;
  }

  /* Browsers can't size a page automatically, so measure the slip and set the page to fit. */
  function fitPrintPage() {
    const height = Math.max(A5_HEIGHT_MM, Math.ceil($("slip").getBoundingClientRect().height * MM_PER_PX) + 1);
    const style = document.createElement("style");
    style.textContent = `@page { size: 148mm ${height}mm; margin: 0; } @media print { .slip { min-height: ${height}mm; } }`;
    document.head.append(style);
  }

  $("close").addEventListener("click", () => window.close());
  $("print").addEventListener("click", () => window.print());

  try {
    const me = await T.api("GET", "/api/admin/me");
    T.setDisplay(me.display);
    const data = await T.api("GET", `/api/admin/payroll/${encodeURIComponent(code)}?month=${encodeURIComponent(month)}`);
    render(data);
    await Promise.all([...$("slip").querySelectorAll("img")].map((img) => (
      img.complete ? null : new Promise((done) => { img.onload = done; img.onerror = done; })
    )));
    fitPrintPage();
    $("print").disabled = false;
    $("hint").textContent = data.slip.status === "final" ? "" : "This slip is a draft until finalized on the Salary page.";
  } catch (err) {
    if (err.status === 401) { location.href = "/admin/login"; return; }
    $("error").textContent = err.message;
    $("error").hidden = false;
  }
})();
