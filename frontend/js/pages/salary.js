Admin.start("salary", "Salary", async (root) => {
  const { esc } = T;
  const money = Admin.fmtMoney;
  const currency = Admin.state.display.currency;
  const withCurrency = (label) => (currency ? `${label} (${currency})` : label);
  const thisMonth = new Intl.DateTimeFormat("en-CA", { timeZone: T.display.timezone }).format(new Date()).slice(0, 7);

  root.innerHTML = `
    <div class="stat-row" id="totals"></div>
    <section class="panel">
      <div class="toolbar">
        <div><label for="month">Month</label><input id="month" type="month" value="${thisMonth}"></div>
        <div class="grow"><label for="q">Employee</label><input id="q" type="search" placeholder="Name or employee ID"></div>
      </div>
      <div id="tbl"></div>
    </section>`;

  const month = () => root.querySelector("#month").value;
  const openSlip = (code) => window.open(`/admin/payslip?employee=${encodeURIComponent(code)}&month=${encodeURIComponent(month())}`, "_blank");

  function renderTotals(totals) {
    const cards = [["Total Basic", totals.basic], ["Allowances", totals.allowances], ["Overtime", totals.overtime],
      ["Gross Pay", totals.gross], ["Deductions", totals.deductions], ["Net Pay", totals.net]];
    root.querySelector("#totals").innerHTML = cards.map(([label, value]) =>
      `<div class="stat"><div class="label">${esc(withCurrency(label))}</div><div class="value stat-money">${esc(money(value))}</div></div>`).join("");
  }

  const statusLabel = (s) => (s === "final" ? '<span class="pill pill-green">Finalized</span>' : '<span class="pill">Draft</span>');

  function lineRows(items, kind, editable, panel, code) {
    const rows = items.filter((i) => i.kind === kind);
    if (!rows.length) return `<p class="muted salary-none">None added.</p>`;
    return `<ul class="salary-lines">${rows.map((i) => `<li>
      <span>${esc(i.name)} <small class="muted">${i.repeats ? "Every month" : "This month only"}</small></span>
      <b>${esc(money(i.amount))}</b>
      ${editable ? `<button class="btn btn-sm btn-danger" type="button" data-remove="${i.id}">Remove</button>` : ""}
    </li>`).join("")}</ul>`;
  }

  function addItemForm(code, kind, reload) {
    const label = kind === "allowance" ? "Allowance" : "Deduction";
    Admin.formModal({
      title: `Add ${label}`, submitLabel: `Add ${label.toLowerCase()}`,
      values: { repeats: "yes" },
      fields: [
        { name: "name", label: `${label} Name`, required: true, full: true, placeholder: kind === "allowance" ? "Such as Transport" : "Such as Loan repayment" },
        { name: "amount", label: withCurrency("Amount"), type: "number", min: 0, step: "0.01", required: true },
        { name: "repeats", label: "Applies to", type: "select", options: [["yes", "Every month from now on"], ["no", "This month only"]] },
      ],
      onSubmit: async (v) => {
        const r = await Admin.call("POST", `/api/admin/payroll/${encodeURIComponent(code)}/items`, {
          kind, name: v.name, amount: v.amount, month: month(), repeats: v.repeats === "yes",
        });
        Admin.toast(r.message, "success");
        await reload();
      },
    });
  }

  function viewEmployee(row) {
    const code = row.employee_id;
    const m = Admin.modal({ title: `${row.name} (${code})`, wide: true, footer: false, body: "" });

    async function reload() {
      let d;
      try {
        d = await Admin.call("GET", `/api/admin/payroll/${encodeURIComponent(code)}?month=${encodeURIComponent(month())}`);
      } catch (err) {
        m.body.innerHTML = `<div class="form-error">${esc(err.message)}</div>`;
        return;
      }
      const t = d.slip.totals;
      const editable = d.slip.status !== "final";
      m.body.innerHTML = `
        <div class="salary-head">${statusLabel(d.slip.status)}
          ${editable ? "" : `<span class="muted">By ${esc(d.slip.finalized_by)}, ${esc(T.fmtDateTime(d.slip.finalized_at))}. These amounts no longer change.</span>`}
        </div>
        <form id="payForm" class="form-grid salary-pay">
          <div class="field"><label for="payBasic">${esc(withCurrency("Basic Salary"))}</label>
            <input id="payBasic" type="number" min="0" step="0.01" value="${esc(d.basic_salary)}"></div>
          <div class="field"><label for="payRate">${esc(withCurrency("OT Pay Rate per Hour"))}</label>
            <input id="payRate" type="number" min="0" step="0.01" value="${esc(d.ot_rate)}"></div>
          <div class="field salary-pay-save"><button class="btn" type="submit">Save Pay Details</button></div>
        </form>
        <div class="salary-columns">
          <section><div class="salary-section-head"><h3>Allowances</h3>${editable ? '<button class="btn btn-sm" type="button" id="addAllowance">Add</button>' : ""}</div>
            ${lineRows(d.items, "allowance", editable)}</section>
          <section><div class="salary-section-head"><h3>Deductions</h3>${editable ? '<button class="btn btn-sm" type="button" id="addDeduction">Add</button>' : ""}</div>
            ${lineRows(d.items, "deduction", editable)}</section>
        </div>
        <dl class="salary-sum">
          <div><dt>Basic Salary</dt><dd>${esc(money(t.basic))}</dd></div>
          <div><dt>Allowances</dt><dd>${esc(money(t.allowances))}</dd></div>
          <div><dt>Overtime${d.slip.overtime_seconds ? ` (${esc(T.fmtDuration(d.slip.overtime_seconds))})` : ""}</dt><dd>${esc(money(t.overtime))}</dd></div>
          <div class="salary-sum-line"><dt>Gross Pay</dt><dd>${esc(money(t.gross))}</dd></div>
          <div><dt>Deductions</dt><dd>- ${esc(money(t.deductions))}</dd></div>
          <div class="salary-sum-net"><dt>${esc(withCurrency("Net Salary"))}</dt><dd>${esc(money(t.net))}</dd></div>
        </dl>
        ${t.net < 0 ? '<div class="banner danger">Deductions are more than the gross pay, so the net salary is negative.</div>' : ""}
        <div class="salary-actions">
          ${editable ? '<button class="btn" type="button" id="finalize">Finalize Slip</button>' : '<button class="btn" type="button" id="reopen">Reopen Slip</button>'}
          <button class="btn btn-primary" type="button" id="print">Print Slip</button>
        </div>`;

      const after = async (r) => {
        Admin.toast(r.message, "success");
        await Promise.all([reload(), table.refresh()]);
      };
      const run = (promise) => promise.then(after).catch((err) => Admin.toast(err.message, "error"));

      m.body.querySelector("#payForm").addEventListener("submit", (event) => {
        event.preventDefault();
        run(Admin.call("PUT", `/api/admin/payroll/${encodeURIComponent(code)}/pay`, {
          basic_salary: m.body.querySelector("#payBasic").value,
          ot_rate: m.body.querySelector("#payRate").value,
        }));
      });
      m.body.querySelector("#print").addEventListener("click", () => openSlip(code));
      const refreshBoth = () => Promise.all([reload(), table.refresh()]);
      const add = (id, kind) => {
        const button = m.body.querySelector(id);
        if (button) button.addEventListener("click", () => addItemForm(code, kind, refreshBoth));
      };
      add("#addAllowance", "allowance");
      add("#addDeduction", "deduction");
      m.body.querySelectorAll("[data-remove]").forEach((button) => button.addEventListener("click", () => {
        run(Admin.call("POST", `/api/admin/payroll/items/${button.dataset.remove}/remove`, { month: month() }));
      }));
      const finalize = m.body.querySelector("#finalize");
      if (finalize) {
        finalize.addEventListener("click", async () => {
          if (!await Admin.confirm({ title: "Finalize slip", message: "Lock this month's amounts for this employee? Later changes to pay will not affect this slip.", confirmLabel: "Finalize" })) return;
          run(Admin.call("POST", `/api/admin/payroll/${encodeURIComponent(code)}/finalize`, { month: month() }));
        });
      }
      const reopen = m.body.querySelector("#reopen");
      if (reopen) {
        reopen.addEventListener("click", async () => {
          if (!await Admin.confirm({ title: "Reopen slip", message: "Reopen this slip? It will show current figures again, which may differ from the finalized amounts.", confirmLabel: "Reopen", danger: true })) return;
          run(Admin.call("POST", `/api/admin/payroll/${encodeURIComponent(code)}/reopen`, { month: month() }));
        });
      }
    }
    reload();
  }

  const table = Admin.table(root.querySelector("#tbl"), {
    sort: "name",
    perPage: 50,
    empty: "No employees for this month.",
    columns: [
      { key: "employee_id", label: "ID", sortable: true },
      { key: "name", label: "Employee", sortable: true },
      { key: "employee_type", label: "Type", sortable: true },
      { key: "basic", label: "Basic", kind: "money", num: true, sortable: true },
      { key: "allowances", label: "Allowances", kind: "money", num: true, sortable: true },
      { key: "overtime", label: "Overtime", kind: "money", num: true, sortable: true },
      { key: "gross", label: "Gross", kind: "money", num: true, sortable: true },
      { key: "deductions", label: "Deductions", kind: "money", num: true, sortable: true },
      { key: "net", label: "Net Salary", kind: "money", num: true, sortable: true },
      { key: "status", label: "Slip", sortable: true, render: (r) => statusLabel(r.status) },
    ],
    actions: (r) => [
      { label: "View", onClick: () => viewEmployee(r) },
      { label: "Print", onClick: () => openSlip(r.employee_id) },
    ],
    load: Admin.localLoad(async () => {
      const q = root.querySelector("#q").value.trim();
      const data = await Admin.call("GET", `/api/admin/payroll?${Admin.queryString({ month: month(), q })}`);
      renderTotals(data.totals);
      return data.items;
    }),
  });

  root.querySelector("#month").addEventListener("change", () => { if (month()) table.reload(); });
  root.querySelector("#q").addEventListener("input", Admin.debounce(() => table.reload()));
  await table.refresh();
});
